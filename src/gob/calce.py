"""Calce entre las ofertas y los perfiles de búsqueda de data/perfiles/*.yml.

Funciones **puras**, como `analytics.py`: reciben `Job` y `Perfil` y devuelven
datos. Leer los YAML es cosa de `perfiles.py`.

Para cada perfil:

1. **Candidatas**: ofertas de sus `categorias`, más las de otras categorías que
   piden algo de `tambien_si_pide`.
2. **Condiciones obligatorias**: descartan. Si la oferta no publica el dato,
   no se descarta: el 62% no publica sueldo y tirarlas sería tirar casi todo.
3. **Calce técnico** (0..1): qué parte de lo que pide la oferta ya cubro. Cada
   tag pesa 1; si es `fuerte` del perfil, 2; si salió del texto y no es tag
   oficial, la mitad. Una oferta con poco que pedir cuenta como si pidiera
   `MIN_DENOMINADOR`: tres tags de tres no es evidencia de nada.
4. **Puntaje** (0..100): 80% calce técnico + 20% condiciones deseables
   (cumple 1, no cumple 0, no lo publica 0,5). Sin deseables, solo técnico.

Por qué así y no "porcentaje de tags que cubro" a secas: ver la sección de
perfiles del README, que cuenta qué salió al probarlo contra los datos reales.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from .analytics import Job, TagRef, _tags_unicos
from .normalize import slugify

PESO_FUERTE = 2.0
PESO_TEXTO = 0.5
MIN_DENOMINADOR = 5.0
PESO_TECNICO = 0.8

#: Calce técnico desde el que una oferta cuenta como "buen calce" en el resumen.
#: Con el stack de nov-2026, por encima de esto quedan entre el 1% y el 20% de
#: las candidatas según el perfil: separa de verdad, no es un umbral decorativo.
UMBRAL_BUEN_CALCE = 0.5

#: Cuántos de los mejores calces se miran para decir "lo que más te abriría".
TOP_TE_ABRIRIA = 20

REMOTO_VALIDOS = frozenset(
    {"fully_remote", "remote_local", "hybrid", "temporarily_remote", "no_remote"}
)
JORNADA_VALIDOS = frozenset({"full_time", "part_time", "freelance", "internship"})
CONDICIONES_VALIDAS = ("remoto", "sueldo_minimo", "paises", "jornada")


# ------------------------------------------------------------------- modelo


@dataclass(frozen=True)
class Condicion:
    clave: str                              # una de CONDICIONES_VALIDAS
    obligatorio: bool
    acepto: frozenset[str] = frozenset()    # remoto / jornada: claves de la API; paises: slugs
    minimo: int | None = None               # sueldo_minimo, en la moneda de la BD


@dataclass(frozen=True)
class Perfil:
    slug: str                               # nombre del archivo, sin .yml
    nombre: str
    descripcion: str = ""
    categorias: frozenset[str] = frozenset()
    tambien_si_pide: frozenset[str] = frozenset()
    fuerte: frozenset[str] = frozenset()
    #: Lo que cubro en este perfil: mi_stack.yml ∪ fuerte, ya canónico.
    stack: frozenset[str] = frozenset()
    condiciones: tuple[Condicion, ...] = ()

    @property
    def obligatorias(self) -> tuple[Condicion, ...]:
        return tuple(c for c in self.condiciones if c.obligatorio)

    @property
    def deseables(self) -> tuple[Condicion, ...]:
        return tuple(c for c in self.condiciones if not c.obligatorio)


@dataclass(frozen=True)
class Chequeo:
    """Resultado de una condición sobre una oferta."""

    clave: str
    obligatorio: bool
    cumple: bool | None      # None: la oferta no publica el dato
    detalle: str             # lo que dice la oferta, para la UI


@dataclass(frozen=True)
class Calce:
    job: Job
    puntaje: int                   # 0..100, es por lo que se ordena
    tecnico: float                 # 0..1
    condiciones: float | None      # 0..1; None si el perfil no tiene deseables
    tiene: tuple[str, ...]         # lo que pide y cubro: fuertes primero
    falta: tuple[str, ...]         # lo que pide y no cubro: oficiales primero
    chequeos: tuple[Chequeo, ...]
    por_categoria: bool            # False: entró por `tambien_si_pide`

    @property
    def buen_calce(self) -> bool:
        return self.tecnico >= UMBRAL_BUEN_CALCE


@dataclass(frozen=True)
class InformeCalce:
    perfil: Perfil
    candidatas: int
    descartadas: int               # por alguna condición obligatoria
    items: tuple[Calce, ...]       # las que quedan, de mejor a peor
    #: Cuántas tumbó cada condición obligatoria. Una oferta que falla dos cuenta
    #: en las dos, así que puede sumar más que `descartadas`.
    descartadas_por: tuple[tuple[str, int], ...] = ()

    @property
    def buen_calce(self) -> int:
        return sum(1 for c in self.items if c.buen_calce)


@dataclass(frozen=True)
class ResumenPerfil:
    """Una fila de la comparación entre perfiles: ¿dónde hay más oportunidad?"""

    perfil: Perfil
    candidatas: int
    descartadas: int
    descartadas_por: tuple[tuple[str, int], ...]
    evaluadas: int
    buen_calce: int
    mejor_puntaje: int | None
    mediana_salario_buen_calce: float | None
    n_salario_buen_calce: int
    #: Lo que más falta entre los TOP_TE_ABRIRIA mejores calces: (slug, ofertas).
    te_abriria: tuple[tuple[str, int], ...]


# ---------------------------------------------------------------- candidatas


def es_candidata(job: Job, perfil: Perfil) -> bool:
    return job.category_slug in perfil.categorias or bool(job.slugs & perfil.tambien_si_pide)


# --------------------------------------------------------------- condiciones


def evaluar_condiciones(job: Job, perfil: Perfil) -> tuple[Chequeo, ...]:
    return tuple(_chequear(job, c) for c in perfil.condiciones)


def _chequear(job: Job, c: Condicion) -> Chequeo:
    if c.clave == "remoto":
        valor = job.remote_modality
        return Chequeo(c.clave, c.obligatorio, None if valor is None else valor in c.acepto, valor or "")
    if c.clave == "jornada":
        valor = job.modality
        return Chequeo(c.clave, c.obligatorio, None if valor is None else valor in c.acepto, valor or "")
    if c.clave == "paises":
        if not job.countries:
            return Chequeo(c.clave, c.obligatorio, None, "")
        cumple = any(slugify(p) in c.acepto for p in job.countries)
        return Chequeo(c.clave, c.obligatorio, cumple, ", ".join(job.countries))
    if c.clave == "sueldo_minimo":
        # Se compara con el techo del rango: la pregunta es "¿puedo llegar a X
        # aquí?", y un rango 1.500–2.500 contesta que sí a 2.000.
        techo = job.max_salary or job.min_salary
        if not techo:
            return Chequeo(c.clave, c.obligatorio, None, "")
        rango = "–".join(str(b) for b in (job.min_salary, job.max_salary) if b)
        return Chequeo(c.clave, c.obligatorio, techo >= (c.minimo or 0), rango)
    raise ValueError(f"condición desconocida: {c.clave!r}")


def _descartada(chequeos: Iterable[Chequeo]) -> bool:
    return any(ch.obligatorio and ch.cumple is False for ch in chequeos)


def _nota_condiciones(chequeos: Sequence[Chequeo]) -> float | None:
    deseables = [ch for ch in chequeos if not ch.obligatorio]
    if not deseables:
        return None
    valor = {True: 1.0, False: 0.0, None: 0.5}
    return sum(valor[ch.cumple] for ch in deseables) / len(deseables)


# ------------------------------------------------------------------- técnico


def peso(tag: TagRef, perfil: Perfil) -> float:
    w = PESO_FUERTE if tag.slug in perfil.fuerte else 1.0
    return w if tag.source == "api" else w * PESO_TEXTO


def calce_tecnico(job: Job, perfil: Perfil) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    """(calce 0..1, lo que tengo, lo que me falta)."""
    tags = _tags_unicos(job)
    cubierto = total = 0.0
    tiene: list[TagRef] = []
    falta: list[TagRef] = []
    for tag in tags:
        w = peso(tag, perfil)
        total += w
        if tag.slug in perfil.stack:
            cubierto += w
            tiene.append(tag)
        else:
            falta.append(tag)
    tiene.sort(key=lambda t: (-peso(t, perfil), t.slug))
    falta.sort(key=lambda t: (t.source != "api", t.slug))
    calce = cubierto / max(total, MIN_DENOMINADOR)
    return calce, tuple(t.slug for t in tiene), tuple(t.slug for t in falta)


# --------------------------------------------------------------------- calce


def puntuar(job: Job, perfil: Perfil) -> Calce:
    tecnico, tiene, falta = calce_tecnico(job, perfil)
    chequeos = evaluar_condiciones(job, perfil)
    nota = _nota_condiciones(chequeos)
    final = tecnico if nota is None else PESO_TECNICO * tecnico + (1 - PESO_TECNICO) * nota
    return Calce(
        job=job,
        puntaje=round(100 * final),
        tecnico=tecnico,
        condiciones=nota,
        tiene=tiene,
        falta=falta,
        chequeos=chequeos,
        por_categoria=job.category_slug in perfil.categorias,
    )


def calzar(jobs: Sequence[Job], perfil: Perfil) -> InformeCalce:
    """Las ofertas del perfil, de mejor a peor calce.

    A igual puntaje manda la más reciente: entre dos ofertas igual de buenas,
    la nueva es la que todavía tiene sentido postular.
    """
    candidatas = [j for j in jobs if es_candidata(j, perfil)]
    calces = [puntuar(j, perfil) for j in candidatas]
    quedan = [c for c in calces if not _descartada(c.chequeos)]
    quedan.sort(key=lambda c: (-c.puntaje, -c.tecnico, -(c.job.published_at or 0), c.job.id))
    por: Counter[str] = Counter(
        ch.clave for c in calces for ch in c.chequeos if ch.obligatorio and ch.cumple is False
    )
    return InformeCalce(
        perfil=perfil,
        candidatas=len(candidatas),
        descartadas=len(calces) - len(quedan),
        items=tuple(quedan),
        descartadas_por=tuple(sorted(por.items(), key=lambda kv: (-kv[1], kv[0]))),
    )


def resumir(informe: InformeCalce) -> ResumenPerfil:
    buenas = [c for c in informe.items if c.buen_calce]
    sueldos = [c.job.salario_medio for c in buenas if c.job.salario_medio]
    faltan: Counter[str] = Counter()
    for c in informe.items[:TOP_TE_ABRIRIA]:
        faltan.update(c.falta)
    return ResumenPerfil(
        perfil=informe.perfil,
        candidatas=informe.candidatas,
        descartadas=informe.descartadas,
        descartadas_por=informe.descartadas_por,
        evaluadas=len(informe.items),
        buen_calce=len(buenas),
        mejor_puntaje=informe.items[0].puntaje if informe.items else None,
        mediana_salario_buen_calce=statistics.median(sueldos) if sueldos else None,
        n_salario_buen_calce=len(sueldos),
        te_abriria=tuple(sorted(faltan.items(), key=lambda kv: (-kv[1], kv[0]))[:5]),
    )


def comparar_perfiles(jobs: Sequence[Job], perfiles: Iterable[Perfil]) -> list[ResumenPerfil]:
    """Un resumen por perfil, de más a menos ofertas con buen calce."""
    resumenes = [resumir(calzar(jobs, p)) for p in perfiles]
    resumenes.sort(key=lambda r: (-r.buen_calce, -(r.mejor_puntaje or 0), r.perfil.slug))
    return resumenes
