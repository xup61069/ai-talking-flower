"""HTTP PCM 串流 TTS：Kokoro / CosyVoice 共用用戶端。"""
from __future__ import annotations

import asyncio
import logging

import httpx
import numpy as np
from opencc import OpenCC

from ..aec import EchoCanceller
from ..audio import resolve_device
from ..config import AudioConfig, TtsConfig
from ..settings import LiveSettings
from .base import clean_speech_text
from .pcm_player import PcmPlayer


LOGGER = logging.getLogger(__name__)


class HttpPcmTTS:
    def __init__(
        self,
        config: TtsConfig,
        audio: AudioConfig,
        aec: EchoCanceller,
        live: LiveSettings | None = None,
        bus=None,
    ) -> None:
        self.config = config
        self.aec = aec
        self.live = live
        self.bus = bus
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
        self._turn_player: PcmPlayer | None = None
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

        owned: PcmPlayer | None = None
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
                    player = PcmPlayer(
                        sample_rate,
                        self._device,
                        self.config.volume,
                        self.aec,
                        self.live,
                        self.bus,
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
