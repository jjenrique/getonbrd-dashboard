# API pública de Get on Board — esquema real observado

> **Este documento describe lo que la API devuelve de verdad**, verificado con llamadas reales el
> **11-ago-2026**, no lo que dice el OpenAPI ([`api-1.json`](../api-1.json)). Donde el spec y la
> realidad no coinciden, lo indico explícitamente.
>
> Las respuestas de ejemplo están en [`api-samples/`](api-samples/). Cada archivo tiene un bloque
> `_meta` con la URL exacta, el HTTP status, la fecha de descarga y si el contenido fue recortado;
> la respuesta tal cual va bajo la clave `response`.

---

## 1. Lo básico

| | |
|---|---|
| Base URL producción | `https://www.getonbrd.com/api/v0/` |
| Base URL sandbox | `https://sandbox.getonbrd.dev/api/v0/` (datos falsos) |
| Autenticación | **Ninguna** para la parte pública (categorías, empresas, ofertas, búsqueda, tablas de referencia) |
| Formato | JSON. Envoltorio tipo JSON:API pero **no estándar** (ver §4) |
| Localización | `?lang=es\|en\|pt`, o cabecera `Accept-Language`. Afecta nombres de categorías, perks y países |
| Paginación | `?page=N&per_page=M`, `per_page` **máximo 120** (default 120) |
| Rate limit | No documentado y no hay cabeceras `X-RateLimit-*`. Este proyecto se autolimita a **≤2 req/s** por cortesía |

Envoltorio de colección:

```json
{ "data": [ … ], "meta": { "page": 1, "per_page": 120, "total_pages": 3 } }
```

`GET /perks` es la excepción: devuelve `data` **sin** `meta`.

Errores: `{"message": "…", "code": "…"}` con el HTTP status correspondiente
(`404 not_found`, `422 unprocessable_content`, `500 internal_server_error`).

---

## 2. Endpoints públicos y cuál usa este proyecto

| Endpoint | ¿Lo usamos? | Para qué |
|---|---|---|
| `GET /categories` | **Sí** | Descubrir los 18 slugs de categoría a recorrer |
| `GET /categories/{slug}/jobs` | **Sí — fuente principal** | Todas las ofertas activas, paginadas, con `expand[]` |
| `GET /seniorities` | Sí | Tabla de referencia (5 filas) |
| `GET /modalities` | Sí | Tabla de referencia (4 filas) |
| `GET /tags` | Opcional | Catálogo completo (~5.860 tags). No hace falta: los tags llegan expandidos en cada job |
| `GET /companies/{id}` | No | La empresa llega expandida en el job. Se deja en el cliente por si acaso |
| `GET /search/jobs` | No (secundario) | **No sirve como catálogo**, ver §3 |
| `GET /countries`, `/cities`, `/regions`, `/perks`, `/headcounts`, `/industries` | Referencia | Se documentan aquí, no se ingieren |
| `GET /tags/{id}/jobs`, `/companies/{id}/jobs` | No | Vistas alternativas del mismo conjunto |
| `GET /insights/{id}` | No | Requiere ids de consulta con nombre que no están documentados; devuelve `422 Invalid insight id` |
| `GET /jobs`, `/applications`, `/processes`, `/professionals`, … | No | **Privados**, requieren `Authorization: Bearer` de una empresa suscrita |

**No existe un endpoint público para una oferta individual.** `GET /jobs/{id}` es privado. Todo lo
que sabemos de una oferta viene del listado — por eso la ingesta debe guardarlo todo de una pasada.

---

## 3. Trampas (esto es lo importante)

### 3.1 `page` y `expand[]` funcionan en `/categories/{slug}/jobs` aunque el spec no los documente

El OpenAPI solo lista `country_code` y `per_page` para este endpoint. En la práctica ambos
funcionan y son imprescindibles:

```bash
curl "https://www.getonbrd.com/api/v0/categories/programacion/jobs?per_page=5&page=2"
# -> meta: {"page": 2, "per_page": 5, "total_pages": 68}
```

Ver [`api-samples/category_jobs_meta_pagination.json`](api-samples/category_jobs_meta_pagination.json).

Pedir una página más allá de `total_pages` devuelve **200 con `data: []`** (no 404), así que el
bucle de paginación puede cortar por `page > total_pages` o por página vacía, indistintamente.
Una categoría inexistente sí da `404 {"code":"not_found"}`.

### 3.2 `expand[]` es frágil: mal escrito devuelve **500**, no 422

- `?expand=tags` (sin corchetes) → **HTTP 500**.
- `?expand[]=perks` (valor no soportado) → **HTTP 500**.

Hay que usar la sintaxis con corchetes y solo valores válidos. Verificados uno por uno en
`/categories/{slug}/jobs`:

| valor | resultado |
|---|---|
| `tags`, `company`, `seniority`, `modality`, `location_cities`, `location_regions`, `location_tenants` | 200, expande |
| `category` | 200, pero **no añade nada** (`category_name` ya viene siempre) |
| `perks` | **500** |

Consecuencia práctica: como el 500 es indistinguible de un error transitorio del servidor, el
cliente **no debe reintentar ciegamente** un 500 con la misma URL para siempre — el backoff tiene
tope de 5 intentos y luego registra el fallo.

Combinación que usa la ingesta (una sola pasada, cero N+1):

```
expand[]=tags&expand[]=company&expand[]=seniority&expand[]=modality&expand[]=location_cities&expand[]=location_regions
```

### 3.3 Los ids colapsados y los expandidos **son distintos**

Sin `expand`, las relaciones traen ids **numéricos internos**. Con `expand`, traen **slugs**.

```jsonc
// sin expand  ->  api-samples/category_jobs_collapsed.json
"tags":    { "data": [ { "id": 123, "type": "tag" }, { "id": 762, "type": "tag" } ] },
"company": { "data": { "id": 16478, "type": "company" } },
"seniority": { "data": { "id": 4, "type": "seniority" } }

// con expand  ->  api-samples/category_jobs_expanded.json
"tags":    { "data": [ { "id": "react", "type": "tag", "attributes": { "name": "React", "keywords": "ReactJS, …" } } ] },
"company": { "data": { "id": "admoai", "type": "company", "attributes": { "name": "Admoai", … } } },
"seniority": { "data": { "id": "4", "type": "seniority", "attributes": { "name": "Senior", "locale_key": "senior" } } }
```

Ojo: `seniority` y `modality` mantienen el id numérico (como string) también expandidos; los que
cambian a slug son `tag` y `company`. **Nunca mezclar datos ingeridos con y sin `expand`.**

### 3.4 `GET /search/jobs` no sirve para listar el catálogo

```bash
curl "https://www.getonbrd.com/api/v0/search/jobs?per_page=5"
# 422 {"message":"One of query, companies, featured, remote, country_code, board_host parameters is required"}
```

Es un buscador, no un catálogo. Por eso la ingesta se apoya en `/categories/{slug}/jobs`.
Con `?query=python` funciona bien y sí acepta `page` y `expand[]`
([`api-samples/search_jobs_query.json`](api-samples/search_jobs_query.json)).

### 3.5 Las relaciones van **dentro de `attributes`**, no en `relationships`

Esto se parece a JSON:API pero no lo es. `job.relationships` **no existe**; `tags`, `company`,
`seniority`, `modality`, `location_cities`, `location_regions` y `location_tenants` son claves de
`job.attributes`.

### 3.6 No hay campo de moneda

`min_salary` / `max_salary` son enteros pelados (`2000`, `3500`). **No existe `currency` en ningún
endpoint** de la API — ni en el público ni en el `POST /jobs` privado. Get on Board publica los
sueldos en **USD brutos mensuales**, así que este proyecto guarda `currency = 'USD'` como constante
y lo marca en la UI como **supuesto**, no como dato de la API.

Cobertura real: **49 de 120** ofertas de `programacion` publican rango (~40%).

### 3.7 No hay tamaño de empresa

`GET /companies/{id}` devuelve `name, description, long_description, projects, benefits, web,
twitter, github, facebook, angellist, country, response_time_in_days, logo`. **No hay headcount ni
size.** Existe `GET /headcounts` pero nada enlaza una empresa con su headcount, así que la columna
`companies.size` del diseño original se elimina.

### 3.8 Solo hay ofertas **activas**

La API no expone ofertas cerradas ni fecha de expiración (solo `published_at`). El histórico para
las tendencias se construye **hacia adelante**, snapshot a snapshot: la primera corrida no puede
mostrar evolución. Que una oferta desaparezca entre dos snapshots es la señal de que se cerró, y
por eso guardamos `first_seen_at` / `last_seen_at` y la tabla `job_snapshots`.

### 3.9 Los textos vienen en HTML

`description`, `projects`, `functions`, `benefits`, `desirable` son HTML (`<ul><li><strong>…`).
Hay que limpiarlos antes de minar keywords, o "strong" y "li" acabarán en el ranking.

---

## 4. Esquema de un job

Fuente: `GET /categories/{slug}/jobs` con `expand[]`. Ejemplo completo en
[`api-samples/category_jobs_expanded.json`](api-samples/category_jobs_expanded.json).

```jsonc
{
  "id": "ux-engineer-senior-admoai-santiago-4b10",   // slug, NO numérico. Es la PK natural
  "type": "job",
  "links": { "public_url": "https://www.getonbrd.com/jobs/ux-engineer-senior-admoai-santiago-4b10" },
  "attributes": { … }
}
```

### Atributos planos

| Campo | Tipo | Notas |
|---|---|---|
| `title` | string | |
| `description` | string (HTML) | Requisitos. Es el campo con más señal técnica |
| `description_headline` | string \| null | Título que la empresa le puso a esa sección ("Requerimientos del cargo") |
| `projects` | string (HTML) | Sobre la empresa / el proyecto |
| `functions` | string (HTML) | Funciones del cargo |
| `functions_headline` | string \| null | |
| `benefits` | string (HTML) | |
| `benefits_headline` | string \| null | |
| `desirable` | string (HTML) | Deseables — señal técnica secundaria |
| `desirable_headline` | string \| null | |
| `remote` | bool | Redundante con `remote_modality`, pero no equivalente: en 120 ofertas hubo 48 `true` frente a 17 `fully_remote` + 31 `remote_local` |
| `remote_modality` | enum | `no_remote` · `temporarily_remote` · `remote_local` · `fully_remote` · `hybrid` |
| `remote_zone` | null | Siempre `null` en las 120 ofertas observadas |
| `countries` | string[] | **Array de nombres**, no de slugs. Incluye un pseudo-país para las ofertas remotas, **y está localizado**: `["Remote"]` con `lang=en`, `["Remoto"]` con `lang=es`. En la ingesta completa (`lang=es`) salen 472 "Chile" y 450 "Remoto" |
| `lang` | string | `es` · `en` · `lang_not_specified` (57% de los casos) |
| `category_name` | string | Localizado por `lang` ("Programación" / "Programming"). El **slug** de categoría no viene en el job: lo aporta la ingesta, que sabe qué categoría está recorriendo |
| `perks` | string[] | Slugs (`flexible_hours`, `computer_provided`, …) |
| `min_salary`, `max_salary` | int \| null | Sin moneda (§3.6) |
| `published_at` | int | **Epoch en segundos** |
| `applications_count` | int | |
| `response_time_in_days` | `{min, max}` | Ambos pueden ser `null` |
| `rejected_reasons` | array | Siempre vacío en lo público |

### Relaciones (dentro de `attributes`, expandidas)

| Campo | Cardinalidad | Con `expand[]` devuelve |
|---|---|---|
| `company` | 1 | `{id: "<slug>", attributes: {name, description, long_description, projects, benefits, web, twitter, github, facebook, angellist, country (ISO, ej. "CL"), response_time_in_days, logo}}` |
| `tags` | 0..N | `{id: "<slug>", attributes: {name, keywords}}`. `keywords` es un string con sinónimos separados por coma, ej. `"ReactJS, React-Native, GatsbyJS, react.js"` |
| `seniority` | 1 | `{id: "1".."5", attributes: {name, locale_key}}` |
| `modality` | 1 | `{id: "1".."4", attributes: {name, locale_key}}` |
| `location_cities` | 0..N | `{id: "<numérico>", type: "tenant_city", attributes: {name, country}}`. **7 de 120 ofertas tienen más de una ciudad** |
| `location_regions` | 0..N | `{id, attributes: {name}}` — casi siempre vacío |
| `location_tenants` | 0..N | Siempre vacío en lo observado |

Por eso el modelo de datos usa tablas puente `job_countries` / `job_cities` en vez de las columnas
simples `country` / `city` del diseño original, con `primary_country` / `primary_city`
denormalizadas para las consultas rápidas.

---

## 5. Tablas de referencia

**Seniorities** (`GET /seniorities`, 5 filas — el id numérico es el que aparece en el job):

| id | name (es) | locale_key |
|---|---|---|
| 1 | Sin experiencia | `no_experience` |
| 2 | Junior | `junior` |
| 3 | Semi Senior | `semi_senior` |
| 4 | Senior | `senior` |
| 5 | Expert | `expert` |

**Modalities** (`GET /modalities`, 4 filas):

| id | name (es) | locale_key |
|---|---|---|
| 1 | Full time | `full_time` |
| 2 | Part time | `part_time` |
| 3 | Freelance | `freelance` |
| 4 | Práctica/Internship | `internship` |

**Otras** (conteos exactos vía `meta.total_pages` con `per_page=1`): `GET /countries` **250** países,
ids tipo slug con rarezas (`brasil`, `espana`, `deutschland`, `belgie`) · `GET /cities` **413**
ciudades con id **numérico** · `GET /regions` **9** · `GET /perks` **41** (sin `meta`, ids slug) ·
`GET /tags` **5.851** tags, id slug + `keywords`.

---

## 6. Categorías y volumen (11-ago-2026)

18 categorías, **~1.022 ofertas activas**. `total_pages` con `per_page=1` da el total exacto por
categoría:

| slug | dimension | nombre (es) | ofertas |
|---|---|---|---|
| `programacion` | programming | Programación | 337 |
| `sysadmin-devops-qa` | sysadmin | SysAdmin / DevOps / QA | 128 |
| `data-science-analytics` | data_analytics | Data / Analytics | 127 |
| `innovacion-agilidad` | innovation_agile | Innovación y Agilidad | 79 |
| `operaciones-management` | operations | Operaciones / Management | 75 |
| `comercial-ventas` | sales | Comercial y Ventas | 56 |
| `machine-learning-ai` | machine_learning | Machine Learning & AI | 42 |
| `servicio-cliente` | customer_support | Servicio al Cliente | 31 |
| `marketing-digital` | digital_marketing | Marketing Digital | 26 |
| `cybersecurity` | cybersecurity | Cybersecurity | 25 |
| `diseno-ux` | design | Diseño / UX | 24 |
| `desarrollo-mobile` | mobile | Desarrollo Mobile | 20 |
| `technical-support` | technical_support | Technical Support | 20 |
| `recursos-humanos` | hr | Recursos Humanos | 10 |
| `otra` | other | Otra | 9 |
| `publicidad-medios` | advertising_media | Publicidad y Medios | 8 |
| `hardware-electronics` | hardware | Hardware / Electronics | 3 |
| `education-coaching` | education_coaching | Education & Coaching | 2 |

**Coste de una corrida completa**: ~27 requests (18 categorías, la mayoría de una sola página de
120) → **≈15 segundos a 2 req/s**. Con `expand[]` completo, una página de 120 ofertas pesa ~1,1 MB
→ **~10 MB de JSON crudo por snapshot**.

---

## 7. Calidad de los tags oficiales

Medido sobre las 120 primeras ofertas de `programacion`:

- **8,7 tags por oferta** de media (mín. 3, máx. 10 — parece haber tope en 10).
- 336 tags distintos.
- Ya vienen **slugificados por GoB**: `react`, `node-js`, `next-js`, `ci-cd`, `html5`, `net`.
- Top 15: `rest` (39), `ci-cd` (34), `react` (30), `javascript` (30), `typescript` (26), `git` (24),
  `node-js` (23), `microservices` (21), `python` (20), `aws` (19), `sql` (19), `postgresql` (18),
  `java` (17), `testing` (15), `docker` (15).

Son buenos, pero hay duplicados semánticos (`html5` vs `html`, `net` vs `dotnet`, `angularjs` vs
`angular`) → de ahí la tabla de alias editable en `data/tag_aliases.yml`. Y no cubren nicho
(SCADA, Modbus, Terraform en detalle) → de ahí la extracción por keywords desde el texto, marcada
en la BD con `source='text'` para poder distinguirla del dato oficial.

---

## 8. Reproducir esta exploración

```bash
# Categorías
curl "https://www.getonbrd.com/api/v0/categories?per_page=120&lang=es"

# Total de ofertas de una categoría (mirar meta.total_pages)
curl "https://www.getonbrd.com/api/v0/categories/programacion/jobs?per_page=1"

# La llamada que usa la ingesta
curl "https://www.getonbrd.com/api/v0/categories/programacion/jobs?per_page=120&page=1&lang=es\
&expand[]=tags&expand[]=company&expand[]=seniority&expand[]=modality\
&expand[]=location_cities&expand[]=location_regions"

# Los 500 de expand mal formado
curl -i "https://www.getonbrd.com/api/v0/categories/programacion/jobs?per_page=1&expand=tags"
curl -i "https://www.getonbrd.com/api/v0/categories/programacion/jobs?per_page=1&expand[]=perks"

# search/jobs exige un filtro
curl "https://www.getonbrd.com/api/v0/search/jobs?per_page=5"
```

Para regenerar todas las muestras de `api-samples/` de una vez:
`python scripts/fetch_samples.py docs/api-samples`

---

## 9. Términos de uso

Este proyecto usa **exclusivamente la API pública**; no hace scraping del HTML de getonbrd.com.
Se identifica con `User-Agent: gob-dashboard/0.1 (uso personal)` y se autolimita a ≤2 req/s.
No se necesitan credenciales, así que no hay `.env` ni secretos en el repo.
