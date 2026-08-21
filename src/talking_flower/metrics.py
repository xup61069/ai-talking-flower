from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import time


class MetricsStore:
    """延遲指標（ASR/TTFT/TTFA/回合總時）持久化，供 HUD 趨勢線查詢。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS turn_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    asr_ms REAL NOT NULL DEFAULT 0,
                    ttft_ms REAL NOT NULL DEFAULT 0,
                    ttfa_ms REAL NOT NULL DEFAULT 0,
                    total_ms REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'user'
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_turn_metrics_ts ON turn_metrics(ts)"
            )

    def add(self, *, asr_ms: float, ttft_ms: float, ttfa_ms: float, total_ms: float, source: str = "user") -> None:
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO turn_metrics(ts, asr_ms, ttft_ms, ttfa_ms, total_ms, source) VALUES (?, ?, ?, ?, ?, ?)",
                (now, float(asr_ms), float(ttft_ms), float(ttfa_ms), float(total_ms), source),
            )

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT ts, asr_ms, ttft_ms, ttfa_ms, total_ms, source FROM turn_metrics ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {
                "ts": r[0],
                "asr_ms": r[1],
                "ttft_ms": r[2],
                "ttfa_ms": r[3],
                "total_ms": r[4],
                "source": r[5],
            }
            for r in reversed(rows)  # 時間正序，方便畫趨勢
        ]

    def summary(self, limit: int = 100) -> dict:
        """最近 limit 筆的統計摘要。"""
        rows = self.recent(limit)
        if not rows:
            return {"count": 0}
        keys = ("asr_ms", "ttft_ms", "ttfa_ms", "total_ms")
        stats: dict[str, float] = {}
        for key in keys:
            values = [r[key] for r in rows if r[key]]
            stats[f"{key}_avg"] = round(sum(values) / len(values), 1) if values else 0.0
            stats[f"{key}_max"] = round(max(values), 1) if values else 0.0
        return {"count": len(rows), **stats}

    def cleanup(self, keep_days: int = 14) -> int:
        cutoff = time.time() - keep_days * 86400
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM turn_metrics WHERE ts < ?", (cutoff,))
            return cursor.rowcount

    def close(self) -> None:
        self._connection.close()
