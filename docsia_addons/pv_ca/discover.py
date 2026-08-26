"""Phase 2 (scrapping) : énumère PV, délibs et pièces des pages CA. Aucun téléchargement."""
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from . import naming

DEFAULT_CURRENT = ("https://www.u-bordeaux-montaigne.fr/fr/universite/organisation/"
                   "conseils-et-commissions/proces_verbaux_ca.html")
DEFAULT_ARCHIVES = ("https://www.u-bordeaux-montaigne.fr/fr/universite/organisation/"
                    "conseils-et-commissions/proces_verbaux_ca/archives_pv_de_ca.html")
USER_AGENT = "docsia-addons-pv-ca/1.0"


def _scrape_page(session, page_url, timeout):
    r = session.get(page_url, timeout=timeout)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows, seen_rows = [], 0
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        date_iso = naming.parse_date_fr(cells[0].get_text(" ", strip=True))
        if not date_iso:
            continue
        seen_rows += 1
        year = int(date_iso[:4])

        delib_links = [a for a in cells[1].find_all("a", href=True)
                       if naming.documentary_href(a["href"])]
        current_ref = ""
        for pos, a in enumerate(delib_links, start=1):
            href = urljoin(page_url, a["href"])
            title = naming.clean_text(a.get_text(" ", strip=True))
            candidate = " ".join([title, urlparse(href).path.rsplit("/", 1)[-1],
                                   naming.clean_text(a.get("title", ""))])
            ref = naming.normalize_delib_ref(candidate, year)
            if pos == 1:
                role, current_ref = "DELIB", ref
            elif ref and ref != current_ref:
                role, current_ref = "DELIB", ref
            else:
                role = "PJ"
            eff_ref = current_ref or ref
            rows.append(dict(page_url=page_url, origin_url=href, seance_date=date_iso,
                             role=role, delib_number=(eff_ref or None), source_title=title,
                             expected_title=naming.expected_title(role, date_iso, eff_ref,
                                                                  naming.base_from(title) or naming.base_from(href))))

        if len(cells) >= 3:
            pv_links = [a for a in cells[2].find_all("a", href=True)
                        if naming.documentary_href(a["href"])]
            for pos, a in enumerate(pv_links, start=1):
                href = urljoin(page_url, a["href"])
                title = naming.clean_text(a.get_text(" ", strip=True))
                role = "PV" if pos == 1 else "PV_ANNEXE"
                rows.append(dict(page_url=page_url, origin_url=href, seance_date=date_iso,
                                 role=role, delib_number=None, source_title=title,
                                 expected_title=naming.expected_title(role, date_iso, "",
                                                                      naming.base_from(title) or naming.base_from(href))))
    return rows, seen_rows


def discover(page_urls=None, start_year=2010, end_year=2025, timeout=60):
    """Renvoie (items, pages_report). Lève si une page renvoie 0 ligne (garde-fou)."""
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
        y = int(row["seance_date"][:4])
        if not (start_year <= y <= end_year):
            continue
        key = (row["seance_date"], row["role"], row["origin_url"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(row)
    return deduped, report
