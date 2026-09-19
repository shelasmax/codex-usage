from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Открывает SQLite-базу Codex строго в режиме read-only."""

    path = Path(db_path).expanduser().resolve()

    if not path.exists():
        raise FileNotFoundError(f"Codex database not found: {path}")

    uri = f"file:{path}?mode=ro"

    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row

    return connection


def get_threads(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Возвращает все Codex threads."""

    return connection.execute(
        """
        SELECT
            id,
            rollout_path,
            created_at,
            updated_at,
            cwd,
            title,
            tokens_used,
            model,
            reasoning_effort
        FROM threads
        ORDER BY created_at
        """
    ).fetchall()


def get_spawn_edges(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Возвращает связи parent -> child между агентами."""

    return connection.execute(
        """
        SELECT
            parent_thread_id,
            child_thread_id,
            status
        FROM thread_spawn_edges
        """
    ).fetchall()