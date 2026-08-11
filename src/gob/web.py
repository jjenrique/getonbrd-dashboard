"""Dashboard: rutas y render, nada más.

    uvicorn gob.web:app --reload

**Ninguna ruta llama a la API de Get on Board.** Todo sale de SQLite, que se
carga entera en memoria (~1.000 ofertas, décimas de segundo) y se recarga sola
cuando el archivo cambia — así una reingesta se ve sin reiniciar el servidor.

Las agregaciones viven en `analytics.py`; aquí solo se traduce querystring →
`Filtros` y dataclass → JSON.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analytics as A
from . import config, queries
from .models import connect, init_db
from .normalize import load_aliases, load_my_stack

app = FastAPI(title="Demanda técnica · Get on Board", docs_url="/docs")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))

SENIORITY_ES = {
    "no_experience": "Sin experiencia",
    "junior": "Junior",
    "semi_senior": "Semi Senior",
    "senior": "Senior",
    "expert": "Expert",
    "sin_dato": "Sin declarar",
}


class Store:
    """Cache de la BD en memoria, invalidada por el mtime del archivo."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._mtime: float | None = None
        self._jobs: list[A.Job] = []
        self._snapshots: list[A.Snapshot] = []
        self._opciones: dict[str, list] = {"categorias": [], "seniorities": [], "paises": []}
        self._estado: dict[str, Any] = {}
        self._stack: frozenset[str] = frozenset()

    def refresh(self) -> None:
        mtime = self.db_path.stat().st_mtime if self.db_path.exists() else None
        # El stack y los alias son archivos que el usuario edita a mano: se
        # releen en cada refresco, no solo al arrancar.
        aliases = load_aliases(config.TAG_ALIASES_PATH)
        self._stack = frozenset(load_my_stack(config.MI_STACK_PATH, aliases))
        if mtime == self._mtime:
            return

        conn = connect(self.db_path)
        init_db(conn)   # BD vacía: mejor una página sin datos que un 500
        try:
            self._jobs = queries.load_jobs(conn)
            self._snapshots = queries.load_snapshots(conn)
            self._opciones = queries.load_opciones(conn)
            self._estado = queries.estado_bd(conn)
        finally:
            conn.close()
        self._mtime = mtime

    @property
    def jobs(self) -> list[A.Job]:
        self.refresh()
        return self._jobs

    @property
    def snapshots(self) -> list[A.Snapshot]:
        self.refresh()
        return self._snapshots

    @property
    def opciones(self) -> dict[str, list]:
        self.refresh()
        return self._opciones

    @property
    def estado(self) -> dict[str, Any]:
        self.refresh()
        return self._estado

    @property
    def mi_stack(self) -> frozenset[str]:
        self.refresh()
        return self._stack


store = Store(config.DB_PATH)


# ------------------------------------------------------------------ filtros


def _epoch(fecha: str | None, *, fin: bool = False) -> int | None:
    """"2026-08-01" -> epoch. `fin=True` apunta al final del día, para que un
    rango con la misma fecha en ambos extremos incluya ese día entero."""
    if not fecha:
        return None
    try:
        dia = datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dia.timestamp()) + (86399 if fin else 0)


def get_filtros(
    categoria: str | None = Query(None),
    seniority: str | None = Query(None),
    remoto: str | None = Query(None),
    pais: str | None = Query(None),
    empresa: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
) -> A.Filtros:
    return A.Filtros(
        categoria=categoria or None,
        seniority=seniority or None,
        remoto={"si": True, "no": False}.get((remoto or "").lower()),
        pais=pais or None,
        empresa=empresa or None,
        desde=_epoch(desde),
        hasta=_epoch(hasta, fin=True),
    )


Filtrados = Depends(get_filtros)


def _jobs(filtros: A.Filtros) -> list[A.Job]:
    return A.filtrar(store.jobs, filtros)


# -------------------------------------------------------------------- página


@app.get("/")
def dashboard(request: Request, filtros: A.Filtros = Filtrados):
    opciones = store.opciones
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "estado": store.estado,
            "opciones": opciones,
            "seniority_es": SENIORITY_ES,
            "query": str(request.url.query),
            "valores": dict(request.query_params),
            "n_snapshots": len(store.snapshots),
            "moneda": config.ASSUMED_CURRENCY,
            "mi_stack": sorted(store.mi_stack),
        },
    )


@app.get("/health")
def health():
    return {"ok": bool(store.estado.get("jobs")), **store.estado}


# ----------------------------------------------------------------- API JSON


@app.get("/api/resumen")
def api_resumen(filtros: A.Filtros = Filtrados):
    jobs = _jobs(filtros)
    r = A.resumen(jobs)
    salario = A.salario_por_tag(jobs)
    return {
        "ofertas": r.ofertas,
        "empresas": r.empresas,
        "tags": r.tags,
        "remotas": r.remotas,
        "con_salario": r.con_salario,
        "pct_con_salario": round(salario.cobertura),
        "mediana_salario": round(salario.mediana) if salario.mediana else None,
        "moneda": salario.currency or config.ASSUMED_CURRENCY,
        "total_sin_filtrar": len(store.jobs),
    }


@app.get("/api/top-tags")
def api_top_tags(limit: int = 25, filtros: A.Filtros = Filtrados):
    return [asdict(t) | {"solo_texto": t.solo_texto} for t in A.top_tags(_jobs(filtros), limit)]


@app.get("/api/tags-seniority")
def api_tags_seniority(limit: int = 12, filtros: A.Filtros = Filtrados):
    matriz = A.tags_por_seniority(_jobs(filtros), limit=limit)
    return {
        "seniorities": [
            {"clave": s, "nombre": SENIORITY_ES.get(s, s)} for s in matriz.seniorities
        ],
        "filas": [
            {"slug": slug, "name": name, "conteos": list(conteos)}
            for slug, name, conteos in matriz.filas
        ],
    }


@app.get("/api/co-ocurrencia")
def api_co_ocurrencia(tag: str, limit: int = 12, filtros: A.Filtros = Filtrados):
    jobs = _jobs(filtros)
    return {
        "tag": tag,
        "ofertas_del_tag": sum(1 for j in jobs if tag in j.slugs),
        "items": [asdict(c) for c in A.co_ocurrencia(jobs, tag, limit)],
    }


@app.get("/api/tendencia")
def api_tendencia(tags: str = "", filtros: A.Filtros = Filtrados):
    elegidos = [t.strip() for t in tags.split(",") if t.strip()]
    jobs = _jobs(filtros)
    if not elegidos:
        elegidos = [t.slug for t in A.top_tags(jobs, 4)]
    series = A.tendencia(jobs, store.snapshots, elegidos)
    return {
        "historico_insuficiente": bool(series and series[0].historico_insuficiente),
        "n_snapshots": len(store.snapshots),
        "series": [
            {
                "slug": s.slug,
                "variacion": s.variacion,
                "puntos": [asdict(p) for p in s.puntos],
            }
            for s in series
        ],
    }


@app.get("/api/salarios")
def api_salarios(limit: int = 15, filtros: A.Filtros = Filtrados):
    jobs = _jobs(filtros)
    global_ = A.salario_por_tag(jobs)
    return {
        "moneda": global_.currency or config.ASSUMED_CURRENCY,
        "min_muestra": A.MIN_MUESTRA_SALARIO,
        "global": _salario(global_),
        "items": [_salario(s) for s in A.salarios_por_tags(jobs, limit=limit)],
    }


def _salario(s: A.Salario) -> dict[str, Any]:
    return asdict(s) | {"suficiente": s.suficiente, "cobertura": round(s.cobertura)}


@app.get("/api/empresas")
def api_empresas(limit: int = 15, filtros: A.Filtros = Filtrados):
    return [
        asdict(e) | {"stack": [{"slug": s, "n": n} for s, n in e.stack]}
        for e in A.empresas_top(_jobs(filtros), limit)
    ]


@app.get("/api/brechas")
def api_brechas(limit: int = 20, filtros: A.Filtros = Filtrados):
    informe = A.brechas(_jobs(filtros), store.mi_stack, limit=limit)
    return {
        "total_ofertas": informe.total_ofertas,
        "cobertura_media": round(informe.cobertura_media),
        "stack": list(informe.stack),
        "sin_demanda": list(informe.sin_demanda),
        "items": [asdict(b) for b in informe.items],
        "stack_vacio": not informe.stack,
    }


@app.get("/api/jobs")
def api_jobs(limit: int = 200, offset: int = 0, filtros: A.Filtros = Filtrados):
    jobs = sorted(_jobs(filtros), key=lambda j: j.published_at or 0, reverse=True)
    pagina = jobs[offset : offset + limit]
    return {
        "total": len(jobs),
        "offset": offset,
        "items": [
            {
                "id": j.id,
                "title": j.title,
                "url": j.url,
                "empresa": j.company_name,
                "categoria": j.category_name,
                "seniority": SENIORITY_ES.get(j.seniority or "", j.seniority),
                "remote_modality": j.remote_modality,
                "paises": list(j.countries),
                "ciudades": list(j.cities),
                "min_salary": j.min_salary,
                "max_salary": j.max_salary,
                "moneda": j.currency,
                "published_at": j.published_at,
                "tags": sorted({t.slug for t in j.tags}),
            }
            for j in pagina
        ],
    }


@app.exception_handler(ValueError)
def _valor_invalido(request: Request, exc: ValueError):
    return JSONResponse({"error": str(exc)}, status_code=400)
