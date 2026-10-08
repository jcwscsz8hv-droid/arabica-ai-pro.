"""Local SQLite history and user-approved glossary, no remote services."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS glossary (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL,
 target TEXT NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('ru-ar','ar-ru')),
 domain TEXT NOT NULL DEFAULT 'general',
 notes TEXT NOT NULL DEFAULT '',
 UNIQUE(source,direction,domain)
);
CREATE TABLE IF NOT EXISTS history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ts TEXT NOT NULL,
 source TEXT NOT NULL,
 target TEXT NOT NULL,
 direction TEXT NOT NULL,
 mode TEXT NOT NULL
);
"""


class LocalStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        finally:
            conn.close()

    def put_term(self, source: str, target: str, direction: str, domain: str = "general", notes: str = "") -> None:
        if direction not in ("ru-ar", "ar-ru") or not source.strip() or not target.strip():
            raise ValueError("Необходимо заполнить термин, перевод и направление")
        with self._connect() as conn:
            conn.execute("""INSERT INTO glossary(source,target,direction,domain,notes)
                VALUES (?,?,?,?,?) ON CONFLICT(source,direction,domain) DO UPDATE SET
                target=excluded.target,notes=excluded.notes""",
                (source.strip(), target.strip(), direction, domain, notes))

    def terms(self, direction: str, limit: int = 500) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT source,target,direction,domain,notes FROM glossary WHERE direction=? ORDER BY source LIMIT ?", (direction, limit)).fetchall()
        return [dict(r) for r in rows]

    def log(self, source: str, target: str, direction: str, mode: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO history(ts,source,target,direction,mode) VALUES (?,?,?,?,?)",
                 (datetime.now(timezone.utc).isoformat(),source,target,direction,mode))

    def recent(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT ts,source,target,direction,mode FROM history ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        return [dict(r) for r in rows]

    def clear_history(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history")
