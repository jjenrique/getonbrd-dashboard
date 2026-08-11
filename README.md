# Dashboard de demanda técnica — Get on Board

Aplicación local que consume la **API pública** de [Get on Board](https://www.getonbrd.com),
guarda las ofertas en SQLite y sirve un dashboard para responder qué se está demandando hoy en el
mercado tech LATAM/Chile:

- ¿Qué tecnologías aparecen en más ofertas activas?
- ¿Cómo se reparten por seniority, modalidad remota y país?
- ¿Qué stacks aparecen juntos?
- ¿Qué está creciendo o cayendo entre snapshots?
- ¿Qué empresas publican más y con qué stack?
- **Brechas**: dado mi stack actual, qué tecnologías me faltan y cuántas ofertas me abriría cada una.

> Solo API pública, nada de scraping del HTML del sitio. Sin credenciales: la API pública no las
> necesita, así que el repo no tiene `.env` ni secretos.

## Estado

| Fase | Qué incluye | Estado |
|---|---|---|
| 1 | Exploración de la API + `docs/API.md` + muestras reales | ✅ Hecha |
| 2 | Ingesta (`gob.ingest`) y BD SQLite poblada | ⏳ Pendiente |
| 3 | `gob.analytics` + tests | ⏳ Pendiente |
| 4 | Dashboard FastAPI + Chart.js | ⏳ Pendiente |

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate en Linux/macOS
pip install -r requirements.txt
```

Requiere Python 3.11+ (probado con 3.13.1).

## Uso

*(disponible a partir de la Fase 2)*

```bash
# Ingesta completa: recorre las 18 categorías y puebla data/gob.db
python -m gob.ingest --full

# Solo algunas categorías
python -m gob.ingest --categories programacion,sysadmin-devops-qa

# Reprocesar sin volver a llamar a la API (usa el JSON crudo de data/raw/)
python -m gob.ingest --from-cache

# Levantar el dashboard  (Fase 4)
uvicorn gob.web:app --reload
```

## La API

**Lee [`docs/API.md`](docs/API.md) antes de tocar el cliente.** Documenta el esquema *real*
observado con llamadas en vivo, no el del OpenAPI, incluyendo las trampas que cuestan tiempo:
`page`/`expand[]` funcionan aunque no estén documentados, `expand` mal escrito devuelve **500**,
los ids colapsados y expandidos son distintos, y `search/jobs` no sirve como catálogo.

Las respuestas de ejemplo están en [`docs/api-samples/`](docs/api-samples/) y se regeneran con:

```bash
python scripts/fetch_samples.py
```

## Límites conocidos

- **Sin moneda**: la API no expone ninguna. Los sueldos se guardan como `USD` por convención de
  Get on Board (brutos/mes) y la UI lo marca como supuesto. Solo ~40% de las ofertas publican rango.
- **Sin tamaño de empresa**: el endpoint público de company no expone headcount.
- **Histórico solo hacia adelante**: la API solo devuelve ofertas activas, así que las tendencias
  empiezan a existir desde la primera corrida de la ingesta.

## Estructura

```
src/gob/          client.py · models.py · normalize.py · ingest.py · queries.py · analytics.py · web.py
templates/ static/    dashboard (Jinja2 + Chart.js por CDN, sin build step)
data/             tag_aliases.yml · tech_keywords.yml · mi_stack.yml   (editables a mano)
data/raw/         JSON crudo por página y snapshot (no versionado)
docs/             API.md · api-samples/
scripts/          fetch_samples.py
tests/            analytics y normalización, con fixtures fijas y sin red
```
