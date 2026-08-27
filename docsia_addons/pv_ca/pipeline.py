"""Chaîne complète de la récolte PV CA, phase par phase, avec suivi en base.

Phases : scrapping -> structuration/stockage -> ingestion Paperless -> métadonnées.
(L'ingestion IA est réconciliée à part par sync_ia ; elle relève de DOCSIA.)
"""
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import requests

from ..common import runs
from ..common.config import env
from ..common.db import connect
from ..common.hashing import sha256_file, md5_file
from ..common.paperless import Paperless
from . import discover as disc
from . import naming

SRC = "pv-ca"
OFFICE = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp"}
MAX_ATTEMPTS = 3
CF = {"folder": "Dossier décisionnel", "number": "Numéro de délibération",
      "session": "Référence séance", "url": "URL source officielle",
      "pdf_url": "URL source officielle (PDF)"}


def _sniff(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(b"%PDF-"):
        return "PDF"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "ZIP"
    return path.suffix[1:].upper() if path.suffix else "BIN"


def _pdf_truncated(path: Path) -> bool:
    """Vrai si le fichier est un PDF (magic %PDF-) mais sans marqueur %%EOF en fin.
    Signe d'une troncature, le plus souvent cote source (fichier casse sur le site)."""
    try:
        with path.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                return False
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read()
        return b"%%EOF" not in tail
    except OSError:
        return False


def _checksum_map(p: Paperless) -> dict:
    return {d["checksum"]: d["id"] for d in p.iter_documents() if d.get("checksum")}


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": disc.USER_AGENT})
    return s


def _download(sess, cache: Path, url: str) -> Path:
    r = sess.get(url, timeout=60)
    r.raise_for_status()
    name = naming.clean_text(naming.base_from(url)) or "document"
    local = cache / (name[:180] + (Path(url.split("#")[0]).suffix or ""))
    local.write_bytes(r.content)
    return local


def _custom_fields_payload(defs, unit) -> dict:
    ids = {}
    if unit.get("delib_number"):
        ids[defs[CF["folder"]]["id"]] = unit["delib_number"]
        ids[defs[CF["number"]]["id"]] = unit["delib_number"]
    if unit.get("seance_date"):
        ids[defs[CF["session"]]["id"]] = str(unit["seance_date"])
    if unit.get("page_url"):
        ids[defs[CF["url"]]["id"]] = unit["page_url"]
    direct = unit.get("origin_url") or ""
    if unit.get("member_path_in_zip") and unit.get("container_url"):
        direct = f"{unit['container_url']}#{unit['member_path_in_zip']}"
    if direct and len(direct) <= 128:
        ids[defs[CF["pdf_url"]]["id"]] = direct
    return ids


def _duplicate_id(res: dict):
    """Paperless autoritaire sur les doublons : si l'échec est un doublon,
    renvoie l'id du document existant (via '(#id)' ou document_id), sinon None."""
    err = str(res.get("error") or "")
    if "duplicate" not in err.lower():
        return None
    m = re.search(r"#(\d+)", err)
    if m:
        return int(m.group(1))
    did = res.get("document_id")
    return int(did) if did else None


def _finalize_import(conn, p, defs, item_id, pid, unit, c):
    runs.mark(conn, item_id, status="IMPORTED", paperless_id=pid)
    c["imported"] = c.get("imported", 0) + 1
    doc = p.get_document(pid)
    p.set_custom_fields(pid, p.existing_custom_values(doc), _custom_fields_payload(defs, unit))
    runs.mark(conn, item_id, phase="metadata", status="METADATA_DONE", paperless_id=pid)
    c["metadata_done"] = c.get("metadata_done", 0) + 1


def process_unit(conn, p, defs, cksums, unit, apply, c):
    """store -> reconcile (sha256) -> import (+poll) -> métadonnées, avec suivi en base."""
    item_id = runs.upsert_item(conn, SRC, unit.get("run_id"), {
        "origin_url": unit["origin_url"], "container_url": unit.get("container_url"),
        "member_path_in_zip": unit.get("member_path_in_zip"),
        "seance_date": unit["seance_date"], "role": unit["role"],
        "delib_number": unit.get("delib_number"), "source_title": unit.get("source_title"),
        "expected_title": unit["expected_title"], "page_url": unit.get("page_url")})
    try:
        local = Path(unit["local_path"])
        sha = sha256_file(local)
        md5 = md5_file(local)
        runs.mark(conn, item_id, phase="store", status="STORED", local_path=str(local),
                  sha256=sha, mime_type=unit.get("mime_type"), size_bytes=local.stat().st_size)
        c["stored"] = c.get("stored", 0) + 1
        # Paperless 2.x utilise MD5 pour son checksum, v3 le SHA-256 : on couvre les deux.
        hit = cksums.get(md5) or cksums.get(sha)
        if hit:
            runs.mark(conn, item_id, status="SKIPPED_ALREADY_PRESENT", paperless_id=hit)
            return
        if not apply:
            runs.mark(conn, item_id, status="READY_TO_IMPORT")
            return
        path, mime = local, (unit.get("mime_type") or "application/octet-stream")
        if local.suffix.lower() in OFFICE:
            td = tempfile.mkdtemp(prefix="conv_")
            subprocess.run(["libreoffice", "--headless", "--convert-to", "pdf",
                            "--outdir", td, str(local)], check=True, capture_output=True)
            pdfs = list(Path(td).glob("*.pdf"))
            if not pdfs:
                raise RuntimeError("conversion Office sans PDF")
            path, mime = pdfs[0], "application/pdf"
        if _pdf_truncated(path):
            runs.mark(conn, item_id, failed_phase="store",
                      error="PDF source tronque (pas de %%EOF) - probablement casse cote site UBM")
            runs.quarantine(conn, item_id)
            c["quarantaine_source"] = c.get("quarantaine_source", 0) + 1
            return item_id
        task = p.post_document(path, unit["expected_title"], mime)
        runs.mark(conn, item_id, phase="import", status="IMPORT_SUBMITTED", task_uuid=task)
        res = p.poll_task(task, tries=5, delay=2)
        if res["status"] == "SUCCESS" and res.get("document_id"):
            _finalize_import(conn, p, defs, item_id, int(res["document_id"]), unit, c)
        elif res["status"] == "FAILURE":
            dup = _duplicate_id(res)
            if dup:
                # Doublon détecté par Paperless (checksum absent de notre map) : on classe comme déjà présent.
                runs.mark(conn, item_id, status="SKIPPED_ALREADY_PRESENT", paperless_id=dup)
                return item_id
            raise RuntimeError(f"tâche import échouée: {res}")
        else:
            # File Paperless engorgée : on n'attend pas la fin de l'OCR, on confirmera plus tard.
            runs.mark(conn, item_id, status="IMPORT_PENDING")
            c["pending"] = c.get("pending", 0) + 1
    except Exception as e:  # noqa: BLE001
        c["errors"] = c.get("errors", 0) + 1
        runs.mark(conn, item_id, status="FAILED", failed_phase="store/import", error=repr(e))
        print("ERREUR", unit.get("origin_url"), repr(e))
    return item_id


def _fetch_units(sess, cache: Path, row: dict) -> list:
    """Télécharge une ressource et renvoie la liste des unités à traiter (fichier ou membres ZIP)."""
    local = _download(sess, cache, row["origin_url"])
    ext = Path(row["origin_url"].split("#")[0].split("?")[0]).suffix.lower()
    # Office (docx/xlsx/pptx/odt...) est un ZIP structurellement, mais reste UN document :
    # on ne l'éclate pas (il sera converti en PDF plus loin). Seuls les vrais .zip sont éclatés.
    if ext in OFFICE or _sniff(local) != "ZIP":
        return [dict(row, local_path=str(local), container_url=None, member_path_in_zip=None)]
    units = []
    with zipfile.ZipFile(local) as zf:
        for m in zf.infolist():
            if m.is_dir():
                continue
            mn = m.filename.replace("\\", "/")
            if mn.startswith("/") or ".." in Path(mn).parts:
                continue
            target = cache / "zip" / f"{local.stem}__{naming.base_from(mn)}{Path(mn).suffix}"
            with zf.open(m) as s, target.open("wb") as d:
                d.write(s.read())
            base = naming.base_from(mn)
            units.append(dict(row, origin_url=f"{row['origin_url']}#{mn}",
                              container_url=row["origin_url"], member_path_in_zip=mn,
                              local_path=str(target),
                              expected_title=naming.expected_title(
                                  row["role"], row["seance_date"],
                                  row.get("delib_number") or "", base)))
    return units


def run(run_id, trigger="manual", apply=False, page_urls=None, years=(2010, 2100), limit=None):
    conn, p = connect(), Paperless()
    defs = p.custom_fields()
    cache = Path(env("CACHE_DIR", "./cache")); (cache / "zip").mkdir(parents=True, exist_ok=True)
    runs.ensure_source(conn, SRC, "Procès-verbaux et délibérations du CA",
                       modality="html-table-scrape",
                       config={"pages": page_urls or [disc.DEFAULT_CURRENT, disc.DEFAULT_ARCHIVES]})
    runs.start_run(conn, SRC, run_id, trigger)
    runs.heartbeat(conn, SRC, run_id)
    c = {}
    try:
        items, report = disc.discover(page_urls, years[0], years[1])
        cksums = _checksum_map(p)
        sess = _session()
        processed = 0
        for row in items:
            c["discovered"] = c.get("discovered", 0) + 1
            if runs.already_done(conn, SRC, row["origin_url"]):
                c["skipped"] = c.get("skipped", 0) + 1
                continue
            if limit and processed >= limit:
                break
            processed += 1
            if processed % 5 == 0:
                runs.heartbeat(conn, SRC, run_id)  # battement de vivacité
            row = dict(row, run_id=run_id)
            try:
                units = _fetch_units(sess, cache, row)
            except Exception as e:  # noqa: BLE001
                c["errors"] = c.get("errors", 0) + 1
                print("FETCH_FAILED", row["origin_url"], repr(e))
                continue
            for u in units:
                process_unit(conn, p, defs, cksums, u, apply, c)
        status = "FAILED" if c.get("errors") and not c.get("imported") else ("PARTIAL" if c.get("errors") else "OK")
        runs.finish_run(conn, SRC, run_id, status, pages_scanned=len(report), **c)
        print(f"DONE run={run_id} status={status} {c} pages={report}")
        return c
    except Exception as e:  # noqa: BLE001  crash brutal : on ne laisse pas le run bloqué en RUNNING
        runs.finish_run(conn, SRC, run_id, "FAILED", last_error=repr(e), **c)
        print(f"CRASH run={run_id} {e!r} {c}")
        raise
    finally:
        conn.close()


def retry(apply=True, max_attempts=MAX_ATTEMPTS):
    """Phase reprise : rejoue les items en échec non quarantainés ; quarantaine au-delà du seuil."""
    conn, p = connect(), Paperless()
    defs = p.custom_fields()
    cache = Path(env("CACHE_DIR", "./cache")); (cache / "zip").mkdir(parents=True, exist_ok=True)
    sess = _session()
    cksums = _checksum_map(p)
    with conn.cursor() as cur:
        cur.execute("""SELECT id, run_id, origin_url, container_url, member_path_in_zip,
                              seance_date, role, delib_number, source_title, expected_title, attempts
                       FROM recolte.a_relancer ORDER BY id""")
        rows = cur.fetchall()
    c = {}
    for (item_id, run_id, origin_url, container_url, member, sdate, role, dnum,
         stitle, etitle, attempts) in rows:
        if attempts >= max_attempts:
            runs.quarantine(conn, item_id)
            c["quarantine"] = c.get("quarantine", 0) + 1
            continue
        runs.bump_attempt(conn, item_id)
        try:
            if member and container_url:
                zpath = _download(sess, cache, container_url)
                target = cache / "zip" / f"{zpath.stem}__{naming.base_from(member)}{Path(member).suffix}"
                with zipfile.ZipFile(zpath) as zf, zf.open(member) as s, target.open("wb") as d:
                    d.write(s.read())
                local = target
            else:
                local = _download(sess, cache, origin_url.split("#")[0])
            unit = dict(run_id=run_id, origin_url=origin_url, container_url=container_url,
                        member_path_in_zip=member, seance_date=sdate, role=role,
                        delib_number=dnum, source_title=stitle, expected_title=etitle,
                        local_path=str(local), mime_type=None)
            process_unit(conn, p, defs, cksums, unit, apply, c)
        except Exception as e:  # noqa: BLE001
            c["errors"] = c.get("errors", 0) + 1
            runs.mark(conn, item_id, status="FAILED", failed_phase="retry", error=repr(e))
    conn.close()
    print(f"DONE retry {c}")
    return c


def sync_ia():
    """Phases 6/7 : stampe embedded_at/available_at depuis DOCSIA (join par paperless_id)."""
    import psycopg
    conn = connect()
    dcx = psycopg.connect(host=env("DOCSIA_PGHOST", "127.0.0.1"),
                          port=int(env("DOCSIA_PGPORT", "5436")),
                          dbname=env("DOCSIA_PGDATABASE", "docsia"),
                          user=env("DOCSIA_PGUSER", required=True),
                          password=env("DOCSIA_PGPASSWORD", required=True))
    with dcx.cursor() as cur:
        cur.execute("""SELECT pd.paperless_id FROM paperless_documents pd
                       JOIN paperless_chunks pc ON pc.document_id = pd.id
                       WHERE pc.embedding IS NOT NULL GROUP BY pd.paperless_id""")
        embedded = [r[0] for r in cur.fetchall()]
    dcx.close()
    n = 0
    with conn.cursor() as cur:
        for pid in embedded:
            cur.execute("""UPDATE recolte.items
                             SET embedded_at=COALESCE(embedded_at,now()),
                                 available_at=COALESCE(available_at,now()), updated_at=now()
                           WHERE paperless_id=%s AND embedded_at IS NULL""", (pid,))
            n += cur.rowcount
    conn.commit(); conn.close()
    print(f"DONE sync_ia embedded_docs={len(embedded)} items_maj={n}")
    return n


def confirm():
    """Réconcilie les imports en attente : interroge la tâche, finalise ou marque en échec."""
    conn, p = connect(), Paperless()
    defs = p.custom_fields()
    with conn.cursor() as cur:
        cur.execute("""SELECT id, task_uuid, origin_url, container_url, member_path_in_zip,
                              page_url, seance_date, role, delib_number, expected_title
                       FROM recolte.items
                       WHERE status='IMPORT_PENDING' AND task_uuid IS NOT NULL
                       ORDER BY id""")
        rows = cur.fetchall()
    c = {}
    for (item_id, task, origin_url, container_url, member, page_url,
         sdate, role, dnum, etitle) in rows:
        res = p.poll_task(task, tries=1, delay=0)
        if res["status"] == "SUCCESS" and res.get("document_id"):
            unit = dict(origin_url=origin_url, container_url=container_url,
                        member_path_in_zip=member, page_url=page_url, seance_date=sdate,
                        role=role, delib_number=dnum, expected_title=etitle)
            _finalize_import(conn, p, defs, item_id, int(res["document_id"]), unit, c)
        elif res["status"] == "FAILURE":
            dup = _duplicate_id(res)
            if dup:
                runs.mark(conn, item_id, status="SKIPPED_ALREADY_PRESENT", paperless_id=dup)
                c["skipped_dup"] = c.get("skipped_dup", 0) + 1
            else:
                runs.mark(conn, item_id, status="FAILED", failed_phase="import",
                          error=str(res.get("error")))
                c["failed"] = c.get("failed", 0) + 1
        else:
            c["still_pending"] = c.get("still_pending", 0) + 1
    conn.close()
    print(f"DONE confirm {c}")
    return c
