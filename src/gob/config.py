"""Rutas y constantes compartidas. Sin lógica."""

from __future__ import annotations

from pathlib import Path

# Raíz del proyecto: src/gob/config.py -> src/gob -> src -> raíz
ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "gob.db"

TAG_ALIASES_PATH = DATA_DIR / "tag_aliases.yml"
TECH_KEYWORDS_PATH = DATA_DIR / "tech_keywords.yml"
MI_STACK_PATH = DATA_DIR / "mi_stack.yml"
PERFILES_DIR = DATA_DIR / "perfiles"

TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

# La API no expone la moneda de min_salary/max_salary en ningún endpoint.
# Get on Board publica los sueldos en USD brutos mensuales; lo guardamos como
# supuesto explícito y la UI lo muestra como tal. Ver docs/API.md §3.6.
ASSUMED_CURRENCY = "USD"
