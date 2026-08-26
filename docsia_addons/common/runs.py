"""Écriture du suivi de récolte (schéma recolte.*) : sources, runs, items, phases."""
from .db import connect, schema

S = schema()


def ensure_source(conn, source_key, label, modality=None, config=None, cadence=None):
    import json
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {S}.sources (source_key,label,modality,config,cadence)
            VALUES (%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (source_key) DO UPDATE
              SET label=EXCLUDED.label, modality=EXCLUDED.modality,
                  cadence=COALESCE(EXCLUDED.cadence, {S}.sources.cadence),
                  updated_at=now()
        """, (source_key, label, modality, json.dumps(config or {}), cadence))
    conn.commit()


def start_run(conn, source_key, run_id, trigger="manual"):
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {S}.runs (source_key,run_id,trigger,status)
            VALUES (%s,%s,%s,'RUNNING')
            ON CONFLICT (source_key,run_id) DO UPDATE
              SET trigger=EXCLUDED.trigger, started_at=now(), status='RUNNING'
        """, (source_key, run_id, trigger))
    conn.commit()


def finish_run(conn, source_key, run_id, status, **counts):
    cols = ["pages_scanned", "discovered", "stored", "imported",
            "metadata_done", "embedded", "errors", "last_error"]
    sets = ", ".join(f"{c}=%s" for c in cols if c in counts)
    vals = [counts[c] for c in cols if c in counts]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {S}.runs SET status=%s, finished_at=now()"
            + (", " + sets if sets else "")
            + " WHERE source_key=%s AND run_id=%s",
            [status, *vals, source_key, run_id],
        )
    conn.commit()


def upsert_item(conn, source_key, run_id, item: dict) -> int:
    """Insère/rafraîchit un item par identité source ; pose discovered_at si nouveau."""
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {S}.items
              (source_key,run_id,origin_url,container_url,member_path_in_zip,
               seance_date,role,delib_number,source_title,expected_title,
               status,discovered_at)
            VALUES (%(source_key)s,%(run_id)s,%(origin_url)s,%(container_url)s,
               %(member_path_in_zip)s,%(seance_date)s,%(role)s,%(delib_number)s,
               %(source_title)s,%(expected_title)s,'DISCOVERED',now())
            ON CONFLICT (source_key,origin_url,
                         coalesce(container_url,''),coalesce(member_path_in_zip,''))
            DO UPDATE SET run_id=EXCLUDED.run_id,
               seance_date=EXCLUDED.seance_date, role=EXCLUDED.role,
               delib_number=EXCLUDED.delib_number, source_title=EXCLUDED.source_title,
               expected_title=EXCLUDED.expected_title, updated_at=now()
            RETURNING id
        """, {**{"source_key": source_key, "run_id": run_id}, **item})
        item_id = cur.fetchone()[0]
    conn.commit()
    return item_id


PHASE_COL = {
    "scrap": "discovered_at", "store": "stored_at", "import": "imported_at",
    "metadata": "metadata_at", "ia": "embedded_at", "available": "available_at",
}


def mark(conn, item_id, phase=None, status=None, error=None, failed_phase=None, **fields):
    """Pose l'horodatage d'une phase et/ou met à jour des colonnes de l'item."""
    sets, vals = [], []
    if phase and phase in PHASE_COL:
        sets.append(f"{PHASE_COL[phase]}=now()")
    for k, v in fields.items():
        sets.append(f"{k}=%s"); vals.append(v)
    if status is not None:
        sets.append("status=%s"); vals.append(status)
    sets.append("failed_phase=%s"); vals.append(failed_phase)
    sets.append("error=%s"); vals.append(error)
    sets.append("updated_at=now()")
    with conn.cursor() as cur:
        cur.execute(f"UPDATE {S}.items SET {', '.join(sets)} WHERE id=%s", [*vals, item_id])
    conn.commit()


def bump_attempt(conn, item_id) -> int:
    with conn.cursor() as cur:
        cur.execute(f"UPDATE {S}.items SET attempts=attempts+1, updated_at=now() "
                    f"WHERE id=%s RETURNING attempts", (item_id,))
        n = cur.fetchone()[0]
    conn.commit()
    return n


def quarantine(conn, item_id):
    with conn.cursor() as cur:
        cur.execute(f"UPDATE {S}.items SET quarantined_at=now(), status='QUARANTINE', "
                    f"updated_at=now() WHERE id=%s", (item_id,))
    conn.commit()
