"""Vuelca por consola lo que dice la BD real. Un vistazo rápido sin dashboard.

    python scripts/reporte.py [--db data/gob.db] [--tag python] [--categoria programacion]

No llama a la API: todo sale de SQLite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gob import analytics as A  # noqa: E402
from gob import config, queries  # noqa: E402
from gob.models import connect  # noqa: E402
from gob.normalize import load_aliases, load_my_stack  # noqa: E402


def titulo(texto: str) -> None:
    # Negrita solo en terminal: al redirigir a un archivo, los códigos ANSI
    # ensucian la salida.
    negrita = ("\033[1m", "\033[0m") if sys.stdout.isatty() else ("", "")
    print(f"\n{negrita[0]}{texto}{negrita[1]}\n" + "─" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--tag", default="python", help="tag para co-ocurrencia y tendencia")
    parser.add_argument("--categoria", help="filtra todo por esta categoría")
    parser.add_argument("--seniority")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    estado = queries.estado_bd(conn)
    if not estado["jobs"]:
        print(f"{args.db} está vacía. Corre primero: python -m gob.ingest --full")
        return 1

    jobs = queries.load_jobs(conn)
    snapshots = queries.load_snapshots(conn)
    filtros = A.Filtros(categoria=args.categoria, seniority=args.seniority)
    jobs_f = A.filtrar(jobs, filtros)

    res = A.resumen(jobs_f)
    titulo("Resumen")
    ultimo = estado["ultimo_snapshot"]
    print(f"  BD: {args.db}   última ingesta: {ultimo['run_at']} ({ultimo['source']})")
    if filtros.activos:
        print(f"  Filtros: categoría={args.categoria} seniority={args.seniority}")
    print(f"  {res.ofertas} ofertas · {res.empresas} empresas · {res.tags} tags · "
          f"{res.remotas} remotas · {res.con_salario} con sueldo publicado")
    print(f"  {len(snapshots)} snapshot(s) acumulados")

    titulo("Top 25 tags")
    for i, t in enumerate(A.top_tags(jobs_f, 25), 1):
        marca = " [solo texto]" if t.solo_texto else ""
        print(f"  {i:2d}. {t.ofertas:4d}  {t.slug:26s} api={t.api:4d} texto={t.texto:4d}{marca}")

    titulo("Tags por seniority (top 10)")
    matriz = A.tags_por_seniority(jobs_f, limit=10)
    print(" " * 24 + " ".join(f"{c[:11]:>11s}" for c in matriz.seniorities))
    for slug, _, conteos in matriz.filas:
        print(f"  {slug:22s}" + " ".join(f"{n:11d}" for n in conteos))

    titulo(f"Co-ocurrencia de «{args.tag}» (top 10 por volumen)")
    co = A.co_ocurrencia(jobs_f, args.tag, 10)
    if not co:
        print(f"  «{args.tag}» no aparece en ninguna oferta del conjunto")
    for c in co:
        print(f"  {c.juntas:4d} juntas ({c.pct:4.0f}% de las de {args.tag})  "
              f"{c.slug:24s} lift={c.lift:.2f}")

    titulo("Sueldos por tag (mediana, solo con muestra suficiente)")
    salarios = A.salarios_por_tags(jobs_f, limit=12)
    print(f"  Importes en {A.salario_por_tag(jobs_f).currency}/mes — SUPUESTO: la API no expone moneda")
    for s in salarios:
        print(f"  {s.mediana:7.0f}  {s.slug:24s} p25={s.p25:6.0f} p75={s.p75:6.0f}  "
              f"n={s.n_con_salario}/{s.n_total} ({s.cobertura:.0f}% publica sueldo)")
    global_ = A.salario_por_tag(jobs_f)
    print(f"  {'':7s}  {'TODO EL CONJUNTO':24s} "
          f"mediana={global_.mediana:.0f}  n={global_.n_con_salario}/{global_.n_total}")

    titulo("Empresas que más publican")
    for e in A.empresas_top(jobs_f, 12):
        print(f"  {e.ofertas:3d}  {e.name[:26]:28s} {e.stack_texto}")

    titulo(f"Tendencia de «{args.tag}»")
    [t] = A.tendencia(jobs_f, snapshots, [args.tag])
    if t.historico_insuficiente:
        print("  Histórico insuficiente: hace falta más de un snapshot.")
        print("  La API solo devuelve ofertas activas, así que la serie empieza")
        print("  en la primera corrida de la ingesta y crece hacia adelante.")
    for p in t.puntos:
        print(f"  {p.run_at}  #{p.snapshot_id}  {p.ofertas:4d} ofertas")

    aliases = load_aliases(config.TAG_ALIASES_PATH)
    stack = load_my_stack(config.MI_STACK_PATH, aliases)
    titulo("Brechas respecto a data/mi_stack.yml")
    informe = A.brechas(jobs_f, stack, limit=15)
    print(f"  Mi stack: {', '.join(informe.stack)}")
    print(f"  Cubro de media el {informe.cobertura_media:.0f}% de los tags de cada oferta\n")
    print(f"  {'OFERTAS':>8s}  {'TECNOLOGÍA':26s} {'AFINIDAD':>8s}")
    for b in informe.items:
        marca = " [solo texto]" if b.solo_texto else ""
        print(f"  {b.ofertas:8d}  {b.slug:26s} {b.afinidad:7.0f}%{marca}")
    if informe.sin_demanda:
        print(f"\n  En tu stack pero sin demanda aquí: {', '.join(informe.sin_demanda)}")
        print("  (puede ser un nombre mal escrito en mi_stack.yml)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
