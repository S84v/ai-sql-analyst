"""Database configuration and connection helpers for AI SQL Analyst."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# db.py lives at <repo>/backend/src/ai_sql_analyst/db.py.
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DatabaseConfig:
    """Connection settings sourced from the environment, never hardcoded."""

    dbname: str
    user: str
    password: str
    host: str
    port: int

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Build a config from the repo-root .env and process environment.

        The process environment takes precedence over .env values.
        """
        load_dotenv(REPO_ROOT / ".env")
        required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        return cls(
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
        )


def connect(config: DatabaseConfig | None = None) -> psycopg.Connection:
    """Open a PostgreSQL connection (defaults to the environment config)."""
    config = config or DatabaseConfig.from_env()
    return psycopg.connect(
        dbname=config.dbname,
        user=config.user,
        password=config.password,
        host=config.host,
        port=config.port,
    )
