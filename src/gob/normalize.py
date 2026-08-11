"""Limpieza de HTML, slugificación de tags, alias y extracción por keywords.

Funciones puras: no tocan la red ni la base de datos, así los tests pueden
cubrirlas con datos fijos.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import yaml

# Etiquetas cuyo contenido no es texto para el lector.
_SKIP_CONTENT = {"script", "style"}
# Etiquetas que separan ideas: al quitarlas hay que dejar un espacio, o
# "</li><li>Python" acabaría pegando dos palabras.
_BLOCK_TAGS = {
    "p", "br", "li", "ul", "ol", "div", "tr", "td", "th", "table",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "blockquote",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_CONTENT:
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT and self._skipping:
            self._skipping -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)


def strip_html(html: str | None) -> str:
    """Convierte el HTML de la API en texto plano. Sin dependencias externas."""
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def slugify(text: str) -> str:
    """"Python 3" -> "python-3";  "Diseño UX" -> "diseno-ux";  ".NET Core" -> "net-core"."""
    slug = strip_accents(text).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# --------------------------------------------------------------------- alias


def load_aliases(path: str | Path) -> dict[str, str]:
    """Lee data/tag_aliases.yml y devuelve `{alias: canónico}` ya slugificado.

    Formato del YAML — la clave es el slug canónico y la lista son sus alias:

        angular:
          - angularjs
          - angular-js
    """
    raw = _load_yaml(path)
    aliases: dict[str, str] = {}
    for canonical, variants in (raw or {}).items():
        canonical_slug = slugify(str(canonical))
        for variant in variants or []:
            aliases[slugify(str(variant))] = canonical_slug
    return aliases


def canonical(slug: str, aliases: dict[str, str]) -> str:
    """Aplica los alias una sola vez (sin cadenas: `a->b->c` no se resuelve a `c`,
    para que un error en el YAML no se convierta en un bucle silencioso)."""
    return aliases.get(slug, slug)


# ------------------------------------------------------------------ keywords


@dataclass(frozen=True)
class TechKeyword:
    """Una tecnología buscable en el texto libre de una oferta."""

    slug: str
    name: str
    pattern: re.Pattern[str]


def load_keywords(path: str | Path) -> list[TechKeyword]:
    """Lee data/tech_keywords.yml.

    Formato — la clave es el slug, `name` el nombre a mostrar y `match` las
    formas que hay que buscar en el texto:

        kubernetes:
          name: Kubernetes
          match: [Kubernetes, K8s, EKS, AKS, GKE]
    """
    raw = _load_yaml(path)
    keywords: list[TechKeyword] = []
    for slug, spec in (raw or {}).items():
        spec = spec or {}
        name = spec.get("name") or str(slug)
        terms = spec.get("match") or [name]
        keywords.append(
            TechKeyword(slug=slugify(str(slug)), name=str(name), pattern=_build_pattern(terms))
        )
    return keywords


def _build_pattern(terms: list[str]) -> re.Pattern[str]:
    """Alternancia con fronteras que respetan `.`, `+` y `#`.

    `\\b` no sirve: `\\bC++\\b` nunca casa porque `+` no es carácter de palabra, y
    `\\bNode.js\\b` casaría dentro de "Node.jsx". Usamos lookarounds explícitos.
    """
    # Los términos largos primero: así "Spring Boot" gana a "Spring".
    ordered = sorted({t.strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    body = "|".join(re.escape(t) for t in ordered)
    # El grupo (?:…) no es decorativo: sin él los lookarounds solo aplicarían al
    # primer y al último término de la alternancia, y "SIEM" casaría en "siempre".
    return re.compile(
        rf"(?<![A-Za-z0-9_+#.])(?:{body})(?![A-Za-z0-9_+#])",
        re.IGNORECASE,
    )


def extract_from_text(text: str, keywords: list[TechKeyword]) -> set[str]:
    """Devuelve los slugs de las tecnologías mencionadas en `text` (ya sin HTML)."""
    if not text:
        return set()
    return {kw.slug for kw in keywords if kw.pattern.search(text)}


# ------------------------------------------------------------------ mi stack


def load_my_stack(path: str | Path, aliases: dict[str, str] | None = None) -> set[str]:
    """Lee data/mi_stack.yml y devuelve los slugs canónicos que ya domino.

    Acepta tanto una lista plana como un dict de listas por área, para poder
    agrupar el archivo a mano sin romper nada.
    """
    raw = _load_yaml(path)
    items: list[str] = []
    if isinstance(raw, dict):
        for value in raw.values():
            items.extend(value or [])
    elif isinstance(raw, list):
        items.extend(raw)
    aliases = aliases or {}
    return {canonical(slugify(str(i)), aliases) for i in items if str(i).strip()}


def _load_yaml(path: str | Path) -> dict | list | None:
    path = Path(path)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
