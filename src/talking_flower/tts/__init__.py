"""TTS 套件：backend 抽象與工廠。

對外介面與舊 tts.py 相同：create_tts、TextToSpeech、clean_speech_text、
WindowsSapiTTS、HttpPcmTTS、_PcmPlayer（tests 相容別名）。
"""
from __future__ import annotations

from ..aec import BypassEchoCanceller, EchoCanceller
from ..config import AudioConfig, TtsConfig
from ..settings import LiveSettings
from .base import TextToSpeech, clean_speech_text
from .http_pcm import HttpPcmTTS
from .pcm_player import PcmPlayer  # noqa: F401
from .pcm_player import _PcmPlayer  # noqa: F401（別名供 tests）
from .sapi import WindowsSapiTTS

__all__ = [
    "TextToSpeech",
    "clean_speech_text",
    "WindowsSapiTTS",
    "HttpPcmTTS",
    "PcmPlayer",
    "_PcmPlayer",
    "create_tts",
]


def create_tts(
    config: TtsConfig,
    audio: AudioConfig,
    aec: EchoCanceller | None = None,
    live: LiveSettings | None = None,
    bus=None,
) -> TextToSpeech:
    if config.backend == "windows_sapi":
        return WindowsSapiTTS(config)
    if config.backend in {"cosyvoice", "kokoro"}:
        return HttpPcmTTS(config, audio, aec or BypassEchoCanceller(audio.sample_rate), live, bus)
    raise ValueError(f"不支援的 TTS backend：{config.backend}")
