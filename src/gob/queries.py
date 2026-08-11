"""Capa fina entre SQLite y `analytics.py`.

Lo único que hace es SELECT y armar los `Job` / `Snapshot` que consumen las
funciones puras. Cero agregación aquí: si aparece un GROUP BY interesante, su
sitio es `analytics.py`, donde se puede testear sin BD.

Con ~1.000 ofertas cargarlo todo cuesta décimas de segundo, así que la app lo
lee una vez y filtra en memoria.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from .analytics import Job, Snapshot, TagRef

#: `description_text` se deja fuera a propósito: son ~4 KB por oferta que el
#: dashboard no usa y multiplicarían por diez lo que se carga en memoria.
_JOBS_SQL = """
SELECT j.id, j.title, j.url, j.company_id, c.name AS company_name,
       j.category_slug, j.category_name, j.seniority, j.modality,
       j.remote, j.remote_modality, j.min_salary, j.max_salary, j.currency,
       j.published_at
FROM jobs j
LEFT JOIN companies c ON c.id = j.company_id
"""

#: Se agrupa por slug CANÓNICO: si una oferta trae "cloud-gcp" (oficial) y "gcp"
#: (del texto), para el análisis es la misma tecnología y una sola oferta.
_TAGS_SQL = """
SELECT jt.job_id, t.canonical_slug, t.name, jt.source
FROM job_tags jt
JOIN tags t ON t.id = jt.tag_id
"""


def load_jobs(conn: sqlite3.Connection) -> list[Job]:
    """Todas las ofertas de la BD, con sus tags, países y ciudades."""
    tags: dict[str, list[TagRef]] = defaultdict(list)
    for row in conn.execute(_TAGS_SQL):
        tags[row["job_id"]].append(
            TagRef(slug=row["canonical_slug"], name=row["name"], source=row["source"])
        )

    countries: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute("SELECT job_id, country FROM job_countries"):
        countries[row["job_id"]].append(row["country"])

    cities: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute("SELECT job_id, city FROM job_cities"):
        cities[row["job_id"]].append(row["city"])

    return [
        Job(
            id=row["id"],
            title=row["title"],
            url=row["url"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            category_slug=row["category_slug"],
            category_name=row["category_name"],
            seniority=row["seniority"],
            modality=row["modality"],
            remote=bool(row["remote"]),
            remote_modality=row["remote_modality"],
            countries=tuple(sorted(countries.get(row["id"], ()))),
            cities=tuple(sorted(cities.get(row["id"], ()))),
            min_salary=row["min_salary"],
            max_salary=row["max_salary"],
            currency=row["currency"],
            published_at=row["published_at"],
            tags=tuple(tags.get(row["id"], ())),
        )
        for row in conn.execute(_JOBS_SQL)
    ]


def load_snapshots(conn: sqlite3.Connection) -> list[Snapshot]:
    """Las corridas registradas, con el conjunto de ofertas vistas en cada una."""
    por_snapshot: dict[int, set[str]] = defaultdict(set)
    for row in conn.execute("SELECT snapshot_id, job_id FROM job_snapshots"):
        por_snapshot[row["snapshot_id"]].add(row["job_id"])

    return [
        Snapshot(
            id=row["id"],
            run_at=row["run_at"],
            job_ids=frozenset(por_snapshot.get(row["id"], frozenset())),
        )
        for row in conn.execute("SELECT id, run_at FROM snapshots ORDER BY run_at, id")
    ]


def load_opciones(conn: sqlite3.Connection) -> dict[str, list]:
    """Valores disponibles para poblar los desplegables de la barra de filtros."""
    categorias = conn.execute(
        """
        SELECT category_slug, COALESCE(MAX(category_name), category_slug) AS nombre, COUNT(*) AS n
        FROM jobs GROUP BY category_slug ORDER BY n DESC
        """
    ).fetchall()
    seniorities = conn.execute(
        "SELECT seniority, COUNT(*) AS n FROM jobs WHERE seniority IS NOT NULL GROUP BY 1 ORDER BY n DESC"
    ).fetchall()
    paises = conn.execute(
        "SELECT country, COUNT(*) AS n FROM job_countries GROUP BY 1 ORDER BY n DESC"
    ).fetchall()
    return {
        "categorias": [(r["category_slug"], r["nombre"], r["n"]) for r in categorias],
        "seniorities": [(r["seniority"], r["n"]) for r in seniorities],
        "paises": [(r["country"], r["n"]) for r in paises],
    }


def estado_bd(conn: sqlite3.Connection) -> dict[str, object]:
    """Para `/health` y para avisar en la UI si la BD está vacía o vieja."""
    ultimo = conn.execute(
        "SELECT id, run_at, jobs_seen, source, notes FROM snapshots ORDER BY run_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return {
        "jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "companies": conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
        "tags": conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
        "snapshots": conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
        "ultimo_snapshot": dict(ultimo) if ultimo else None,
    }
