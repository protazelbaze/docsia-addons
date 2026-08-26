"""Phase 2 (scrapping) : énumère PV, délibérations et pièces des pages CA. Aucun téléchargement.

Structure réelle de la colonne 2 : une délibération (nom de fichier `délib n°NN - ...`)
suivie de ses pièces jointes (fichiers sans ce marqueur), puis la délib suivante.
Le numéro et la base du titre viennent du NOM DE FICHIER décodé, pas du texte du lien.
"""
import requests
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup

from . import naming

DEFAULT_CURRENT = ("https://www.u-bordeaux-montaigne.fr/fr/universite/organisation/"
                   "conseils-et-commissions/proces_verbaux_ca.html")
DEFAULT_ARCHIVES = ("https://www.u-bordeaux-montaigne.fr/fr/universite/organisation/"
                    "conseils-et-commissions/proces_verbaux_ca/archives_pv_de_ca.html")
USER_AGENT = "docsia-addons-pv-ca/1.0"


def _filename(href: str) -> str:
    return unquote(urlparse(href).path.rsplit("/", 1)[-1])


def _row(page_url, href, date_iso, role, ref, text):
    fname = _filename(href)
    base = naming.base_from(fname) or naming.base_from(text) or naming.base_from(href)
    return dict(page_url=page_url, origin_url=href, seance_date=date_iso, role=role,
                delib_number=(ref or None), source_title=text,
                expected_title=naming.expected_title(role, date_iso, ref or "", base))


def _scrape_page(session, page_url, timeout):
    r = session.get(page_url, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows, seen = [], 0
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        date_iso = naming.parse_date_fr(cells[0].get_text(" ", strip=True))
        if not date_iso:
            continue
        seen += 1
        year = int(date_iso[:4])

        # Colonne 2 : délibérations (fichier "délib n°NN") + leurs pièces jointes.
        current_ref = ""
        for a in cells[1].find_all("a", href=True):
            if not naming.documentary_href(a["href"]):
                continue
            href = urljoin(page_url, a["href"])
            text = naming.clean_text(a.get_text(" ", strip=True))
            fname = _filename(href)
            ref = naming.normalize_delib_ref(f"{fname} {text} {a.get('title','')}", year)
            is_delib = bool(ref) and ("delib" in naming.ascii_fold(fname)
                                      or "delib" in naming.ascii_fold(text))
            if is_delib:
                current_ref = ref
                rows.append(_row(page_url, href, date_iso, "DELIB", ref, text))
            else:
                rows.append(_row(page_url, href, date_iso, "PJ", current_ref, text))

        # Colonne 3 : PV puis annexes de PV.
        if len(cells) >= 3:
            pv_links = [a for a in cells[2].find_all("a", href=True)
                        if naming.documentary_href(a["href"])]
            for pos, a in enumerate(pv_links, start=1):
                href = urljoin(page_url, a["href"])
                text = naming.clean_text(a.get_text(" ", strip=True))
                role = "PV" if pos == 1 else "PV_ANNEXE"
                rows.append(_row(page_url, href, date_iso, role, "", text))
    return rows, seen


def discover(page_urls=None, start_year=2010, end_year=2100, timeout=60):
    """Renvoie (items, rapport_pages). Lève si une page renvoie 0 ligne (garde-fou)."""
    pages = page_urls or [DEFAULT_CURRENT, DEFAULT_ARCHIVES]
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    all_rows, report = [], {}
    for url in pages:
        rows, seen = _scrape_page(s, url, timeout)
        report[url] = seen
        if seen == 0:
            raise RuntimeError(f"Garde-fou : 0 ligne extraite de {url} (structure changée ?)")
        all_rows.extend(rows)

    seen_keys, deduped = set(), []
    for row in all_rows:
        if not (start_year <= int(row["seance_date"][:4]) <= end_year):
            continue
        key = (row["seance_date"], row["role"], row["origin_url"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(row)
    return deduped, report
