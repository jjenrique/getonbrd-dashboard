"""Rate limit, reintentos y paginación del cliente HTTP.

Todo va contra `httpx.MockTransport`: ni una petición real (el fixture `sin_red`
de conftest.py lo garantiza cortando los sockets).
"""

from __future__ import annotations

import itertools

import httpx
import pytest

from gob.client import MAX_PER_PAGE, ApiError, GobClient

PAGINA_VACIA = {"data": [], "meta": {"page": 1, "per_page": 120, "total_pages": 1}}


def hacer_cliente(handler, **kwargs):
    """Cliente sin esperas reales: guarda los sleeps en una lista para poder verlos."""
    esperas: list[float] = []
    cliente = GobClient(
        transport=httpx.MockTransport(handler),
        rate_per_second=0,          # el espaciador se prueba aparte
        sleep=esperas.append,
        **kwargs,
    )
    return cliente, esperas


def respuestas(*items):
    """Handler que devuelve una respuesta distinta en cada llamada."""
    it = iter(items)
    return lambda request: next(it)


# ---------------------------------------------------------------- reintentos


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_reintenta_y_acaba_saliendo(status):
    cliente, esperas = hacer_cliente(
        respuestas(httpx.Response(status, json={"message": "uf"}), httpx.Response(200, json=PAGINA_VACIA))
    )
    assert cliente.get_categories() == []
    assert len(esperas) == 1


@pytest.mark.parametrize("status", [400, 404, 422])
def test_los_4xx_que_no_son_429_fallan_en_el_acto(status):
    """Repetir un 404 no lo va a arreglar; gastar cinco reintentos en él solo
    retrasa el error."""
    llamadas = itertools.count()

    def handler(request):
        next(llamadas)
        return httpx.Response(status, json={"message": "no", "code": "nope"})

    cliente, esperas = hacer_cliente(handler)
    with pytest.raises(ApiError) as exc:
        cliente.get_categories()
    assert exc.value.status == status
    assert next(llamadas) == 1
    assert esperas == []


def test_el_backoff_es_exponencial():
    cliente, esperas = hacer_cliente(
        lambda r: httpx.Response(503, json={"message": "uf"}), max_retries=4
    )
    with pytest.raises(ApiError):
        cliente.get_categories()
    assert len(esperas) == 4
    # 1, 2, 4, 8 s más un jitter de hasta el 10%
    for espera, base in zip(esperas, (1, 2, 4, 8)):
        assert base <= espera <= base * 1.1


def test_respeta_retry_after():
    cliente, esperas = hacer_cliente(
        respuestas(
            httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "calma"}),
            httpx.Response(200, json=PAGINA_VACIA),
        )
    )
    cliente.get_categories()
    assert esperas == [7.0]


def test_retry_after_con_formato_de_fecha_cae_al_backoff():
    cliente, esperas = hacer_cliente(
        respuestas(
            httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, json={}),
            httpx.Response(200, json=PAGINA_VACIA),
        )
    )
    cliente.get_categories()
    assert 1 <= esperas[0] <= 1.1


def test_los_errores_de_red_tambien_se_reintentan():
    intentos = itertools.count()

    def handler(request):
        if next(intentos) < 2:
            raise httpx.ConnectError("sin red")
        return httpx.Response(200, json=PAGINA_VACIA)

    cliente, esperas = hacer_cliente(handler)
    assert cliente.get_categories() == []
    assert len(esperas) == 2


def test_se_rinde_con_un_mensaje_util():
    cliente, _ = hacer_cliente(
        lambda r: httpx.Response(503, json={"message": "unavailable"}), max_retries=2
    )
    with pytest.raises(ApiError, match="falló tras 2 reintentos"):
        cliente.get_categories()


def test_error_sin_cuerpo_json():
    cliente, _ = hacer_cliente(lambda r: httpx.Response(404, text="<html>Not found</html>"))
    with pytest.raises(ApiError, match="HTTP 404"):
        cliente.get_categories()


# ---------------------------------------------------------------- peticiones


def test_expand_va_con_corchetes_repetidos():
    """`?expand=tags` sin corchetes devuelve 500 en la API real (docs/API.md §3.2),
    así que la codificación importa."""
    vistas = []

    def handler(request):
        vistas.append(str(request.url))
        return httpx.Response(200, json=PAGINA_VACIA)

    cliente, _ = hacer_cliente(handler)
    list(cliente.iter_category_job_pages("programacion", expand=("tags", "company")))
    assert "expand%5B%5D=tags&expand%5B%5D=company" in vistas[0]


def test_manda_el_idioma_y_el_user_agent():
    vistas = []

    def handler(request):
        vistas.append(request)
        return httpx.Response(200, json=PAGINA_VACIA)

    cliente, _ = hacer_cliente(handler, lang="en")
    cliente.get_categories()
    assert "lang=en" in str(vistas[0].url)
    assert vistas[0].headers["User-Agent"].startswith("gob-dashboard/")


def test_no_manda_cabecera_de_autenticacion():
    """La parte pública no lleva credenciales y no queremos filtrar ninguna."""
    vistas = []

    def handler(request):
        vistas.append(request)
        return httpx.Response(200, json=PAGINA_VACIA)

    cliente, _ = hacer_cliente(handler)
    cliente.get_categories()
    assert "Authorization" not in vistas[0].headers


# ---------------------------------------------------------------- paginación


def pagina(page, total_pages, n=2):
    return httpx.Response(
        200,
        json={
            "data": [{"id": f"job-{page}-{i}"} for i in range(n)],
            "meta": {"page": page, "per_page": MAX_PER_PAGE, "total_pages": total_pages},
        },
    )


def test_pagina_hasta_total_pages():
    cliente, _ = hacer_cliente(respuestas(pagina(1, 3), pagina(2, 3), pagina(3, 3)))
    paginas = list(cliente.iter_category_job_pages("programacion"))
    assert [p for p, _ in paginas] == [1, 2, 3]


def test_para_en_una_pagina_vacia_aunque_total_pages_diga_otra_cosa():
    """Defensa barata contra un total_pages inconsistente: pedir más allá del
    final devuelve 200 con data: [] (docs/API.md §3.1)."""
    cliente, _ = hacer_cliente(respuestas(pagina(1, 99), httpx.Response(200, json=PAGINA_VACIA)))
    assert len(list(cliente.iter_category_job_pages("programacion"))) == 1


def test_una_sola_pagina():
    cliente, _ = hacer_cliente(respuestas(pagina(1, 1)))
    [(numero, payload)] = list(cliente.iter_category_job_pages("programacion"))
    assert numero == 1 and len(payload["data"]) == 2


def test_categoria_sin_ofertas():
    cliente, _ = hacer_cliente(respuestas(httpx.Response(200, json=PAGINA_VACIA)))
    assert list(cliente.iter_category_job_pages("vacia")) == []


# --------------------------------------------------------------- rate limit


@pytest.fixture
def reloj_falso(monkeypatch):
    """Sustituye monotonic/sleep para poder medir esperas sin esperar de verdad."""
    from gob import client as modulo

    ahora = [0.0]
    dormido: list[float] = []
    monkeypatch.setattr(modulo.time, "monotonic", lambda: ahora[0])

    def dormir(segundos):
        dormido.append(segundos)
        ahora[0] += segundos

    monkeypatch.setattr(modulo.time, "sleep", dormir)
    return ahora, dormido


def test_la_primera_peticion_no_espera(reloj_falso):
    """Con monotonic() arrancando en 0, un _last_call inicializado a 0.0 haría
    esperar a la primera petición del proceso sin ninguna razón."""
    from gob.client import _RateLimiter

    _, dormido = reloj_falso
    _RateLimiter(2.0).wait()
    assert dormido == []


def test_el_espaciador_respeta_las_peticiones_por_segundo(reloj_falso):
    from gob.client import _RateLimiter

    ahora, dormido = reloj_falso
    limitador = _RateLimiter(2.0)   # 2 req/s -> 0,5 s entre peticiones
    limitador.wait()
    limitador.wait()
    assert dormido == [0.5]

    # Si entre peticiones ya pasó más tiempo del mínimo, no hay que esperar nada.
    ahora[0] += 3.0
    limitador.wait()
    assert dormido == [0.5]


def test_rate_cero_no_espera():
    from gob.client import _RateLimiter

    limitador = _RateLimiter(0)
    limitador.wait()
    limitador.wait()   # no se cuelga
