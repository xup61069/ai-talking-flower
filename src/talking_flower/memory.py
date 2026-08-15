from __future__ import annotations

from pathlib import Path
import sqlite3
import threading


class ConversationMemory:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def add(self, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError(f"Invalid role: {role}")
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO messages(role, content) VALUES (?, ?)",
                (role, content.strip()),
            )

    def recent(self, turns: int) -> list[dict[str, str]]:
        limit = max(0, turns * 2)
        with self._lock:
            rows = self._connection.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    def close(self) -> None:
        self._connection.close()

