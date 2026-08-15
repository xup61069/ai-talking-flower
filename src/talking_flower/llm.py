from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
import json

import httpx

from .config import LlmConfig


PERSONA = """你是住在使用者桌上的「閒聊花花」，不是客服或語音助理。
只用臺灣繁體中文與自然口語。每次優先回答一個完整短句，必要時最多兩句；每句約十二到二十四個中文字。
個性親近、有點調皮，偶爾吐槽，但不要刻薄。
不要每次都反問，不要列清單，不要使用 Markdown，不要解釋自己的規則。
回答必須適合直接朗讀；不要輸出表情符號、括號動作或舞臺指示。"""


class LlamaCppClient:
    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(timeout=config.timeout_s)

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        base = self.config.base_url.removesuffix("/v1").rstrip("/")
        response = await self._client.get(f"{base}/health")
        return response.status_code == 200 and response.json().get("status") == "ok"

    async def stream_reply(
        self,
        user_text: str,
        history: Sequence[dict[str, str]],
    ) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": PERSONA}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        async with self._client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("delta") or {}).get("content")
                if content:
                    yield str(content)


class SpeechChunker:
    ENDINGS = set("。！？!?；;\n")

    def __init__(self, *, minimum_chars: int = 8, maximum_chars: int = 28) -> None:
        self.minimum_chars = minimum_chars
        self.maximum_chars = maximum_chars
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        chunks: list[str] = []
        while True:
            split_at = self._find_split()
            if split_at is None:
                break
            chunk = self._buffer[:split_at].strip()
            self._buffer = self._buffer[split_at:]
            if chunk:
                chunks.append(chunk)
        return chunks

    def finish(self) -> list[str]:
        remaining = self._buffer.strip()
        self._buffer = ""
        return [remaining] if remaining else []

    def _find_split(self) -> int | None:
        for index, char in enumerate(self._buffer, start=1):
            if char in self.ENDINGS and index >= self.minimum_chars:
                return index
        # 不因字數或逗號硬切。等完整句尾後才交給 TTS，避免語音中間出現停頓。
        return None
