from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging

import numpy as np
import sounddevice as sd

from .config import AudioConfig


LOGGER = logging.getLogger(__name__)


def _hostapi_name(device: dict) -> str:
    return str(sd.query_hostapis(device["hostapi"])["name"])


def resolve_device(name: str, hostapi: str, *, input_device: bool) -> int:
    wanted_name = name.casefold()
    wanted_host = hostapi.casefold()
    candidates: list[tuple[int, dict]] = []
    for index, device in enumerate(sd.query_devices()):
        channel_key = "max_input_channels" if input_device else "max_output_channels"
        if int(device[channel_key]) <= 0:
            continue
        if wanted_name and wanted_name not in str(device["name"]).casefold():
            continue
        if wanted_host and wanted_host not in _hostapi_name(device).casefold():
            continue
        candidates.append((index, device))

    if not candidates:
        kind = "輸入" if input_device else "輸出"
        raise RuntimeError(f"找不到{kind}裝置：{name!r}（{hostapi or '任何介面'}）")
    return candidates[0][0]


def list_audio_devices() -> str:
    lines: list[str] = []
    for index, device in enumerate(sd.query_devices()):
        inputs = int(device["max_input_channels"])
        outputs = int(device["max_output_channels"])
        if inputs <= 0 and outputs <= 0:
            continue
        lines.append(
            f"{index:3d}  in={inputs} out={outputs} "
            f"rate={int(device['default_samplerate'])}  "
            f"{device['name']}  [{_hostapi_name(device)}]"
        )
    return "\n".join(lines)


class AudioInput:
    def __init__(self, config: AudioConfig) -> None:
        self.config = config
        self.device_index = resolve_device(
            config.input_device,
            config.input_hostapi,
            input_device=True,
        )
        device = sd.query_devices(self.device_index)
        if config.input_channel >= int(device["max_input_channels"]):
            raise RuntimeError(
                f"輸入聲道 {config.input_channel} 超出裝置可用範圍 "
                f"0..{int(device['max_input_channels']) - 1}"
            )
        self._device_channels = int(device["max_input_channels"])
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=100)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: sd.InputStream | None = None

    def _enqueue(self, frame: np.ndarray) -> None:
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            LOGGER.warning("音訊處理落後，丟棄一個輸入區塊")

    def _callback(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        if status:
            LOGGER.warning("音訊輸入狀態：%s", status)
        if self._loop is None:
            return
        channel = self.config.input_channel
        frame = np.asarray(indata[:, channel], dtype=np.float32).copy()
        self._loop.call_soon_threadsafe(self._enqueue, frame)

    async def __aenter__(self) -> "AudioInput":
        self._loop = asyncio.get_running_loop()
        blocksize = int(self.config.sample_rate * self.config.block_ms / 1000)
        self._stream = sd.InputStream(
            device=self.device_index,
            samplerate=self.config.sample_rate,
            blocksize=blocksize,
            channels=self._device_channels,
            dtype="float32",
            callback=self._callback,
            latency="low",
        )
        self._stream.start()
        device = sd.query_devices(self.device_index)
        LOGGER.info(
            "開始監聽：%s [%s]，聲道 %d",
            device["name"],
            _hostapi_name(device),
            self.config.input_channel + 1,
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    async def frames(self) -> AsyncIterator[np.ndarray]:
        while True:
            yield await self._queue.get()

    def drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break


class BlockResampler:
    def __init__(self, source_rate: int, target_rate: int) -> None:
        self.source_rate = source_rate
        self.target_rate = target_rate
        if source_rate % target_rate != 0:
            raise ValueError("第一版只支援整數比例降採樣")
        self.factor = source_rate // target_rate

    def process(self, frame: np.ndarray) -> np.ndarray:
        if self.factor == 1:
            return frame.astype(np.float32, copy=False)
        length = len(frame) - (len(frame) % self.factor)
        if length <= 0:
            return np.empty(0, dtype=np.float32)
        reshaped = frame[:length].reshape(-1, self.factor)
        return reshaped.mean(axis=1, dtype=np.float32)

