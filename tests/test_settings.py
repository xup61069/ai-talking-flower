from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.bus import RestartRequired, RuntimeControl, StatusBus
from talking_flower.config import load_config
from talking_flower.controller import FlowerController
from talking_flower.llm import LlamaCppClient
from talking_flower.memory import ConversationMemory
from talking_flower.settings import LiveSettings, SettingsStore, set_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SettingsStoreTests(unittest.TestCase):
    def test_v1_settings_migrated_to_v2(self) -> None:
        """無 _schema_version 視為 v1：app.persona_preset 自動遷移並寫回版本。"""
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            settings_path.write_text(
                json.dumps({"app.persona_preset": "night", "tts.volume": 55}),
                encoding="utf-8",
            )
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=settings_path,
            )
            self.assertEqual(store.value("profile.persona_preset"), "night")
            self.assertEqual(store._schema_version, 2)
            # 寫回後檔案含版本標記且舊 key 已移除
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["_schema_version"], 2)
            self.assertNotIn("app.persona_preset", saved)
            self.assertIn("profile.persona_preset", saved)

    def test_v2_settings_pass_through(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            settings_path.write_text(
                json.dumps({"_schema_version": 2, "tts.volume": 80}),
                encoding="utf-8",
            )
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=settings_path,
            )
            self.assertEqual(store.value("tts.volume"), 80)
            self.assertEqual(store._schema_version, 2)

    def test_new_save_carries_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=settings_path,
            )
            store.set("tts.volume", 66)
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["_schema_version"], 2)
            self.assertEqual(saved["tts.volume"], 66)

    def test_merge_with_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=Path(directory) / "settings.json",
            )
            store.set("tts.volume", 55)
            store.set("llm.temperature", 1.2)
            config = store.load_config()
            self.assertEqual(config.tts.volume, 55)
            self.assertEqual(config.llm.temperature, 1.2)
            self.assertEqual(config.tts.backend, "kokoro")
            self.assertEqual(store.value("tts.volume"), 55)
            self.assertTrue(store.settings_path.is_file())

    def test_invalid_override_ignored_and_numbers_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "llm.base_url": "null",
                        "tts.backend": "bogus",
                        "tts.volume": 999,
                        "llm.temperature": 5.0,
                    }
                ),
                encoding="utf-8",
            )
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=settings_path,
            )
            config = store.load_config()
            self.assertEqual(config.llm.base_url, "http://127.0.0.1:8080/v1")
            self.assertEqual(config.tts.backend, "kokoro")
            self.assertEqual(config.tts.volume, 100)
            self.assertEqual(config.llm.temperature, 2.0)

    def test_invalid_values_rejected_and_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=Path(directory) / "settings.json",
            )
            with self.assertRaises(ValueError):
                store.set("tts.backend", "bogus")
            with self.assertRaises(ValueError):
                store.set("llm.persona", None)
            with self.assertRaises(ValueError):
                store.set("llm.base_url", "null")
            store.set("tts.volume", 300)
            self.assertEqual(store.value("tts.volume"), 100)

    def test_persona_comes_from_config(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        self.assertIn("閒聊花花", config.llm.persona)

    def test_idle_chat_defaults(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        self.assertFalse(config.idle_chat.enabled)
        self.assertGreater(config.idle_chat.timeout_s, 0)

    def test_live_settings_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=Path(directory) / "settings.json",
            )
            live = LiveSettings(store)
            self.assertEqual(live.volume, 100)
            self.assertTrue(live.set("tts.volume", 70))
            self.assertEqual(live.volume, 70)
            self.assertFalse(live.set("tts.backend", "cosyvoice"))

    def test_live_name_and_manual_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=Path(directory) / "settings.json",
            )
            live = LiveSettings(store)
            self.assertEqual(live.name, "花花")
            self.assertFalse(live.manual_busy)
            self.assertTrue(live.set("app.name", "小花"))
            self.assertEqual(live.name, "小花")

    def test_set_path(self) -> None:
        target: dict = {}
        set_path(target, "a.b", 1)
        set_path(target, "a.c", 2)
        self.assertEqual(target, {"a": {"b": 1, "c": 2}})

    def test_invalid_path_rejected(self) -> None:
        store = SettingsStore(PROJECT_ROOT / "config.toml")
        with self.assertRaises(KeyError):
            store.set("nope.nothing", 1)


class BusTests(unittest.TestCase):
    def test_restart_required_flag(self) -> None:
        runtime = RuntimeControl()
        self.assertFalse(runtime.restart_requested)
        runtime.request_restart()
        self.assertTrue(runtime.restart_requested)

    def test_restart_required_exception_is_raised(self) -> None:
        with self.assertRaises(RestartRequired):
            raise RestartRequired()


class MemoryTests(unittest.TestCase):
    def test_clear_count_list_older(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = ConversationMemory(Path(directory) / "test.db")
            for i in range(6):
                memory.add("user" if i % 2 == 0 else "assistant", f"訊息{i}")
            self.assertEqual(memory.count(), 6)
            self.assertEqual(len(memory.list_all()), 6)
            older = memory.older_than(2)
            self.assertEqual(len(older), 2)
            self.assertEqual(older[0]["content"], "訊息0")
            memory.clear()
            self.assertEqual(memory.count(), 0)
            memory.close()


class LlmHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_returns_false_on_connection_error(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        client = LlamaCppClient(config.llm)
        request = httpx.Request("GET", client.config.base_url)
        try:
            with mock.patch.object(
                client._client,
                "get",
                new=mock.AsyncMock(
                    side_effect=httpx.ConnectError("down", request=request)
                ),
            ):
                self.assertFalse(await client.health())
        finally:
            await client.close()


class ControllerSummaryTests(unittest.TestCase):
    def test_persona_retains_summary_cache_and_clears(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        controller = FlowerController(
            config=config,
            asr=mock.MagicMock(),
            llm=mock.MagicMock(),
            tts=mock.MagicMock(),
            memory=mock.MagicMock(),
            aec=mock.MagicMock(),
            reminders=mock.MagicMock(),
        )
        base_persona = controller._persona_with_summary()
        self.assertNotIn("背景（較早的對話摘要）", base_persona)

        controller._summary_cache = "使用者喜歡喝烏龍茶。"
        persona_with_cache = controller._persona_with_summary()
        self.assertIn("背景（較早的對話摘要）：\n使用者喜歡喝烏龍茶。", persona_with_cache)

        controller.clear_summary()
        self.assertEqual(controller._summary_cache, "")
        self.assertEqual(controller._summary_count, 0)
        self.assertNotIn("背景（較早的對話摘要）", controller._persona_with_summary())


if __name__ == "__main__":
    unittest.main()

