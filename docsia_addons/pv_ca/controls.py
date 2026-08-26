"""Phase contrôle qualité : OCR non vide, doublons, quarantaine. Lecture + flag content_ok."""
from ..common.db import connect
from ..common.paperless import Paperless


def controls(check_ocr: bool = True, ocr_limit: int | None = None) -> dict:
    conn, p = connect(), Paperless()
    empty = checked = 0
    if check_ocr:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, paperless_id FROM recolte.items
                           WHERE paperless_id IS NOT NULL AND content_ok IS NULL
                           ORDER BY id""" + (f" LIMIT {int(ocr_limit)}" if ocr_limit else ""))
            todo = cur.fetchall()
        for item_id, pid in todo:
            try:
                doc = p.get_document(pid)
                ok = bool((doc.get("content") or "").strip())
            except Exception:  # noqa: BLE001
                ok = False
            with conn.cursor() as cur:
                cur.execute("UPDATE recolte.items SET content_ok=%s, updated_at=now() WHERE id=%s",
                            (ok, item_id))
            checked += 1
            if not ok:
                empty += 1
        conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT en_quarantaine, ocr_vide, doublons_sha256, doublons_titre FROM recolte.controles")
        q, ocr_vide, dsha, dtitle = cur.fetchone()
    conn.close()
    report = {"ocr_checked": checked, "ocr_empty_new": empty, "ocr_vide_total": ocr_vide,
              "quarantaine": q, "doublons_sha256": dsha, "doublons_titre": dtitle}
    print(f"DONE controls {report}")
    return report
