"""Parseo de la respuesta de la API y escritura en SQLite.

Se apoya en tests/fixtures/api_jobs.json, que tiene la forma exacta del endpoint
`GET /categories/{slug}/jobs?expand[]=…`. Nada de red: la BD es un archivo
temporal y los payloads salen de la fixture.
"""

from __future__ import annotations

import json

import pytest

from gob import queries
from gob.ingest import (
    ingest_from_cache,
    latest_cache_dir,
    parse_job,
    refresh_aliases,
    store_job,
)
from gob.models import connect, init_db
from gob.normalize import load_keywords

ALIASES = {"cloud-gcp": "gcp", "html5": "html", "next-js": "nextjs"}
AHORA = "2026-08-11T00:00:00+00:00"
DESPUES = "2026-08-12T00:00:00+00:00"


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def keywords():
    return load_keywords("data/tech_keywords.yml")


def guardar(conn, parsed, *, snapshot_id=1, now=AHORA, keywords=(), aliases=ALIASES):
    conn.execute("INSERT OR IGNORE INTO snapshots (id, run_at) VALUES (?, ?)", (snapshot_id, now))
    with conn:
        store_job(conn, parsed, snapshot_id=snapshot_id, now=now, aliases=aliases, keywords=list(keywords))


# -------------------------------------------------------------------- parseo


def test_parse_job_campos_planos(api_jobs_fixture):
    job = parse_job(api_jobs_fixture[0], "programacion")["job"]
    assert job["id"] == "ux-engineer-senior-admoai-santiago-4b10"
    assert job["title"] == "UX Engineer (Senior)"
    assert job["url"] == "https://www.getonbrd.com/jobs/ux-engineer-senior-admoai-santiago-4b10"
    assert job["category_slug"] == "programacion"      # lo aporta la ingesta
    assert job["category_name"] == "Programación"      # lo trae la API
    assert job["seniority"] == "senior" and job["seniority_id"] == 4
    assert job["modality"] == "full_time" and job["modality_id"] == 1
    assert job["remote"] == 0 and job["remote_modality"] == "hybrid"
    assert job["published_at"] == 1786391259
    assert job["applications_count"] == 41


def test_parse_job_moneda_supuesta(api_jobs_fixture):
    """La API no expone moneda en ningún endpoint: la constante es nuestra."""
    from gob.config import ASSUMED_CURRENCY

    assert parse_job(api_jobs_fixture[1], "sysadmin")["job"]["currency"] == ASSUMED_CURRENCY
    # Y se pone aunque no haya importes, para que la columna sea uniforme.
    assert parse_job(api_jobs_fixture[0], "programacion")["job"]["currency"] == ASSUMED_CURRENCY


def test_parse_job_geografia_multiple(api_jobs_fixture):
    parsed = parse_job(api_jobs_fixture[1], "sysadmin-devops-qa")
    assert parsed["countries"] == ["Chile", "Peru"]
    assert [c["city"] for c in parsed["cities"]] == ["Santiago", "Lima"]
    # Las columnas denormalizadas se quedan con el primero, y por eso NO bastan.
    assert parsed["job"]["primary_country"] == "Chile"
    assert parsed["job"]["primary_city"] == "Santiago"


def test_parse_job_relaciones_expandidas(api_jobs_fixture):
    parsed = parse_job(api_jobs_fixture[0], "programacion")
    assert parsed["company"]["id"] == "admoai"
    assert parsed["company"]["name"] == "Admoai"
    assert parsed["company"]["country"] == "CL"
    assert parsed["company"]["response_time_min"] == 1
    assert [t["id"] for t in parsed["api_tags"]] == ["react", "next-js", "typescript", "html5"]


def test_parse_job_limpia_el_html_y_conserva_el_crudo(api_jobs_fixture):
    job = parse_job(api_jobs_fixture[0], "programacion")["job"]
    assert "<strong>" in job["description_raw"]
    assert "<" not in job["description_text"]
    assert "5+ años con React, Next.js y TypeScript." in job["description_text"]
    # El crudo guarda los cinco campos, incluso los vacíos.
    assert set(json.loads(job["description_raw"])) == {
        "description", "functions", "desirable", "projects", "benefits",
    }


def test_parse_job_soporta_relaciones_colapsadas(api_jobs_fixture):
    """No es lo que ingerimos, pero no puede reventar: sin expand, las relaciones
    llegan con id numérico y sin attributes (docs/API.md §3.3)."""
    parsed = parse_job(api_jobs_fixture[2], "otra")
    assert parsed["job"]["seniority"] is None          # no hay locale_key que leer
    assert parsed["job"]["seniority_id"] == 2          # el id numérico sí está
    assert parsed["company"]["id"] == "16478"          # ojo: es el id interno, no el slug
    assert parsed["api_tags"] == []


def test_parse_job_campos_ausentes_o_nulos(api_jobs_fixture):
    job = parse_job(api_jobs_fixture[2], "otra")["job"]
    assert job["min_salary"] is None and job["max_salary"] is None
    assert job["primary_country"] is None and job["primary_city"] is None
    assert job["description_text"] == "Sin tecnologías reconocibles."


def test_parse_job_es_puro(api_jobs_fixture):
    """No debe mutar el payload: `ingest_from_cache` lo relee del disco."""
    copia = json.loads(json.dumps(api_jobs_fixture[0]))
    parse_job(api_jobs_fixture[0], "programacion")
    assert api_jobs_fixture[0] == copia


# ---------------------------------------------------------------- escritura


def test_store_job_escribe_todo(db, api_jobs_fixture):
    guardar(db, parse_job(api_jobs_fixture[1], "sysadmin-devops-qa"))
    q = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    assert q("SELECT COUNT(*) FROM jobs") == 1
    assert q("SELECT COUNT(*) FROM companies") == 1
    assert q("SELECT COUNT(*) FROM job_countries") == 2
    assert q("SELECT COUNT(*) FROM job_cities") == 2
    assert q("SELECT COUNT(*) FROM job_perks") == 2
    assert q("SELECT COUNT(*) FROM job_snapshots") == 1


def test_los_alias_se_guardan_resueltos(db, api_jobs_fixture):
    guardar(db, parse_job(api_jobs_fixture[0], "programacion"))
    tags = dict(db.execute("SELECT id, canonical_slug FROM tags").fetchall())
    assert tags["html5"] == "html"         # el slug crudo se conserva
    assert tags["next-js"] == "nextjs"
    assert tags["react"] == "react"


def test_reingerir_no_duplica_y_conserva_first_seen(db, api_jobs_fixture):
    parsed = parse_job(api_jobs_fixture[0], "programacion")
    guardar(db, parsed, now=AHORA)
    guardar(db, parsed, snapshot_id=2, now=DESPUES)

    fila = db.execute("SELECT first_seen_at, last_seen_at FROM jobs").fetchone()
    assert fila["first_seen_at"] == AHORA
    assert fila["last_seen_at"] == DESPUES
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM job_tags").fetchone()[0] == 4
    assert db.execute("SELECT COUNT(*) FROM job_snapshots").fetchone()[0] == 2


def test_una_oferta_que_pierde_un_tag_lo_pierde_en_la_bd(db, api_jobs_fixture):
    parsed = parse_job(api_jobs_fixture[0], "programacion")
    guardar(db, parsed)
    recortado = {**parsed, "api_tags": parsed["api_tags"][:2]}
    guardar(db, recortado, snapshot_id=2, now=DESPUES)
    assert db.execute("SELECT COUNT(*) FROM job_tags").fetchone()[0] == 2


def test_los_tags_del_texto_no_pisan_a_los_oficiales(db, keywords):
    """El caso que dio problemas de verdad: la oferta trae el tag oficial
    "cloud-gcp" y el texto menciona "Google Cloud". Con el alias, ambos son la
    misma tecnología, así que solo puede quedar una fila, la oficial."""
    payload = {
        "id": "j1",
        "links": {"public_url": "https://x"},
        "attributes": {
            "title": "DevOps",
            "description": "<p>Desplegamos en Google Cloud con Terraform.</p>",
            "tags": {"data": [{"id": "cloud-gcp", "attributes": {"name": "Google Cloud Platform"}}]},
            "countries": [],
        },
    }
    guardar(db, parse_job(payload, "sysadmin"), keywords=keywords)

    filas = {
        r["canonical_slug"]: r["source"]
        for r in db.execute(
            "SELECT t.canonical_slug, jt.source FROM job_tags jt JOIN tags t ON t.id = jt.tag_id"
        )
    }
    assert filas["gcp"] == "api"          # una sola fila, y gana la oficial
    assert filas["terraform"] == "text"   # lo que no viene de la API sí se añade


def test_from_api_se_queda_en_1_aunque_luego_llegue_por_texto(db, api_jobs_fixture, keywords):
    guardar(db, parse_job(api_jobs_fixture[0], "programacion"), keywords=keywords)
    assert db.execute("SELECT from_api FROM tags WHERE id = 'react'").fetchone()[0] == 1


def test_una_oferta_sin_empresa_no_revienta(db):
    payload = {"id": "j1", "links": {}, "attributes": {"title": "X", "countries": []}}
    guardar(db, parse_job(payload, "otra"))
    assert db.execute("SELECT company_id FROM jobs").fetchone()[0] is None


# ------------------------------------------------------------- desde caché


def test_ingest_from_cache_reconstruye_la_bd(db, tmp_path, api_jobs_fixture, keywords):
    cache = tmp_path / "20260811T000000Z"
    cache.mkdir()
    (cache / "programacion_p1.json").write_text(
        json.dumps({"data": api_jobs_fixture[:2]}), encoding="utf-8"
    )
    (cache / "otra_p1.json").write_text(
        json.dumps({"data": api_jobs_fixture[2:]}), encoding="utf-8"
    )

    resultado = ingest_from_cache(db, cache, aliases=ALIASES, keywords=keywords)
    assert resultado.jobs_seen == 3
    assert sorted(resultado.categories) == ["otra", "programacion"]
    categorias = dict(db.execute("SELECT category_slug, COUNT(*) FROM jobs GROUP BY 1").fetchall())
    assert categorias == {"programacion": 2, "otra": 1}


def test_ingest_from_cache_saca_la_categoria_del_nombre_del_archivo(db, tmp_path, api_jobs_fixture):
    cache = tmp_path / "run"
    cache.mkdir()
    (cache / "sysadmin-devops-qa_p7.json").write_text(
        json.dumps({"data": api_jobs_fixture[:1]}), encoding="utf-8"
    )
    ingest_from_cache(db, cache, aliases=ALIASES, keywords=[])
    assert db.execute("SELECT category_slug FROM jobs").fetchone()[0] == "sysadmin-devops-qa"


def test_ingest_from_cache_anota_los_archivos_raros_y_sigue(db, tmp_path, api_jobs_fixture):
    cache = tmp_path / "run"
    cache.mkdir()
    (cache / "programacion_p1.json").write_text(
        json.dumps({"data": api_jobs_fixture[:1]}), encoding="utf-8"
    )
    (cache / "basura.json").write_text("{}", encoding="utf-8")
    resultado = ingest_from_cache(db, cache, aliases=ALIASES, keywords=[])
    assert resultado.jobs_seen == 1
    assert any("basura.json" in e for e in resultado.errors)


def test_ingest_from_cache_sin_paginas(db, tmp_path):
    (tmp_path / "vacia").mkdir()
    with pytest.raises(FileNotFoundError):
        ingest_from_cache(db, tmp_path / "vacia", aliases={}, keywords=[])


def test_latest_cache_dir_coge_la_mas_reciente(tmp_path):
    for nombre in ("20260810T000000Z", "20260811T120000Z", "20260811T010000Z"):
        (tmp_path / nombre).mkdir()
    assert latest_cache_dir(tmp_path).name == "20260811T120000Z"


def test_latest_cache_dir_sin_corridas(tmp_path):
    with pytest.raises(FileNotFoundError):
        latest_cache_dir(tmp_path)


# --------------------------------------------------------------- reaplicar


def test_refresh_aliases_no_necesita_reingesta(db, api_jobs_fixture):
    guardar(db, parse_job(api_jobs_fixture[0], "programacion"), aliases={})
    assert db.execute("SELECT canonical_slug FROM tags WHERE id='html5'").fetchone()[0] == "html5"

    cambiados = refresh_aliases(db, {"html5": "html"})
    assert cambiados == 1
    assert db.execute("SELECT canonical_slug FROM tags WHERE id='html5'").fetchone()[0] == "html"
    assert refresh_aliases(db, {"html5": "html"}) == 0   # idempotente


# ----------------------------------------------------- ingesta -> analytics


def test_lo_ingerido_se_puede_leer_como_jobs_de_analytics(db, api_jobs_fixture, keywords):
    """Cierra el círculo: parse_job -> SQLite -> queries.load_jobs -> analytics."""
    for payload in api_jobs_fixture[:2]:
        guardar(db, parse_job(payload, "programacion"), keywords=keywords)

    jobs = {j.id: j for j in queries.load_jobs(db)}
    assert len(jobs) == 2

    devops = jobs["devops-multipais-ejemplo-remote"]
    assert devops.company_name == "Gamma"
    assert devops.countries == ("Chile", "Peru")
    assert devops.salario_medio == 3000
    # El tag oficial "cloud-gcp" llega a analytics ya canonizado como "gcp".
    assert "gcp" in devops.slugs and "cloud-gcp" not in devops.slugs
    # Y las keywords del texto añaden lo que los tags oficiales no cubren.
    assert {"terraform", "scada", "modbus"} <= devops.slugs

    snapshots = queries.load_snapshots(db)
    assert len(snapshots) == 1 and len(snapshots[0].job_ids) == 2
