"""Limpieza de HTML, slugificación, alias y extracción por keywords."""

from __future__ import annotations

import pytest

from gob.normalize import (
    canonical,
    extract_from_text,
    load_aliases,
    load_keywords,
    load_my_stack,
    slugify,
    strip_accents,
    strip_html,
)

# --------------------------------------------------------------------- slugs


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("Python", "python"),
        ("Python 3", "python-3"),
        ("PYTHON", "python"),
        ("  Python  ", "python"),
        ("Diseño / UX", "diseno-ux"),
        ("Programación", "programacion"),
        ("Node.js", "node-js"),
        (".NET Core", "net-core"),
        ("C++", "c"),
        ("Ruby on Rails", "ruby-on-rails"),
        ("CI/CD", "ci-cd"),
        ("Åland", "aland"),
        ("---", ""),
        ("", ""),
    ],
)
def test_slugify(entrada, esperado):
    assert slugify(entrada) == esperado


def test_strip_accents_no_toca_lo_que_no_lleva_tilde():
    assert strip_accents("Kubernetes") == "Kubernetes"
    assert strip_accents("Concepción") == "Concepcion"


# ---------------------------------------------------------------------- HTML


def test_strip_html_separa_los_bloques():
    # Sin el espacio al cerrar bloque saldría "añosPython" y la keyword no casaría.
    html = "<ul><li><strong>5 años</strong></li><li>Python</li></ul>"
    assert strip_html(html) == "5 años Python"


def test_strip_html_resuelve_entidades_y_colapsa_espacios():
    # El &nbsp; se colapsa junto al resto: \s+ en Python también casa \xa0, y así
    # el texto que se mina por keywords no arrastra espacios raros.
    assert strip_html("<p>C&amp;C   &nbsp; ok</p>") == "C&C ok"


def test_strip_html_ignora_script_y_style():
    assert strip_html("<p>ok</p><script>var docker = 1;</script>") == "ok"


@pytest.mark.parametrize("entrada", [None, "", "   "])
def test_strip_html_vacios(entrada):
    assert strip_html(entrada) == ""


def test_strip_html_texto_sin_etiquetas_pasa_igual():
    assert strip_html("Buscamos Python") == "Buscamos Python"


# --------------------------------------------------------------------- alias


@pytest.fixture(scope="module")
def aliases():
    return load_aliases("data/tag_aliases.yml")


def test_alias_reales_del_proyecto(aliases):
    assert canonical("angularjs", aliases) == "angular"
    assert canonical("net", aliases) == "dotnet"
    assert canonical("html5", aliases) == "html"
    assert canonical("node-js", aliases) == "nodejs"
    assert canonical("cloud-gcp", aliases) == "gcp"


def test_alias_deja_pasar_lo_desconocido(aliases):
    assert canonical("kubernetes", aliases) == "kubernetes"
    assert canonical("tecnologia-inventada", aliases) == "tecnologia-inventada"


def test_alias_normaliza_las_claves_del_yaml(tmp_path):
    ruta = tmp_path / "alias.yml"
    ruta.write_text("Node.JS:\n  - NODE-JS\n  - Node JS\n", encoding="utf-8")
    aliases = load_aliases(ruta)
    assert canonical("node-js", aliases) == "node-js"  # clave "Node.JS" -> "node-js"
    assert canonical("node-js", aliases) == canonical("node-js", aliases)


def test_alias_no_encadena(tmp_path):
    """`a -> b` y `b -> c` NO debe resolver a -> c: un error en el YAML sería un
    bucle silencioso, y preferimos que se vea."""
    ruta = tmp_path / "alias.yml"
    ruta.write_text("b:\n  - a\nc:\n  - b\n", encoding="utf-8")
    aliases = load_aliases(ruta)
    assert canonical("a", aliases) == "b"
    assert canonical("b", aliases) == "c"


def test_alias_archivo_inexistente_no_revienta(tmp_path):
    assert load_aliases(tmp_path / "no-existe.yml") == {}


# ------------------------------------------------------------------ keywords


@pytest.fixture(scope="module")
def keywords():
    return load_keywords("data/tech_keywords.yml")


def extraer(texto, keywords):
    return extract_from_text(texto, keywords)


def test_keywords_encuentra_lo_evidente(keywords):
    texto = "Buscamos experiencia en Docker, Kubernetes y Terraform."
    assert {"docker", "kubernetes", "terraform"} <= extraer(texto, keywords)


def test_keywords_cubre_lo_industrial_que_los_tags_oficiales_no_traen(keywords):
    texto = "Integración de PLC con Modbus TCP hacia el SCADA de planta vía OPC UA."
    assert {"plc", "modbus", "scada", "opc-ua"} <= extraer(texto, keywords)


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Node.js en el backend", "nodejs"),
        ("Stack .NET", "dotnet"),
        ("Programación en C++", "cpp"),
        ("Servicios en C#", "csharp"),
        ("scikit-learn y PyTorch", "scikit-learn"),
        ("Pipelines con dbt", "dbt"),
    ],
)
def test_keywords_con_puntuacion(texto, esperado, keywords):
    """`\\b` no serviría: nunca casaría "C++" y "Node.js" casaría en "Node.jsx"."""
    assert esperado in extraer(texto, keywords)


@pytest.mark.parametrize(
    "texto, no_esperado",
    [
        # El bug real que esto vigila: sin agrupar la alternancia en (?:…), los
        # lookarounds solo aplicaban al primer y último término y "SIEM" casaba
        # dentro de "siempre".
        ("manteniendo siempre el criterio técnico", "siem"),
        ("trabajamos con Nodejsx", "nodejs"),
        ("usamos Dockerfiles", "kubernetes"),
        ("la sociedad anónima", "siem"),
        ("Google Analytics y Google Ads", "gcp"),
        ("plataforma Sparkling", "spark"),
    ],
)
def test_keywords_no_casan_dentro_de_otra_palabra(texto, no_esperado, keywords):
    assert no_esperado not in extraer(texto, keywords)


def test_keywords_ignoran_mayusculas(keywords):
    assert extraer("KUBERNETES", keywords) == extraer("kubernetes", keywords)


def test_keywords_texto_vacio(keywords):
    assert extraer("", keywords) == set()
    assert extraer(None, keywords) == set()


def test_keywords_termino_largo_gana_al_corto(tmp_path):
    """Con "Spring" y "Spring Boot" en la misma lista, el patrón tiene que
    intentar primero el largo o "Spring Boot" nunca se distinguiría."""
    ruta = tmp_path / "kw.yml"
    ruta.write_text("spring:\n  name: Spring\n  match: [Spring, Spring Boot]\n", encoding="utf-8")
    kw = load_keywords(ruta)[0]
    assert kw.pattern.search("usamos Spring Boot").group(0) == "Spring Boot"


def test_keywords_por_defecto_busca_el_nombre(tmp_path):
    ruta = tmp_path / "kw.yml"
    ruta.write_text("grafana:\n  name: Grafana\n", encoding="utf-8")
    assert extract_from_text("dashboards en Grafana", load_keywords(ruta)) == {"grafana"}


# ------------------------------------------------------------------ mi stack


def test_mi_stack_acepta_dict_de_areas(tmp_path, aliases):
    ruta = tmp_path / "stack.yml"
    ruta.write_text("lenguajes:\n  - Python\nherramientas:\n  - Node.js\n", encoding="utf-8")
    assert load_my_stack(ruta, aliases) == {"python", "nodejs"}


def test_mi_stack_acepta_lista_plana(tmp_path, aliases):
    ruta = tmp_path / "stack.yml"
    ruta.write_text("- Python\n- AngularJS\n", encoding="utf-8")
    assert load_my_stack(ruta, aliases) == {"python", "angular"}


def test_mi_stack_vacio(tmp_path):
    assert load_my_stack(tmp_path / "no-existe.yml") == set()
