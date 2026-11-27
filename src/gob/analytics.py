"""Agregaciones sobre las ofertas ya ingeridas.

Todas las funciones son **puras**: reciben listas de `Job` / `Snapshot` y
devuelven datos. No abren conexiones ni llaman a la red — de eso se encarga
`queries.py`. Así los tests corren con fixtures fijas y sin BD.

Con ~1.000 ofertas cabe todo en memoria de sobra, así que se prefiere filtrar y
agregar en Python (legible y testeable) antes que repartir la lógica en SQL.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

#: Por debajo de esta muestra no publicamos medianas de sueldo: con 3 datos
#: cualquier cifra es ruido, y darla sin avisar sería engañoso.
MIN_MUESTRA_SALARIO = 5


@dataclass(frozen=True)
class TagRef:
    """Un tag asociado a una oferta, ya resuelto a su slug canónico."""

    slug: str           # canónico, tras aplicar data/tag_aliases.yml
    name: str
    source: str         # 'api' (tag oficial) | 'text' (extraído de la descripción)


@dataclass(frozen=True)
class Job:
    id: str
    title: str
    url: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    category_slug: str | None = None
    category_name: str | None = None
    seniority: str | None = None
    modality: str | None = None
    remote: bool = False
    remote_modality: str | None = None
    countries: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    min_salary: int | None = None
    max_salary: int | None = None
    currency: str | None = None
    published_at: int | None = None      # epoch en segundos
    tags: tuple[TagRef, ...] = ()

    @property
    def slugs(self) -> frozenset[str]:
        return frozenset(t.slug for t in self.tags)

    @property
    def salario_medio(self) -> float | None:
        """Punto medio del rango. Si solo hay un extremo, ese."""
        bounds = [b for b in (self.min_salary, self.max_salary) if b]
        return sum(bounds) / len(bounds) if bounds else None


@dataclass(frozen=True)
class Snapshot:
    id: int
    run_at: str
    job_ids: frozenset[str]


@dataclass(frozen=True)
class Filtros:
    """Filtros del dashboard. `None` significa "no filtrar por esto"."""

    categoria: str | None = None
    seniority: str | None = None
    remoto: bool | None = None
    pais: str | None = None
    desde: int | None = None      # epoch en segundos, inclusive
    hasta: int | None = None      # epoch en segundos, inclusive
    empresa: str | None = None

    @property
    def activos(self) -> bool:
        return any(
            v is not None
            for v in (self.categoria, self.seniority, self.remoto, self.pais,
                      self.desde, self.hasta, self.empresa)
        )


def activas(jobs: Sequence[Job], snapshots: Sequence[Snapshot]) -> list[Job]:
    """Las ofertas vistas en la última corrida de la ingesta.

    La BD conserva todas las ofertas que alguna vez vio (para poder calcular
    tendencias), así que tras varias corridas mezcla activas con vencidas. Para
    decidir a qué postular solo sirven las que siguen publicadas. Sin snapshots
    no hay forma de saberlo y se devuelve todo.
    """
    if not snapshots:
        return list(jobs)
    ultima = max(snapshots, key=lambda s: (s.run_at, s.id))
    return [j for j in jobs if j.id in ultima.job_ids]


def filtrar(jobs: Sequence[Job], filtros: Filtros | None = None) -> list[Job]:
    """Aplica los filtros del dashboard. Sin filtros, devuelve todo."""
    if filtros is None or not filtros.activos:
        return list(jobs)
    return [j for j in jobs if _pasa(j, filtros)]


def _pasa(job: Job, f: Filtros) -> bool:
    if f.categoria and job.category_slug != f.categoria:
        return False
    if f.seniority and job.seniority != f.seniority:
        return False
    if f.remoto is not None and job.remote != f.remoto:
        return False
    if f.pais and f.pais not in job.countries:
        return False
    if f.empresa and job.company_id != f.empresa:
        return False
    if f.desde is not None and (job.published_at is None or job.published_at < f.desde):
        return False
    if f.hasta is not None and (job.published_at is None or job.published_at > f.hasta):
        return False
    return True


# ------------------------------------------------------------------ top tags


@dataclass(frozen=True)
class TagCount:
    slug: str
    name: str
    ofertas: int
    api: int         # ofertas donde el tag vino como tag oficial
    texto: int       # ofertas donde salió de la descripción

    @property
    def solo_texto(self) -> bool:
        """El tag no existe como tag oficial en ninguna oferta del conjunto."""
        return self.api == 0


def top_tags(
    jobs: Sequence[Job],
    limit: int = 25,
    *,
    filtros: Filtros | None = None,
    source: str | None = None,
) -> list[TagCount]:
    """Tags más demandados, contando **ofertas distintas** (no apariciones).

    `source='api'` o `'text'` restringe a una procedencia; por defecto cuenta ambas.
    """
    jobs = filtrar(jobs, filtros)
    ofertas: Counter[str] = Counter()
    por_fuente: dict[str, Counter[str]] = {"api": Counter(), "text": Counter()}
    nombres: dict[str, str] = {}

    for job in jobs:
        for tag in _tags_unicos(job, source):
            ofertas[tag.slug] += 1
            por_fuente[tag.source][tag.slug] += 1
            nombres.setdefault(tag.slug, tag.name)

    ordenados = sorted(ofertas.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        TagCount(slug, nombres[slug], n, por_fuente["api"][slug], por_fuente["text"][slug])
        for slug, n in ordenados[:limit]
    ]


def _tags_unicos(job: Job, source: str | None = None) -> list[TagRef]:
    """Un `TagRef` por slug canónico. Si el mismo canónico llega por las dos vías,
    manda 'api': el dato oficial pesa más que nuestra heurística de texto."""
    mejor: dict[str, TagRef] = {}
    for tag in job.tags:
        if source and tag.source != source:
            continue
        actual = mejor.get(tag.slug)
        if actual is None or (actual.source == "text" and tag.source == "api"):
            mejor[tag.slug] = tag
    return list(mejor.values())


# ------------------------------------------------------------ por seniority


#: Orden natural, no alfabético. Los que la API no clasifica van al final.
ORDEN_SENIORITY = ("no_experience", "junior", "semi_senior", "senior", "expert")


@dataclass(frozen=True)
class MatrizSeniority:
    seniorities: tuple[str, ...]
    filas: tuple[tuple[str, str, tuple[int, ...]], ...]  # (slug, nombre, conteos)

    def total_por_tag(self) -> dict[str, int]:
        return {slug: sum(conteos) for slug, _, conteos in self.filas}


def tags_por_seniority(
    jobs: Sequence[Job],
    tags: Sequence[str] | None = None,
    *,
    limit: int = 15,
    filtros: Filtros | None = None,
) -> MatrizSeniority:
    """Matriz tag × seniority. Sin `tags`, usa los `limit` más demandados."""
    jobs = filtrar(jobs, filtros)
    if tags is None:
        tags = [t.slug for t in top_tags(jobs, limit)]

    presentes = {j.seniority for j in jobs if j.seniority}
    columnas = tuple(s for s in ORDEN_SENIORITY if s in presentes)
    if any(j.seniority is None for j in jobs):
        columnas += ("sin_dato",)

    conteos: dict[str, Counter[str]] = defaultdict(Counter)
    nombres: dict[str, str] = {}
    objetivo = set(tags)
    for job in jobs:
        columna = job.seniority if job.seniority in columnas else "sin_dato"
        for tag in _tags_unicos(job):
            if tag.slug in objetivo:
                conteos[tag.slug][columna] += 1
                nombres.setdefault(tag.slug, tag.name)

    filas = tuple(
        (slug, nombres.get(slug, slug), tuple(conteos[slug][c] for c in columnas))
        for slug in tags
    )
    return MatrizSeniority(seniorities=columnas, filas=filas)


# ------------------------------------------------------------- co-ocurrencia


@dataclass(frozen=True)
class CoOcurrencia:
    slug: str
    name: str
    juntas: int          # ofertas que piden ambos
    ofertas: int         # ofertas que piden el otro tag, en total
    lift: float          # >1 = aparecen juntos más de lo que el azar explicaría
    pct: float           # % de las ofertas del tag base que además piden este


def co_ocurrencia(
    jobs: Sequence[Job],
    tag: str,
    limit: int = 15,
    *,
    filtros: Filtros | None = None,
) -> list[CoOcurrencia]:
    """Con qué otras tecnologías aparece `tag`.

    Además del conteo bruto se da el **lift**: cuántas veces más aparecen juntos
    respecto a lo esperable si fueran independientes. El conteo bruto siempre
    corona lo más común (git, sql); el lift saca las parejas de verdad.
    """
    jobs = filtrar(jobs, filtros)
    total = len(jobs)
    con_tag = [j for j in jobs if tag in j.slugs]
    if not con_tag or total == 0:
        return []

    globales: Counter[str] = Counter()
    nombres: dict[str, str] = {}
    for job in jobs:
        for t in _tags_unicos(job):
            globales[t.slug] += 1
            nombres.setdefault(t.slug, t.name)

    juntas: Counter[str] = Counter()
    for job in con_tag:
        for t in _tags_unicos(job):
            if t.slug != tag:
                juntas[t.slug] += 1

    p_base = len(con_tag) / total
    resultados = [
        CoOcurrencia(
            slug=slug,
            name=nombres.get(slug, slug),
            juntas=n,
            ofertas=globales[slug],
            lift=(n / total) / (p_base * globales[slug] / total) if globales[slug] else 0.0,
            pct=100.0 * n / len(con_tag),
        )
        for slug, n in juntas.items()
    ]
    resultados.sort(key=lambda c: (-c.juntas, c.slug))
    return resultados[:limit]


# ----------------------------------------------------------------- tendencia


@dataclass(frozen=True)
class PuntoTendencia:
    snapshot_id: int
    run_at: str
    ofertas: int


@dataclass(frozen=True)
class Tendencia:
    slug: str
    puntos: tuple[PuntoTendencia, ...]
    #: Con menos de dos snapshots no hay evolución que enseñar. La UI tiene que
    #: decirlo en vez de pintar una línea plana que parece un dato.
    historico_insuficiente: bool

    @property
    def variacion(self) -> int | None:
        if len(self.puntos) < 2:
            return None
        return self.puntos[-1].ofertas - self.puntos[0].ofertas


def tendencia(
    jobs: Sequence[Job],
    snapshots: Sequence[Snapshot],
    tags: Iterable[str],
) -> list[Tendencia]:
    """Evolución del nº de ofertas por tag, snapshot a snapshot.

    Ojo con la interpretación: la API solo devuelve ofertas activas, así que el
    histórico empieza en la primera corrida de la ingesta. No hay retroactividad.
    """
    por_id = {j.id: j for j in jobs}
    ordenados = sorted(snapshots, key=lambda s: (s.run_at, s.id))
    insuficiente = len(ordenados) < 2

    resultado = []
    for tag in tags:
        puntos = tuple(
            PuntoTendencia(
                snapshot_id=snap.id,
                run_at=snap.run_at,
                ofertas=sum(
                    1 for jid in snap.job_ids
                    if (job := por_id.get(jid)) is not None and tag in job.slugs
                ),
            )
            for snap in ordenados
        )
        resultado.append(Tendencia(slug=tag, puntos=puntos, historico_insuficiente=insuficiente))
    return resultado


# ------------------------------------------------------------------ salarios


@dataclass(frozen=True)
class Salario:
    slug: str | None            # None = el conjunto entero, sin filtrar por tag
    name: str | None
    n_total: int                # ofertas del conjunto
    n_con_salario: int          # las que publican algún extremo del rango
    currency: str | None
    mediana: float | None = None      # sobre el punto medio del rango
    p25: float | None = None
    p75: float | None = None
    minimo: int | None = None
    maximo: int | None = None

    @property
    def suficiente(self) -> bool:
        return self.n_con_salario >= MIN_MUESTRA_SALARIO

    @property
    def cobertura(self) -> float:
        """% de ofertas que publican sueldo. Siempre visible en la UI."""
        return 100.0 * self.n_con_salario / self.n_total if self.n_total else 0.0


def salario_por_tag(
    jobs: Sequence[Job],
    tag: str | None = None,
    *,
    filtros: Filtros | None = None,
) -> Salario:
    """Estadísticos de sueldo del conjunto, o de las ofertas que piden `tag`.

    Devuelve `mediana`/`p25`/`p75` en `None` si la muestra no llega a
    `MIN_MUESTRA_SALARIO`, pero `n_con_salario` y `n_total` van **siempre**, para
    que la UI pueda decir sobre cuántos datos habla.

    Los importes no llevan moneda en la API: `currency` es el supuesto USD que
    fija la ingesta (docs/API.md §3.6).
    """
    jobs = filtrar(jobs, filtros)
    if tag is not None:
        jobs = [j for j in jobs if tag in j.slugs]

    name = next((t.name for j in jobs for t in j.tags if t.slug == tag), tag)
    con_salario = [j for j in jobs if j.salario_medio is not None]
    valores = sorted(j.salario_medio for j in con_salario)  # type: ignore[misc]
    currency = next((j.currency for j in con_salario if j.currency), None)

    base = Salario(
        slug=tag,
        name=name,
        n_total=len(jobs),
        n_con_salario=len(con_salario),
        currency=currency,
    )
    if not base.suficiente:
        return base
    return replace(
        base,
        mediana=_percentil(valores, 50),
        p25=_percentil(valores, 25),
        p75=_percentil(valores, 75),
        minimo=min((j.min_salary for j in con_salario if j.min_salary), default=None),
        maximo=max((j.max_salary for j in con_salario if j.max_salary), default=None),
    )


def salarios_por_tags(
    jobs: Sequence[Job],
    tags: Sequence[str] | None = None,
    *,
    limit: int = 20,
    filtros: Filtros | None = None,
    solo_suficientes: bool = True,
) -> list[Salario]:
    """`salario_por_tag` para varios tags, ordenado por mediana descendente.

    Con `solo_suficientes` se descartan los tags con muestra insuficiente en vez
    de listarlos con la mediana vacía.
    """
    jobs = filtrar(jobs, filtros)
    if tags is None:
        tags = [t.slug for t in top_tags(jobs, limit * 3)]
    resultados = [salario_por_tag(jobs, t) for t in tags]
    if solo_suficientes:
        resultados = [s for s in resultados if s.suficiente]
    resultados.sort(key=lambda s: (-(s.mediana or 0), s.slug or ""))
    return resultados[:limit]


def _percentil(ordenados: Sequence[float], p: float) -> float | None:
    """Percentil por interpolación lineal. `ordenados` tiene que venir ordenado."""
    if not ordenados:
        return None
    if len(ordenados) == 1:
        return float(ordenados[0])
    pos = (len(ordenados) - 1) * p / 100.0
    bajo = int(pos)
    alto = min(bajo + 1, len(ordenados) - 1)
    peso = pos - bajo
    return ordenados[bajo] * (1 - peso) + ordenados[alto] * peso


# ------------------------------------------------------------------ empresas


@dataclass(frozen=True)
class Empresa:
    id: str
    name: str
    ofertas: int
    stack: tuple[tuple[str, int], ...]   # (slug, nº de ofertas de la empresa)
    con_salario: int

    @property
    def stack_texto(self) -> str:
        return ", ".join(slug for slug, _ in self.stack)


def empresas_top(
    jobs: Sequence[Job],
    limit: int = 20,
    *,
    stack_size: int = 5,
    filtros: Filtros | None = None,
) -> list[Empresa]:
    """Empresas que más publican y el stack que domina en sus ofertas."""
    jobs = filtrar(jobs, filtros)
    por_empresa: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        if job.company_id:
            por_empresa[job.company_id].append(job)

    empresas = []
    for company_id, ofertas in por_empresa.items():
        conteo: Counter[str] = Counter()
        for job in ofertas:
            conteo.update(t.slug for t in _tags_unicos(job))
        empresas.append(
            Empresa(
                id=company_id,
                name=ofertas[0].company_name or company_id,
                ofertas=len(ofertas),
                stack=tuple(sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))[:stack_size]),
                con_salario=sum(1 for j in ofertas if j.salario_medio is not None),
            )
        )
    empresas.sort(key=lambda e: (-e.ofertas, e.name.lower()))
    return empresas[:limit]


# ------------------------------------------------------------------- brechas


@dataclass(frozen=True)
class Brecha:
    slug: str
    name: str
    ofertas: int          # ofertas que lo piden y yo no tengo
    afinidad: float       # % medio del RESTO de esas ofertas que ya cubro
    solo_texto: bool      # el tag no existe como tag oficial: señal más débil


@dataclass(frozen=True)
class InformeBrechas:
    total_ofertas: int
    cobertura_media: float               # % medio de tags que ya cubro por oferta
    stack: tuple[str, ...]
    items: tuple[Brecha, ...] = ()
    #: Tags de mi stack que no aparecen en ninguna oferta del conjunto. Puede ser
    #: un nombre mal escrito en mi_stack.yml, o algo que ya nadie pide.
    sin_demanda: tuple[str, ...] = ()


def cobertura(job: Job, stack: frozenset[str]) -> float:
    """Fracción de los tags de una oferta que ya cubro. 0..1."""
    if not job.slugs:
        return 0.0
    return len(job.slugs & stack) / len(job.slugs)


def brechas(
    jobs: Sequence[Job],
    mi_stack: Iterable[str],
    *,
    limit: int = 25,
    filtros: Filtros | None = None,
    umbral_minimo: int = 3,
) -> InformeBrechas:
    """Qué tecnologías demandadas no tengo, ordenadas por volumen de ofertas.

    La idea original —ordenar por "ofertas que se desbloquean del todo"— no
    sobrevive al contacto con los datos: una oferta pide 10,4 tags de media,
    entre ellos cosas como `documentation` o `team-leadership`, así que es una
    lista de deseos y no un checklist. Con un stack de 10 tecnologías, ninguna
    oferta pasa del 60% de cobertura y "solo me falta esto" da 1 caso. Ordenar
    por ahí sería ordenar por ruido.

    Así que se ordena por **volumen** y se acompaña de la **afinidad**: qué
    porcentaje del resto de esas ofertas ya cubro. Es lo que separa "lo piden
    muchos y estás cerca" de "lo piden muchos pero es otro mundo" — con el mismo
    volumen, `jira` (9%) y `typescript` (19%) no significan lo mismo para mí.
    """
    jobs = filtrar(jobs, filtros)
    stack = frozenset(mi_stack)

    ofertas: Counter[str] = Counter()
    suma_cobertura: dict[str, float] = defaultdict(float)
    nombres: dict[str, str] = {}
    con_api: set[str] = set()
    demandados: set[str] = set()
    cobertura_total = 0.0

    for job in jobs:
        cov = cobertura(job, stack)
        cobertura_total += cov
        faltan = job.slugs - stack
        for tag in _tags_unicos(job):
            demandados.add(tag.slug)
            if tag.source == "api":
                con_api.add(tag.slug)
            if tag.slug not in faltan:
                continue
            nombres.setdefault(tag.slug, tag.name)
            ofertas[tag.slug] += 1
            # La afinidad mide el RESTO de la oferta: el tag que falta no cuenta
            # ni a favor ni en contra, o una oferta con pocos tags saldría
            # artificialmente afín.
            suma_cobertura[tag.slug] += cov

    items = [
        Brecha(
            slug=slug,
            name=nombres.get(slug, slug),
            ofertas=n,
            afinidad=100.0 * suma_cobertura[slug] / n,
            solo_texto=slug not in con_api,
        )
        for slug, n in ofertas.items()
        if n >= umbral_minimo
    ]
    items.sort(key=lambda b: (-b.ofertas, -b.afinidad, b.slug))

    return InformeBrechas(
        total_ofertas=len(jobs),
        cobertura_media=100.0 * cobertura_total / len(jobs) if jobs else 0.0,
        stack=tuple(sorted(stack)),
        items=tuple(items[:limit]),
        sin_demanda=tuple(sorted(stack - demandados)),
    )


# -------------------------------------------------------------------- varios


@dataclass(frozen=True)
class Resumen:
    ofertas: int
    empresas: int
    tags: int
    con_salario: int
    remotas: int
    categorias: tuple[tuple[str, int], ...] = field(default=())
    paises: tuple[tuple[str, int], ...] = field(default=())


def resumen(jobs: Sequence[Job], *, filtros: Filtros | None = None) -> Resumen:
    """Cifras de cabecera del dashboard."""
    jobs = filtrar(jobs, filtros)
    categorias = Counter(j.category_name or j.category_slug or "?" for j in jobs)
    paises = Counter(p for j in jobs for p in j.countries)
    return Resumen(
        ofertas=len(jobs),
        empresas=len({j.company_id for j in jobs if j.company_id}),
        tags=len({t.slug for j in jobs for t in j.tags}),
        con_salario=sum(1 for j in jobs if j.salario_medio is not None),
        remotas=sum(1 for j in jobs if j.remote),
        categorias=tuple(categorias.most_common()),
        paises=tuple(paises.most_common()),
    )
