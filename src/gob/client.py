"""Cliente HTTP de la API pública de Get on Board.

Solo transporte: rate limiting, reintentos y desempaquetado del sobre JSON.
Nada de lógica de negocio ni de base de datos — de eso se encarga `ingest.py`.

El esquema real de la API está documentado en docs/API.md. Los detalles que
condicionan este archivo:

- `/categories/{slug}/jobs` sí acepta `page` y `expand[]` aunque el OpenAPI no
  los documente (§3.1).
- `expand` mal formado devuelve 500, no 422 (§3.2), así que un 500 puede ser
  culpa nuestra: reintentamos con tope y luego fallamos ruidosamente.
- Pedir una página más allá de `total_pages` devuelve 200 con `data: []`.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

PROD_BASE_URL = "https://www.getonbrd.com"
SANDBOX_BASE_URL = "https://sandbox.getonbrd.dev"
API_PREFIX = "/api/v0"

USER_AGENT = "gob-dashboard/0.1 (uso personal; https://github.com/local/gob-dashboard)"

#: Valores de `expand[]` verificados uno por uno en /categories/{slug}/jobs.
#: `perks` NO está: devuelve 500. Ver docs/API.md §3.2.
JOB_EXPAND = (
    "tags",
    "company",
    "seniority",
    "modality",
    "location_cities",
    "location_regions",
)

MAX_PER_PAGE = 120

#: Reintentamos estos status. El resto de 4xx son errores nuestros y no mejoran
#: repitiendo la misma petición.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class ApiError(RuntimeError):
    """Fallo de la API que no se pudo resolver reintentando."""

    def __init__(self, message: str, *, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class _RateLimiter:
    """Espaciador simple de peticiones: como mucho `rate` por segundo."""

    def __init__(self, rate_per_second: float):
        self._min_interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        # None y no 0.0: en plataformas donde monotonic() arranca cerca de cero,
        # un 0.0 haría que la PRIMERA petición del proceso esperase sin motivo.
        self._last_call: float | None = None

    def wait(self) -> None:
        if not self._min_interval:
            return
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


class GobClient:
    """Cliente de la parte pública de la API. No necesita credenciales."""

    def __init__(
        self,
        *,
        base_url: str = PROD_BASE_URL,
        lang: str = "es",
        rate_per_second: float = 2.0,
        max_retries: int = 5,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self.lang = lang
        self.max_retries = max_retries
        self._limiter = _RateLimiter(rate_per_second)
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + API_PREFIX,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> GobClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ HTTP

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET con rate limit y reintento exponencial. Devuelve el JSON ya parseado."""
        params = {"lang": self.lang, **(params or {})}
        last_error: str = ""
        last_status: int | None = None

        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            response: httpx.Response | None = None
            try:
                response = self._http.get(path, params=params)
            except httpx.HTTPError as exc:  # timeouts, DNS, conexión caída…
                last_error, last_status = f"{type(exc).__name__}: {exc}", None
            else:
                if response.status_code < 400:
                    return response.json()
                last_status = response.status_code
                last_error = _describe_error(response)
                if response.status_code not in RETRYABLE_STATUS:
                    raise ApiError(last_error, status=last_status, url=str(response.url))

            if attempt == self.max_retries:
                break
            delay = _backoff_delay(attempt, _retry_after(response))
            log.warning(
                "%s%s -> %s. Reintento %d/%d en %.1fs",
                path, _fmt_params(params), last_error, attempt + 1, self.max_retries, delay,
            )
            self._sleep(delay)

        raise ApiError(
            f"{path}{_fmt_params(params)} falló tras {self.max_retries} reintentos: {last_error}",
            status=last_status,
            url=path,
        )

    # ------------------------------------------------------- Tablas de apoyo

    def get_categories(self) -> list[dict[str, Any]]:
        """Las 18 categorías. Cabe de sobra en una página."""
        return self._get("/categories", {"per_page": MAX_PER_PAGE})["data"]

    def get_seniorities(self) -> list[dict[str, Any]]:
        return self._get("/seniorities", {"per_page": MAX_PER_PAGE})["data"]

    def get_modalities(self) -> list[dict[str, Any]]:
        return self._get("/modalities", {"per_page": MAX_PER_PAGE})["data"]

    def get_company(self, company_id: str) -> dict[str, Any]:
        return self._get(f"/companies/{company_id}")["data"]

    def iter_tags(self, per_page: int = MAX_PER_PAGE) -> Iterator[dict[str, Any]]:
        """Catálogo completo de tags (~5.851). La ingesta no lo necesita: los tags
        llegan expandidos dentro de cada oferta."""
        for payload in self._iter_pages("/tags", {}, per_page):
            yield from payload["data"]

    # ---------------------------------------------------------------- Ofertas

    def iter_category_job_pages(
        self,
        category_slug: str,
        *,
        expand: tuple[str, ...] = JOB_EXPAND,
        per_page: int = MAX_PER_PAGE,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """Pagina las ofertas de una categoría. Cede `(nº de página, payload crudo)`.

        Se cede el payload entero, sin tocar, para que la ingesta pueda cachearlo
        en disco tal cual antes de procesarlo.
        """
        params = {"expand[]": list(expand)}
        for page, payload in enumerate(self._iter_pages(f"/categories/{category_slug}/jobs", params, per_page), start=1):
            yield page, payload

    def search_jobs(self, **params: Any) -> dict[str, Any]:
        """`/search/jobs` exige al menos uno de: query, companies, featured, remote,
        country_code, board_host. Sin ellos devuelve 422 (docs/API.md §3.4).
        No lo usa la ingesta; está para consultas puntuales."""
        return self._get("/search/jobs", params)

    # -------------------------------------------------------------- Interno

    def _iter_pages(
        self, path: str, params: dict[str, Any], per_page: int
    ) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            payload = self._get(path, {**params, "per_page": per_page, "page": page})
            rows = payload.get("data", [])
            if not rows:
                return
            yield payload
            total_pages = (payload.get("meta") or {}).get("total_pages")
            if total_pages is not None and page >= total_pages:
                return
            page += 1


def _backoff_delay(attempt: int, retry_after: float | None) -> float:
    """1, 2, 4, 8, 16 s con jitter. `Retry-After` manda si el servidor lo envía."""
    if retry_after is not None:
        return min(retry_after, 60.0)
    return min(2.0**attempt, 16.0) * (1.0 + random.random() * 0.1)


def _retry_after(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None  # formato fecha HTTP: no vale la pena parsearlo, usamos el backoff


def _describe_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("message") or body.get("code") or ""
    except ValueError:
        detail = response.text[:200]
    return f"HTTP {response.status_code} {detail}".strip()


def _fmt_params(params: dict[str, Any]) -> str:
    interesting = {k: v for k, v in params.items() if k in ("page", "per_page")}
    return f" {interesting}" if interesting else ""
