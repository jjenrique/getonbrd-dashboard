"""Congela el dashboard en un solo HTML que se abre sin servidor.

    python scripts/exportar_estatico.py                     # -> dist/dashboard.html
    python scripts/exportar_estatico.py --salida mi.html --db data/gob.db

Sirve para mirar el dashboard desde donde no se puede levantar uvicorn (el
teléfono, por ejemplo): el archivo lleva dentro todo lo que la página pide a
/api/*, así que se puede abrir tal cual, subir a cualquier hosting estático o
publicar como página. Chart.js sigue viniendo de la CDN, como en vivo.

Cómo: le pide cada ruta a la app real con `TestClient` —el mismo código y los
mismos números que el dashboard en vivo— y deja las respuestas en un <script>.
`static/estatico.js` reemplaza a fetch() y contesta desde ahí, así que
dashboard.js no se entera de que no hay servidor.

Lo que no puede ser estático: la barra de filtros. Se cambia por un aviso y
todas las cifras van sin filtrar.

**Si cambias en dashboard.js los parámetros con que se llama a /api/*,
actualiza las PETICIONES de abajo**: una respuesta que falta sale en la página
como error, no como un panel vacío que parece dato.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from gob import config, web

# Los mismos parámetros con que dashboard.js llama a cada ruta.
PASO_OFERTAS = 50          # PASO_OFERTAS en dashboard.js
PASO_CALCE = 25            # PASO_CALCE
TOP_TAGS = 25              # pintarTop
CANDIDATOS_TENDENCIA = 14  # montarSelectorTendencia
FIJAS = (
    "/api/resumen",
    f"/api/top-tags?limit={TOP_TAGS}",
    "/api/tags-seniority?limit=12",
    "/api/tendencia",
    "/api/salarios?limit=15",
    "/api/brechas?limit=20",
    "/api/empresas?limit=15",
    "/api/perfiles",
)


def respuestas(cliente: TestClient) -> dict[str, object]:
    """Todas las respuestas que la página puede pedir sin filtros, por URL."""
    r: dict[str, object] = {}

    def pedir(url: str):
        resp = cliente.get(url)
        resp.raise_for_status()
        r[url] = resp.json()
        return r[url]

    for url in FIJAS:
        pedir(url)

    top = [t["slug"] for t in r[f"/api/top-tags?limit={TOP_TAGS}"]]
    for slug in top:
        pedir(f"/api/co-ocurrencia?tag={quote(slug, safe='')}&limit=12")
    for slug in top[:CANDIDATOS_TENDENCIA]:
        pedir(f"/api/tendencia?tags={quote(slug, safe='')}")

    _paginar(pedir, "/api/jobs?", PASO_OFERTAS)
    for fila in r["/api/perfiles"]["perfiles"]:
        slug = quote(fila["perfil"]["slug"], safe="")
        _paginar(pedir, f"/api/calce?perfil={slug}&", PASO_CALCE)
    return r


def _paginar(pedir, prefijo: str, paso: int) -> None:
    offset = 0
    while True:
        d = pedir(f"{prefijo}limit={paso}&offset={offset}")
        offset += paso
        if offset >= d["total"]:
            return


def _json_para_script(datos: object) -> str:
    """JSON que se puede pegar dentro de <script> sin que un "</script>" metido
    en un título de oferta cierre la etiqueta antes de tiempo."""
    texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    return texto.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _leer_sin_cierre(ruta: Path) -> str:
    texto = ruta.read_text(encoding="utf-8")
    if "</script" in texto.lower() or "</style" in texto.lower():
        raise ValueError(f"{ruta.name} contiene una etiqueta de cierre y no se puede incrustar")
    return texto


def exportar(db_path: Path, salida: Path, *, ahora: datetime | None = None) -> Path:
    web.store = web.Store(db_path)
    cliente = TestClient(web.app)
    ahora = ahora or datetime.now(timezone.utc)

    contexto = web.contexto_pagina()
    contexto["estatico"] = {
        "generado": ahora.strftime("%Y-%m-%d %H:%M"),
        "css": _leer_sin_cierre(config.STATIC_DIR / "dashboard.css"),
        "shim": _leer_sin_cierre(config.STATIC_DIR / "estatico.js"),
        "js": _leer_sin_cierre(config.STATIC_DIR / "dashboard.js"),
        "datos": _json_para_script({"respuestas": respuestas(cliente)}),
    }
    html = web.templates.get_template("dashboard.html").render(contexto)

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(html, encoding="utf-8")
    return salida


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=config.DB_PATH)
    ap.add_argument("--salida", type=Path, default=config.ROOT / "dist" / "dashboard.html")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"No existe {args.db}: corre antes `python -m gob.ingest --full`.", file=sys.stderr)
        return 1
    ruta = exportar(args.db, args.salida)
    print(f"{ruta}  ({ruta.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
