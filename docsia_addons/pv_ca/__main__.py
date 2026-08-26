"""CLI de l'addon PV CA.

  initdb        applique sql/*.sql (schéma recolte)
  discover      scrape à blanc (rapport, aucune écriture)
  backfill      rétro CF5 sur l'existant Paperless [--apply] [--limit N]
  run           chaîne complète scrap->stock->import->métadonnées [--apply]
  retry         rejoue les items en échec, quarantaine au-delà du seuil [--apply]
  controls      contrôle qualité (OCR non vide, doublons, quarantaine)
  prune-cache   purge le cache local des documents importés [--apply] [--days N]
  sync-ia       stampe l'état d'embedding depuis DOCSIA (phase IA/disponibilité)
"""
import argparse
from datetime import datetime
from pathlib import Path

from ..common.db import connect


def _initdb():
    sql_dir = Path(__file__).resolve().parents[2] / "sql"
    conn = connect()
    for f in sorted(sql_dir.glob("*.sql")):
        with conn.cursor() as cur:
            cur.execute(f.read_text(encoding="utf-8"))
        conn.commit()
        print("applied", f.name)
    conn.close()
    print("DONE initdb")


def main():
    ap = argparse.ArgumentParser(prog="docsia_addons.pv_ca")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("initdb")
    sub.add_parser("discover")
    b = sub.add_parser("backfill"); b.add_argument("--apply", action="store_true"); b.add_argument("--limit", type=int)
    r = sub.add_parser("run"); r.add_argument("--apply", action="store_true"); r.add_argument("--run-id"); r.add_argument("--trigger", default="manual")
    rt = sub.add_parser("retry"); rt.add_argument("--apply", action="store_true", default=True)
    co = sub.add_parser("controls"); co.add_argument("--ocr-limit", type=int)
    pc = sub.add_parser("prune-cache"); pc.add_argument("--apply", action="store_true"); pc.add_argument("--days", type=int)
    sub.add_parser("sync-ia")

    a = ap.parse_args()
    if a.cmd == "initdb":
        _initdb()
    elif a.cmd == "discover":
        from . import discover as disc
        items, report = disc.discover()
        print("pages:", report, "| items:", len(items))
        for it in items[:15]:
            print(" ", it["seance_date"], it["role"], "->", it["expected_title"])
    elif a.cmd == "backfill":
        from .backfill import backfill_session_ref
        backfill_session_ref(apply=a.apply, limit=a.limit)
    elif a.cmd == "run":
        from .pipeline import run
        run(a.run_id or datetime.now().strftime("%Y%m%d_%H%M%S"), trigger=a.trigger, apply=a.apply)
    elif a.cmd == "retry":
        from .pipeline import retry
        retry(apply=a.apply)
    elif a.cmd == "controls":
        from .controls import controls
        controls(ocr_limit=a.ocr_limit)
    elif a.cmd == "prune-cache":
        from .prune_cache import prune_cache
        prune_cache(apply=a.apply, days=a.days)
    elif a.cmd == "sync-ia":
        from .pipeline import sync_ia
        sync_ia()


if __name__ == "__main__":
    main()
