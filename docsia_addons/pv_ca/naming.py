"""Règles métier PV CA : dates FR, détection de liens, numéro de délib, charte de titres."""
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote

MONTHS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}
DOC_EXTS = {".pdf", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            ".odt", ".ods", ".odp"}


def ascii_fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c)).lower()


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date_fr(text: str):
    t = ascii_fold(clean_text(text))
    for pat, order in [(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", "ymd"),
                       (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", "dmy")]:
        m = re.search(pat, t)
        if m:
            a, b, c = map(int, m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return datetime(y, mo, d).date().isoformat()
            except ValueError:
                pass
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTHS_FR) + r")\s+(\d{4})\b", t)
    if m:
        d, mon, y = m.groups()
        try:
            return datetime(int(y), MONTHS_FR[mon], int(d)).date().isoformat()
        except ValueError:
            return None
    return None


def documentary_href(href: str) -> bool:
    if not href:
        return False
    low = href.lower()
    path = urlparse(low).path
    return ("download=true" in low or any(path.endswith(e) for e in DOC_EXTS)
            or "/medias/fichier/" in low or "/_resource/" in low)


def base_from(name: str) -> str:
    return Path(clean_text(unquote(name or ""))).stem


def normalize_delib_ref(text: str, year: int) -> str:
    """Renvoie CA-AAAA-NNN si un numéro est repérable, sinon ''."""
    folded = ascii_fold(clean_text(text))
    m = re.search(r"\bca[-_\s]*(20\d{2})[-_\s]*(\d{1,3})\b", folded)
    if m:
        return f"CA-{int(m.group(1)):04d}-{int(m.group(2)):03d}"
    m = re.search(r"\b(20\d{2})[-_\s]+(\d{1,3})\b", folded)
    if m:
        return f"CA-{int(m.group(1)):04d}-{int(m.group(2)):03d}"
    for pat in (r"\bdeliberation(?:\s+n(?:o|°)?)?\s*[-_:]?\s*(\d{1,3})\b",
                r"\bdelib(?:\s+n(?:o|°)?)?\s*[-_:]?\s*(\d{1,3})\b",
                r"\bn(?:o|°)\s*(\d{1,3})\b"):
        m = re.search(pat, folded)
        if m:
            return f"CA-{int(year):04d}-{int(m.group(1)):03d}"
    return ""


def expected_title(role: str, seance_iso: str, delib_ref: str, base: str) -> str:
    """Charte : PV -> CA-date-PV-base ; délib numérotée -> CA-AAAA-NNN-base ;
    délib datée (historique) -> CA-date-base."""
    role = (role or "").upper()
    parts = []
    if role in ("PV", "PV_ANNEXE"):
        parts = [f"CA-{seance_iso}-PV", base] if seance_iso else ["CA-PV", base]
    elif role in ("DELIB", "PJ"):
        if delib_ref:
            parts = [delib_ref, base]
        elif seance_iso:
            parts = [f"CA-{seance_iso}", base]
        else:
            parts = [base]
    else:
        parts = [f"CA-{seance_iso}" if seance_iso else "CA", base]
    return "-".join(p for p in parts if p)


# --- pour le rétro-backfill depuis les titres Paperless existants ---
DECISION_RE = re.compile(r"^(CA-\d{4}-\d{3})(?:-|$)")
PV_RE = re.compile(r"^CA-(\d{4}-\d{2}-\d{2})-PV-")
DATED_RE = re.compile(r"^CA-(\d{4}-\d{2}-\d{2})-")


def session_ref_from_title(title: str) -> str:
    """Extrait la date de séance (YYYY-MM-DD) d'un titre PV ou daté ; '' sinon."""
    m = PV_RE.match(title or "") or DATED_RE.match(title or "")
    return m.group(1) if m else ""
