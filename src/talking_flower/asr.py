from __future__ import annotations

import asyncio
import logging
import re
from typing import Protocol

import numpy as np
from opencc import OpenCC

from .config import AsrConfig


LOGGER = logging.getLogger(__name__)
TAG_PATTERN = re.compile(r"<\|[^|]+\|>")
TRADITIONAL_CONVERTER = OpenCC("s2twp")


def normalize_transcript(text: str) -> str:
    text = TAG_PATTERN.sub("", text).strip()
    return TRADITIONAL_CONVERTER.convert(text).strip()


class SpeechRecognizer(Protocol):
    async def load(self) -> None: ...
    async def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


class FunASRStreamingRecognizer:
    def __init__(self, config: AsrConfig) -> None:
        self.config = config
        self._model = None

    async def load(self) -> None:
        if self._model is not None:
            return
        LOGGER.info("載入語音辨識模型：%s（第一次可能需要下載）", self.config.model)
        await asyncio.to_thread(self._load_sync)
        LOGGER.info("語音辨識模型已就緒")

    def _load_sync(self) -> None:
        from funasr import AutoModel

        self._model = AutoModel(
            model=self.config.model,
            device=self.config.device,
            disable_update=True,
        )

    async def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        await self.load()
        return await asyncio.to_thread(self._transcribe_sync, audio, sample_rate)

    def _transcribe_sync(self, audio: np.ndarray, sample_rate: int) -> str:
        if sample_rate != 16000:
            raise ValueError(f"FunASR 輸入必須是 16kHz，目前為 {sample_rate}")
        if self._model is None:
            raise RuntimeError("ASR model is not loaded")

        chunk_size = list(self.config.chunk_size)
        stride = chunk_size[1] * 960
        cache: dict = {}
        transcript = ""
        for offset in range(0, len(audio), stride):
            chunk = audio[offset : offset + stride]
            is_final = offset + stride >= len(audio)
            result = self._model.generate(
                input=chunk,
                cache=cache,
                is_final=is_final,
                chunk_size=chunk_size,
                encoder_chunk_look_back=self.config.encoder_chunk_look_back,
                decoder_chunk_look_back=self.config.decoder_chunk_look_back,
                disable_pbar=True,
            )
            if not result:
                continue
            piece = normalize_transcript(str(result[0].get("text", "")))
            if not piece:
                continue
            if piece.startswith(transcript):
                transcript = piece
            elif not transcript.endswith(piece):
                transcript += piece
        return transcript.strip()


def create_recognizer(config: AsrConfig) -> SpeechRecognizer:
    if config.backend == "funasr_streaming":
        return FunASRStreamingRecognizer(config)
    raise ValueError(f"不支援的 ASR backend：{config.backend}")
