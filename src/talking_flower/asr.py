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


class StreamingSession:
    """真串流辨識 session：邊錄邊餵 16k 幀，UI 即時顯示部分文字。

    分塊語義與批次模式一致（湊滿 stride 才推論、結尾 is_final=True），
    因此最終文字與整段轉錄幾乎相同；差異僅在運算攤到說話過程中進行。
    """

    def __init__(self, recognizer: "FunASRStreamingRecognizer") -> None:
        self._recognizer = recognizer
        chunk_size = list(recognizer.config.chunk_size)
        self._stride = max(1, int(chunk_size[1] * 960))
        self._cache: dict = {}
        self._buffer = np.empty(0, dtype=np.float32)
        self._transcript = ""

    @property
    def text(self) -> str:
        return self._transcript

    def _infer(self, chunk: np.ndarray, is_final: bool) -> str:
        model = self._recognizer.model
        if model is None:
            raise RuntimeError("ASR model is not loaded")
        result = model.generate(
            input=chunk,
            cache=self._cache,
            is_final=is_final,
            chunk_size=list(self._recognizer.config.chunk_size),
            encoder_chunk_look_back=self._recognizer.config.encoder_chunk_look_back,
            decoder_chunk_look_back=self._recognizer.config.decoder_chunk_look_back,
            disable_pbar=True,
        )
        piece = normalize_transcript(str(result[0].get("text", ""))) if result else ""
        if not piece:
            return self._transcript
        # 與批次模式相同的合併規則：串流輸出可能是累積全文或增量
        if piece.startswith(self._transcript):
            self._transcript = piece
        elif not self._transcript.endswith(piece):
            self._transcript += piece
        return self._transcript

    def feed(self, frame: np.ndarray) -> str:
        """餵入一個 16k 音框；湊滿 stride 才推論，回傳目前部分文字。"""
        self._buffer = np.concatenate([self._buffer, frame.astype(np.float32, copy=False)])
        while len(self._buffer) >= self._stride:
            chunk = self._buffer[: self._stride]
            self._buffer = self._buffer[self._stride :]
            self._infer(chunk, False)
        return self._transcript

    def finish(self) -> str:
        """收尾：剩餘緩衝以 is_final=True 推論後回傳完整文字並重置。"""
        try:
            if len(self._buffer) > 0:
                chunk = self._buffer
            else:
                # 快取內仍有未沖刷的上下文；以短靜音觸發最終解碼
                chunk = np.zeros(max(160, self._stride // 8), dtype=np.float32)
            self._buffer = np.empty(0, dtype=np.float32)
            self._infer(chunk, True)
            return self._transcript.strip()
        finally:
            self.reset()

    def reset(self) -> None:
        self._cache = {}
        self._buffer = np.empty(0, dtype=np.float32)
        self._transcript = ""


# process 級模型快取，避免 RestartRequired 重建時重複載入 Paraformer
_MODEL_CACHE: dict[tuple[str, str], object] = {}


class FunASRStreamingRecognizer:
    _streaming_supported = True

    def __init__(self, config: AsrConfig) -> None:
        self.config = config
        self._model = None

    @property
    def model(self):
        return self._model

    def create_stream(self) -> StreamingSession:
        return StreamingSession(self)

    async def load(self) -> None:
        if self._model is not None:
            return
        cache_key = (self.config.model, self.config.device)
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            self._model = cached
            LOGGER.info("沿用已快取的 ASR 模型：%s", self.config.model)
            return
        LOGGER.info("載入語音辨識模型：%s（第一次可能需要下載）", self.config.model)
        await asyncio.to_thread(self._load_sync)
        _MODEL_CACHE[cache_key] = self._model
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
