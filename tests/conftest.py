"""Fixtures compartidas y la red cortada de raíz."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from gob.analytics import Job, Snapshot, TagRef

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Ningún test puede llamar a la API.

    Si alguno lo intenta, falla en el acto con un mensaje claro en vez de tardar
    30 s y depender de que haya internet. `httpx.MockTransport` no abre sockets,
    así que los tests del cliente siguen funcionando.
    """

    def prohibido(*args, **kwargs):
        raise AssertionError(
            "Un test intentó abrir un socket. Los tests van con fixtures fijas, "
            "sin llamadas reales a la API."
        )

    monkeypatch.setattr(socket, "socket", prohibido)
    monkeypatch.setattr(socket, "create_connection", prohibido)


@pytest.fixture(scope="session")
def jobs_fixture() -> list[Job]:
    """15 ofertas inventadas, con casos incómodos a propósito:
    sin tags, sin sueldo, sin seniority, multi-país y tags por texto."""
    raw = json.loads((FIXTURES / "jobs.json").read_text(encoding="utf-8"))
    return [_to_job(row) for row in raw]


@pytest.fixture(scope="session")
def snapshots_fixture() -> list[Snapshot]:
    raw = json.loads((FIXTURES / "snapshots.json").read_text(encoding="utf-8"))
    return [
        Snapshot(id=s["id"], run_at=s["run_at"], job_ids=frozenset(s["job_ids"]))
        for s in raw
    ]


@pytest.fixture(scope="session")
def api_jobs_fixture() -> list[dict]:
    """Payloads con la forma exacta de la API, para probar `parse_job`."""
    return json.loads((FIXTURES / "api_jobs.json").read_text(encoding="utf-8"))["data"]


def _to_job(row: dict) -> Job:
    return Job(
        id=row["id"],
        title=row["title"],
        url=row.get("url"),
        company_id=row.get("company_id"),
        company_name=row.get("company_name"),
        category_slug=row.get("category_slug"),
        category_name=row.get("category_name"),
        seniority=row.get("seniority"),
        modality=row.get("modality"),
        remote=row.get("remote", False),
        remote_modality=row.get("remote_modality"),
        countries=tuple(row.get("countries", ())),
        cities=tuple(row.get("cities", ())),
        min_salary=row.get("min_salary"),
        max_salary=row.get("max_salary"),
        currency=row.get("currency"),
        published_at=row.get("published_at"),
        tags=tuple(TagRef(slug=t[0], name=t[1], source=t[2]) for t in row.get("tags", ())),
    )
