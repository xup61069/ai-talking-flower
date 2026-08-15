from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np

from talking_flower.bus import RestartRequired, RuntimeControl, StatusBus
from talking_flower.config import load_config
from talking_flower.memory import ConversationMemory
from talking_flower.settings import LiveSettings, SettingsStore, set_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SettingsStoreTests(unittest.TestCase):
    def test_merge_with_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(PROJECT_ROOT / "config.toml")
            store.settings_path = Path(directory) / "settings.json"
            store.set("tts.volume", 55)
            store.set("llm.temperature", 1.2)
            config = store.load_config()
            self.assertEqual(config.tts.volume, 55)
            self.assertEqual(config.llm.temperature, 1.2)
            self.assertEqual(config.tts.backend, "kokoro")
            self.assertEqual(store.value("tts.volume"), 55)
            self.assertTrue(store.settings_path.is_file())

    def test_persona_comes_from_config(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        self.assertIn("閒聊花花", config.llm.persona)

    def test_idle_chat_defaults(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        self.assertFalse(config.idle_chat.enabled)
        self.assertGreater(config.idle_chat.timeout_s, 0)

    def test_live_settings_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(PROJECT_ROOT / "config.toml")
            store.settings_path = Path(directory) / "settings.json"
            live = LiveSettings(store)
            self.assertEqual(live.volume, 100)
            self.assertTrue(live.set("tts.volume", 70))
            self.assertEqual(live.volume, 70)
            self.assertFalse(live.set("tts.backend", "cosyvoice"))

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


if __name__ == "__main__":
    unittest.main()
