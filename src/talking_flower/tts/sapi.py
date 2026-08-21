"""Windows SAPI TTS：離線備援後端。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging

from ..config import TtsConfig
from .base import clean_speech_text


LOGGER = logging.getLogger(__name__)


class WindowsSapiTTS:
    def __init__(self, config: TtsConfig) -> None:
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flower-tts")
        self._voice = None

    def _ensure_voice(self):
        if self._voice is not None:
            return self._voice
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        for token in voice.GetVoices():
            if self.config.voice.casefold() in token.GetDescription().casefold():
                voice.Voice = token
                break
        else:
            LOGGER.warning("找不到指定 SAPI 聲音 %s，使用系統預設", self.config.voice)
        voice.Rate = self.config.rate
        voice.Volume = self.config.volume
        self._voice = voice
        return voice

    def _speak_sync(self, text: str) -> None:
        voice = self._ensure_voice()
        voice.Speak(text)

    async def speak(self, text: str, on_first_byte=None, style: str = "") -> None:
        _ = style  # SAPI 不支援動態風格，忽略
        cleaned = clean_speech_text(text)
        if not cleaned.strip():
            return
        if on_first_byte is not None:
            try:
                on_first_byte()
            except Exception:
                pass
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._speak_sync, cleaned)

    async def health(self) -> bool:
        return True

    async def begin_turn(self) -> None:
        return

    async def end_turn(self) -> None:
        return

    def abort_turn(self) -> None:
        return

    async def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
