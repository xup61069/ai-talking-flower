from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
import time


@dataclass
class Reminder:
    id: int
    text: str
    trigger_at: float
    created_at: float
    spoken: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "trigger_at": self.trigger_at,
            "created_at": self.created_at,
            "spoken": self.spoken,
            "due_in_s": max(0, int(self.trigger_at - time.time())),
        }


class ReminderScheduler:
    """管理定時提醒事項，支援持久化到 SQLite 並線程安全取出到期項目。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    trigger_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    spoken INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def add(self, text: str, in_seconds: float) -> Reminder:
        now = time.time()
        trigger_at = now + max(0.0, float(in_seconds))
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO reminders(text, trigger_at, created_at, spoken) VALUES (?, ?, ?, 0)",
                (text.strip(), trigger_at, now),
            )
            reminder_id = cursor.lastrowid
        return Reminder(
            id=int(reminder_id),
            text=text.strip(),
            trigger_at=trigger_at,
            created_at=now,
            spoken=False,
        )

    def list_active(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, text, trigger_at, created_at, spoken FROM reminders WHERE spoken = 0 ORDER BY trigger_at ASC"
            ).fetchall()
        now = time.time()
        return [
            {
                "id": row[0],
                "text": row[1],
                "trigger_at": row[2],
                "created_at": row[3],
                "spoken": bool(row[4]),
                "due_in_s": max(0, int(row[2] - now)),
            }
            for row in rows
        ]

    def list_all(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, text, trigger_at, created_at, spoken FROM reminders ORDER BY id DESC LIMIT 50"
            ).fetchall()
        now = time.time()
        return [
            {
                "id": row[0],
                "text": row[1],
                "trigger_at": row[2],
                "created_at": row[3],
                "spoken": bool(row[4]),
                "due_in_s": max(0, int(row[2] - now)),
            }
            for row in rows
        ]

    def delete(self, reminder_id: int) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM reminders WHERE id = ?", (reminder_id,)
            )
            return cursor.rowcount > 0

    def clear(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM reminders")

    def pop_due(self) -> list[Reminder]:
        now = time.time()
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT id, text, trigger_at, created_at FROM reminders WHERE spoken = 0 AND trigger_at <= ?",
                (now,),
            ).fetchall()
            if not rows:
                return []
            ids = [row[0] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            self._connection.execute(
                f"UPDATE reminders SET spoken = 1 WHERE id IN ({placeholders})", ids
            )
        return [
            Reminder(id=r[0], text=r[1], trigger_at=r[2], created_at=r[3], spoken=True)
            for r in rows
        ]

    def close(self) -> None:
        self._connection.close()
