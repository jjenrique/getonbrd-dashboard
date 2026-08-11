"""Rutas del dashboard, contra una BD temporal.

Sigue sin haber red: `TestClient` habla ASGI en proceso y el fixture `sin_red`
de conftest.py deja los sockets cortados. Que estos tests pasen es también la
prueba de que **ninguna ruta llama a la API de Get on Board**: si alguna lo
intentara, reventaría en el acto.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from gob import web
from gob.ingest import parse_job, store_job
from gob.models import connect, init_db

ALIASES = {"cloud-gcp": "gcp"}


@pytest.fixture
def cliente(tmp_path, api_jobs_fixture, monkeypatch):
    """App apuntando a una BD recién construida desde la fixture de la API."""
    db = tmp_path / "web.db"
    conn = connect(db)
    init_db(conn)
    conn.execute("INSERT INTO snapshots (id, run_at) VALUES (1, '2026-08-01T00:00:00+00:00')")
    with conn:
        for payload in api_jobs_fixture:
            store_job(
                conn, parse_job(payload, "programacion"),
                snapshot_id=1, now="2026-08-01T00:00:00+00:00", aliases=ALIASES, keywords=[],
            )
    conn.close()

    stack = tmp_path / "mi_stack.yml"
    stack.write_text("- React\n- TypeScript\n", encoding="utf-8")
    monkeypatch.setattr(web.config, "MI_STACK_PATH", stack)
    monkeypatch.setattr(web, "store", web.Store(db))
    return TestClient(web.app)


RUTAS = [
    "/api/resumen",
    "/api/top-tags",
    "/api/tags-seniority",
    "/api/tendencia",
    "/api/salarios",
    "/api/empresas",
    "/api/brechas",
    "/api/jobs",
    "/health",
]


@pytest.mark.parametrize("ruta", RUTAS)
def test_las_rutas_responden(cliente, ruta):
    assert cliente.get(ruta).status_code == 200


def test_la_pagina_se_renderiza(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "Demanda técnica" in r.text
    assert "dashboard.js" in r.text


def test_resumen(cliente):
    d = cliente.get("/api/resumen").json()
    assert d["ofertas"] == 3
    assert d["moneda"] == "USD"


def test_los_filtros_se_aplican(cliente):
    """El filtro llega hasta el JSON, no solo hasta el formulario."""
    todas = cliente.get("/api/resumen").json()
    filtradas = cliente.get("/api/resumen?seniority=senior").json()
    assert filtradas["ofertas"] == 1
    assert filtradas["total_sin_filtrar"] == todas["ofertas"]


def test_filtro_de_pais_usa_la_tabla_puente(cliente):
    """La oferta multipaís tiene que salir con Chile y con Peru."""
    for pais in ("Chile", "Peru"):
        ids = {j["id"] for j in cliente.get(f"/api/jobs?pais={pais}").json()["items"]}
        assert "devops-multipais-ejemplo-remote" in ids


def test_filtro_de_fechas(cliente):
    """`hasta` apunta al final del día: un rango con la misma fecha en ambos
    extremos tiene que incluir ese día entero."""
    assert cliente.get("/api/resumen?desde=2027-01-01").json()["ofertas"] == 0
    assert cliente.get("/api/resumen?hasta=2020-01-01").json()["ofertas"] == 0
    assert cliente.get("/api/resumen?desde=2026-08-09&hasta=2026-08-09").json()["ofertas"] == 1


def test_una_fecha_ilegible_no_rompe_la_pagina(cliente):
    """Alguien edita la URL a mano: mejor ignorar el filtro que devolver un 500."""
    assert cliente.get("/api/resumen?desde=ayer").json()["ofertas"] == 3


def test_top_tags_marca_la_procedencia(cliente):
    tags = {t["slug"]: t for t in cliente.get("/api/top-tags").json()}
    assert tags["react"]["api"] == 1
    assert tags["gcp"]["slug"] == "gcp"     # "cloud-gcp" llega ya canonizado


def test_tendencia_avisa_con_un_solo_snapshot(cliente):
    d = cliente.get("/api/tendencia?tags=react").json()
    assert d["historico_insuficiente"] and d["n_snapshots"] == 1


def test_salarios_informa_la_muestra(cliente):
    """Con 3 ofertas no hay mediana, pero el n tiene que ir igual."""
    d = cliente.get("/api/salarios").json()
    assert d["global"]["mediana"] is None
    assert d["global"]["n_con_salario"] == 1 and d["global"]["n_total"] == 3
    assert d["moneda"] == "USD"


def test_brechas_usa_mi_stack(cliente):
    d = cliente.get("/api/brechas?limit=20").json()
    assert set(d["stack"]) == {"react", "typescript"}
    assert not d["stack_vacio"]
    assert {"react", "typescript"}.isdisjoint({b["slug"] for b in d["items"]})


def test_brechas_avisa_si_el_stack_esta_vacio(cliente, tmp_path, monkeypatch):
    vacio = tmp_path / "vacio.yml"
    vacio.write_text("", encoding="utf-8")
    monkeypatch.setattr(web.config, "MI_STACK_PATH", vacio)
    web.store._mtime = None
    assert cliente.get("/api/brechas").json()["stack_vacio"]


def test_jobs_pagina_y_ordena_por_fecha(cliente):
    d = cliente.get("/api/jobs?limit=2").json()
    assert d["total"] == 3 and len(d["items"]) == 2
    fechas = [j["published_at"] for j in d["items"]]
    assert fechas == sorted(fechas, reverse=True)
    assert all(j["url"] for j in d["items"])


def test_health(cliente):
    d = cliente.get("/health").json()
    assert d["ok"] and d["jobs"] == 3


def test_la_bd_se_recarga_sola_al_reingerir(cliente, tmp_path, api_jobs_fixture):
    """El store cachea por mtime: una reingesta se ve sin reiniciar el servidor."""
    assert cliente.get("/api/resumen").json()["ofertas"] == 3

    conn = connect(tmp_path / "web.db")
    nuevo = json.loads(json.dumps(api_jobs_fixture[0]))
    nuevo["id"] = "oferta-nueva"
    with conn:
        store_job(
            conn, parse_job(nuevo, "programacion"),
            snapshot_id=1, now="2026-08-02T00:00:00+00:00", aliases=ALIASES, keywords=[],
        )
    conn.close()

    assert cliente.get("/api/resumen").json()["ofertas"] == 4


def test_una_bd_vacia_no_revienta(tmp_path, monkeypatch):
    """Antes de la primera ingesta la página tiene que cargar y decirlo."""
    monkeypatch.setattr(web, "store", web.Store(tmp_path / "no-existe.db"))
    cliente = TestClient(web.app)
    r = cliente.get("/")
    assert r.status_code == 200
    assert "No hay datos todavía" in r.text
    assert cliente.get("/health").json()["ok"] is False
    assert cliente.get("/api/top-tags").json() == []
