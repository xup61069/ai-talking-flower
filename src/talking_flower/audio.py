from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging

import numpy as np
import sounddevice as sd

from .config import AudioConfig


LOGGER = logging.getLogger(__name__)

# ── Backend 抽象：SoundDevice / Null（供 Linux CI 無音訊裝置時跑通管線） ──


class AudioBackend:
    """音訊後端抽象；SoundDevice 為真實 PortAudio，Null 為靜音回退。"""

    kind: str = "base"

    def list_devices(self) -> list[dict]:
        raise NotImplementedError

    def resolve_input_device(self, name: str, hostapi: str) -> int:
        raise NotImplementedError

    def create_input_stream(self, *args, **kwargs):
        raise NotImplementedError


class SoundDeviceBackend(AudioBackend):
    kind = "sounddevice"

    def list_devices(self) -> list[dict]:
        return list(sd.query_devices())

    def resolve_input_device(self, name: str, hostapi: str) -> int:
        return resolve_device(name, hostapi, input_device=True)

    def create_input_stream(self, *args, **kwargs):
        return sd.InputStream(*args, **kwargs)


class NullBackend(AudioBackend):
    """無音訊裝置時的靜音後端：list_devices 回空，InputStream 產生零幀。"""

    kind = "null"

    def list_devices(self) -> list[dict]:
        return []

    def resolve_input_device(self, name: str, hostapi: str) -> int:
        return -1  # 虛擬裝置索引

    def create_input_stream(self, *args, **kwargs):
        # 回傳一個會在 start() 後每 block_ms 產生零幀的 dummy，介面與 sd.InputStream 一致
        samplerate = kwargs.get("samplerate", 48000)
        blocksize = kwargs.get("blocksize", 960)
        callback = kwargs.get("callback")
        channels = kwargs.get("channels", 1)

        class _NullStream:
            def __init__(self, **kw):
                self.samplerate = kw.get("samplerate", samplerate)
                self.blocksize = kw.get("blocksize", blocksize)
                self.callback = kw.get("callback", callback)
                self.channels = kw.get("channels", channels)
                self._running = False
                self._thread = None

            def start(self):
                import threading
                import time as _time

                self._running = True

                def _loop():
                    import numpy as _np

                    while self._running:
                        data = _np.zeros((self.blocksize, self.channels), dtype="float32")
                        try:
                            self.callback(data, self.blocksize, None, None)
                        except Exception:
                            pass
                        _time.sleep(self.blocksize / self.samplerate)

                self._thread = threading.Thread(target=_loop, daemon=True)
                self._thread.start()

            def stop(self):
                self._running = False

            def close(self):
                self.stop()

        return _NullStream(**kwargs)


def get_audio_backend() -> AudioBackend:
    """依環境選擇後端：有 sounddevice 且有輸入裝置 → SoundDevice，否則 Null。"""
    try:
        devices = sd.query_devices()
        has_input = any(int(d.get("max_input_channels", 0)) > 0 for d in devices)
        if has_input:
            return SoundDeviceBackend()
    except Exception:
        pass
    LOGGER.warning("無可用輸入裝置，切換到 NullBackend（靜音）")
    return NullBackend()


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
    backend = get_audio_backend()
    if backend.kind == "null":
        return "（無可用輸入裝置，NullBackend 靜音模式）"
    lines: list[str] = []
    for index, device in enumerate(backend.list_devices()):
        inputs = int(device["max_input_channels"])
        outputs = int(device["max_output_channels"])
        if inputs <= 0 and outputs <= 0:
            continue
        lines.append(
            f"{index:3d}  in={inputs} out={outputs} "
            f"rate={int(device['default_samplerate'])}  "
            f"{device['name']}  [{_hostapi_name(device)}]"
        )
    return "\n".join(lines) if lines else "（無可用輸入裝置）"


class AudioInput:
    def __init__(self, config: AudioConfig) -> None:
        self.config = config
        self._backend = get_audio_backend()
        if self._backend.kind == "null":
            self.device_index = -1
            self._device_channels = max(1, int(config.input_channel) + 1)
            LOGGER.info("AudioInput 使用 NullBackend（靜音）")
        else:
            self.device_index = self._backend.resolve_input_device(
                config.input_device, config.input_hostapi
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
        if self._backend.kind == "null":
            self._stream = self._backend.create_input_stream(
                samplerate=self.config.sample_rate,
                blocksize=blocksize,
                channels=self._device_channels,
                callback=self._callback,
            )
            self._stream.start()
            LOGGER.info("開始監聽：NullBackend 靜音模式，聲道 %d", self.config.input_channel + 1)
            return self
        self._stream = self._backend.create_input_stream(
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
    """有狀態高品質重採樣，優先 soxr.ResampleStream，無則 scipy overlap-save，回退 boxcar。"""

    def __init__(self, source_rate: int, target_rate: int) -> None:
        self.source_rate = source_rate
        self.target_rate = target_rate
        if source_rate == target_rate:
            self._mode = "passthrough"
            return
        # 1. 優先 soxr 天生有狀態、品質最高
        try:
            import soxr  # type: ignore

            self._soxr = soxr.ResampleStream(source_rate, target_rate, 1, dtype="float32", quality="HQ")
            self._mode = "soxr"
            # 預建備援參數，避免 soxr 失敗後 boxcar 因未設 factor 而名不副實
            try:
                from math import gcd

                from scipy.signal import resample_poly  # type: ignore

                g = gcd(source_rate, target_rate)
                self._up = target_rate // g
                self._down = source_rate // g
                self._resample_poly = resample_poly  # type: ignore
                self._tail_len = 512
                self._tail = np.zeros(0, dtype=np.float32)
            except Exception:
                pass
            if source_rate % target_rate == 0:
                self.factor = source_rate // target_rate
            return
        except Exception:
            pass
        # 2. scipy 有狀態 overlap-save
        try:
            from math import gcd

            from scipy.signal import resample_poly  # type: ignore

            g = gcd(source_rate, target_rate)
            self._up = target_rate // g
            self._down = source_rate // g
            self._resample_poly = resample_poly  # type: ignore
            self._mode = "scipy_stateful"
            # 保留尾部以消除 FIR 瞬態：估計濾波器長度 ~10*max(up,down)，取 2 倍保險
            self._tail_len = 512
            self._tail = np.zeros(0, dtype=np.float32)
            self._filter_delay = 0  # 首次塊需丟棄的輸出樣本
            # 預熱延遲：soxr 約有 64 樣本延遲，scipy 無延遲但有邊界效應
            return
        except Exception:
            pass
        # 3. 回退整數倍 boxcar
        if source_rate % target_rate == 0:
            self._mode = "boxcar"
            self.factor = source_rate // target_rate
        else:
            raise ValueError("scipy/soxr 未安裝時只支援整數比例降採樣")

    def process(self, frame: np.ndarray) -> np.ndarray:
        if getattr(self, "_mode", None) == "passthrough":
            return frame.astype(np.float32, copy=False)
        if getattr(self, "_mode", None) == "soxr":
            try:
                out = self._soxr.resample_chunk(frame.astype(np.float32))
                return out.astype(np.float32, copy=False)
            except Exception as e:
                LOGGER.warning("soxr 重採樣失敗，降級到備援：%s", e)
                self._mode = "scipy_stateful" if hasattr(self, "_resample_poly") else "interp"
        if getattr(self, "_mode", None) == "scipy_stateful":
            try:
                # overlap-save 備援（soxr 為主，此路徑僅備援，不追 bit-exact）
                extended = np.concatenate([self._tail, frame.astype(np.float32)]) if len(self._tail) else frame.astype(np.float32)
                resampled = self._resample_poly(extended, self._up, self._down)
                # 計算應保留的輸出長度與應丟棄的前緣
                # tail 在輸入域佔 tail_len，輸出域佔 tail_len*up/down
                discard = int(round(len(self._tail) * self._up / self._down)) if len(self._tail) else 0
                # 更新 tail 為本塊尾部
                keep = min(len(frame), self._tail_len)
                self._tail = frame[-keep:].astype(np.float32) if keep > 0 else np.zeros(0, dtype=np.float32)
                if discard > 0 and len(resampled) > discard:
                    resampled = resampled[discard:]
                # 裁至預期長度（避免浮點誤差）
                expected = int(round(len(frame) * self._up / self._down))
                if len(resampled) > expected:
                    resampled = resampled[:expected]
                return resampled.astype(np.float32, copy=False)
            except Exception as e:
                LOGGER.warning("scipy 重採樣失敗，降級到 boxcar/interp：%s", e)
                self._mode = "boxcar"
        # 回退 boxcar
        if getattr(self, "factor", 0) and self.factor > 1:  # type: ignore[attr-defined]
            length = len(frame) - (len(frame) % self.factor)  # type: ignore[attr-defined]
            if length <= 0:
                return np.empty(0, dtype=np.float32)
            reshaped = frame[:length].reshape(-1, self.factor)  # type: ignore[attr-defined]
            return reshaped.mean(axis=1, dtype=np.float32)
        expected_len = int(round(len(frame) * self.target_rate / self.source_rate))
        if expected_len <= 0:
            return np.empty(0, dtype=np.float32)
        x_old = np.arange(len(frame), dtype=np.float32)
        x_new = np.linspace(0, len(frame) - 1, expected_len, dtype=np.float32)
        return np.interp(x_new, x_old, frame).astype(np.float32)

    def flush(self) -> np.ndarray:
        """排空 soxr 剩餘延遲輸出（turn 結束時呼叫）。"""
        if getattr(self, "_mode", None) == "soxr":
            try:
                tail = self._soxr.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
                return tail.astype(np.float32, copy=False) if len(tail) else np.empty(0, dtype=np.float32)
            except Exception:
                return np.empty(0, dtype=np.float32)
        return np.empty(0, dtype=np.float32)

