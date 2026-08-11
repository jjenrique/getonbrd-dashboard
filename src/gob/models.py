"""Esquema SQLite y apertura de conexiones.

Divergencias respecto al diseño inicial, todas justificadas por lo que la API
devuelve de verdad (ver docs/API.md):

- `jobs.country` / `jobs.city` -> tablas puente `job_countries` / `job_cities`,
  porque `countries` es un array y una oferta puede tener varias ciudades (§4).
  Se conservan `primary_country` / `primary_city` denormalizadas para consultas
  y tabla rápidas.
- `jobs.currency` es la constante `USD`: la API no expone moneda (§3.6).
- `companies.size` no existe: el endpoint público no expone headcount (§3.7).
- `job_snapshots` no estaba en el diseño original pero hace falta para calcular
  la tendencia de un tag por snapshot.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id                  TEXT PRIMARY KEY,   -- slug de la API (ej. "admoai")
    name                TEXT NOT NULL,
    country             TEXT,               -- código ISO ("CL"), no el nombre
    web                 TEXT,
    logo                TEXT,
    response_time_min   INTEGER,
    response_time_max   INTEGER
);

CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,   -- slug de la API, PK natural
    title               TEXT NOT NULL,
    company_id          TEXT REFERENCES companies(id),
    category_slug       TEXT NOT NULL,      -- lo aporta la ingesta: el job no lo trae
    category_name       TEXT,
    seniority           TEXT,               -- locale_key: senior, semi_senior, junior…
    seniority_id        INTEGER,
    modality            TEXT,               -- locale_key: full_time, freelance…
    modality_id         INTEGER,
    remote              INTEGER NOT NULL DEFAULT 0,
    remote_modality     TEXT,               -- no_remote|temporarily_remote|remote_local|fully_remote|hybrid
    remote_zone         TEXT,
    primary_country     TEXT,               -- primer valor de countries[]; puede ser "Remote"
    primary_city        TEXT,
    min_salary          INTEGER,
    max_salary          INTEGER,
    currency            TEXT,               -- constante "USD" (supuesto, ver docs/API.md §3.6)
    published_at        INTEGER,            -- epoch en segundos
    url                 TEXT,
    applications_count  INTEGER,
    lang                TEXT,
    description_raw     TEXT,               -- JSON con los 5 campos HTML tal cual
    description_text    TEXT,               -- los mismos, sin HTML y concatenados
    first_seen_at       TEXT NOT NULL,      -- ISO-8601 UTC
    last_seen_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id                  TEXT PRIMARY KEY,   -- slug crudo: el de la API, o el de la keyword
    name                TEXT NOT NULL,
    canonical_slug      TEXT NOT NULL,      -- tras aplicar data/tag_aliases.yml
    keywords            TEXT,               -- sinónimos que publica la API
    from_api            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_tags (
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    tag_id              TEXT NOT NULL REFERENCES tags(id),
    source              TEXT NOT NULL,      -- 'api' (tag oficial) | 'text' (extraído de la descripción)
    PRIMARY KEY (job_id, tag_id)
);

CREATE TABLE IF NOT EXISTS job_countries (
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    country             TEXT NOT NULL,
    PRIMARY KEY (job_id, country)
);

CREATE TABLE IF NOT EXISTS job_cities (
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    city                TEXT NOT NULL,
    country             TEXT,
    PRIMARY KEY (job_id, city)
);

CREATE TABLE IF NOT EXISTS job_perks (
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    perk                TEXT NOT NULL,
    PRIMARY KEY (job_id, perk)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at              TEXT NOT NULL,      -- ISO-8601 UTC
    jobs_seen           INTEGER NOT NULL DEFAULT 0,
    categories          TEXT,               -- categorías recorridas, separadas por coma
    source              TEXT NOT NULL DEFAULT 'api',   -- 'api' | 'cache'
    notes               TEXT                -- errores no fatales de la corrida
);

CREATE TABLE IF NOT EXISTS job_snapshots (
    snapshot_id         INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    job_id              TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    PRIMARY KEY (snapshot_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_job_tags_tag        ON job_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_tags_canonical      ON tags(canonical_slug);
CREATE INDEX IF NOT EXISTS idx_jobs_category       ON jobs(category_slug);
CREATE INDEX IF NOT EXISTS idx_jobs_seniority      ON jobs(seniority);
CREATE INDEX IF NOT EXISTS idx_jobs_published      ON jobs(published_at);
CREATE INDEX IF NOT EXISTS idx_jobs_company        ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_job_snapshots_snap  ON job_snapshots(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_job_countries_c     ON job_countries(country);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Abre la BD con las pragmas que queremos en todas partes."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Crea el esquema si no existe. Idempotente."""
    conn.executescript(SCHEMA)
    conn.commit()
