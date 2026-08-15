from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import queue
import threading
from typing import Protocol

import httpx
import numpy as np
from opencc import OpenCC
import sounddevice as sd

from .aec import BypassEchoCanceller, EchoCanceller
from .audio import resolve_device
from .config import AudioConfig, TtsConfig


LOGGER = logging.getLogger(__name__)


class TextToSpeech(Protocol):
    async def speak(self, text: str) -> None: ...
    async def health(self) -> bool: ...
    async def close(self) -> None: ...
    async def begin_turn(self) -> None: ...
    async def end_turn(self) -> None: ...
    def abort_turn(self) -> None: ...


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

    async def speak(self, text: str) -> None:
        if not text.strip():
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._speak_sync, text)

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


class _PcmPlayer:
    def __init__(
        self,
        source_rate: int,
        device: int | None,
        volume: int,
        aec: EchoCanceller,
    ) -> None:
        self.source_rate = source_rate
        self.sample_rate = aec.sample_rate if aec.enabled else source_rate
        self.device = device
        self.volume = volume
        self.aec = aec
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=24)
        self._error: BaseException | None = None
        self._stop = threading.Event()
        self._dump_path = os.environ.get("FLOWER_TTS_DUMP", "").strip()
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
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        samples *= max(0, min(self.volume, 100)) / 100.0
        if self.sample_rate != self.source_rate:
            if self.sample_rate % self.source_rate:
                raise ValueError("AEC 播放端目前只支援整數倍率升採樣")
            factor = self.sample_rate // self.source_rate
            positions = np.arange(len(samples) * factor, dtype=np.float32) / factor
            samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    async def write(self, data: bytes) -> None:
        if self._error is not None:
            raise self._error
        await asyncio.to_thread(self._queue.put, self._prepare(data))

    async def finish(self) -> None:
        if self._thread.is_alive() and not self._stop.is_set():
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


class HttpPcmTTS:
    def __init__(self, config: TtsConfig, audio: AudioConfig, aec: EchoCanceller) -> None:
        self.config = config
        self.aec = aec
        self._converter = OpenCC("t2s") if config.backend == "kokoro" else None
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=httpx.Timeout(config.timeout_s, connect=5.0),
        )
        self._device: int | None = None
        if audio.output_device:
            self._device = resolve_device(
                audio.output_device,
                audio.output_hostapi,
                input_device=False,
            )
        # 一輪對話只建立一個播放器：第一句的音訊還在播放時，下一句的音訊
        # 已經開始生成並送入佇列，句子之間不會因為 TTS 生成而停頓。
        self._turn_player: _PcmPlayer | None = None
        self._in_turn = False

    async def health(self) -> bool:
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
            return response.json().get("status") == "ok"
        except (httpx.HTTPError, ValueError):
            return False

    async def begin_turn(self) -> None:
        self._turn_player = None
        self._in_turn = True

    async def end_turn(self) -> None:
        self._in_turn = False
        player, self._turn_player = self._turn_player, None
        if player is not None:
            await player.finish()

    def abort_turn(self) -> None:
        self._in_turn = False
        player, self._turn_player = self._turn_player, None
        if player is not None:
            player.abort()

    async def speak(self, text: str) -> None:
        if not text.strip():
            return

        owned: _PcmPlayer | None = None
        # CosyVoice stream=True 的分塊接縫相位不連續（每個 chunk 開頭能量歸零），
        # 直接接起來會聽到規律的爆音；保留上一段尾部與下一段頭部做 20 ms 交叉淡化。
        blend_samples = 480
        blend_carry: np.ndarray | None = None
        try:
            request_text = self._converter.convert(text) if self._converter is not None else text
            async with self._client.stream(
                "POST",
                "/v1/tts",
                json={
                    "text": request_text,
                    "voice": self.config.voice,
                    "speed": self.config.speed,
                },
            ) as response:
                response.raise_for_status()
                sample_rate = int(response.headers.get("X-Sample-Rate", self.config.sample_rate))
                player = self._turn_player
                if player is None:
                    player = _PcmPlayer(sample_rate, self._device, self.config.volume, self.aec)
                    if self._in_turn:
                        self._turn_player = player
                    else:
                        owned = player
                received = 0
                # httpx chunk 不保證對齊取樣邊界；若直接把奇數位元組切掉，
                # 後續全部樣本會錯位變成雜訊，所以要保留未對齊的位元組給下一段。
                pending_bytes = b""

                async def write_samples(samples: np.ndarray) -> None:
                    nonlocal received
                    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                    received += len(pcm)
                    await player.write(pcm)

                async for chunk in response.aiter_bytes():
                    data = pending_bytes + chunk
                    aligned = len(data) - len(data) % 2
                    pending_bytes = data[aligned:]
                    if not aligned:
                        continue
                    samples = np.frombuffer(data[:aligned], dtype="<i2").astype(np.float32) / 32768.0
                    if blend_carry is not None:
                        if len(samples) >= blend_samples:
                            ramp = np.linspace(0.0, 1.0, blend_samples, dtype=np.float32)
                            samples[:blend_samples] = (
                                samples[:blend_samples] * ramp + blend_carry * (1.0 - ramp)
                            )
                            blend_carry = None
                        else:
                            samples = np.concatenate([blend_carry, samples])
                            blend_carry = None
                    if len(samples) > blend_samples:
                        blend_carry = samples[-blend_samples:].copy()
                        samples = samples[:-blend_samples]
                    await write_samples(samples)
                if blend_carry is not None:
                    await write_samples(blend_carry)
                if received == 0:
                    raise RuntimeError("TTS 沒有回傳任何音訊")
        except asyncio.CancelledError:
            if owned is not None:
                owned.abort()
            raise
        except httpx.HTTPError as error:
            if owned is not None:
                owned.abort()
            raise RuntimeError(f"TTS 連線失敗：{error}") from error
        finally:
            if owned is not None:
                await owned.finish()

    async def close(self) -> None:
        await self._client.aclose()


def create_tts(
    config: TtsConfig,
    audio: AudioConfig,
    aec: EchoCanceller | None = None,
) -> TextToSpeech:
    if config.backend == "windows_sapi":
        return WindowsSapiTTS(config)
    if config.backend in {"cosyvoice", "kokoro"}:
        return HttpPcmTTS(config, audio, aec or BypassEchoCanceller(audio.sample_rate))
    raise ValueError(f"不支援的 TTS backend：{config.backend}")
