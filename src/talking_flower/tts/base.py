"""TTS 共用基礎：文字淨化與 TextToSpeech Protocol。"""
from __future__ import annotations

import re
from typing import Protocol

EMOJI_PATTERN = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]+",
    flags=re.UNICODE,
)


def clean_speech_text(text: str) -> str:
    """過濾 Markdown 標記、Emoji、舞臺動作指示、角色前綴與提示詞殘留，回傳適合直接朗讀的純文字。"""
    if not text:
        return ""
    # 去除特殊標籤如 <|endofprompt|> 等
    text = re.sub(r"<\|[^|>]*\|>", "", text)
    # 去除程式碼區塊與 Markdown
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 去除括號內動作或舞臺指示
    text = re.sub(r"（[^）]*）", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"【[^】]*】", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    # 去除常見的 LLM 角色自稱與回覆標頭前綴（例如「花花：」、「回答：」等）
    text = re.sub(r"^(?:花花|助理|Assistant|AI|模型|回答|回覆)\s*[：:]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:你注意到使用者已經安靜|主動說一句適合此刻的話)[^：:。\n]*[：:。\n]\s*", "", text)
    text = EMOJI_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


class TextToSpeech(Protocol):
    async def speak(self, text: str, on_first_byte=None) -> None: ...
    async def health(self) -> bool: ...
    async def close(self) -> None: ...
    async def begin_turn(self) -> None: ...
    async def end_turn(self) -> None: ...
    def abort_turn(self) -> None: ...
