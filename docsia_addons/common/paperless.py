"""Client Paperless-ngx (API REST). Gère l'en-tête Host pour l'appel interne."""
import time
import requests
from .config import env


class Paperless:
    def __init__(self):
        self.base = env("PAPERLESS_URL", required=True).rstrip("/")
        self.token = env("PAPERLESS_TOKEN", required=True)
        self.host_header = env("PAPERLESS_HOST_HEADER", "")
        self.timeout = int(env("HTTP_TIMEOUT", "60"))
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Token {self.token}"})
        if self.host_header:
            self.s.headers.update({"Host": self.host_header})

    # --- custom fields ---
    def custom_fields(self) -> dict:
        """Renvoie {nom: {'id':.., 'data_type':..}} (pagination interne)."""
        out, page = {}, 1
        while True:
            r = self.s.get(f"{self.base}/api/custom_fields/",
                           params={"page": page, "page_size": 100}, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            for f in data.get("results", []):
                out[f["name"]] = {"id": f["id"], "data_type": f.get("data_type")}
            if not data.get("next"):
                break
            page += 1
        return out

    # --- documents ---
    def iter_documents(self, query: str | None = None, page_size: int = 100):
        """Pagine sur l'URL interne (ne suit pas le lien next absolu de Paperless,
        qui pointe vers l'URL publique et repasserait par Cloudflare Access)."""
        page = 1
        while True:
            params = {"page": page, "page_size": page_size}
            if query:
                params["query"] = query
            r = self.s.get(f"{self.base}/api/documents/", params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            for d in data.get("results", []):
                yield d
            if not data.get("next"):
                break
            page += 1

    def get_document(self, doc_id: int) -> dict:
        r = self.s.get(f"{self.base}/api/documents/{doc_id}/", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def existing_custom_values(doc: dict) -> dict:
        """{field_id: value} déjà posés sur le document."""
        out = {}
        for item in doc.get("custom_fields") or []:
            fid = item.get("field")
            if fid is not None:
                out[int(fid)] = item.get("value")
        return out

    def set_custom_fields(self, doc_id: int, existing: dict, new_by_id: dict) -> None:
        """Fusionne existant + nouveau et PATCH (Paperless remplace toute la liste)."""
        merged = dict(existing)
        for fid, val in new_by_id.items():
            if val not in (None, ""):
                merged[int(fid)] = val
        payload = {"custom_fields": [{"field": fid, "value": v}
                                     for fid, v in sorted(merged.items())]}
        r = self.s.patch(f"{self.base}/api/documents/{doc_id}/", json=payload, timeout=self.timeout)
        r.raise_for_status()

    # --- import + tâche ---
    def post_document(self, path, title: str, mime: str) -> str:
        """Soumet un document ; renvoie l'UUID de tâche."""
        from pathlib import Path
        p = Path(path)
        with p.open("rb") as fh:
            r = self.s.post(
                f"{self.base}/api/documents/post_document/",
                data={"title": title},
                files={"document": (p.name, fh, mime or "application/octet-stream")},
                timeout=max(self.timeout, 180),
            )
        r.raise_for_status()
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    def poll_task(self, task_uuid: str, tries: int = 30, delay: float = 2.0) -> dict:
        """Attend la fin d'une tâche ; renvoie {'status':.., 'document_id':..}."""
        for _ in range(tries):
            r = self.s.get(f"{self.base}/api/tasks/", params={"task_id": task_uuid}, timeout=self.timeout)
            r.raise_for_status()
            rows = r.json()
            rows = rows.get("results", rows) if isinstance(rows, dict) else rows
            if rows:
                t = rows[0]
                status = t.get("status")
                if status in ("SUCCESS", "FAILURE"):
                    return {"status": status,
                            "document_id": t.get("related_document"),
                            "error": t.get("result") if status == "FAILURE" else None}
            time.sleep(delay)
        return {"status": "TIMEOUT", "document_id": None, "error": "poll timeout"}
