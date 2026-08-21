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
class IdleChatConfig:
    enabled: bool
    timeout_s: float
    prompt: str


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
    persona: str = ""


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
    emotion_enabled: bool = False


@dataclass(frozen=True)
class ProfileConfig:
    persona_preset: str = "energetic"


@dataclass(frozen=True)
class Config:
    project_root: Path
    app: AppConfig
    audio: AudioConfig
    aec: AecConfig
    interaction: InteractionConfig
    idle_chat: IdleChatConfig
    vad: VadConfig
    asr: AsrConfig
    llm: LlmConfig
    tts: TtsConfig
    profile: ProfileConfig = ProfileConfig()


def _filter_fields(datacls, data: dict) -> dict:
    """容忍未知 key：只保留 dataclass 定義的欄位，避免 AppConfig(**raw) 炸裂。"""
    allowed = set(datacls.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    return {k: v for k, v in data.items() if k in allowed}


def config_from_raw(project_root: Path, raw: dict) -> Config:
    app_raw = raw["app"]
    database = Path(app_raw["database"])
    if not database.is_absolute():
        database = project_root / database

    # 未知區段（如舊版 app.persona_preset）一律容忍，不特判刪除
    profile_raw = raw.get("profile", {})
    if not isinstance(profile_raw, dict):
        profile_raw = {}

    app_fields = _filter_fields(AppConfig, app_raw)
    app_fields.pop("database", None)
    return Config(
        project_root=project_root,
        app=AppConfig(database=database, **app_fields),  # type: ignore[arg-type]
        audio=AudioConfig(**_filter_fields(AudioConfig, raw["audio"])),
        aec=AecConfig(**_filter_fields(AecConfig, raw["aec"])),
        interaction=InteractionConfig(**_filter_fields(InteractionConfig, raw["interaction"])),
        idle_chat=IdleChatConfig(**_filter_fields(IdleChatConfig, raw["idle_chat"])),
        vad=VadConfig(**_filter_fields(VadConfig, raw["vad"])),
        asr=AsrConfig(
            **_filter_fields(AsrConfig, {k: v for k, v in raw["asr"].items() if k != "chunk_size"}),
            chunk_size=tuple(int(value) for value in raw["asr"]["chunk_size"]),
        ),
        llm=LlmConfig(**_filter_fields(LlmConfig, raw["llm"])),
        tts=TtsConfig(**_filter_fields(TtsConfig, raw["tts"])),
        profile=ProfileConfig(**_filter_fields(ProfileConfig, profile_raw)),
    )


def load_config(path: str | Path) -> Config:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return config_from_raw(config_path.parent, raw)
