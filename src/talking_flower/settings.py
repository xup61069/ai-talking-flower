from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
import logging
from pathlib import Path

from .config import Config, config_from_raw


LOGGER = logging.getLogger(__name__)

LIVE = "live"
RESTART = "restart"


@dataclass(frozen=True)
class SettingSpec:
    path: str
    kind: str
    label: str
    apply: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: tuple[str, ...] | None = None
    default: object = None


# 所有可以在 UI 調整的項目。apply="live" 立即生效，apply="restart" 需要重建管線。
SPECS: tuple[SettingSpec, ...] = (
    # 應用程式
    SettingSpec("app.name", "str", "角色名稱", LIVE, default="花花"),
    SettingSpec("app.log_level", "choice", "日誌等級", RESTART, options=("DEBUG", "INFO", "WARNING")),
    # 音訊
    SettingSpec("audio.input_device", "str", "麥克風裝置", RESTART),
    SettingSpec("audio.input_hostapi", "str", "麥克風介面", RESTART),
    SettingSpec("audio.input_channel", "int", "麥克風聲道", RESTART, 0, 16, 1),
    SettingSpec("audio.output_device", "str", "輸出裝置", RESTART),
    SettingSpec("audio.output_hostapi", "str", "輸出介面", RESTART),
    SettingSpec("audio.sample_rate", "int", "取樣率", RESTART, 8000, 192000, 1000),
    SettingSpec("audio.asr_sample_rate", "int", "ASR 取樣率", RESTART, 8000, 48000, 1000),
    SettingSpec("audio.block_ms", "int", "區塊長度(ms)", RESTART, 10, 100, 10),
    # AEC
    SettingSpec("aec.enabled", "bool", "啟用回音消除", RESTART),
    SettingSpec("aec.delay_ms", "int", "AEC 延遲(ms)", LIVE, 0, 500, 5),
    SettingSpec("aec.noise_suppression", "bool", "降噪", RESTART),
    # 互動
    SettingSpec("interaction.barge_in_enabled", "bool", "允許插話打斷", LIVE),
    # 主動碎碎念
    SettingSpec("idle_chat.enabled", "bool", "主動碎碎念", LIVE),
    SettingSpec("idle_chat.timeout_s", "float", "安靜多久觸發(秒)", LIVE, 30, 3600, 30),
    SettingSpec("idle_chat.prompt", "text", "碎碎念提示詞", LIVE),
    # VAD
    SettingSpec("vad.threshold", "float", "VAD 門檻", RESTART, 0.0, 1.0, 0.05),
    SettingSpec("vad.minimum_rms", "float", "最低 RMS", RESTART, 0.0, 0.1, 0.001),
    SettingSpec("vad.noise_multiplier", "float", "噪音倍率", RESTART, 1.0, 10.0, 0.1),
    SettingSpec("vad.calibration_ms", "int", "環境校準(ms)", RESTART, 0, 10000, 500),
    SettingSpec("vad.speech_start_ms", "int", "開始判定(ms)", RESTART, 20, 2000, 20),
    SettingSpec("vad.speech_end_ms", "int", "結束判定(ms)", RESTART, 100, 3000, 50),
    SettingSpec("vad.pre_roll_ms", "int", "前段緩衝(ms)", RESTART, 0, 1000, 20),
    SettingSpec("vad.minimum_speech_ms", "int", "最短語音(ms)", RESTART, 100, 3000, 20),
    SettingSpec("vad.maximum_speech_s", "float", "最長語音(秒)", RESTART, 1, 60, 1),
    # ASR
    SettingSpec("asr.backend", "str", "ASR 後端", RESTART),
    SettingSpec("asr.model", "str", "ASR 模型", RESTART),
    SettingSpec("asr.device", "choice", "ASR 裝置", RESTART, options=("cpu", "cuda")),
    # LLM
    SettingSpec("llm.base_url", "str", "LLM 位址", RESTART),
    SettingSpec("llm.model", "str", "LLM 模型", RESTART),
    SettingSpec("llm.temperature", "float", "溫度", LIVE, 0.0, 2.0, 0.05),
    SettingSpec("llm.top_p", "float", "Top-P", LIVE, 0.0, 1.0, 0.05),
    SettingSpec("llm.max_tokens", "int", "最大 Token", LIVE, 16, 512, 8),
    SettingSpec("llm.recent_turns", "int", "記得的輪數", LIVE, 1, 30, 1),
    SettingSpec("llm.persona", "text", "人設", LIVE),
    # TTS
    SettingSpec(
        "tts.backend",
        "choice",
        "TTS 後端",
        RESTART,
        options=("kokoro", "cosyvoice", "windows_sapi"),
    ),
    SettingSpec("tts.voice", "str", "語音", RESTART),
    SettingSpec("tts.rate", "int", "語速(SAPI)", RESTART, -10, 10, 1),
    SettingSpec("tts.volume", "int", "音量", LIVE, 0, 100, 5),
    SettingSpec("tts.base_url", "str", "TTS 位址", RESTART),
    SettingSpec("tts.timeout_s", "float", "TTS 逾時(秒)", RESTART, 5, 300, 5),
    SettingSpec("tts.sample_rate", "int", "TTS 取樣率", RESTART, 8000, 48000, 1000),
    SettingSpec("tts.speed", "float", "語速", LIVE, 0.5, 2.0, 0.05),
    SettingSpec(
        "profile.persona_preset",
        "choice",
        "性格預設",
        LIVE,
        options=("energetic", "night", "work_buddy", "snarky"),
        default="energetic",
    ),
    # Web 控制台
    SettingSpec("web.auth_token", "str", "控制台 Token（空=本機信任）", RESTART, default=""),
)

SPEC_BY_PATH = {spec.path: spec for spec in SPECS}

# settings.json schema 版本；遷移分派表：{來源版: 升級函式}
SETTINGS_SCHEMA_VERSION = 2


def _migrate_v1_to_v2(raw: dict) -> dict:
    """v1 → v2：app.persona_preset 改為 profile.persona_preset。"""
    if "app.persona_preset" in raw:
        raw["profile.persona_preset"] = raw.pop("app.persona_preset")
    return raw


MIGRATIONS: dict[int, callable] = {
    1: _migrate_v1_to_v2,
}


def set_path(target: dict, path: str, value: object) -> None:
    section, _, key = path.partition(".")
    target.setdefault(section, {})[key] = value


def get_path(source: dict, path: str) -> object:
    section, _, key = path.partition(".")
    return source[section][key]


class SettingsStore:
    """config.toml 當基底，UI 修改存到 data/settings.json 覆蓋。"""

    def __init__(self, config_path: Path, settings_path: Path | None = None) -> None:
        self.config_path = config_path.resolve()
        self.project_root = self.config_path.parent
        self.settings_path = (
            Path(settings_path).resolve()
            if settings_path is not None
            else self.project_root / "data" / "settings.json"
        )
        self._base = self._read_toml()
        self._overrides: dict[str, object] = {}
        self._schema_version: int = SETTINGS_SCHEMA_VERSION
        self._load_overrides()

    def _read_toml(self) -> dict:
        import tomllib

        with self.config_path.open("rb") as handle:
            return tomllib.load(handle)

    def _load_overrides(self) -> None:
        if not self.settings_path.is_file():
            return
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            LOGGER.warning("無法讀取 %s：%s", self.settings_path, error)
            return
        if not isinstance(raw, dict):
            LOGGER.warning("settings.json 格式錯誤，忽略")
            return

        # schema 版本遷移：無 schema_version 視為 v1，逐步升級至最新
        stored_version = int(raw.pop("_schema_version", 1) or 1)
        migrated = False
        while stored_version < SETTINGS_SCHEMA_VERSION:
            step = MIGRATIONS.get(stored_version)
            if step is None:
                LOGGER.error("缺少 settings schema v%d 的遷移函式，中止於此版", stored_version)
                break
            raw = step(raw)
            stored_version += 1
            migrated = True
            LOGGER.info("settings.json 已從 v%d 遷移至 v%d", stored_version - 1, stored_version)
        if migrated:
            raw["_schema_version"] = SETTINGS_SCHEMA_VERSION

        for path, value in raw.items():
            if path in SPEC_BY_PATH:
                try:
                    self._overrides[path] = _coerce(SPEC_BY_PATH[path], value)
                except (TypeError, ValueError) as error:
                    LOGGER.warning("忽略無效的設定 %s=%r：%s", path, value, error)
        self._schema_version = stored_version
        if migrated:
            try:
                self._save()
            except Exception:
                pass

    def merged_raw(self) -> dict:
        raw = deepcopy(self._base)
        for path, value in self._overrides.items():
            set_path(raw, path, value)
        return raw

    def load_config(self) -> Config:
        # config_from_raw 已對未知 key 容忍，無需特判刪除
        return config_from_raw(self.project_root, self.merged_raw())

    def value(self, path: str) -> object:
        if path in self._overrides:
            return self._overrides[path]
        try:
            return get_path(self._base, path)
        except KeyError:
            spec = SPEC_BY_PATH.get(path)
            if spec is not None and spec.default is not None:
                return spec.default
            raise

    def set(self, path: str, value: object) -> None:
        spec = SPEC_BY_PATH.get(path)
        if spec is None:
            raise KeyError(f"不支援的設定路徑：{path}")
        value = _coerce(spec, value)
        self._overrides[path] = value
        self._save()

    def _save(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self._overrides)
        payload["_schema_version"] = SETTINGS_SCHEMA_VERSION
        self.settings_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def as_payload(self) -> list[dict]:
        payload: list[dict] = []
        for spec in SPECS:
            payload.append(
                {
                    "path": spec.path,
                    "kind": spec.kind,
                    "label": spec.label,
                    "apply": spec.apply,
                    "value": self.value(spec.path),
                    "minimum": spec.minimum,
                    "maximum": spec.maximum,
                    "step": spec.step,
                    "options": list(spec.options) if spec.options else None,
                }
            )
        return payload


def _coerce(spec: SettingSpec, value: object) -> object:
    if value is None:
        raise ValueError("值不能是 null")
    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}
    if spec.kind == "int":
        return _clamp(spec, int(value))
    if spec.kind == "float":
        return _clamp(spec, float(value))
    if spec.kind == "text" or spec.kind == "str":
        text = str(value)
        if text.strip().casefold() in {"null", "none"}:
            raise ValueError("值不能是 null")
        return text
    if spec.kind == "choice":
        text = str(value)
        if spec.options is not None and text not in spec.options:
            allowed = "、".join(spec.options)
            raise ValueError(f"必須是 {allowed} 其中之一")
        return text
    return value


def _clamp(spec: SettingSpec, number: int | float) -> int | float:
    if spec.minimum is not None:
        number = max(number, spec.minimum)
    if spec.maximum is not None:
        number = min(number, spec.maximum)
    return number


class LiveSettings:
    """執行中立即生效的旋鈕；controller 每輪/每次說話時讀取。"""

    def __init__(self, store: SettingsStore) -> None:
        config = store.load_config()
        self.name: str = config.app.name
        self.volume: int = config.tts.volume
        self.speed: float = config.tts.speed
        self.temperature: float = config.llm.temperature
        self.top_p: float = config.llm.top_p
        self.max_tokens: int = config.llm.max_tokens
        self.recent_turns: int = config.llm.recent_turns
        self.persona: str = config.llm.persona
        self.barge_in_enabled: bool = config.interaction.barge_in_enabled
        self.idle_chat_enabled: bool = config.idle_chat.enabled
        self.idle_chat_timeout_s: float = config.idle_chat.timeout_s
        self.idle_chat_prompt: str = config.idle_chat.prompt
        self.listening: bool = True
        self.manual_busy: bool = False
        # 持久化的 preset，統一走 profile.persona_preset
        try:
            preset_val = store.value("profile.persona_preset")
            self.persona_preset: str = str(preset_val) if preset_val else "energetic"
        except (KeyError, AttributeError, ValueError):
            self.persona_preset = "energetic"

    LIVE_PATHS: dict[str, str] = {
        "app.name": "name",
        "tts.volume": "volume",
        "tts.speed": "speed",
        "llm.temperature": "temperature",
        "llm.top_p": "top_p",
        "llm.max_tokens": "max_tokens",
        "llm.recent_turns": "recent_turns",
        "llm.persona": "persona",
        "interaction.barge_in_enabled": "barge_in_enabled",
        "idle_chat.enabled": "idle_chat_enabled",
        "idle_chat.timeout_s": "idle_chat_timeout_s",
        "idle_chat.prompt": "idle_chat_prompt",
        "profile.persona_preset": "persona_preset",
    }

    def set(self, path: str, value: object) -> bool:
        field = self.LIVE_PATHS.get(path)
        if field is None:
            return False
        spec = SPEC_BY_PATH[path]
        setattr(self, field, _coerce(spec, value))
        return True


def load_store(config_path: Path) -> SettingsStore:
    return SettingsStore(config_path)
