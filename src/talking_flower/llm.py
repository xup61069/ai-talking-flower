from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
import json

import httpx

from .config import LlmConfig


class LlamaCppClient:
    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(timeout=config.timeout_s)

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            base = self.config.base_url.removesuffix("/v1").rstrip("/")
            response = await self._client.get(f"{base}/health")
            return response.status_code == 200 and response.json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    async def _complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": False,
        }
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        response = await self._client.post(url, json=payload)
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            return ""
        return str((choices[0].get("message") or {}).get("content", "")).strip()

    async def stream_reply(
        self,
        user_text: str,
        history: Sequence[dict[str, str]],
        *,
        persona: str,
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": persona}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
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

    async def summarize(self, messages: Sequence[dict[str, str]]) -> str:
        """把舊對話壓成一小段中文摘要，注入下一輪的 system prompt。"""
        if not messages:
            return ""
        lines = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        prompt = (
            "以下是更早的對話。把它壓成最多三句的中文摘要，"
            "保留重要的使用者事實與兩人之間關係的變化，不要評論。\n"
            "內容：\n" + lines[-4000:] + "\n摘要："
        )
        try:
            return await self._complete(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                top_p=0.9,
                max_tokens=120,
            )
        except httpx.HTTPError:
            return ""


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
