"""scripts/exportar_estatico.py: la página congelada tiene que traer todo lo que
dashboard.js pide, y ningún texto de una oferta puede romper el HTML."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gob import web
from gob.ingest import parse_job, store_job
from gob.models import connect, init_db

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("exportar_estatico", RAIZ / "scripts" / "exportar_estatico.py")
exportar_estatico = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exportar_estatico)

MALICIOSO = "</script><script>alert(1)</script>"


@pytest.fixture
def pagina(tmp_path, api_jobs_fixture, monkeypatch):
    db = tmp_path / "web.db"
    conn = connect(db)
    init_db(conn)
    conn.execute("INSERT INTO snapshots (id, run_at) VALUES (1, '2026-08-01T00:00:00+00:00')")
    payloads = json.loads(json.dumps(api_jobs_fixture))
    payloads[0]["attributes"]["title"] = MALICIOSO
    with conn:
        for payload in payloads:
            store_job(
                conn, parse_job(payload, "programacion"),
                snapshot_id=1, now="2026-08-01T00:00:00+00:00", aliases={}, keywords=[],
            )
    conn.close()

    stack = tmp_path / "mi_stack.yml"
    stack.write_text("- React\n", encoding="utf-8")
    perfiles = tmp_path / "perfiles"
    perfiles.mkdir()
    (perfiles / "front.yml").write_text("nombre: Front\ncategorias: [programacion]\n", encoding="utf-8")
    monkeypatch.setattr(web.config, "MI_STACK_PATH", stack)
    monkeypatch.setattr(web.config, "PERFILES_DIR", perfiles)
    monkeypatch.setattr(web, "store", web.store)      # exportar() lo reemplaza: que se restaure

    salida = exportar_estatico.exportar(
        db, tmp_path / "dist" / "dashboard.html", ahora=datetime(2026, 11, 26, 22, 0, tzinfo=timezone.utc)
    )
    return salida.read_text(encoding="utf-8")


def _datos(html: str) -> dict:
    m = re.search(r"window\.GOB_ESTATICO = (\{.*?\});</script>", html, re.S)
    assert m, "no está el bloque de datos"
    return json.loads(m.group(1))["respuestas"]


def test_trae_todo_lo_que_pide_la_pagina(pagina):
    r = _datos(pagina)
    for url in exportar_estatico.FIJAS:
        assert url in r
    top = [t["slug"] for t in r["/api/top-tags?limit=25"]]
    assert top and all(f"/api/co-ocurrencia?tag={t}&limit=12" in r for t in top)
    assert f"/api/tendencia?tags={top[0]}" in r
    assert r["/api/jobs?limit=50&offset=0"]["total"] == 3
    assert r["/api/calce?perfil=front&limit=25&offset=0"]["total"] == 3


def test_es_un_solo_archivo_sin_servidor(pagina):
    assert "/static/" not in pagina                      # CSS y JS van dentro
    assert '<form class="filtros"' not in pagina        # sin servidor no hay filtros
    assert "Versión estática" in pagina and "2026-11-26 22:00" in pagina
    assert "window.fetch = function" in pagina          # el reemplazo de fetch


def test_un_titulo_malicioso_no_rompe_el_html(pagina):
    assert MALICIOSO not in pagina
    # el título viaja escapado en el JSON y vuelve intacto al leerlo
    titulos = {j["title"] for j in _datos(pagina)["/api/jobs?limit=50&offset=0"]["items"]}
    assert MALICIOSO in titulos


def test_sin_bd_avisa_y_sale_con_error(tmp_path, capsys):
    assert exportar_estatico.main(["--db", str(tmp_path / "no.db")]) == 1
    assert "ingest" in capsys.readouterr().err
