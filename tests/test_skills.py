from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.commands import VoiceCommander
from talking_flower.controller import FlowerController
from talking_flower.config import load_config
from talking_flower.reminders import ReminderScheduler
from talking_flower.skills import load_builtin_skills
from talking_flower.skills.weather import fetch_cwa_summary


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_controller(store=None):
    ctrl = mock.MagicMock()
    ctrl.live = mock.MagicMock()
    ctrl.live.volume = 80
    ctrl.live.speed = 0.9
    ctrl.live.set = mock.MagicMock(return_value=True)
    ctrl.bus = None
    ctrl.store = store
    temp = tempfile.TemporaryDirectory()
    ctrl._temp_dir = temp
    ctrl.reminders = ReminderScheduler(Path(temp.name) / "r.db")
    return ctrl


class SkillsRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        load_builtin_skills()
        self.commander = VoiceCommander()

    def test_registry_has_builtin_and_weather(self) -> None:
        names = self.commander.skill_names
        for expected in ("time_query", "relative_reminder", "absolute_reminder", "switch_persona", "volume_speed", "weather"):
            self.assertIn(expected, names)

    def test_time_query_via_registry(self) -> None:
        ctrl = _make_controller()
        try:
            res = self.commander.try_execute("現在幾點？", ctrl)
            self.assertTrue(res.handled)
            self.assertEqual(res.action, "time_query")
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    def test_reminder_via_registry(self) -> None:
        ctrl = _make_controller()
        try:
            res = self.commander.try_execute("5分鐘後提醒我喝水", ctrl)
            self.assertTrue(res.handled)
            self.assertEqual(res.action, "add_reminder")
            self.assertIn("喝水", res.reply)
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    def test_persona_switch_via_registry_persists(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            from talking_flower.settings import SettingsStore

            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=Path(temp.name) / "settings.json",
            )
            ctrl = _make_controller(store=store)
            try:
                res = self.commander.try_execute("切換到夜間模式", ctrl)
                self.assertTrue(res.handled)
                self.assertEqual(store.value("profile.persona_preset"), "night")
            finally:
                ctrl.reminders.close()
                ctrl._temp_dir.cleanup()
        finally:
            temp.cleanup()

    def test_volume_up_via_registry(self) -> None:
        ctrl = _make_controller()
        try:
            res = self.commander.try_execute("大聲一點", ctrl)
            self.assertTrue(res.handled)
            self.assertEqual(ctrl.live.volume, 95)
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    def test_unmatched_falls_through(self) -> None:
        ctrl = _make_controller()
        try:
            res = self.commander.try_execute("跟我聊聊今天發生的事", ctrl)
            self.assertFalse(res.handled)
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    def test_skill_exception_does_not_break_chain(self) -> None:
        registry = type(load_builtin_skills())()  # 空 registry 同型別
        calls: list[str] = []

        def bad_handler(text, controller):
            raise RuntimeError("boom")

        def good_handler(text, controller):
            calls.append("good")
            from talking_flower.commands import CommandResult

            return CommandResult(handled=True, reply="ok", action="ok")

        registry.register("bad", bad_handler)
        registry.register("good", good_handler)
        result = registry.try_execute("anything", None)
        self.assertTrue(result.handled)
        self.assertEqual(calls, ["good"])


CWA_FIXTURE = {
    "records": {
        "location": [
            {
                "weatherElement": [
                    {
                        "elementName": "Wx",
                        "time": [
                            {"startTime": "2026-08-22 06:00:00", "parameter": {"parameterName": "多雲"}},
                            {"startTime": "2026-08-23 06:00:00", "parameter": {"parameterName": "短暫陣雨"}},
                        ],
                    },
                    {
                        "elementName": "MinT",
                        "time": [
                            {"startTime": "2026-08-22 06:00:00", "parameter": {"parameterName": "26"}},
                            {"startTime": "2026-08-23 06:00:00", "parameter": {"parameterName": "25"}},
                        ],
                    },
                    {
                        "elementName": "MaxT",
                        "time": [
                            {"startTime": "2026-08-22 06:00:00", "parameter": {"parameterName": "33"}},
                            {"startTime": "2026-08-23 06:00:00", "parameter": {"parameterName": "31"}},
                        ],
                    },
                ]
            }
        ]
    }
}


class WeatherSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        load_builtin_skills()
        self.commander = VoiceCommander()

    def test_fetch_summary_today(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=CWA_FIXTURE))
        )
        summary = fetch_cwa_summary("KEY", "臺北市", "今天", client=client)
        self.assertIsNotNone(summary)
        self.assertIn("臺北市", summary)
        self.assertIn("多雲", summary)
        self.assertIn("26 到 33 度", summary)
        client.close()

    def test_fetch_summary_tomorrow_rain_hint(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=CWA_FIXTURE))
        )
        summary = fetch_cwa_summary("KEY", "臺北市", "明天", client=client)
        self.assertIsNotNone(summary)
        self.assertIn("陣雨", summary)
        self.assertIn("帶傘", summary)
        client.close()

    def test_no_api_key_falls_through_to_llm(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            from talking_flower.settings import SettingsStore

            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=Path(temp.name) / "settings.json",
            )
            ctrl = _make_controller(store=store)
            try:
                res = self.commander.try_execute("明天會下雨嗎？", ctrl)
                self.assertFalse(res.handled)  # 交給 LLM
            finally:
                ctrl.reminders.close()
                ctrl._temp_dir.cleanup()
        finally:
            temp.cleanup()

    def test_with_api_key_handles_rain_question(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            from talking_flower.settings import SettingsStore

            store = SettingsStore(
                PROJECT_ROOT / "config.toml",
                settings_path=Path(temp.name) / "settings.json",
            )
            store.set("weather.api_key", "TESTKEY")
            ctrl = _make_controller(store=store)
            try:
                with mock.patch(
                    "talking_flower.skills.weather.fetch_cwa_summary",
                    return_value="臺北市的天气：⛅多雲，26 到 33 度。",
                ):
                    res = self.commander.try_execute("明天會下雨嗎？", ctrl)
                self.assertTrue(res.handled)
                self.assertEqual(res.action, "weather")
            finally:
                ctrl.reminders.close()
                ctrl._temp_dir.cleanup()
        finally:
            temp.cleanup()


class WakeWordTests(unittest.IsolatedAsyncioTestCase):
    """低成本喚醒詞：ASR 前綴偵測（interaction.wake_word 非空時啟用）。"""

    def _make_controller(self, wake_word: str) -> FlowerController:
        config = load_config(PROJECT_ROOT / "config.toml")
        live = mock.MagicMock()
        live.wake_word = wake_word
        live.persona_preset = "energetic"
        live.listening = True
        live.manual_busy = False
        tts = mock.MagicMock()
        tts.begin_turn = mock.AsyncMock()
        tts.end_turn = mock.AsyncMock()
        tts.speak = mock.AsyncMock()
        temp = tempfile.TemporaryDirectory()
        ctrl = FlowerController(
            config=config,
            asr=mock.AsyncMock(),
            llm=mock.AsyncMock(),
            tts=tts,
            memory=mock.MagicMock(),
            aec=mock.MagicMock(),
            live=live,
            bus=None,
            reminders=mock.MagicMock(),
        )
        ctrl._temp_dir = temp
        return ctrl

    async def _speak_text(self, ctrl, spoken: str) -> None:
        ctrl.asr.transcribe = mock.AsyncMock(return_value=spoken)
        await ctrl._handle_utterance(b"\x00" * 1600)

    async def test_utterance_without_wake_word_is_ignored(self) -> None:
        ctrl = self._make_controller("花花")
        try:
            await self._speak_text(ctrl, "今天天氣真好")
            ctrl.tts.speak.assert_not_called()
            # 也不應送 LLM
            ctrl.llm.stream_reply.assert_not_called()
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    async def test_bare_wake_word_gets_ack(self) -> None:
        ctrl = self._make_controller("花花")
        try:
            await self._speak_text(ctrl, "花花")
            ctrl.tts.speak.assert_called_once()
            args = ctrl.tts.speak.call_args[0]
            self.assertIn("我在", args[0])
            ctrl.llm.stream_reply.assert_not_called()
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    async def test_wake_word_plus_command_executes(self) -> None:
        ctrl = self._make_controller("花花")
        try:
            await self._speak_text(ctrl, "花花現在幾點")
            ctrl.tts.speak.assert_called_once()
            reply = ctrl.tts.speak.call_args[0][0]
            self.assertIn("現在時間是", reply)
            ctrl.llm.stream_reply.assert_not_called()
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    async def test_empty_wake_word_passes_through(self) -> None:
        ctrl = self._make_controller("")
        try:
            await self._speak_text(ctrl, "現在幾點")
            ctrl.tts.speak.assert_called_once()
            reply = ctrl.tts.speak.call_args[0][0]
            self.assertIn("現在時間是", reply)
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
