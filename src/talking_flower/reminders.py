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
    repeat_daily_hhmm: str = ""  # "HH:MM" 表示每天重複；空字串為一次性

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "trigger_at": self.trigger_at,
            "created_at": self.created_at,
            "spoken": self.spoken,
            "repeat_daily_hhmm": self.repeat_daily_hhmm,
            "due_in_s": max(0, int(self.trigger_at - time.time())),
        }


class ReminderScheduler:
    """管理定時提醒事項，支援持久化到 SQLite、線程安全取出到期項目與每日重複。"""

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
            self._migrate()

    def _migrate(self) -> None:
        cols = [row[1] for row in self._connection.execute("PRAGMA table_info(reminders)")]
        if "repeat_daily_hhmm" not in cols:
            self._connection.execute("ALTER TABLE reminders ADD COLUMN repeat_daily_hhmm TEXT NOT NULL DEFAULT ''")

    def add(self, text: str, in_seconds: float, *, repeat_daily_hhmm: str = "") -> Reminder:
        now = time.time()
        trigger_at = now + max(0.0, float(in_seconds))
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO reminders(text, trigger_at, created_at, spoken, repeat_daily_hhmm) VALUES (?, ?, ?, 0, ?)",
                (text.strip(), trigger_at, now, repeat_daily_hhmm),
            )
            reminder_id = cursor.lastrowid
        return Reminder(
            id=int(reminder_id),
            text=text.strip(),
            trigger_at=trigger_at,
            created_at=now,
            spoken=False,
            repeat_daily_hhmm=repeat_daily_hhmm,
        )

    def add_absolute(self, text: str, trigger_at: float, *, repeat_daily_hhmm: str = "") -> Reminder:
        """以絕對時間戳新增提醒（供絕對時間 parser 使用）。"""
        now = time.time()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO reminders(text, trigger_at, created_at, spoken, repeat_daily_hhmm) VALUES (?, ?, ?, 0, ?)",
                (text.strip(), float(trigger_at), now, repeat_daily_hhmm),
            )
            reminder_id = cursor.lastrowid
        return Reminder(
            id=int(reminder_id),
            text=text.strip(),
            trigger_at=float(trigger_at),
            created_at=now,
            spoken=False,
            repeat_daily_hhmm=repeat_daily_hhmm,
        )

    def list_active(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, text, trigger_at, created_at, spoken, repeat_daily_hhmm FROM reminders WHERE spoken = 0 ORDER BY trigger_at ASC"
            ).fetchall()
        now = time.time()
        return [
            {
                "id": row[0],
                "text": row[1],
                "trigger_at": row[2],
                "created_at": row[3],
                "spoken": bool(row[4]),
                "repeat_daily_hhmm": row[5] or "",
                "due_in_s": max(0, int(row[2] - now)),
            }
            for row in rows
        ]

    def list_all(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, text, trigger_at, created_at, spoken, repeat_daily_hhmm FROM reminders ORDER BY id DESC LIMIT 50"
            ).fetchall()
        now = time.time()
        return [
            {
                "id": row[0],
                "text": row[1],
                "trigger_at": row[2],
                "created_at": row[3],
                "spoken": bool(row[4]),
                "repeat_daily_hhmm": row[5] or "",
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
                "SELECT id, text, trigger_at, created_at, repeat_daily_hhmm FROM reminders WHERE spoken = 0 AND trigger_at <= ?",
                (now,),
            ).fetchall()
            if not rows:
                return []
            one_time_ids: list[int] = []
            results: list[Reminder] = []
            for r in rows:
                reminder = Reminder(
                    id=r[0], text=r[1], trigger_at=r[2], created_at=r[3], spoken=True,
                    repeat_daily_hhmm=r[4] or "",
                )
                results.append(reminder)
                if not (r[4] or "").strip():
                    one_time_ids.append(r[0])
            if one_time_ids:
                placeholders = ",".join("?" for _ in one_time_ids)
                self._connection.execute(
                    f"UPDATE reminders SET spoken = 1 WHERE id IN ({placeholders})", one_time_ids
                )
        # 每日重複：排程下一次觸發（在鎖外，add 會自行拿鎖）
        for reminder in results:
            if not reminder.repeat_daily_hhmm:
                continue
            try:
                hh, mm = reminder.repeat_daily_hhmm.split(":", 1)
                import datetime as _dt

                target = _dt.datetime.now().replace(
                    hour=int(hh), minute=int(mm), second=0, microsecond=0
                )
                next_at = target.timestamp()
                if next_at <= time.time():
                    next_at += 86400
                self.reschedule(reminder.id, next_at)
            except Exception:
                pass
        return [r for r in results]

    def reschedule(self, reminder_id: int, trigger_at: float) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE reminders SET spoken = 0, trigger_at = ? WHERE id = ?",
                (float(trigger_at), int(reminder_id)),
            )

    def next_due_in(self) -> float | None:
        """回傳距離下一個未響提醒的秒數，未找到回傳 None。"""
        with self._lock:
            row = self._connection.execute(
                "SELECT trigger_at FROM reminders WHERE spoken = 0 ORDER BY trigger_at ASC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return max(0.0, float(row[0]) - time.time())

    def cleanup_old(self, days: int = 7) -> int:
        """刪除已響且超過 days 天的舊提醒，回傳刪除筆數。"""
        cutoff = time.time() - days * 86400
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM reminders WHERE spoken = 1 AND trigger_at < ?", (cutoff,)
            )
            return cursor.rowcount

    def close(self) -> None:
        self._connection.close()
