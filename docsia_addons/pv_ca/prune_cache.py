"""Phase rétention : purge le cache local des documents déjà importés (ou plus vieux que N jours)."""
import time
from pathlib import Path
from ..common.db import connect


def prune_cache(apply: bool = False, days: int | None = None) -> dict:
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("""SELECT id, local_path FROM recolte.items
                       WHERE local_path IS NOT NULL AND imported_at IS NOT NULL""")
        rows = cur.fetchall()
    cutoff = (time.time() - days * 86400) if days else None
    freed = n = 0
    for item_id, path in rows:
        p = Path(path)
        if not p.exists():
            continue
        if cutoff and p.stat().st_mtime > cutoff:
            continue
        size = p.stat().st_size
        if apply:
            try:
                p.unlink()
                with conn.cursor() as cur:
                    cur.execute("UPDATE recolte.items SET local_path=NULL, updated_at=now() WHERE id=%s",
                                (item_id,))
                conn.commit()
            except OSError:
                continue
        freed += size
        n += 1
    conn.close()
    mo = round(freed / 1_048_576, 1)
    print(f"DONE prune_cache {'(applied)' if apply else '(dry-run)'} fichiers={n} espace={mo} Mo")
    return {"files": n, "mb": mo, "applied": apply}
