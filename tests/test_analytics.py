"""Agregaciones sobre las 15 ofertas de tests/fixtures/jobs.json.

Las cifras esperadas están calculadas a mano sobre esa fixture, no copiadas de
la salida del código. El reparto de tags de la fixture es:

    python 9 (j01-j05, j08, j10, j14, j15)   docker 4 (j01 texto, j05-j07)
    javascript 3 (j04, j11, j12)             kubernetes 3 (j05-j07)
    react 3 (j11, j12, j14)                  sql 3 (j08-j10)
    aws 2 (j02, j05)   django 2 (j01, j02)   postgresql 2 (j01, j03)
    excel / java / typescript 1 cada uno
    llm, spark, terraform 1 cada uno y SOLO por texto
    j13 no tiene ningún tag
"""

from __future__ import annotations

import pytest

from gob.analytics import (
    MIN_MUESTRA_SALARIO,
    Filtros,
    Job,
    TagRef,
    brechas,
    co_ocurrencia,
    cobertura,
    empresas_top,
    filtrar,
    resumen,
    salario_por_tag,
    salarios_por_tags,
    tags_por_seniority,
    tendencia,
    top_tags,
)


def job(id_, tags=(), **kwargs) -> Job:
    """Job mínimo para los casos de borde que no merecen entrar en la fixture."""
    return Job(
        id=id_,
        title=kwargs.pop("title", id_),
        tags=tuple(TagRef(t, t.title(), kwargs.pop("source", "api")) for t in tags),
        **kwargs,
    )


# ------------------------------------------------------------------- filtros


def test_sin_filtros_devuelve_todo(jobs_fixture):
    assert len(filtrar(jobs_fixture)) == 15
    assert len(filtrar(jobs_fixture, Filtros())) == 15


@pytest.mark.parametrize(
    "filtros, esperado",
    [
        (Filtros(categoria="sysadmin-devops-qa"), 3),
        (Filtros(seniority="senior"), 6),           # j01, j02, j05, j07, j12, j14
        (Filtros(remoto=True), 8),
        (Filtros(remoto=False), 7),
        (Filtros(pais="Chile"), 7),                 # j01, j03, j06, j07, j09, j13, j15
        (Filtros(pais="Peru"), 2),                  # j04 y j06 (multipaís)
        (Filtros(empresa="acme"), 2),
        (Filtros(desde=1751000000), 5),             # j11..j15
        (Filtros(hasta=1750200000), 3),             # j01..j03
        (Filtros(categoria="programacion", seniority="senior"), 4),
    ],
)
def test_filtros(jobs_fixture, filtros, esperado):
    assert len(filtrar(jobs_fixture, filtros)) == esperado


def test_el_filtro_de_pais_mira_todos_los_paises_no_solo_el_primero(jobs_fixture):
    """j06 es ["Chile", "Peru"]: tiene que salir en ambos filtros. Es justo el
    caso que se perdería con una columna `country` simple."""
    ids_chile = {j.id for j in filtrar(jobs_fixture, Filtros(pais="Chile"))}
    ids_peru = {j.id for j in filtrar(jobs_fixture, Filtros(pais="Peru"))}
    assert "j06" in ids_chile and "j06" in ids_peru


def test_filtro_por_fecha_descarta_las_ofertas_sin_fecha():
    jobs = [job("con", published_at=100), job("sin", published_at=None)]
    assert [j.id for j in filtrar(jobs, Filtros(desde=1))] == ["con"]
    assert [j.id for j in filtrar(jobs, Filtros(hasta=1000))] == ["con"]


# ------------------------------------------------------------------ top tags


def test_top_tags_cuenta_ofertas_distintas(jobs_fixture):
    top = top_tags(jobs_fixture, limit=3)
    assert [(t.slug, t.ofertas) for t in top] == [("python", 9), ("docker", 4), ("javascript", 3)]


def test_top_tags_desempata_alfabeticamente(jobs_fixture):
    """javascript, kubernetes, react y sql tienen 3 cada uno."""
    empatados = [t.slug for t in top_tags(jobs_fixture, limit=6)][2:]
    assert empatados == ["javascript", "kubernetes", "react", "sql"]


def test_top_tags_separa_las_dos_procedencias(jobs_fixture):
    docker = next(t for t in top_tags(jobs_fixture, limit=10) if t.slug == "docker")
    assert (docker.ofertas, docker.api, docker.texto) == (4, 3, 1)  # j01 vino del texto
    assert not docker.solo_texto


def test_top_tags_marca_lo_que_solo_existe_en_el_texto(jobs_fixture):
    top = {t.slug: t for t in top_tags(jobs_fixture, limit=50)}
    assert top["llm"].solo_texto and top["llm"].api == 0
    assert not top["python"].solo_texto


def test_top_tags_filtrado_por_procedencia(jobs_fixture):
    solo_texto = {t.slug for t in top_tags(jobs_fixture, limit=50, source="text")}
    assert solo_texto == {"docker", "llm", "spark", "terraform"}


def test_top_tags_respeta_los_filtros(jobs_fixture):
    top = top_tags(jobs_fixture, limit=5, filtros=Filtros(categoria="sysadmin-devops-qa"))
    assert [(t.slug, t.ofertas) for t in top][:2] == [("docker", 3), ("kubernetes", 3)]


def test_top_tags_sin_ofertas():
    assert top_tags([]) == []


def test_top_tags_con_ofertas_sin_tags():
    assert top_tags([job("a"), job("b")]) == []


def test_un_tag_que_llega_por_las_dos_vias_cuenta_una_vez():
    """Si el alias une el oficial "cloud-gcp" y el "gcp" del texto, es la misma
    tecnología y una sola oferta; y manda la procedencia oficial."""
    j = Job(
        id="x",
        title="x",
        tags=(TagRef("gcp", "GCP", "text"), TagRef("gcp", "Google Cloud Platform", "api")),
    )
    [tag] = top_tags([j])
    assert (tag.ofertas, tag.api, tag.texto) == (1, 1, 0)


# -------------------------------------------------------------- por seniority


def test_tags_por_seniority_usa_el_orden_natural(jobs_fixture):
    matriz = tags_por_seniority(jobs_fixture, ["python"])
    assert matriz.seniorities == (
        "no_experience", "junior", "semi_senior", "senior", "expert", "sin_dato",
    )


def test_tags_por_seniority_reparte_bien(jobs_fixture):
    matriz = tags_por_seniority(jobs_fixture, ["python", "react"])
    filas = {slug: conteos for slug, _, conteos in matriz.filas}
    #                       no_exp junior semi senior expert sin_dato
    assert filas["python"] == (0, 1, 3, 4, 1, 0)
    assert filas["react"] == (0, 0, 0, 2, 0, 1)   # j11 no declara seniority
    assert matriz.total_por_tag() == {"python": 9, "react": 3}


def test_tags_por_seniority_sin_columna_sin_dato_si_todas_la_declaran():
    jobs = [job("a", ["python"], seniority="senior")]
    assert tags_por_seniority(jobs).seniorities == ("senior",)


def test_tags_por_seniority_toma_los_top_si_no_le_paso_tags(jobs_fixture):
    matriz = tags_por_seniority(jobs_fixture, limit=2)
    assert [slug for slug, _, _ in matriz.filas] == ["python", "docker"]


# -------------------------------------------------------------- co-ocurrencia


def test_co_ocurrencia_de_kubernetes(jobs_fixture):
    """kubernetes está en j05, j06 y j07; docker está en los tres."""
    resultado = {c.slug: c for c in co_ocurrencia(jobs_fixture, "kubernetes")}
    assert resultado["docker"].juntas == 3
    assert resultado["docker"].pct == pytest.approx(100.0)
    assert set(resultado) == {"docker", "python", "aws", "terraform"}


def test_co_ocurrencia_lift(jobs_fixture):
    """15 ofertas, 3 con kubernetes, 4 con docker, 3 juntas.
    lift = (3/15) / ((3/15) * (4/15)) = 3.75"""
    docker = next(c for c in co_ocurrencia(jobs_fixture, "kubernetes") if c.slug == "docker")
    assert docker.lift == pytest.approx(3.75)


def test_co_ocurrencia_no_se_incluye_a_si_mismo(jobs_fixture):
    assert all(c.slug != "python" for c in co_ocurrencia(jobs_fixture, "python"))


def test_co_ocurrencia_de_un_tag_inexistente(jobs_fixture):
    assert co_ocurrencia(jobs_fixture, "cobol") == []


def test_co_ocurrencia_de_un_tag_solitario():
    assert co_ocurrencia([job("a", ["python"])], "python") == []


# ----------------------------------------------------------------- tendencia


def test_tendencia_por_snapshot(jobs_fixture, snapshots_fixture):
    """python: 7 en el snapshot 1 (j01-j05, j08, j10); 6 en el 2 (j03 se cae);
    8 en el 3 (entran j14 y j15)."""
    [t] = tendencia(jobs_fixture, snapshots_fixture, ["python"])
    assert [p.ofertas for p in t.puntos] == [7, 6, 8]
    assert t.variacion == 1
    assert not t.historico_insuficiente


def test_tendencia_ordena_por_fecha_no_por_id(jobs_fixture, snapshots_fixture):
    desordenados = [snapshots_fixture[2], snapshots_fixture[0], snapshots_fixture[1]]
    [t] = tendencia(jobs_fixture, desordenados, ["python"])
    assert [p.snapshot_id for p in t.puntos] == [1, 2, 3]


def test_tendencia_avisa_cuando_no_hay_historico(jobs_fixture, snapshots_fixture):
    """Con un solo snapshot no hay evolución: la UI tiene que decirlo en vez de
    pintar una línea plana que parece un dato."""
    [t] = tendencia(jobs_fixture, snapshots_fixture[:1], ["python"])
    assert t.historico_insuficiente
    assert t.variacion is None


def test_tendencia_de_varios_tags_a_la_vez(jobs_fixture, snapshots_fixture):
    assert [t.slug for t in tendencia(jobs_fixture, snapshots_fixture, ["python", "react"])] == [
        "python", "react",
    ]


def test_tendencia_ignora_ids_de_snapshot_que_ya_no_estan_en_jobs(jobs_fixture, snapshots_fixture):
    """Un snapshot viejo puede referirse a ofertas ya borradas de la BD."""
    from gob.analytics import Snapshot

    fantasma = Snapshot(id=9, run_at="2026-08-20T00:00:00+00:00", job_ids=frozenset({"no-existe"}))
    [t] = tendencia(jobs_fixture, [*snapshots_fixture, fantasma], ["python"])
    assert t.puntos[-1].ofertas == 0


# ------------------------------------------------------------------ salarios


def test_salario_de_python(jobs_fixture):
    """7 de las 9 ofertas de python publican rango; los puntos medios ordenados
    son 1500, 1650, 2500, 3000, 3500, 4500, 5500."""
    s = salario_por_tag(jobs_fixture, "python")
    assert (s.n_total, s.n_con_salario) == (9, 7)
    assert s.suficiente
    assert s.mediana == pytest.approx(3000)
    assert s.p25 == pytest.approx(2075)     # interpolado entre 1650 y 2500
    assert s.p75 == pytest.approx(4000)     # interpolado entre 3500 y 4500
    assert (s.minimo, s.maximo) == (1000, 6000)
    assert s.currency == "USD"


def test_salario_oculta_la_mediana_con_muestra_pequena(jobs_fixture):
    """docker: 4 ofertas, solo 3 con sueldo. Devolver una mediana de 3 datos sin
    avisar sería inventarse una cifra."""
    s = salario_por_tag(jobs_fixture, "docker")
    assert (s.n_total, s.n_con_salario) == (4, 3)
    assert not s.suficiente
    assert s.mediana is None and s.p25 is None and s.p75 is None
    assert s.cobertura == pytest.approx(75.0)   # la muestra SÍ se informa siempre


def test_salario_en_el_limite_exacto_de_la_muestra():
    jobs = [
        job(f"j{i}", ["x"], min_salary=1000 * i, max_salary=1000 * i, currency="USD")
        for i in range(1, MIN_MUESTRA_SALARIO + 1)
    ]
    assert salario_por_tag(jobs, "x").suficiente
    assert not salario_por_tag(jobs[:-1], "x").suficiente


def test_salario_del_conjunto_entero(jobs_fixture):
    s = salario_por_tag(jobs_fixture)
    assert (s.slug, s.n_total, s.n_con_salario) == (None, 15, 9)


def test_salario_usa_el_punto_medio_del_rango():
    jobs = [job("a", ["x"], min_salary=1000, max_salary=3000)]
    assert jobs[0].salario_medio == 2000


def test_salario_con_un_solo_extremo_del_rango():
    assert job("a", min_salary=None, max_salary=3000).salario_medio == 3000
    assert job("b", min_salary=1000, max_salary=None).salario_medio == 1000
    assert job("c").salario_medio is None


def test_salario_de_un_tag_inexistente(jobs_fixture):
    s = salario_por_tag(jobs_fixture, "cobol")
    assert (s.n_total, s.n_con_salario, s.cobertura) == (0, 0, 0.0)


def test_salarios_por_tags_descarta_los_de_muestra_corta(jobs_fixture):
    resultados = salarios_por_tags(jobs_fixture)
    assert all(s.suficiente for s in resultados)
    assert [s.slug for s in resultados] == ["python"]   # el único que llega a 5


def test_salarios_por_tags_puede_listar_los_insuficientes(jobs_fixture):
    slugs = {s.slug for s in salarios_por_tags(jobs_fixture, solo_suficientes=False)}
    assert "docker" in slugs


# ------------------------------------------------------------------ empresas


def test_empresas_top(jobs_fixture):
    empresas = empresas_top(jobs_fixture)
    assert [(e.id, e.ofertas) for e in empresas[:3]] == [
        ("beta", 3), ("delta", 3), ("epsilon", 3),
    ]


def test_empresa_stack_dominante(jobs_fixture):
    """Gamma publica j06, j07 y j08: docker y kubernetes en dos cada uno."""
    gamma = next(e for e in empresas_top(jobs_fixture) if e.id == "gamma")
    assert gamma.stack[:2] == (("docker", 2), ("kubernetes", 2))
    assert gamma.con_salario == 2


def test_empresas_ignora_las_ofertas_sin_empresa(jobs_fixture):
    """j15 no tiene company_id: no puede inventarse una fila."""
    assert all(e.id for e in empresas_top(jobs_fixture))
    assert sum(e.ofertas for e in empresas_top(jobs_fixture)) == 14


def test_empresas_sin_ofertas():
    assert empresas_top([]) == []


# ------------------------------------------------------------------- brechas


def test_brechas_ordena_por_volumen(jobs_fixture):
    informe = brechas(jobs_fixture, {"python", "sql"}, umbral_minimo=1)
    assert (informe.items[0].slug, informe.items[0].ofertas) == ("docker", 4)
    assert [b.ofertas for b in informe.items] == sorted(
        (b.ofertas for b in informe.items), reverse=True
    )


def test_brechas_desempata_por_afinidad(jobs_fixture):
    """javascript, kubernetes y react empatan a 3 ofertas: delante va aquel cuyas
    ofertas ya cubro más, que es el que tengo más a mano."""
    informe = brechas(jobs_fixture, {"python", "sql"}, umbral_minimo=1)
    empatados = [b for b in informe.items if b.ofertas == 3]
    assert {b.slug for b in empatados} == {"javascript", "kubernetes", "react"}
    assert [b.afinidad for b in empatados] == sorted(
        (b.afinidad for b in empatados), reverse=True
    )


def test_brechas_excluye_lo_que_ya_tengo(jobs_fixture):
    informe = brechas(jobs_fixture, {"python", "docker"}, umbral_minimo=1)
    assert {"python", "docker"}.isdisjoint({b.slug for b in informe.items})


def test_brechas_afinidad(jobs_fixture):
    """django solo está en j01 y j02. Con stack {python}: en j01 cubro 1 de 4
    tags (25%) y en j02 1 de 3 (33,3%) -> afinidad media 29,2%."""
    informe = brechas(jobs_fixture, {"python"}, umbral_minimo=1)
    django = next(b for b in informe.items if b.slug == "django")
    assert django.afinidad == pytest.approx((25 + 100 / 3) / 2)


def test_brechas_marca_lo_que_solo_viene_del_texto(jobs_fixture):
    informe = brechas(jobs_fixture, set(), umbral_minimo=1)
    por_slug = {b.slug: b for b in informe.items}
    assert por_slug["llm"].solo_texto
    assert not por_slug["docker"].solo_texto   # en j05-j07 sí es tag oficial


def test_brechas_avisa_de_lo_que_tengo_y_nadie_pide(jobs_fixture):
    """Suele ser un nombre mal escrito en mi_stack.yml."""
    informe = brechas(jobs_fixture, {"python", "cobol", "fortran"}, umbral_minimo=1)
    assert informe.sin_demanda == ("cobol", "fortran")


def test_brechas_umbral_minimo(jobs_fixture):
    informe = brechas(jobs_fixture, set(), umbral_minimo=3)
    assert all(b.ofertas >= 3 for b in informe.items)
    assert "typescript" not in {b.slug for b in informe.items}   # solo 1 oferta


def test_brechas_cobertura_media(jobs_fixture):
    """Con el stack vacío no cubro nada; con todos los tags, todo."""
    assert brechas(jobs_fixture, set()).cobertura_media == pytest.approx(0.0)
    todos = {t.slug for j in jobs_fixture for t in j.tags}
    completo = brechas(jobs_fixture, todos)
    # j13 no tiene tags: cuenta como 0% de cobertura, no como 100%.
    assert completo.cobertura_media == pytest.approx(100 * 14 / 15)
    assert completo.items == ()


def test_cobertura_de_una_oferta_sin_tags():
    assert cobertura(job("vacia"), frozenset({"python"})) == 0.0


def test_brechas_sin_ofertas():
    informe = brechas([], {"python"})
    assert informe.total_ofertas == 0 and informe.cobertura_media == 0.0


# ------------------------------------------------------------------- resumen


def test_resumen(jobs_fixture):
    r = resumen(jobs_fixture)
    assert r.ofertas == 15
    assert r.empresas == 5           # j15 no tiene empresa
    assert r.con_salario == 9
    assert r.remotas == 8
    assert r.categorias[0] == ("Programación", 8)
    assert dict(r.paises)["Chile"] == 7


def test_resumen_respeta_filtros(jobs_fixture):
    assert resumen(jobs_fixture, filtros=Filtros(categoria="diseno-ux")).ofertas == 1
