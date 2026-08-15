from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import math

import numpy as np

from .config import VadConfig


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VadStatus:
    rms: float
    threshold: float
    speech: bool
    active: bool
    calibrating: bool
    probability: float | None = None


class UtteranceSegmenter:
    """Adaptive low-cost VAD used to bring up the complete audio chain."""

    def __init__(self, config: VadConfig, *, sample_rate: int, block_ms: int) -> None:
        self.config = config
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self._pre_roll: deque[np.ndarray] = deque(
            maxlen=max(1, math.ceil(config.pre_roll_ms / block_ms))
        )
        self._start_frames = max(1, math.ceil(config.speech_start_ms / block_ms))
        self._end_frames = max(1, math.ceil(config.speech_end_ms / block_ms))
        self._minimum_voiced_frames = max(1, math.ceil(config.minimum_speech_ms / block_ms))
        self._maximum_frames = max(1, math.ceil(config.maximum_speech_s * 1000 / block_ms))
        self._noise_floor = config.minimum_rms / config.noise_multiplier
        self.backend = config.backend
        self._ten_vad = None
        if config.backend == "ten_vad":
            try:
                from ten_vad import TenVad

                hop_size = round(sample_rate * block_ms / 1000)
                self._ten_vad = TenVad(hop_size=hop_size, threshold=config.threshold)
                LOGGER.info("TEN VAD 已啟用：%d samples，threshold %.2f", hop_size, config.threshold)
            except Exception:
                LOGGER.exception("TEN VAD 無法初始化，改用能量式 VAD")
                self.backend = "energy"
        elif config.backend != "energy":
            raise ValueError(f"不支援的 VAD backend：{config.backend}")

        self._calibration_remaining = (
            max(0, math.ceil(config.calibration_ms / block_ms))
            if self.backend == "energy"
            else 0
        )
        self._calibration_values: list[float] = []
        self.reset()

    def reset(self) -> None:
        self._active = False
        self._speech_run = 0
        self._silence_run = 0
        self._voiced_frames = 0
        self._frames: list[np.ndarray] = []
        self._pre_roll.clear()

    @property
    def active(self) -> bool:
        return self._active

    def push(self, frame: np.ndarray) -> tuple[np.ndarray | None, VadStatus]:
        if frame.size == 0:
            status = VadStatus(0.0, self.config.minimum_rms, False, self._active, False)
            return None, status

        rms = float(np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
        if self._calibration_remaining > 0:
            self._calibration_values.append(rms)
            self._calibration_remaining -= 1
            if self._calibration_remaining == 0 and self._calibration_values:
                measured = float(np.median(np.asarray(self._calibration_values)))
                self._noise_floor = max(
                    measured,
                    self.config.minimum_rms / self.config.noise_multiplier,
                )
                self._calibration_values.clear()
            threshold = max(
                self.config.minimum_rms,
                self._noise_floor * self.config.noise_multiplier,
            )
            return None, VadStatus(rms, threshold, False, False, True)

        probability: float | None = None
        if self._ten_vad is not None:
            pcm16 = np.clip(frame, -1.0, 1.0)
            pcm16 = np.asarray(np.rint(pcm16 * 32767.0), dtype=np.int16)
            probability, flag = self._ten_vad.process(pcm16)
            if probability < 0 or flag < 0:
                raise RuntimeError("TEN VAD 處理失敗；請檢查 hop size 與 DLL")
            threshold = self.config.threshold
            speech = bool(flag)
        else:
            threshold = max(
                self.config.minimum_rms,
                self._noise_floor * self.config.noise_multiplier,
            )
            speech = rms >= threshold

        if not self._active:
            self._pre_roll.append(frame.copy())
            if speech:
                self._speech_run += 1
            else:
                self._speech_run = 0
                if self._ten_vad is None:
                    self._noise_floor = 0.995 * self._noise_floor + 0.005 * max(rms, 1e-6)

            if self._speech_run >= self._start_frames:
                self._active = True
                self._frames = list(self._pre_roll)
                self._voiced_frames = self._speech_run
                self._silence_run = 0
        else:
            self._frames.append(frame.copy())
            if speech:
                self._voiced_frames += 1
                self._silence_run = 0
            else:
                self._silence_run += 1

            timed_out = len(self._frames) >= self._maximum_frames
            ended = self._silence_run >= self._end_frames
            if timed_out or ended:
                completed = None
                if self._voiced_frames >= self._minimum_voiced_frames:
                    completed = np.concatenate(self._frames).astype(np.float32, copy=False)
                self.reset()
                status = VadStatus(rms, threshold, speech, False, False, probability)
                return completed, status

        return None, VadStatus(rms, threshold, speech, self._active, False, probability)
