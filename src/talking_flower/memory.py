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

    def older_than(self, turns: int) -> list[dict[str, str]]:
        """回傳超過最近 turns 輪的更早訊息（依時間先後），供摘要使用。"""
        skip = max(0, turns * 2)
        with self._lock:
            rows = self._connection.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT -1 OFFSET ?",
                (skip,),
            ).fetchall()
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    def list_all(self) -> list[dict[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT role, content, created_at FROM messages ORDER BY id"
            ).fetchall()
        return [
            {"role": role, "content": content, "created_at": created_at}
            for role, content, created_at in rows
        ]

    def count(self) -> int:
        with self._lock:
            (count,) = self._connection.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()
        return int(count)

    def clear(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM messages")

    def close(self) -> None:
        self._connection.close()

