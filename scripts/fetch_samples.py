"""Descarga muestras reales de la API publica de Get on Board y las guarda en docs/api-samples/.

Solo stdlib: se ejecuta antes de instalar dependencias.
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.getonbrd.com/api/v0"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "api-samples"
UA = "gob-dashboard/0.1 (uso personal; exploracion API publica)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read().decode("utf-8"))


def save(out_dir, filename, url, note, transform=None):
    status, headers, body = fetch(url)
    if transform:
        body = transform(body)
    doc = {
        "_meta": {
            "url": url,
            "http_status": status,
            "content_type": headers.get("Content-Type"),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": note,
        },
        "response": body,
    }
    path = out_dir / filename
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  {filename}  <- HTTP {status}  ({path.stat().st_size} bytes)")
    time.sleep(0.5)  # <= 2 req/s


def trim_jobs(n, truncate_text=True):
    def _t(body):
        body["data"] = body["data"][:n]
        if truncate_text:
            for job in body["data"]:
                a = job["attributes"]
                for k in ("description", "projects", "functions", "benefits", "desirable"):
                    if isinstance(a.get(k), str) and len(a[k]) > 400:
                        a[k] = a[k][:400] + " …[recortado para la muestra]"
                comp = a.get("company", {}).get("data")
                if isinstance(comp, dict) and "attributes" in comp:
                    ca = comp["attributes"]
                    for k in ("description", "long_description", "projects", "benefits"):
                        if isinstance(ca.get(k), str) and len(ca[k]) > 300:
                            ca[k] = ca[k][:300] + " …[recortado para la muestra]"
        return body
    return _t


def trim_list(n):
    def _t(body):
        body["data"] = body["data"][:n]
        return body
    return _t


def trim_company(body):
    a = body["data"]["attributes"]
    for k in ("description", "long_description", "projects", "benefits"):
        if isinstance(a.get(k), str) and len(a[k]) > 300:
            a[k] = a[k][:300] + " …[recortado para la muestra]"
    return body


def main(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Guardando muestras en {out_dir}")

    expand = "&".join(
        f"expand[]={x}"
        for x in ("tags", "company", "seniority", "modality", "location_cities", "location_regions")
    )

    save(out_dir, "categories.json", f"{BASE}/categories?per_page=120&lang=es",
         "Las 18 categorias completas. Sin recortar.")

    save(out_dir, "category_jobs_collapsed.json",
         f"{BASE}/categories/programacion/jobs?per_page=2&lang=es",
         "SIN expand: fijate en que tags/company/seniority traen ids NUMERICOS. "
         "Recortado a 2 ofertas y textos a 400 chars.",
         transform=trim_jobs(2))

    save(out_dir, "category_jobs_expanded.json",
         f"{BASE}/categories/programacion/jobs?per_page=3&lang=es&{expand}",
         "CON expand: los mismos ids ahora son SLUGS y traen attributes. Esta es la forma que usa la ingesta. "
         "Recortado a 3 ofertas y textos a 400 chars.",
         transform=trim_jobs(3))

    save(out_dir, "category_jobs_meta_pagination.json",
         f"{BASE}/categories/programacion/jobs?per_page=120&page=3&lang=es",
         "Prueba de que `page` funciona aunque el OpenAPI no lo documente para este endpoint. "
         "Recortado a 1 oferta; lo importante es el bloque `meta`.",
         transform=trim_jobs(1))

    save(out_dir, "tags.json", f"{BASE}/tags?per_page=10",
         "Recortado a 10 de ~5.850 tags (meta.total_pages con per_page=10).",
         transform=trim_list(10))

    save(out_dir, "seniorities.json", f"{BASE}/seniorities?per_page=120&lang=es",
         "Las 5 seniorities completas. Sin recortar.")

    save(out_dir, "modalities.json", f"{BASE}/modalities?per_page=120&lang=es",
         "Las 4 modalidades completas. Sin recortar.")

    save(out_dir, "countries.json", f"{BASE}/countries?per_page=120&lang=es",
         "Recortado a 10 de ~250 paises. Los ids son slugs (chile, brasil, espana).",
         transform=trim_list(10))

    save(out_dir, "cities.json", f"{BASE}/cities?per_page=120&lang=es",
         "Recortado a 10 ciudades (tenant_city). El id es NUMERICO, no slug.",
         transform=trim_list(10))

    save(out_dir, "perks.json", f"{BASE}/perks", "Recortado a 10 perks. Este endpoint NO trae `meta`.",
         transform=trim_list(10))

    save(out_dir, "company.json", f"{BASE}/companies/admoai?lang=es",
         "Detalle de empresa. NO existe campo size/headcount. `country` es codigo ISO (CL). "
         "Textos recortados a 300 chars.",
         transform=trim_company)

    save(out_dir, "search_jobs_422.json", f"{BASE}/search/jobs?per_page=5",
         "search/jobs NO sirve para listar el catalogo: exige un filtro. Por eso la ingesta usa "
         "/categories/{slug}/jobs.")

    save(out_dir, "search_jobs_query.json", f"{BASE}/search/jobs?query=python&per_page=2&lang=es",
         "search/jobs funcionando con `query`. Recortado a 2 ofertas y textos a 400 chars.",
         transform=trim_jobs(2))

    save(out_dir, "expand_error_no_brackets.json",
         f"{BASE}/categories/programacion/jobs?per_page=1&expand=tags",
         "`expand` sin corchetes revienta el servidor: HTTP 500, no 422.")

    save(out_dir, "expand_error_unknown_value.json",
         f"{BASE}/categories/programacion/jobs?per_page=1&expand[]=perks",
         "Un valor de expand no soportado tambien da HTTP 500. Hay que usar solo los verificados.")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
