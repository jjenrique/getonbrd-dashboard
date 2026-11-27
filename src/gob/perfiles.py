"""Lee los perfiles de búsqueda de data/perfiles/*.yml.

Un archivo por perfil; el nombre del archivo es su slug. Los que empiezan con
`_` se ignoran, para poder guardar borradores al lado sin que aparezcan.

Un perfil mal escrito **no tumba el dashboard**: se devuelve su error junto con
los perfiles que sí cargaron, y la UI lo muestra como aviso. Como todo esto se
edita a mano, el mensaje dice qué archivo y qué campo, con los valores válidos.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .calce import (
    CONDICIONES_VALIDAS,
    JORNADA_VALIDOS,
    REMOTO_VALIDOS,
    Condicion,
    Perfil,
)
from .normalize import canonical, slugify

CAMPOS_VALIDOS = (
    "nombre", "descripcion", "categorias", "tambien_si_pide", "fuerte", "condiciones",
)


def load_perfiles(
    directorio: str | Path,
    aliases: dict[str, str] | None = None,
    mi_stack: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[Perfil], list[str]]:
    """(perfiles ordenados por nombre de archivo, errores legibles)."""
    directorio = Path(directorio)
    if not directorio.is_dir():
        return [], []
    perfiles: list[Perfil] = []
    errores: list[str] = []
    for archivo in sorted(directorio.glob("*.yml")):
        if archivo.name.startswith("_"):
            continue
        try:
            perfiles.append(cargar_perfil(archivo, aliases or {}, frozenset(mi_stack)))
        except (ValueError, yaml.YAMLError) as exc:
            errores.append(f"{archivo.name}: {exc}")
    return perfiles, errores


def cargar_perfil(archivo: Path, aliases: dict[str, str], mi_stack: frozenset[str]) -> Perfil:
    with archivo.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError("el archivo tiene que ser un diccionario de campos")

    sobran = set(raw) - set(CAMPOS_VALIDOS)
    if sobran:
        raise ValueError(
            f"campo(s) desconocido(s): {', '.join(sorted(sobran))}. "
            f"Válidos: {', '.join(CAMPOS_VALIDOS)}"
        )
    nombre = str(raw.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("falta `nombre`")

    def tags(campo: str) -> frozenset[str]:
        return frozenset(canonical(slugify(str(v)), aliases) for v in _lista(raw, campo) if str(v).strip())

    categorias = frozenset(slugify(str(v)) for v in _lista(raw, "categorias"))
    tambien = tags("tambien_si_pide")
    if not categorias and not tambien:
        raise ValueError("sin `categorias` ni `tambien_si_pide` el perfil no busca en ningún lado")
    fuerte = tags("fuerte")

    return Perfil(
        slug=archivo.stem,
        nombre=nombre,
        descripcion=str(raw.get("descripcion") or "").strip(),
        categorias=categorias,
        tambien_si_pide=tambien,
        fuerte=fuerte,
        stack=frozenset(mi_stack) | fuerte,
        condiciones=_condiciones(raw.get("condiciones") or {}),
    )


def _lista(raw: dict, campo: str) -> list:
    valor = raw.get(campo)
    if valor is None:
        return []
    if not isinstance(valor, list):
        raise ValueError(f"`{campo}` tiene que ser una lista")
    return valor


def _condiciones(raw: dict) -> tuple[Condicion, ...]:
    if not isinstance(raw, dict):
        raise ValueError("`condiciones` tiene que ser un diccionario")
    sobran = set(raw) - set(CONDICIONES_VALIDAS)
    if sobran:
        raise ValueError(
            f"condición(es) desconocida(s): {', '.join(sorted(sobran))}. "
            f"Válidas: {', '.join(CONDICIONES_VALIDAS)}"
        )
    salida: list[Condicion] = []
    for clave in CONDICIONES_VALIDAS:          # orden fijo, el de la UI
        if clave not in raw or raw[clave] is None:
            continue
        cfg = raw[clave]
        if not isinstance(cfg, dict):
            raise ValueError(f"`condiciones.{clave}` tiene que ser un diccionario")
        obligatorio = cfg.get("obligatorio", False)
        if not isinstance(obligatorio, bool):
            raise ValueError(f"`condiciones.{clave}.obligatorio` tiene que ser true o false")

        if clave == "sueldo_minimo":
            usd = cfg.get("usd")
            if not isinstance(usd, int) or isinstance(usd, bool) or usd <= 0:
                raise ValueError("`condiciones.sueldo_minimo.usd` tiene que ser un entero positivo")
            salida.append(Condicion(clave, obligatorio, minimo=usd))
            continue

        acepto = cfg.get("acepto")
        if not isinstance(acepto, list) or not acepto:
            raise ValueError(f"`condiciones.{clave}.acepto` tiene que ser una lista no vacía")
        if clave == "paises":
            valores = frozenset(slugify(str(v)) for v in acepto)
        else:
            valores = frozenset(str(v).strip() for v in acepto)
            validos = REMOTO_VALIDOS if clave == "remoto" else JORNADA_VALIDOS
            malos = valores - validos
            if malos:
                raise ValueError(
                    f"`condiciones.{clave}.acepto` no reconoce {', '.join(sorted(malos))}. "
                    f"Válidos: {', '.join(sorted(validos))}"
                )
        salida.append(Condicion(clave, obligatorio, acepto=valores))
    return tuple(salida)
