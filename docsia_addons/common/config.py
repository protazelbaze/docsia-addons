"""Configuration par variables d'environnement (chargées depuis .env si présent)."""
import os
from pathlib import Path


def _load_dotenv():
    # Chargement minimal d'un .env à la racine du dépôt, sans dépendance externe.
    root = Path(__file__).resolve().parents[2]
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()


def env(name: str, default=None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and (val is None or val == ""):
        raise SystemExit(f"Variable d'environnement manquante: {name}")
    return val
