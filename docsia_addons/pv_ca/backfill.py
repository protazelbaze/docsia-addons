"""Rétro-backfill (mode reprise sur l'existant Paperless) : pose CF5 'Référence séance'
sur les PV et délibs datées, à partir du titre. Ne touche pas aux délibs numérotées
(leur date n'est pas dans le titre) ni aux champs déjà renseignés."""
from collections import Counter
from ..common.paperless import Paperless
from . import naming

CF_SESSION = "Référence séance"


def backfill_session_ref(apply: bool = False, limit: int | None = None) -> dict:
    p = Paperless()
    fields = p.custom_fields()
    if CF_SESSION not in fields:
        raise SystemExit(f"Champ Paperless absent : {CF_SESSION}")
    fid = fields[CF_SESSION]["id"]

    st = Counter()
    for doc in p.iter_documents():
        title = doc.get("title") or ""
        ref = naming.session_ref_from_title(title)
        if not ref:
            st["hors_perimetre"] += 1
            continue
        existing = p.existing_custom_values(doc)
        if existing.get(fid):
            st["deja_pose"] += 1
            continue
        st["a_poser"] += 1
        if apply:
            try:
                p.set_custom_fields(doc["id"], existing, {fid: ref})
                st["pose"] += 1
            except Exception as e:  # noqa: BLE001
                st["erreur"] += 1
                print("ERREUR", doc["id"], title, repr(e))
        else:
            print("DRY", doc["id"], title, "->", ref)
        if limit and st["a_poser"] >= limit:
            break
    print(f"DONE {dict(st)}")
    return dict(st)
