from __future__ import annotations

import asyncio
from collections import deque
import logging
import threading
import time


LOGGER = logging.getLogger(__name__)


class RestartRequired(Exception):
    """管線需要以新設定重建（用於 UI 調整需重啟的項目）。"""


class RuntimeControl:
    """Web UI 對執行中管線的控制旗標。"""

    def __init__(self) -> None:
        self._restart = asyncio.Event()

    def request_restart(self) -> None:
        self._restart.set()

    @property
    def restart_requested(self) -> bool:
        return self._restart.is_set()


class StatusBus:
    """狀態、逐字稿、音量、日誌的發佈/訂閱。

    publish 可由任何執行緒呼叫；WebSocket 訂閱者收到 JSON 事件。
    保留最近 HISTORY 筆事件，供新訂閱者補看。
    """

    HISTORY = 200

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._history: deque[dict] = deque(maxlen=self.HISTORY)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: dict) -> None:
        self._history.append(event)
        loop = self._loop
        if loop is None:
            return
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def history(self) -> list[dict]:
        return list(self._history)

    async def subscribe(self) -> asyncio.Queue[dict]:
        loop = asyncio.get_running_loop()
        self.attach_loop(loop)
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(queue)


class BusLogHandler(logging.Handler):
    """把 log 列送進 StatusBus。"""

    def __init__(self, bus: StatusBus) -> None:
        super().__init__()
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self._bus.publish({"type": "log", "time": time.time(), "message": message})
        except Exception:
            pass
