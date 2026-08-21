"""共用型別：AppContext 與 Token 雜湊。"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

from ..bus import RuntimeControl, StatusBus
from ..controller import FlowerController
from ..memory import ConversationMemory
from ..settings import LiveSettings, SettingsStore


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class AppContext:
    store: SettingsStore
    live: LiveSettings
    bus: StatusBus
    runtime: RuntimeControl
    controller: FlowerController | None = None
    memory: ConversationMemory | None = None
    restart_note: str = field(default="")
