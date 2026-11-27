"""Calce entre ofertas y perfiles. Cifras calculadas a mano, no copiadas de la salida.

Recordatorio de los pesos (src/gob/calce.py): tag oficial 1, `fuerte` 2,
deducido del texto la mitad; denominador mínimo 5; puntaje = 80% técnico +
20% condiciones deseables (cumple 1, no cumple 0, sin dato 0,5).
"""

from __future__ import annotations

import pytest

from gob.analytics import Job, Snapshot, TagRef, activas
from gob.calce import (
    MIN_DENOMINADOR,
    TOP_TE_ABRIRIA,
    Condicion,
    Perfil,
    calce_tecnico,
    calzar,
    comparar_perfiles,
    es_candidata,
    evaluar_condiciones,
    puntuar,
    resumir,
)


def job(id_, tags=(), texto=(), **kwargs) -> Job:
    """`tags` llegan como oficiales; `texto`, como deducidos de la descripción."""
    refs = [TagRef(t, t.title(), "api") for t in tags] + [TagRef(t, t.title(), "text") for t in texto]
    kwargs.setdefault("category_slug", "programacion")
    return Job(id=id_, title=kwargs.pop("title", id_), tags=tuple(refs), **kwargs)


def perfil(**kwargs) -> Perfil:
    base = dict(
        slug="p",
        nombre="Perfil",
        categorias=frozenset({"programacion"}),
        tambien_si_pide=frozenset({"php"}),
        fuerte=frozenset({"python"}),
        stack=frozenset({"python", "sql"}),
    )
    base.update(kwargs)
    return Perfil(**base)


REMOTO = Condicion("remoto", True, acepto=frozenset({"fully_remote", "hybrid"}))
SUELDO = Condicion("sueldo_minimo", False, minimo=2000)
PAISES = Condicion("paises", False, acepto=frozenset({"chile", "remoto"}))
JORNADA = Condicion("jornada", False, acepto=frozenset({"full_time"}))


# ---------------------------------------------------------------- activas


def test_activas_son_las_de_la_ultima_corrida():
    jobs = [job("a"), job("b"), job("c")]
    snaps = [
        Snapshot(2, "2026-11-26T22:00:00+00:00", frozenset({"b", "c"})),
        Snapshot(1, "2026-08-11T05:00:00+00:00", frozenset({"a", "b"})),
    ]
    assert [j.id for j in activas(jobs, snaps)] == ["b", "c"]


def test_sin_snapshots_no_se_puede_saber_y_va_todo():
    assert len(activas([job("a"), job("b")], [])) == 2


# ------------------------------------------------------------- candidatas


@pytest.mark.parametrize(
    "oferta, esperado",
    [
        (job("cat", ["java"]), True),                                          # por categoría
        (job("tag", ["php"], category_slug="data-science-analytics"), True),   # por tambien_si_pide
        (job("no", ["python"], category_slug="data-science-analytics"), False),  # python no abre la puerta
    ],
)
def test_es_candidata(oferta, esperado):
    assert es_candidata(oferta, perfil()) is esperado


def test_marca_si_entro_por_categoria_o_por_tag():
    p = perfil()
    assert puntuar(job("cat", ["java"]), p).por_categoria
    assert not puntuar(job("tag", ["php"], category_slug="otra"), p).por_categoria


# ---------------------------------------------------------------- técnico


def test_lo_fuerte_pesa_doble():
    # python 2 (fuerte) + sql 1 de 2+1+1+1+1 = 6 -> 3/6
    calce, tiene, falta = calce_tecnico(job("j", ["python", "sql", "docker", "react", "kubernetes"]), perfil())
    assert calce == pytest.approx(0.5)
    assert tiene == ("python", "sql")                  # lo fuerte primero
    assert set(falta) == {"docker", "react", "kubernetes"}


def test_lo_deducido_del_texto_pesa_la_mitad():
    # python 2 + sql(texto) 0,5 de 2 + 0,5 + docker 1 + react 1 + k8s 1 + aws 1 = 6,5
    oferta = job("j", ["python", "docker", "react", "kubernetes", "aws"], texto=["sql"])
    calce, _, _ = calce_tecnico(oferta, perfil())
    assert calce == pytest.approx(2.5 / 6.5)


def test_pocos_tags_no_inflan_el_calce():
    """Una oferta que solo pide python no es un calce del 100%."""
    calce, _, _ = calce_tecnico(job("j", ["python"]), perfil())
    assert calce == pytest.approx(2 / MIN_DENOMINADOR)


def test_oferta_sin_tags_calza_cero():
    assert calce_tecnico(job("vacia"), perfil())[0] == 0


def test_el_tag_oficial_manda_sobre_el_de_texto():
    """El mismo slug por las dos vías cuenta una vez, con peso de oficial."""
    doble = job("j", ["sql", "a", "b", "c", "d"], texto=["sql"])
    simple = job("k", ["sql", "a", "b", "c", "d"])
    assert calce_tecnico(doble, perfil())[0] == calce_tecnico(simple, perfil())[0] == pytest.approx(1 / 5)


def test_lo_que_falta_pone_primero_lo_oficial():
    _, _, falta = calce_tecnico(job("j", ["zeta"], texto=["alfa"]), perfil())
    assert falta == ("zeta", "alfa")


# ------------------------------------------------------------ condiciones


@pytest.mark.parametrize(
    "kwargs, cumple, detalle",
    [
        ({"min_salary": 1500, "max_salary": 2500}, True, "1500–2500"),   # se mira el techo
        ({"min_salary": 1800}, False, "1800"),                             # solo un extremo: ese
        ({}, None, ""),                                                    # no publica
    ],
)
def test_sueldo_minimo(kwargs, cumple, detalle):
    (ch,) = evaluar_condiciones(job("j", **kwargs), perfil(condiciones=(SUELDO,)))
    assert (ch.cumple, ch.detalle) == (cumple, detalle)


@pytest.mark.parametrize(
    "paises, cumple",
    [(("Chile",), True), (("Remoto",), True), (("Perú", "Colombia"), False), ((), None)],
)
def test_paises_compara_por_slug(paises, cumple):
    (ch,) = evaluar_condiciones(job("j", countries=paises), perfil(condiciones=(PAISES,)))
    assert ch.cumple is cumple


@pytest.mark.parametrize("modalidad, cumple", [("hybrid", True), ("no_remote", False), (None, None)])
def test_remoto(modalidad, cumple):
    (ch,) = evaluar_condiciones(job("j", remote_modality=modalidad), perfil(condiciones=(REMOTO,)))
    assert ch.cumple is cumple


def test_jornada():
    (ch,) = evaluar_condiciones(job("j", modality="freelance"), perfil(condiciones=(JORNADA,)))
    assert ch.cumple is False and ch.detalle == "freelance"


def test_lo_obligatorio_descarta_pero_la_falta_de_dato_no():
    p = perfil(condiciones=(REMOTO,))
    jobs = [
        job("presencial", ["python"], remote_modality="no_remote"),
        job("hibrida", ["python"], remote_modality="hybrid"),
        job("sin-dato", ["python"]),
    ]
    informe = calzar(jobs, p)
    assert informe.candidatas == 3 and informe.descartadas == 1
    assert {c.job.id for c in informe.items} == {"hibrida", "sin-dato"}
    assert informe.descartadas_por == (("remoto", 1),)


def test_sueldo_obligatorio_no_descarta_a_quien_no_publica():
    p = perfil(condiciones=(Condicion("sueldo_minimo", True, minimo=2000),))
    informe = calzar([job("bajo", max_salary=1000), job("callado")], p)
    assert [c.job.id for c in informe.items] == ["callado"]


# ---------------------------------------------------------------- puntaje


def test_puntaje_sin_deseables_es_el_tecnico():
    c = puntuar(job("j", ["python", "sql", "docker", "react", "kubernetes"]), perfil())
    assert c.puntaje == 50 and c.condiciones is None


def test_puntaje_mezcla_80_20_con_lo_deseable():
    # técnico 0,5; deseables: sueldo cumple (1) y país sin dato (0,5) -> 0,75
    # 0,8 * 0,5 + 0,2 * 0,75 = 0,55
    oferta = job("j", ["python", "sql", "docker", "react", "kubernetes"], max_salary=3000)
    c = puntuar(oferta, perfil(condiciones=(SUELDO, PAISES)))
    assert c.condiciones == pytest.approx(0.75)
    assert c.puntaje == 55


def test_lo_obligatorio_no_suma_puntos():
    """Una condición obligatoria filtra; si además sumara, contaría dos veces."""
    oferta = job("j", ["python", "sql", "docker", "react", "kubernetes"], remote_modality="hybrid")
    assert puntuar(oferta, perfil(condiciones=(REMOTO,))).puntaje == 50


def test_calzar_ordena_por_puntaje_y_luego_por_lo_mas_reciente():
    jobs = [
        job("vieja", ["python"], published_at=100),
        job("mejor", ["python", "sql"], published_at=50),
        job("nueva", ["python"], published_at=200),
    ]
    assert [c.job.id for c in calzar(jobs, perfil()).items] == ["mejor", "nueva", "vieja"]


# ---------------------------------------------------------------- resumen


def test_resumir_cuenta_el_buen_calce_y_lo_que_falta():
    jobs = [
        job("buena", ["python", "sql", "docker"], max_salary=3000),   # 3/5 = 0,6
        job("justa", ["python", "sql", "docker", "react"]),          # 3/5 = 0,6
        job("floja", ["python", "docker", "react", "aws"]),          # 2/5 = 0,4
        job("fuera", ["python"], category_slug="otra"),               # no es candidata
    ]
    r = resumir(calzar(jobs, perfil()))
    assert (r.candidatas, r.evaluadas, r.buen_calce) == (3, 3, 2)
    assert r.mediana_salario_buen_calce == 3000 and r.n_salario_buen_calce == 1
    assert r.te_abriria[:2] == (("docker", 3), ("react", 2))


def test_te_abriria_mira_solo_los_mejores():
    jobs = [job(f"j{i:02}", ["python", "sql"], published_at=i) for i in range(TOP_TE_ABRIRIA)]
    jobs.append(job("peor", ["python", "rust", "go", "zig"], published_at=0))
    assert "rust" not in dict(resumir(calzar(jobs, perfil())).te_abriria)


def test_comparar_perfiles_pone_primero_donde_hay_mas_oportunidad():
    jobs = [job("a", ["python", "sql"]), job("b", ["php", "laravel"], category_slug="otra")]
    python = perfil(slug="python")
    php = perfil(slug="php", fuerte=frozenset({"php"}), stack=frozenset({"php"}))
    # perfil python: "a" calza 3/5 (buen calce); "b" entra por php pero calza 0.
    # perfil php: "b" calza 2/5 (no llega a buen calce); "a" calza 0.
    orden = [r.perfil.slug for r in comparar_perfiles(jobs, [php, python])]
    assert orden == ["python", "php"]


def test_perfil_sin_candidatas_no_revienta():
    r = resumir(calzar([], perfil()))
    assert r.mejor_puntaje is None and r.buen_calce == 0 and r.te_abriria == ()
