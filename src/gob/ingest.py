"""Descarga las ofertas activas y las vuelca en SQLite.

    python -m gob.ingest --full
    python -m gob.ingest --categories programacion,sysadmin-devops-qa
    python -m gob.ingest --from-cache          # reprocesa sin tocar la red
    python -m gob.ingest --refresh-aliases     # reaplica data/tag_aliases.yml

Dos garantías que condicionan el diseño:

1. **No perder lo descargado.** Cada página se escribe en `data/raw/<run>/` ANTES
   de tocar la BD. Si el proceso muere a mitad, `--from-cache` reconstruye todo
   sin volver a llamar a la API.
2. **Idempotencia.** El upsert va por `id` de oferta y conserva `first_seen_at`.
   Correrla dos veces deja exactamente las mismas filas.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .client import JOB_EXPAND, MAX_PER_PAGE, PROD_BASE_URL, SANDBOX_BASE_URL, ApiError, GobClient
from .models import connect, init_db
from .normalize import (
    TechKeyword,
    canonical,
    extract_from_text,
    load_aliases,
    load_keywords,
    strip_html,
)

log = logging.getLogger("gob.ingest")

#: Campos HTML de una oferta que se conservan crudos y se minan por keywords.
TEXT_FIELDS = ("description", "functions", "desirable", "projects", "benefits")

_CACHE_NAME = re.compile(r"^(?P<category>.+)_p(?P<page>\d+)\.json$")


@dataclass
class IngestResult:
    snapshot_id: int
    jobs_seen: int = 0
    pages: int = 0
    categories: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_dir: Path | None = None


# --------------------------------------------------------------------- parseo


def _rel_one(attributes: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Saca una relación 1:1 de `attributes`. Ojo: en esta API las relaciones van
    dentro de `attributes`, no en `relationships` (docs/API.md §3.5)."""
    data = (attributes.get(key) or {}).get("data")
    return data if isinstance(data, dict) else None


def _rel_many(attributes: dict[str, Any], key: str) -> list[dict[str, Any]]:
    data = (attributes.get(key) or {}).get("data")
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _expanded(rel: dict[str, Any] | None) -> dict[str, Any]:
    """`attributes` de una relación expandida, o `{}` si vino colapsada."""
    return (rel or {}).get("attributes") or {}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_job(raw: dict[str, Any], category_slug: str) -> dict[str, Any]:
    """Convierte una oferta de la API en las filas que van a la BD.

    Función pura: no toca red ni BD. Espera el payload CON `expand[]`; si viene
    colapsado, las relaciones traen ids numéricos y no slugs, así que company_id
    y los tags saldrían mal (docs/API.md §3.3).
    """
    attributes = raw.get("attributes") or {}

    company_rel = _rel_one(attributes, "company")
    company = None
    if company_rel:
        company_attrs = _expanded(company_rel)
        response_time = company_attrs.get("response_time_in_days") or {}
        company = {
            "id": str(company_rel["id"]),
            "name": company_attrs.get("name") or str(company_rel["id"]),
            "country": company_attrs.get("country"),
            "web": company_attrs.get("web") or None,
            "logo": company_attrs.get("logo") or None,
            "response_time_min": _int_or_none(response_time.get("min")),
            "response_time_max": _int_or_none(response_time.get("max")),
        }

    seniority_rel = _rel_one(attributes, "seniority")
    modality_rel = _rel_one(attributes, "modality")

    countries = [c for c in (attributes.get("countries") or []) if c]
    cities = [
        {"city": _expanded(c).get("name"), "country": _expanded(c).get("country")}
        for c in _rel_many(attributes, "location_cities")
        if _expanded(c).get("name")
    ]

    description_raw = {f: attributes.get(f) for f in TEXT_FIELDS}
    description_text = " ".join(strip_html(attributes.get(f)) for f in TEXT_FIELDS).strip()

    job = {
        "id": raw["id"],
        "title": attributes.get("title") or "",
        "company_id": company["id"] if company else None,
        "category_slug": category_slug,
        "category_name": attributes.get("category_name"),
        "seniority": _expanded(seniority_rel).get("locale_key"),
        "seniority_id": _int_or_none((seniority_rel or {}).get("id")),
        "modality": _expanded(modality_rel).get("locale_key"),
        "modality_id": _int_or_none((modality_rel or {}).get("id")),
        "remote": 1 if attributes.get("remote") else 0,
        "remote_modality": attributes.get("remote_modality"),
        "remote_zone": attributes.get("remote_zone"),
        "primary_country": countries[0] if countries else None,
        "primary_city": cities[0]["city"] if cities else None,
        "min_salary": _int_or_none(attributes.get("min_salary")),
        "max_salary": _int_or_none(attributes.get("max_salary")),
        "currency": config.ASSUMED_CURRENCY,
        "published_at": _int_or_none(attributes.get("published_at")),
        "url": (raw.get("links") or {}).get("public_url"),
        "applications_count": _int_or_none(attributes.get("applications_count")),
        "lang": attributes.get("lang"),
        "description_raw": json.dumps(description_raw, ensure_ascii=False),
        "description_text": description_text,
    }

    api_tags = [
        {
            "id": str(tag["id"]),
            "name": _expanded(tag).get("name") or str(tag["id"]),
            "keywords": _expanded(tag).get("keywords"),
        }
        for tag in _rel_many(attributes, "tags")
    ]

    return {
        "job": job,
        "company": company,
        "api_tags": api_tags,
        "countries": countries,
        "cities": cities,
        "perks": [p for p in (attributes.get("perks") or []) if p],
    }


# ---------------------------------------------------------------- escritura


_JOB_COLUMNS = (
    "id title company_id category_slug category_name seniority seniority_id modality modality_id "
    "remote remote_modality remote_zone primary_country primary_city min_salary max_salary currency "
    "published_at url applications_count lang description_raw description_text"
).split()

_JOB_UPSERT = f"""
INSERT INTO jobs ({", ".join(_JOB_COLUMNS)}, first_seen_at, last_seen_at)
VALUES ({", ".join(":" + c for c in _JOB_COLUMNS)}, :now, :now)
ON CONFLICT(id) DO UPDATE SET
    {", ".join(f"{c} = excluded.{c}" for c in _JOB_COLUMNS if c != "id")},
    last_seen_at = excluded.last_seen_at
"""  # first_seen_at se omite a propósito: no se toca nunca tras la primera vez.


def store_job(
    conn: sqlite3.Connection,
    parsed: dict[str, Any],
    *,
    snapshot_id: int,
    now: str,
    aliases: dict[str, str],
    keywords: list[TechKeyword],
) -> None:
    """Escribe una oferta y todo lo que cuelga de ella. Idempotente."""
    job = parsed["job"]
    job_id = job["id"]

    if parsed["company"]:
        conn.execute(
            """
            INSERT INTO companies (id, name, country, web, logo, response_time_min, response_time_max)
            VALUES (:id, :name, :country, :web, :logo, :response_time_min, :response_time_max)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, country = excluded.country, web = excluded.web,
                logo = excluded.logo, response_time_min = excluded.response_time_min,
                response_time_max = excluded.response_time_max
            """,
            parsed["company"],
        )

    conn.execute(_JOB_UPSERT, {**job, "now": now})

    # Las relaciones se reescriben enteras: si una oferta pierde un tag entre
    # corridas, la BD tiene que reflejarlo.
    for table in ("job_tags", "job_countries", "job_cities", "job_perks"):
        conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))

    api_canonicals: set[str] = set()
    for tag in parsed["api_tags"]:
        _upsert_tag(conn, tag["id"], tag["name"], tag["keywords"], from_api=True, aliases=aliases)
        api_canonicals.add(canonical(tag["id"], aliases))
        conn.execute(
            "INSERT OR IGNORE INTO job_tags (job_id, tag_id, source) VALUES (?, ?, 'api')",
            (job_id, tag["id"]),
        )

    # Los tags del texto nunca pisan a los oficiales. La comparación va por slug
    # CANÓNICO, no por slug crudo: si no, una oferta con el tag oficial
    # "cloud-gcp" sumaría además un "gcp" del texto y la misma oferta aparecería
    # como api y como texto a la vez.
    by_slug = {kw.slug: kw for kw in keywords}
    for slug in extract_from_text(job["description_text"], keywords):
        if canonical(slug, aliases) in api_canonicals:
            continue
        _upsert_tag(conn, slug, by_slug[slug].name, None, from_api=False, aliases=aliases)
        conn.execute(
            "INSERT OR IGNORE INTO job_tags (job_id, tag_id, source) VALUES (?, ?, 'text')",
            (job_id, slug),
        )

    conn.executemany(
        "INSERT OR IGNORE INTO job_countries (job_id, country) VALUES (?, ?)",
        [(job_id, c) for c in parsed["countries"]],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO job_cities (job_id, city, country) VALUES (?, ?, ?)",
        [(job_id, c["city"], c["country"]) for c in parsed["cities"]],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO job_perks (job_id, perk) VALUES (?, ?)",
        [(job_id, p) for p in parsed["perks"]],
    )
    conn.execute(
        "INSERT OR IGNORE INTO job_snapshots (snapshot_id, job_id) VALUES (?, ?)",
        (snapshot_id, job_id),
    )


def _upsert_tag(
    conn: sqlite3.Connection,
    slug: str,
    name: str,
    keywords: str | None,
    *,
    from_api: bool,
    aliases: dict[str, str],
) -> None:
    conn.execute(
        """
        INSERT INTO tags (id, name, canonical_slug, keywords, from_api)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name           = excluded.name,
            canonical_slug = excluded.canonical_slug,
            keywords       = COALESCE(excluded.keywords, tags.keywords),
            from_api       = MAX(tags.from_api, excluded.from_api)
        """,
        (slug, name, canonical(slug, aliases), keywords, int(from_api)),
    )


# ------------------------------------------------------------------ corridas


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _start_snapshot(conn: sqlite3.Connection, source: str) -> int:
    cursor = conn.execute(
        "INSERT INTO snapshots (run_at, jobs_seen, source) VALUES (?, 0, ?)",
        (_now_iso(), source),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _finish_snapshot(conn: sqlite3.Connection, result: IngestResult) -> None:
    conn.execute(
        "UPDATE snapshots SET jobs_seen = ?, categories = ?, notes = ? WHERE id = ?",
        (
            result.jobs_seen,
            ",".join(result.categories),
            "\n".join(result.errors) or None,
            result.snapshot_id,
        ),
    )
    conn.commit()


def ingest_from_api(
    conn: sqlite3.Connection,
    client: GobClient,
    *,
    categories: list[str] | None = None,
    per_page: int = MAX_PER_PAGE,
    raw_root: Path = config.RAW_DIR,
    aliases: dict[str, str],
    keywords: list[TechKeyword],
) -> IngestResult:
    """Recorre las categorías pedidas (o todas) y puebla la BD."""
    if not categories:
        categories = [c["id"] for c in client.get_categories()]
        log.info("Descubiertas %d categorías", len(categories))

    snapshot_id = _start_snapshot(conn, "api")
    run_dir = raw_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    result = IngestResult(snapshot_id=snapshot_id, run_dir=run_dir)

    for category in categories:
        try:
            seen = _ingest_category(
                conn, client, category,
                snapshot_id=snapshot_id, per_page=per_page, run_dir=run_dir,
                aliases=aliases, keywords=keywords, result=result,
            )
        except ApiError as exc:
            # Una categoría rota no puede tumbar la corrida entera: se anota y
            # se sigue. Lo ya descargado queda en run_dir y en la BD.
            message = f"{category}: {exc}"
            log.error("Categoría %s falló, se continúa con el resto: %s", category, exc)
            result.errors.append(message)
            continue
        result.categories.append(category)
        log.info("%-26s %4d ofertas", category, seen)

    _finish_snapshot(conn, result)
    return result


def _ingest_category(
    conn: sqlite3.Connection,
    client: GobClient,
    category: str,
    *,
    snapshot_id: int,
    per_page: int,
    run_dir: Path,
    aliases: dict[str, str],
    keywords: list[TechKeyword],
    result: IngestResult,
) -> int:
    seen = 0
    for page, payload in client.iter_category_job_pages(
        category, expand=JOB_EXPAND, per_page=per_page
    ):
        # Primero a disco, después a la BD. Si el proceso muere aquí en medio,
        # `--from-cache` recupera esta página.
        _write_raw(run_dir, category, page, payload)
        result.pages += 1
        seen += _store_page(
            conn, payload["data"], category,
            snapshot_id=snapshot_id, aliases=aliases, keywords=keywords,
        )
        result.jobs_seen += len(payload["data"])
    return seen


def _write_raw(run_dir: Path, category: str, page: int, payload: dict[str, Any]) -> None:
    path = run_dir / f"{category}_p{page}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _store_page(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    category: str,
    *,
    snapshot_id: int,
    aliases: dict[str, str],
    keywords: list[TechKeyword],
) -> int:
    now = _now_iso()
    with conn:  # una transacción por página
        for raw in rows:
            store_job(
                conn, parse_job(raw, category),
                snapshot_id=snapshot_id, now=now, aliases=aliases, keywords=keywords,
            )
    return len(rows)


def ingest_from_cache(
    conn: sqlite3.Connection,
    cache_dir: Path,
    *,
    aliases: dict[str, str],
    keywords: list[TechKeyword],
) -> IngestResult:
    """Reprocesa el JSON crudo de una corrida anterior. No toca la red."""
    files = sorted(cache_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No hay páginas cacheadas en {cache_dir}")

    snapshot_id = _start_snapshot(conn, "cache")
    result = IngestResult(snapshot_id=snapshot_id, run_dir=cache_dir)
    categories: set[str] = set()

    for path in files:
        match = _CACHE_NAME.match(path.name)
        if not match:
            result.errors.append(f"nombre de archivo no reconocido: {path.name}")
            continue
        category = match.group("category")
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("data", [])
        _store_page(conn, rows, category, snapshot_id=snapshot_id, aliases=aliases, keywords=keywords)
        result.jobs_seen += len(rows)
        result.pages += 1
        categories.add(category)

    result.categories = sorted(categories)
    _finish_snapshot(conn, result)
    return result


def refresh_aliases(conn: sqlite3.Connection, aliases: dict[str, str]) -> int:
    """Reaplica data/tag_aliases.yml sobre la tabla `tags`, sin red ni reingesta."""
    rows = conn.execute("SELECT id, canonical_slug FROM tags").fetchall()
    changed = [
        (canonical(row["id"], aliases), row["id"])
        for row in rows
        if canonical(row["id"], aliases) != row["canonical_slug"]
    ]
    with conn:
        conn.executemany("UPDATE tags SET canonical_slug = ? WHERE id = ?", changed)
    return len(changed)


def latest_cache_dir(raw_root: Path = config.RAW_DIR) -> Path:
    dirs = sorted((d for d in raw_root.glob("*") if d.is_dir()), reverse=True)
    if not dirs:
        raise FileNotFoundError(f"No hay corridas cacheadas en {raw_root}")
    return dirs[0]


# ----------------------------------------------------------------------- CLI


def _summary(conn: sqlite3.Connection, result: IngestResult) -> str:
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("jobs", "companies", "tags", "job_tags", "snapshots")
    }
    by_source = dict(
        conn.execute("SELECT source, COUNT(*) FROM job_tags GROUP BY source").fetchall()
    )
    lines = [
        "",
        f"Snapshot #{result.snapshot_id}: {result.jobs_seen} ofertas en {result.pages} páginas "
        f"({len(result.categories)} categorías)",
        f"  jobs={counts['jobs']}  companies={counts['companies']}  tags={counts['tags']}  "
        f"job_tags={counts['job_tags']} (api={by_source.get('api', 0)}, texto={by_source.get('text', 0)})",
        f"  snapshots acumulados: {counts['snapshots']}",
    ]
    if result.run_dir:
        lines.append(f"  JSON crudo en: {result.run_dir}")
    if result.errors:
        lines.append(f"  ERRORES ({len(result.errors)}):")
        lines += [f"    - {e}" for e in result.errors]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gob.ingest",
        description="Ingesta las ofertas activas de Get on Board a SQLite.",
    )
    parser.add_argument("--full", action="store_true", help="recorre las 18 categorías")
    parser.add_argument("--categories", help="lista separada por comas de slugs de categoría")
    parser.add_argument(
        "--from-cache", action="store_true",
        help="reprocesa el JSON de data/raw/ sin llamar a la API",
    )
    parser.add_argument("--cache-dir", type=Path, help="corrida concreta a reprocesar (por defecto, la última)")
    parser.add_argument(
        "--refresh-aliases", action="store_true",
        help="reaplica data/tag_aliases.yml a la tabla tags y termina",
    )
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--sandbox", action="store_true", help="usa sandbox.getonbrd.dev")
    parser.add_argument("--per-page", type=int, default=MAX_PER_PAGE, help=f"máximo {MAX_PER_PAGE}")
    parser.add_argument("--rate", type=float, default=2.0, help="peticiones por segundo (default 2)")
    parser.add_argument("--lang", default="es", choices=("es", "en", "pt"))
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx loguea cada request en INFO; con 27 peticiones tapa el resumen.
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    if not any((args.full, args.categories, args.from_cache, args.refresh_aliases)):
        build_parser().print_help()
        print("\nElige una acción: --full, --categories, --from-cache o --refresh-aliases")
        return 2
    if args.per_page > MAX_PER_PAGE:
        print(f"--per-page no puede pasar de {MAX_PER_PAGE} (límite de la API)")
        return 2

    aliases = load_aliases(config.TAG_ALIASES_PATH)
    keywords = load_keywords(config.TECH_KEYWORDS_PATH)
    log.info("%d alias y %d keywords cargadas", len(aliases), len(keywords))

    conn = connect(args.db)
    init_db(conn)
    try:
        if args.refresh_aliases:
            changed = refresh_aliases(conn, aliases)
            print(f"Alias reaplicados: {changed} tags cambiaron de canónico")
            return 0

        if args.from_cache:
            cache_dir = args.cache_dir or latest_cache_dir()
            log.info("Reprocesando %s (sin red)", cache_dir)
            result = ingest_from_cache(conn, cache_dir, aliases=aliases, keywords=keywords)
        else:
            categories = (
                [c.strip() for c in args.categories.split(",") if c.strip()]
                if args.categories else None
            )
            base_url = SANDBOX_BASE_URL if args.sandbox else PROD_BASE_URL
            with GobClient(base_url=base_url, lang=args.lang, rate_per_second=args.rate) as client:
                result = ingest_from_api(
                    conn, client,
                    categories=categories, per_page=args.per_page,
                    aliases=aliases, keywords=keywords,
                )

        print(_summary(conn, result))
        return 1 if result.errors else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
