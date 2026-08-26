"""Connexion PostgreSQL (base Paperless, schéma de suivi des récoltes)."""
import psycopg
from .config import env


def connect():
    return psycopg.connect(
        host=env("PGHOST", "127.0.0.1"),
        port=int(env("PGPORT", "5432")),
        dbname=env("PGDATABASE", required=True),
        user=env("PGUSER", required=True),
        password=env("PGPASSWORD", required=True),
        autocommit=False,
    )


def schema() -> str:
    return env("PG_SCHEMA", "recolte")
