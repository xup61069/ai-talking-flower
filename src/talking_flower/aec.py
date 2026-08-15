from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import threading
from typing import Protocol

import numpy as np

from .config import AecConfig


LOGGER = logging.getLogger(__name__)
FloatPointer = ctypes.POINTER(ctypes.c_float)


class EchoCanceller(Protocol):
    enabled: bool
    sample_rate: int
    frame_size: int

    def process_render(self, frame: np.ndarray) -> None: ...
    def process_capture(self, frame: np.ndarray) -> np.ndarray: ...
    def close(self) -> None: ...


class BypassEchoCanceller:
    enabled = False

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self.frame_size = sample_rate // 100

    def process_render(self, frame: np.ndarray) -> None:
        return

    def process_capture(self, frame: np.ndarray) -> np.ndarray:
        return frame

    def close(self) -> None:
        return


class WebRtcAec3:
    enabled = True

    def __init__(self, config: AecConfig, sample_rate: int, library_path: Path) -> None:
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        self._closed = False
        self._dll = ctypes.CDLL(str(library_path.resolve()))
        self._bind()

        self._apm = self._dll.webrtc_apm_create()
        self._config = self._dll.webrtc_apm_config_create()
        if not self._apm or not self._config:
            self.close()
            raise RuntimeError("無法建立 WebRTC APM")

        self._dll.webrtc_apm_config_set_echo_canceller(self._config, 1, 0)
        self._dll.webrtc_apm_config_set_noise_suppression(
            self._config,
            int(config.noise_suppression),
            1,
        )
        self._dll.webrtc_apm_config_set_gain_controller1(self._config, 0, 2, 3, 9, 1)
        self._dll.webrtc_apm_config_set_gain_controller2(self._config, 0)
        self._dll.webrtc_apm_config_set_high_pass_filter(self._config, 1)
        self._dll.webrtc_apm_config_set_pipeline(self._config, sample_rate, 0, 0, 0)
        self._check(self._dll.webrtc_apm_apply_config(self._apm, self._config), "套用設定")

        self._input = self._dll.webrtc_apm_stream_config_create(sample_rate, 1)
        self._output = self._dll.webrtc_apm_stream_config_create(sample_rate, 1)
        self._reverse_input = self._dll.webrtc_apm_stream_config_create(sample_rate, 1)
        self._reverse_output = self._dll.webrtc_apm_stream_config_create(sample_rate, 1)
        if not all((self._input, self._output, self._reverse_input, self._reverse_output)):
            self.close()
            raise RuntimeError("無法建立 WebRTC APM 音訊格式")

        self._check(self._dll.webrtc_apm_initialize(self._apm), "初始化")
        self._dll.webrtc_apm_set_stream_delay_ms(self._apm, config.delay_ms)
        self.frame_size = int(self._dll.webrtc_apm_get_frame_size(sample_rate))
        if self.frame_size != sample_rate // 100:
            self.close()
            raise RuntimeError(f"WebRTC APM 回傳不合理的音框大小：{self.frame_size}")
        LOGGER.info("WebRTC AEC3 就緒：%d Hz，延遲 %d ms", sample_rate, config.delay_ms)

    def _bind(self) -> None:
        dll = self._dll
        dll.webrtc_apm_create.restype = ctypes.c_void_p
        dll.webrtc_apm_destroy.argtypes = [ctypes.c_void_p]
        dll.webrtc_apm_config_create.restype = ctypes.c_void_p
        dll.webrtc_apm_config_destroy.argtypes = [ctypes.c_void_p]
        dll.webrtc_apm_config_set_echo_canceller.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        dll.webrtc_apm_config_set_noise_suppression.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        dll.webrtc_apm_config_set_gain_controller1.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        dll.webrtc_apm_config_set_gain_controller2.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.webrtc_apm_config_set_high_pass_filter.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.webrtc_apm_config_set_pipeline.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        dll.webrtc_apm_apply_config.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        dll.webrtc_apm_apply_config.restype = ctypes.c_int
        dll.webrtc_apm_stream_config_create.argtypes = [ctypes.c_int, ctypes.c_size_t]
        dll.webrtc_apm_stream_config_create.restype = ctypes.c_void_p
        dll.webrtc_apm_stream_config_destroy.argtypes = [ctypes.c_void_p]
        dll.webrtc_apm_initialize.argtypes = [ctypes.c_void_p]
        dll.webrtc_apm_initialize.restype = ctypes.c_int
        dll.webrtc_apm_process_stream.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FloatPointer),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(FloatPointer),
        ]
        dll.webrtc_apm_process_stream.restype = ctypes.c_int
        dll.webrtc_apm_process_reverse_stream.argtypes = dll.webrtc_apm_process_stream.argtypes
        dll.webrtc_apm_process_reverse_stream.restype = ctypes.c_int
        dll.webrtc_apm_set_stream_delay_ms.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dll.webrtc_apm_get_frame_size.argtypes = [ctypes.c_int]
        dll.webrtc_apm_get_frame_size.restype = ctypes.c_size_t

    @staticmethod
    def _check(code: int, operation: str) -> None:
        if code != 0:
            raise RuntimeError(f"WebRTC APM {operation}失敗：錯誤碼 {code}")

    @staticmethod
    def _channels(frame: np.ndarray) -> tuple[ctypes.Array, np.ndarray]:
        contiguous = np.ascontiguousarray(frame, dtype=np.float32)
        pointer = contiguous.ctypes.data_as(FloatPointer)
        return (FloatPointer * 1)(pointer), contiguous

    def process_render(self, frame: np.ndarray) -> None:
        if len(frame) != self.frame_size:
            raise ValueError(f"AEC render 音框必須是 {self.frame_size} 個樣本")
        source_pointers, source = self._channels(frame)
        output = np.empty_like(source)
        output_pointers, output = self._channels(output)
        with self._lock:
            self._check(
                self._dll.webrtc_apm_process_reverse_stream(
                    self._apm,
                    source_pointers,
                    self._reverse_input,
                    self._reverse_output,
                    output_pointers,
                ),
                "處理播放參考",
            )

    def process_capture(self, frame: np.ndarray) -> np.ndarray:
        if len(frame) % self.frame_size:
            raise ValueError(f"AEC capture 長度必須是 {self.frame_size} 的倍數")
        processed: list[np.ndarray] = []
        with self._lock:
            for start in range(0, len(frame), self.frame_size):
                source_pointers, source = self._channels(frame[start : start + self.frame_size])
                output = np.empty_like(source)
                output_pointers, output = self._channels(output)
                self._check(
                    self._dll.webrtc_apm_process_stream(
                        self._apm,
                        source_pointers,
                        self._input,
                        self._output,
                        output_pointers,
                    ),
                    "處理麥克風",
                )
                processed.append(output)
        return np.concatenate(processed) if processed else np.empty(0, dtype=np.float32)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        dll = getattr(self, "_dll", None)
        if dll is None:
            return
        for name in ("_input", "_output", "_reverse_input", "_reverse_output"):
            pointer = getattr(self, name, None)
            if pointer:
                dll.webrtc_apm_stream_config_destroy(pointer)
                setattr(self, name, None)
        config = getattr(self, "_config", None)
        if config:
            dll.webrtc_apm_config_destroy(config)
            self._config = None
        apm = getattr(self, "_apm", None)
        if apm:
            dll.webrtc_apm_destroy(apm)
            self._apm = None


def create_echo_canceller(config: AecConfig, sample_rate: int, project_root: Path) -> EchoCanceller:
    if not config.enabled:
        return BypassEchoCanceller(sample_rate)
    if config.backend != "webrtc_aec3":
        raise ValueError(f"不支援的 AEC backend：{config.backend}")
    library_path = project_root / config.library
    if not library_path.is_file():
        raise FileNotFoundError(f"找不到 WebRTC APM：{library_path}")
    return WebRtcAec3(config, sample_rate, library_path)
