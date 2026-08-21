from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import threading

import numpy as np


VECTOR_DIM = 384


def embed_text(text: str, dim: int = VECTOR_DIM) -> np.ndarray:
    """輕量 hash embedding：字符 bigram 哈希到固定維度，L2 正規化。

    無外部依賴，確定性高，適合本機小資料量的語意召回（非 SOTA，但比 LIKE 好）。
    中文按字符切 bigram，英文按詞切會更準，但此處統一以字符 bigram 簡化。
    """
    vec = np.zeros(dim, dtype=np.float32)
    cleaned = text.strip()
    if not cleaned:
        return vec
    # 產生 bigram + 單字符（邊界）
    grams: list[str] = []
    for i in range(len(cleaned)):
        grams.append(cleaned[i])
        if i + 1 < len(cleaned):
            grams.append(cleaned[i : i + 2])
    for gram in grams:
        h = hashlib.md5(gram.encode("utf-8")).digest()
        idx = int.from_bytes(h[:2], "little") % dim
        # 用 gram 長度作權重：bigram 權重略高
        weight = 1.5 if len(gram) == 2 else 1.0
        vec[idx] += weight
    norm = float(np.linalg.norm(vec))
    if norm > 1e-9:
        vec /= norm
    return vec


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
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS message_vectors (
                    message_id INTEGER PRIMARY KEY,
                    embedding BLOB NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
                )
                """
            )

    def add(self, role: str, content: str) -> int:
        if role not in {"user", "assistant"}:
            raise ValueError(f"Invalid role: {role}")
        text = content.strip()
        vec = embed_text(text)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO messages(role, content) VALUES (?, ?)",
                (role, text),
            )
            msg_id = int(cursor.lastrowid)
            try:
                self._connection.execute(
                    "INSERT INTO message_vectors(message_id, embedding) VALUES (?, ?)",
                    (msg_id, vec.tobytes()),
                )
            except Exception:
                pass
        return msg_id

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

    def list_all(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        rows.reverse()
        return [
            {"id": row[0], "role": row[1], "content": row[2], "created_at": row[3]}
            for row in rows
        ]

    def search(self, keyword: str, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, role, content, created_at FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{keyword.strip()}%", limit),
            ).fetchall()
        return [
            {"id": row[0], "role": row[1], "content": row[2], "created_at": row[3]}
            for row in rows
        ]

    def delete(self, message_id: int) -> bool:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM message_vectors WHERE message_id = ?", (message_id,))
            cursor = self._connection.execute(
                "DELETE FROM messages WHERE id = ?", (message_id,)
            )
            return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock:
            (count,) = self._connection.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()
        return int(count)

    def search_vector(self, query: str, limit: int = 5) -> list[dict]:
        """向量語意搜尋：對 query 做同樣 hash embedding，暴力 cosine 检索。"""
        qvec = embed_text(query)
        qnorm = float(np.linalg.norm(qvec))
        if qnorm < 1e-9:
            return []
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.id, m.role, m.content, m.created_at, v.embedding
                FROM messages m
                JOIN message_vectors v ON v.message_id = m.id
                ORDER BY m.id DESC LIMIT 1000
                """
            ).fetchall()
        scored: list[tuple[float, dict]] = []
        for row in rows:
            msg_id, role, content, created_at, blob = row
            try:
                vec = np.frombuffer(blob, dtype=np.float32)
                if vec.size != VECTOR_DIM:
                    continue
                # cosine = dot (both L2 normalized)
                score = float(np.dot(qvec, vec))
            except Exception:
                continue
            if score > 0.05:  # 過濾極低相似度
                scored.append((score, {"id": msg_id, "role": role, "content": content, "created_at": created_at, "score": round(score, 3)}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[: max(1, int(limit))]]

    def clear(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM message_vectors")
            self._connection.execute("DELETE FROM messages")

    def close(self) -> None:
        self._connection.close()

