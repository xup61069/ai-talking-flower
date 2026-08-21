from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import queue
import threading
import re
from typing import Protocol

import httpx
import numpy as np
from opencc import OpenCC
import sounddevice as sd

from .aec import BypassEchoCanceller, EchoCanceller
from .audio import resolve_device
from .config import AudioConfig, TtsConfig
from .settings import LiveSettings


LOGGER = logging.getLogger(__name__)

EMOJI_PATTERN = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]+",
    flags=re.UNICODE,
)


def clean_speech_text(text: str) -> str:
    """過濾 Markdown 標記、Emoji、舞臺動作指示、角色前綴與提示詞殘留，回傳適合直接朗讀的純文字。"""
    if not text:
        return ""
    # 去除特殊標籤如 <|endofprompt|> 等
    text = re.sub(r"<\|[^|>]*\|>", "", text)
    # 去除程式碼區塊與 Markdown
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 去除括號內動作或舞臺指示
    text = re.sub(r"（[^）]*）", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"【[^】]*】", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    # 去除常見的 LLM 角色自稱與回覆標頭前綴（例如「花花：」、「回答：」等）
    text = re.sub(r"^(?:花花|助理|Assistant|AI|模型|回答|回覆)\s*[：:]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:你注意到使用者已經安靜|主動說一句適合此刻的話)[^：:。\n]*[：:。\n]\s*", "", text)
    text = EMOJI_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()



class TextToSpeech(Protocol):
    async def speak(self, text: str, on_first_byte=None) -> None: ...
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

    async def speak(self, text: str, on_first_byte=None) -> None:
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


class _PcmPlayer:
    def __init__(
        self,
        source_rate: int,
        device: int | None,
        volume: int,
        aec: EchoCanceller,
        live: LiveSettings | None = None,
    ) -> None:
        self.source_rate = source_rate
        self.sample_rate = aec.sample_rate if aec.enabled else source_rate
        self.device = device
        self._volume = volume
        self.live = live
        self.aec = aec
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
                except Exception:
                    # 回退線性
                    pass
                # soxr 成功即直接返回
                if self._resampler_mode == "soxr":
                    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            if self._resampler_mode == "scipy":
                try:
                    # overlap-save 消除邊界瞬態
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
                except Exception:
                    pass
                return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
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


class HttpPcmTTS:
    def __init__(
        self,
        config: TtsConfig,
        audio: AudioConfig,
        aec: EchoCanceller,
        live: LiveSettings | None = None,
    ) -> None:
        self.config = config
        self.aec = aec
        self.live = live
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

    async def speak(self, text: str, on_first_byte=None) -> None:
        cleaned = clean_speech_text(text)
        if not cleaned.strip():
            return

        owned: _PcmPlayer | None = None
        # CosyVoice stream=True 的分塊接縫相位不連續（每個 chunk 開頭能量歸零），
        # 直接接起來會聽到規律的爆音；保留上一段尾部與下一段頭部做 20 ms 交叉淡化。
        blend_samples = 480
        blend_carry: np.ndarray | None = None
        first_byte_fired = False

        def fire_first_byte():
            nonlocal first_byte_fired
            if not first_byte_fired and on_first_byte is not None:
                first_byte_fired = True
                try:
                    on_first_byte()
                except Exception:
                    pass

        try:
            request_text = self._converter.convert(cleaned) if self._converter is not None else cleaned
            speed = self.live.speed if self.live is not None else self.config.speed
            async with self._client.stream(
                "POST",
                "/v1/tts",
                json={
                    "text": request_text,
                    "voice": self.config.voice,
                    "speed": speed,
                },
            ) as response:
                response.raise_for_status()
                sample_rate = int(response.headers.get("X-Sample-Rate", self.config.sample_rate))
                player = self._turn_player
                if player is None:
                    player = _PcmPlayer(
                        sample_rate,
                        self._device,
                        self.config.volume,
                        self.aec,
                        self.live,
                    )
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
                    if chunk:
                        fire_first_byte()
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
    live: LiveSettings | None = None,
) -> TextToSpeech:
    if config.backend == "windows_sapi":
        return WindowsSapiTTS(config)
    if config.backend in {"cosyvoice", "kokoro"}:
        return HttpPcmTTS(config, audio, aec or BypassEchoCanceller(audio.sample_rate), live)
    raise ValueError(f"不支援的 TTS backend：{config.backend}")
