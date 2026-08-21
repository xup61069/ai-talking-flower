"""技能插件系統：register(pattern/handler) 即插即用，VoiceCommander 為首個內建集合。

新技能三步驟：
1. 在 skills/ 下新增模組，寫 handler(text, controller) -> CommandResult | None
2. 用 @register_skill("名稱") 裝飾
3. 在 skills/__init__.py 的 load_builtin_skills() import 該模組

handler 回傳 None 或 handled=False 表示不處理，交給下一個技能或 LLM。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from ..commands import CommandResult


if TYPE_CHECKING:
    from ..controller import FlowerController

LOGGER = logging.getLogger(__name__)

SkillHandler = Callable[[str, "FlowerController"], "CommandResult | None"]


class SkillRegistry:
    """依註冊順序嘗試各技能；第一個 handled=True 的結果即回傳。"""

    def __init__(self) -> None:
        self._skills: list[tuple[str, SkillHandler]] = []

    def register(self, name: str, handler: SkillHandler | None = None):
        """可當裝飾器 @registry.register("天氣")，或直接 register("天氣", fn)。"""
        if handler is not None:
            self._add(name, handler)
            return handler

        def decorator(fn: SkillHandler):
            self._add(name, fn)
            return fn

        return decorator

    def _add(self, name: str, handler: SkillHandler) -> None:
        self._skills.append((name, handler))
        LOGGER.debug("已註冊技能：%s", name)

    def try_execute(self, user_text: str, controller) -> CommandResult:
        text = user_text.strip()
        if not text:
            return CommandResult(handled=False)
        for name, handler in self._skills:
            try:
                result = handler(text, controller)
            except Exception:
                LOGGER.exception("技能 %s 執行失敗（略過）", name)
                continue
            if result is not None and result.handled:
                return result
        return CommandResult(handled=False)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self._skills]


# 全域預設 registry；controller 透過 VoiceCommander 使用它
DEFAULT_REGISTRY = SkillRegistry()

_builtin_loaded = False


def register_skill(name: str, registry: SkillRegistry | None = None):
    """模組層級裝飾器：把 handler 掛到 DEFAULT_REGISTRY。"""
    target = registry if registry is not None else DEFAULT_REGISTRY
    return target.register(name)


def load_builtin_skills() -> SkillRegistry:
    """載入內建技能（冪等：以旗標控制，不依賴註冊狀態——
    外部模組可能先單獨 import 某技能檔而預先註冊部分項目）。"""
    global _builtin_loaded
    if not _builtin_loaded:
        _builtin_loaded = True
        from . import builtin  # noqa: F401  註冊時間/提醒/性格/音量/語速
        from . import weather  # noqa: F401  註冊天氣（無 key 時自動讓位給 LLM）

    return DEFAULT_REGISTRY
