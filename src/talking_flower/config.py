from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AppConfig:
    name: str
    mode: str
    database: Path
    log_level: str


@dataclass(frozen=True)
class AudioConfig:
    input_device: str
    input_hostapi: str
    input_channel: int
    sample_rate: int
    asr_sample_rate: int
    block_ms: int
    output_device: str
    output_hostapi: str


@dataclass(frozen=True)
class VadConfig:
    backend: str
    threshold: float
    minimum_rms: float
    noise_multiplier: float
    calibration_ms: int
    speech_start_ms: int
    speech_end_ms: int
    pre_roll_ms: int
    minimum_speech_ms: int
    maximum_speech_s: float


@dataclass(frozen=True)
class AecConfig:
    enabled: bool
    backend: str
    library: str
    delay_ms: int
    noise_suppression: bool


@dataclass(frozen=True)
class InteractionConfig:
    barge_in_enabled: bool


@dataclass(frozen=True)
class AsrConfig:
    backend: str
    model: str
    device: str
    chunk_size: tuple[int, int, int]
    encoder_chunk_look_back: int
    decoder_chunk_look_back: int


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    temperature: float
    top_p: float
    max_tokens: int
    timeout_s: float
    recent_turns: int


@dataclass(frozen=True)
class TtsConfig:
    backend: str
    voice: str
    rate: int
    volume: int
    base_url: str
    timeout_s: float
    sample_rate: int
    speed: float


@dataclass(frozen=True)
class Config:
    project_root: Path
    app: AppConfig
    audio: AudioConfig
    aec: AecConfig
    interaction: InteractionConfig
    vad: VadConfig
    asr: AsrConfig
    llm: LlmConfig
    tts: TtsConfig


def load_config(path: str | Path) -> Config:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    project_root = config_path.parent
    app_raw = raw["app"]
    database = Path(app_raw["database"])
    if not database.is_absolute():
        database = project_root / database

    return Config(
        project_root=project_root,
        app=AppConfig(
            name=str(app_raw["name"]),
            mode=str(app_raw["mode"]),
            database=database,
            log_level=str(app_raw["log_level"]),
        ),
        audio=AudioConfig(**raw["audio"]),
        aec=AecConfig(**raw["aec"]),
        interaction=InteractionConfig(**raw["interaction"]),
        vad=VadConfig(**raw["vad"]),
        asr=AsrConfig(
            **{key: value for key, value in raw["asr"].items() if key != "chunk_size"},
            chunk_size=tuple(int(value) for value in raw["asr"]["chunk_size"]),
        ),
        llm=LlmConfig(**raw["llm"]),
        tts=TtsConfig(**raw["tts"]),
    )
