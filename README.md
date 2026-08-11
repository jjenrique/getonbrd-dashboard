# Dashboard de demanda técnica — Get on Board

Aplicación local que consume la **API pública** de [Get on Board](https://www.getonbrd.com),
guarda las ofertas en SQLite y sirve un dashboard para responder qué se está demandando hoy en el
mercado tech LATAM/Chile:

- ¿Qué tecnologías aparecen en más ofertas activas?
- ¿Cómo se reparten por seniority, modalidad remota y país?
- ¿Qué stacks aparecen juntos?
- ¿Qué está creciendo o cayendo entre snapshots?
- ¿Qué empresas publican más y con qué stack?
- **Brechas**: dado mi stack actual, qué tecnologías me faltan y cuánto me abriría cada una.

> Solo API pública, nada de scraping del HTML del sitio. Sin credenciales: la API pública no las
> necesita, así que el repo no tiene `.env` ni secretos.

![Dashboard](docs/capturas/dashboard.png)

<details>
<summary>En modo oscuro</summary>

![Dashboard en modo oscuro](docs/capturas/dashboard-oscuro.png)

Los tonos oscuros no son una inversión de los claros: son pasos elegidos para el fondo oscuro y
validados contra él.
</details>

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate en Linux/macOS
pip install -r requirements.txt
pip install -e .                # deja `python -m gob.ingest` disponible
```

Requiere Python 3.11+ (probado con 3.13.1).

## Uso

```bash
# 1. Poblar la BD: recorre las 18 categorías y escribe data/gob.db
python -m gob.ingest --full

# 2. Levantar el dashboard en http://127.0.0.1:8000
uvicorn gob.web:app --reload
```

Eso es todo. Una corrida completa son **1.022 ofertas** de 18 categorías, 224 empresas y 10.601
asignaciones de tag (8.394 oficiales + 2.207 sacadas del texto de la oferta), en **~45 s** y 23
peticiones.

Otros comandos:

```bash
# Solo algunas categorías
python -m gob.ingest --categories programacion,sysadmin-devops-qa

# Reprocesar sin volver a llamar a la API (usa el JSON crudo de data/raw/)
python -m gob.ingest --from-cache

# Reaplicar data/tag_aliases.yml después de editarlo, sin reingestar
python -m gob.ingest --refresh-aliases

# Ver el análisis por consola, sin dashboard
python scripts/reporte.py --tag python --categoria programacion
```

Opciones útiles de la ingesta: `--db RUTA`, `--sandbox`, `--per-page N` (máx. 120), `--rate N`
(peticiones por segundo, 2 por defecto), `--lang es|en|pt`, `-v`.

**Cada corrida es idempotente**: el upsert va por `id` de oferta y conserva `first_seen_at`.
Correrla dos veces deja exactamente las mismas filas, solo avanza `last_seen_at` y añade un
snapshot.

**Cada página se guarda en disco antes de tocar la BD** (`data/raw/<corrida>/<categoria>_pN.json`),
así que si la ingesta muere a mitad no se pierde lo descargado y `--from-cache` lo reconstruye.
Cada corrida ocupa ~9 MB; las carpetas viejas de `data/raw/` se pueden borrar a mano.

## El dashboard

Una sola página, sin build step: Jinja2 + Chart.js por CDN. **No llama a la API en tiempo de
request**: lee la BD entera en memoria y la recarga sola cuando el archivo cambia, así una
reingesta se ve sin reiniciar el servidor.

| Panel | Qué contesta |
|---|---|
| Barra de filtros | categoría, seniority, remoto, país y rango de fechas; se aplican a todo lo demás |
| KPIs | ofertas, empresas, tecnologías, % remoto y mediana de sueldo con su cobertura |
| Top 25 tecnologías | barras apiladas que separan el **tag oficial** de lo **deducido del texto** |
| Reparto por seniority | a qué nivel se pide cada tecnología |
| Evolución entre snapshots | hasta 5 tecnologías a elegir; avisa si no hay histórico suficiente |
| Co-ocurrencia | con qué aparece una tecnología, con conteo y **lift** |
| Sueldos | mediana, p25 y p75 por tecnología, con el *n* siempre a la vista |
| Mis brechas | lo que se pide y no tengo, por volumen y afinidad |
| Empresas | quién publica más y con qué stack |
| Ofertas | tabla filtrada con enlace a la oferta original |

Cada gráfico trae su **tabla alterna** (`Ver como tabla`) y modo claro/oscuro con conmutador.
La paleta está validada con el validador de la guía de dataviz (banda de luminosidad, suelo de
croma, separación para daltonismo y contraste), en ambos modos.

### Sobre el panel de brechas

El diseño original —ordenar por "ofertas que se desbloquean del todo"— no sobrevive a los datos
reales: una oferta pide **10,4 tags de media**, entre ellos cosas como `documentation` o
`team-leadership`. Es una carta a los Reyes, no un checklist. Con un stack de 10 tecnologías
ninguna oferta pasa del 60% de cobertura y "solo me falta esto" da **1 caso en 1.022**.

Así que ordena por **volumen** y acompaña con la **afinidad**: qué porcentaje del resto de esas
ofertas ya cubres. Es lo que separa "lo piden muchos y estás cerca" de "lo piden muchos pero es
otro mundo" — con el mismo volumen, `jira` (10%) y `typescript` (19%) no significan lo mismo.

## Los ficheros que puedes editar a mano

| Archivo | Para qué |
|---|---|
| [`data/mi_stack.yml`](data/mi_stack.yml) | Tu stack actual, para el panel de brechas |
| [`data/tag_aliases.yml`](data/tag_aliases.yml) | Agrupar duplicados semánticos (`angularjs` → `angular`, `net` → `dotnet`) y puentear del castellano al slug de la API. Tras editarlo: `--refresh-aliases` |
| [`data/tech_keywords.yml`](data/tech_keywords.yml) | Tecnologías a buscar en el texto de la oferta, para lo que los tags oficiales no cubren (SCADA, Modbus, Airflow…). Tras editarlo hay que reprocesar: `--from-cache` |

Si algo de tu stack aparece en "sin demanda aquí", suele ser una de dos: el mercado no lo pide, o
el nombre no casa con ningún tag. Lo segundo se arregla con un alias.

## Tests

```bash
pytest -q          # 180 tests, ~3 s
```

Con fixtures fijas y **sin salir a internet**: `tests/conftest.py` bloquea la resolución de nombres
y las conexiones a hosts externos, así que un test que intente llamar a la API falla en el acto con
un mensaje claro. Los del cliente HTTP usan `httpx.MockTransport`; los del dashboard, `TestClient`.

## La API

**Lee [`docs/API.md`](docs/API.md) antes de tocar el cliente.** Documenta el esquema *real*
observado con llamadas en vivo, no el del OpenAPI, incluyendo las trampas que cuestan tiempo:
`page`/`expand[]` funcionan aunque no estén documentados, `expand` mal escrito devuelve **500**,
los ids colapsados y expandidos son distintos, y `search/jobs` no sirve como catálogo.

Las respuestas de ejemplo están en [`docs/api-samples/`](docs/api-samples/) y se regeneran con
`python scripts/fetch_samples.py`.

## Límites conocidos

- **Sin moneda**: la API no expone ninguna, en ningún endpoint. Los sueldos se guardan como `USD`
  brutos/mes por convención de Get on Board, y tanto la UI como los docs lo marcan como supuesto.
  Solo el 42% de las ofertas publican rango.
- **Sin tamaño de empresa**: el endpoint público de company no expone headcount.
- **Histórico solo hacia adelante**: la API solo devuelve ofertas activas, así que las tendencias
  empiezan a existir desde la primera corrida de la ingesta. Con menos de dos snapshots, el
  gráfico lo dice en vez de pintar una línea plana que parece un dato.
- **Concentración**: una sola empresa (BC Tecnología) publica ~25% de las ofertas activas. Conviene
  tenerlo presente al leer cualquier ranking global.

## Estructura

```
src/gob/
  client.py     cliente HTTP: rate limit y reintentos, sin lógica de negocio
  models.py     esquema SQLite
  normalize.py  limpieza de HTML, slugs, alias y extracción por keywords
  ingest.py     descarga + upsert idempotente + snapshots  (python -m gob.ingest)
  queries.py    SELECTs que alimentan a analytics
  analytics.py  agregaciones puras, sin BD ni red
  web.py        app FastAPI: solo rutas y render
templates/ static/   dashboard (Jinja2 + Chart.js por CDN, sin build step)
data/         tag_aliases.yml · tech_keywords.yml · mi_stack.yml  (editables a mano)
data/raw/     JSON crudo por página y snapshot (no versionado)
docs/         API.md · api-samples/ · capturas/
scripts/      fetch_samples.py · reporte.py
tests/        fixtures fijas, sin red
```
