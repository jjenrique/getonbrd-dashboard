"""Carga de data/perfiles/*.yml: lo que se edita a mano tiene que fallar con un
mensaje que diga qué arreglar, y un perfil roto no puede tumbar a los demás."""

from __future__ import annotations

import textwrap

import pytest

from gob import config
from gob.normalize import load_aliases, load_my_stack
from gob.perfiles import load_perfiles

ALIASES = {"redes": "networking", "virtualizacion": "virtualization"}

VALIDO = """
nombre: Redes
descripcion: Prueba.
categorias: [cybersecurity]
tambien_si_pide: [Firewall]
fuerte:
  - Redes
  - Virtualización
condiciones:
  remoto:
    acepto: [fully_remote, hybrid]
    obligatorio: true
  sueldo_minimo:
    usd: 1800
  paises:
    acepto: [Chile, Perú]
"""


def escribir(directorio, nombre, contenido):
    (directorio / nombre).write_text(textwrap.dedent(contenido), encoding="utf-8")


def test_carga_un_perfil_valido(tmp_path):
    escribir(tmp_path, "redes.yml", VALIDO)
    (p,), errores = load_perfiles(tmp_path, ALIASES, {"python"})
    assert errores == []
    assert p.slug == "redes" and p.nombre == "Redes"
    assert p.fuerte == {"networking", "virtualization"}          # slugificado y con alias
    assert p.stack == {"python", "networking", "virtualization"}  # mi_stack ∪ fuerte
    assert p.tambien_si_pide == {"firewall"}

    remoto, sueldo, paises = p.condiciones
    assert remoto.obligatorio and remoto.acepto == {"fully_remote", "hybrid"}
    assert sueldo.minimo == 1800 and not sueldo.obligatorio      # obligatorio: false por defecto
    assert paises.acepto == {"chile", "peru"}


def test_ignora_los_borradores_con_guion_bajo(tmp_path):
    escribir(tmp_path, "_borrador.yml", "esto: no es un perfil")
    escribir(tmp_path, "redes.yml", VALIDO)
    perfiles, errores = load_perfiles(tmp_path, ALIASES)
    assert [p.slug for p in perfiles] == ["redes"] and errores == []


def test_sin_directorio_no_hay_perfiles_ni_errores(tmp_path):
    assert load_perfiles(tmp_path / "no-existe") == ([], [])


@pytest.mark.parametrize(
    "contenido, pista",
    [
        ("nombre: X\ncategorias: [a]\ncondicion: {}\n", "campo(s) desconocido(s): condicion"),
        ("categorias: [a]\n", "falta `nombre`"),
        ("nombre: X\n", "no busca en ningún lado"),
        ("nombre: X\ncategorias: programacion\n", "`categorias` tiene que ser una lista"),
        (
            "nombre: X\ncategorias: [a]\ncondiciones:\n  remoto:\n    acepto: [remoto]\n",
            "Válidos: fully_remote, hybrid, no_remote, remote_local, temporarily_remote",
        ),
        (
            "nombre: X\ncategorias: [a]\ncondiciones:\n  jornada:\n    acepto: [completa]\n",
            "no reconoce completa",
        ),
        ("nombre: X\ncategorias: [a]\ncondiciones:\n  sueldo_minimo:\n    usd: mucho\n", "entero positivo"),
        ("nombre: X\ncategorias: [a]\ncondiciones:\n  salario: {}\n", "condición(es) desconocida(s): salario"),
        (
            "nombre: X\ncategorias: [a]\ncondiciones:\n  remoto:\n    acepto: [hybrid]\n    obligatorio: si\n",
            "true o false",
        ),
        ("nombre: X\ncategorias: [a\n", "redes.yml"),     # YAML que ni siquiera parsea
    ],
)
def test_los_errores_dicen_que_arreglar(tmp_path, contenido, pista):
    escribir(tmp_path, "redes.yml", contenido)
    perfiles, (error,) = load_perfiles(tmp_path, ALIASES)
    assert perfiles == []
    assert error.startswith("redes.yml: ")
    assert pista in error


def test_un_perfil_roto_no_tumba_a_los_demas(tmp_path):
    escribir(tmp_path, "bueno.yml", VALIDO)
    escribir(tmp_path, "roto.yml", "nombre: X\n")
    perfiles, errores = load_perfiles(tmp_path, ALIASES)
    assert [p.slug for p in perfiles] == ["bueno"]
    assert len(errores) == 1 and errores[0].startswith("roto.yml")


def test_los_perfiles_del_repo_cargan_sin_errores():
    """Los cuatro de ejemplo son los que verá quien clone el repo: tienen que valer."""
    aliases = load_aliases(config.TAG_ALIASES_PATH)
    stack = load_my_stack(config.MI_STACK_PATH, aliases)
    perfiles, errores = load_perfiles(config.PERFILES_DIR, aliases, stack)
    assert errores == []
    assert {p.slug for p in perfiles} == {"backend", "datos", "redes-ciberseguridad", "ti-infra"}
    for p in perfiles:
        assert p.stack >= stack and p.fuerte <= p.stack
        assert {c.clave for c in p.condiciones} == {"remoto", "sueldo_minimo", "paises", "jornada"}
