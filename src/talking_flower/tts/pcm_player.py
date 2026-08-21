"""PCM 播放器：有狀態重採樣、AEC render 參考、真實 RMS 回傳、靜音保水。"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading

import numpy as np
import sounddevice as sd

from ..aec import EchoCanceller
from ..settings import LiveSettings


LOGGER = logging.getLogger(__name__)


class PcmPlayer:
    def __init__(
        self,
        source_rate: int,
        device: int | None,
        volume: int,
        aec: EchoCanceller,
        live: LiveSettings | None = None,
        bus=None,
    ) -> None:
        self.source_rate = source_rate
        self.sample_rate = aec.sample_rate if aec.enabled else source_rate
        self.device = device
        self._volume = volume
        self.live = live
        self.aec = aec
        self.bus = bus
        # RMS 節流：每 3 個音框（60ms）發布一次，避免 bus 洪泛
        self._rms_frame_stride = 3
        self._rms_counter = 0
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=24)
        self._error: BaseException | None = None
        self._stop = threading.Event()
        self._dump_path = os.environ.get("FLOWER_TTS_DUMP", "").strip()
        # 有狀態重採樣器（soxr 無則 scipy overlap），避免每塊 FIR 瞬態
        self._resampler = None
        self._resampler_tail = np.zeros(0, dtype=np.float32)
        self._resampler_mode = "none"
        if self.sample_rate != self.source_rate:
            try:
                import soxr  # type: ignore

                self._resampler = soxr.ResampleStream(self.source_rate, self.sample_rate, 1, dtype="float32", quality="HQ")
                self._resampler_mode = "soxr"
            except Exception:
                try:
                    from math import gcd

                    from scipy.signal import resample_poly  # type: ignore

                    g = gcd(self.sample_rate, self.source_rate)
                    self._resampler_up = self.sample_rate // g
                    self._resampler_down = self.source_rate // g
                    self._resampler_fn = resample_poly  # type: ignore
                    self._resampler_mode = "scipy"
                    self._resampler_tail_len = 512
                except Exception:
                    self._resampler_mode = "interp"
        self._thread = threading.Thread(
            target=self._run,
            name="flower-audio-output",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            pending = b""
            frame_bytes = self.aec.frame_size * 2
            # 佇列暫時沒有音訊時，每次餵 50 ms 的靜音框，讓裝置緩衝維持滿水位，
            # 避免 WASAPI underflow 在句子之間與分塊之間爆音。
            silence_burst = max(1, int(0.05 * self.sample_rate / self.aec.frame_size))
            silence_frame = b"\x00" * frame_bytes
            silence_ref = np.zeros(self.aec.frame_size, dtype=np.float32)
            silence_frames = 0
            dump_frames: list[bytes] | None = [] if self._dump_path else None
            with sd.RawOutputStream(
                device=self.device,
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                latency="high",
            ) as stream:
                while True:
                    try:
                        chunk = self._queue.get(timeout=0.05)
                    except queue.Empty:
                        stopped = False
                        for _ in range(silence_burst):
                            if self._stop.is_set():
                                stopped = True
                                break
                            if self.aec.enabled:
                                self.aec.process_render(silence_ref)
                            stream.write(silence_frame)
                            silence_frames += 1
                            if dump_frames is not None:
                                dump_frames.append(silence_frame)
                        if stopped:
                            return
                        continue
                    if chunk is None:
                        break
                    pending += chunk
                    while len(pending) >= frame_bytes:
                        audio_frame = pending[:frame_bytes]
                        pending = pending[frame_bytes:]
                        if self._stop.is_set():
                            return
                        if self.aec.enabled:
                            reference = np.frombuffer(audio_frame, dtype="<i2").astype(np.float32)
                            self.aec.process_render(reference / 32768.0)
                        stream.write(audio_frame)
                        # 真實播放音量回傳（bus.publish 已執行緒安全）
                        self._rms_counter += 1
                        if self.bus is not None and self._rms_counter % self._rms_frame_stride == 0:
                            try:
                                samples_f = np.frombuffer(audio_frame, dtype="<i2").astype(np.float32) / 32768.0
                                rms = float(np.sqrt(np.mean(np.square(samples_f))))
                                self.bus.publish({"type": "tts_rms", "rms": round(rms, 4)})
                            except Exception:
                                pass
                        if dump_frames is not None:
                            dump_frames.append(audio_frame)
                if pending and not self._stop.is_set():
                    if self.aec.enabled:
                        reference = np.frombuffer(pending, dtype="<i2").astype(np.float32) / 32768.0
                        padded = np.zeros(self.aec.frame_size, dtype=np.float32)
                        padded[: len(reference)] = reference
                        self.aec.process_render(padded)
                    stream.write(pending)
                    if dump_frames is not None:
                        dump_frames.append(pending)
                if not self._stop.is_set():
                    # Pa_CloseStream 會丟棄裝置內尚未播完的緩衝；先 stop 讓尾音播完再關閉。
                    stream.stop()
            if silence_frames:
                LOGGER.info(
                    "TTS 播放補了 %d 個靜音框（%.0f ms）",
                    silence_frames,
                    silence_frames * frame_bytes / 2 / self.sample_rate * 1000,
                )
            if dump_frames is not None:
                np.save(self._dump_path, np.frombuffer(b"".join(dump_frames), dtype="<i2"))
                LOGGER.info("TTS dump 已寫入 %s（%d 個音框）", self._dump_path, len(dump_frames))
        except BaseException as error:
            self._error = error

    def _prepare(self, data: bytes) -> bytes:
        volume = self.live.volume if self.live is not None else self._volume
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        samples *= max(0, min(volume, 100)) / 100.0
        if self.sample_rate != self.source_rate:
            if self._resampler_mode == "soxr" and self._resampler is not None:
                try:
                    samples = self._resampler.resample_chunk(samples).astype(np.float32)
                    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                except Exception as e:
                    LOGGER.warning("soxr 重採樣失敗，降級到 scipy/interp：%s", e)
                    # 降級後落到下方 fallback，不直接返回原 24k 資料
                    self._resampler_mode = "scipy" if hasattr(self, "_resampler_fn") else "interp"
            if self._resampler_mode == "scipy":
                try:
                    # overlap-save 消除邊界瞬態（備援路徑，soxr 為主）
                    extended = np.concatenate([self._resampler_tail, samples]) if len(self._resampler_tail) else samples
                    resampled = self._resampler_fn(extended, self._resampler_up, self._resampler_down)  # type: ignore[attr-defined]
                    discard = int(round(len(self._resampler_tail) * self._resampler_up / self._resampler_down)) if len(self._resampler_tail) else 0  # type: ignore[attr-defined]
                    if discard and len(resampled) > discard:
                        resampled = resampled[discard:]
                    expected = int(round(len(samples) * self._resampler_up / self._resampler_down))  # type: ignore[attr-defined]
                    if len(resampled) > expected:
                        resampled = resampled[:expected]
                    # 更新 tail
                    keep = min(len(samples), self._resampler_tail_len)  # type: ignore[attr-defined]
                    self._resampler_tail = samples[-keep:].astype(np.float32) if keep > 0 else np.zeros(0, dtype=np.float32)
                    samples = resampled.astype(np.float32)
                    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                except Exception as e:
                    LOGGER.warning("scipy 重採樣失敗，降級到線性內插：%s", e)
                    self._resampler_mode = "interp"
                    # 落到下方線性備援
            # 回退線性
            if self.sample_rate % self.source_rate == 0:
                factor = self.sample_rate // self.source_rate
                positions = np.arange(len(samples) * factor, dtype=np.float32) / factor
                samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
            else:
                expected_len = int(round(len(samples) * self.sample_rate / self.source_rate))
                if expected_len > 0:
                    x_old = np.arange(len(samples), dtype=np.float32)
                    x_new = np.linspace(0, len(samples) - 1, expected_len, dtype=np.float32)
                    samples = np.interp(x_new, x_old, samples).astype(np.float32)
        return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    def _flush_resampler(self) -> bytes:
        if self._resampler_mode == "soxr" and self._resampler is not None:
            try:
                tail = self._resampler.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
                if len(tail):
                    return (np.clip(tail, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            except Exception:
                pass
        return b""

    async def write(self, data: bytes) -> None:
        if self._error is not None:
            raise self._error
        await asyncio.to_thread(self._queue.put, self._prepare(data))

    async def finish(self) -> None:
        if self._thread.is_alive() and not self._stop.is_set():
            # 先排空 soxr 延遲
            try:
                flush_bytes = self._flush_resampler()
                if flush_bytes:
                    await asyncio.to_thread(self._queue.put, flush_bytes)
            except Exception:
                pass
            await asyncio.to_thread(self._queue.put, None)
        await asyncio.to_thread(self._thread.join)
        if self._error is not None:
            raise self._error

    def abort(self) -> None:
        self._stop.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass


# 舊名稱相容別名（tests 直接引用 _PcmPlayer）
_PcmPlayer = PcmPlayer
