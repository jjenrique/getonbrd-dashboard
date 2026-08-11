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
| 2 | Ingesta (`gob.ingest`) y BD SQLite poblada | ✅ Hecha |
| 3 | `gob.analytics` + tests | ⏳ Pendiente |
| 4 | Dashboard FastAPI + Chart.js | ⏳ Pendiente |

Una corrida completa hoy: **1.022 ofertas** de 18 categorías, 224 empresas y 10.601
asignaciones de tag (8.394 oficiales + 2.207 sacadas del texto), en **~45 s** y 23 peticiones.

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate en Linux/macOS
pip install -r requirements.txt
```

Requiere Python 3.11+ (probado con 3.13.1).

## Uso

```bash
pip install -e .        # deja `python -m gob.ingest` disponible

# Ingesta completa: recorre las 18 categorías y puebla data/gob.db
python -m gob.ingest --full

# Solo algunas categorías
python -m gob.ingest --categories programacion,sysadmin-devops-qa

# Reprocesar sin volver a llamar a la API (usa el JSON crudo de data/raw/)
python -m gob.ingest --from-cache

# Reaplicar data/tag_aliases.yml después de editarlo, sin reingestar
python -m gob.ingest --refresh-aliases

# Levantar el dashboard  (Fase 4)
uvicorn gob.web:app --reload
```

Opciones útiles: `--db RUTA`, `--sandbox`, `--per-page N` (máx. 120), `--rate N` (peticiones
por segundo, 2 por defecto), `--lang es|en|pt`, `-v`.

**Cada corrida es idempotente**: el upsert va por `id` de oferta y conserva `first_seen_at`.
Correrla dos veces deja exactamente las mismas filas, solo avanza `last_seen_at` y añade un
snapshot.

**Cada página se guarda en disco antes de tocar la BD** (`data/raw/<corrida>/<categoria>_pN.json`),
así que si la ingesta muere a mitad no se pierde lo descargado y `--from-cache` lo reconstruye.
Cada corrida ocupa ~9 MB; las carpetas viejas de `data/raw/` se pueden borrar a mano cuando estorben.

## Los ficheros que puedes editar a mano

| Archivo | Para qué |
|---|---|
| [`data/tag_aliases.yml`](data/tag_aliases.yml) | Agrupar duplicados semánticos (`angularjs` → `angular`, `net` → `dotnet`). Tras editarlo: `--refresh-aliases` |
| [`data/tech_keywords.yml`](data/tech_keywords.yml) | Tecnologías a buscar en el texto de la oferta, para lo que los tags oficiales no cubren (SCADA, Modbus, Airflow…). Tras editarlo hay que reprocesar: `--from-cache` |
| [`data/mi_stack.yml`](data/mi_stack.yml) | Tu stack actual, para el panel de brechas. **Viene con un ejemplo: cámbialo** |

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
