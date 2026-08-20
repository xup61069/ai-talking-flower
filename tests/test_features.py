from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

import httpx
from httpx import ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.bus import RuntimeControl, StatusBus
from talking_flower.config import load_config
from talking_flower.controller import FlowerController
from talking_flower.memory import ConversationMemory
from talking_flower.personas import get_persona_by_id, list_personas, PERSONA_PRESETS
from talking_flower.reminders import ReminderScheduler
from talking_flower.settings import LiveSettings, SettingsStore
from talking_flower.web import AppContext, WebServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PersonasTests(unittest.TestCase):
    def test_list_and_get_personas(self) -> None:
        personas = list_personas()
        self.assertGreaterEqual(len(personas), 4)
        ids = {p["id"] for p in personas}
        self.assertIn("energetic", ids)
        self.assertIn("night", ids)
        self.assertIn("work_buddy", ids)
        self.assertIn("snarky", ids)

        energetic = get_persona_by_id("energetic")
        self.assertIsNotNone(energetic)
        self.assertEqual(energetic.name, "元氣花花")
        self.assertGreater(len(energetic.poke_replies), 0)

        none_persona = get_persona_by_id("non_existent_123")
        self.assertIsNone(none_persona)


class RemindersTests(unittest.TestCase):
    def test_reminder_add_list_pop_delete(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(temp.name) / "test_reminders.db"
            scheduler = ReminderScheduler(db_path)
            r1 = scheduler.add("喝水", in_seconds=0.0)
            r2 = scheduler.add("休息", in_seconds=3600)

            active = scheduler.list_active()
            self.assertEqual(len(active), 2)

            due = scheduler.pop_due()
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].text, "喝水")
            self.assertTrue(due[0].spoken)

            # Now active should only have r2
            active_after = scheduler.list_active()
            self.assertEqual(len(active_after), 1)
            self.assertEqual(active_after[0]["text"], "休息")

            # Delete r2
            deleted = scheduler.delete(r2.id)
            self.assertTrue(deleted)
            self.assertEqual(len(scheduler.list_active()), 0)
            scheduler.close()
        finally:
            temp.cleanup()


class MemorySearchTests(unittest.TestCase):
    def test_search_and_delete_message(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            memory = ConversationMemory(Path(temp.name) / "test.db")
            memory.add("user", "今天天氣好嗎？")
            memory.add("assistant", "今天陽光明媚喔！")
            memory.add("user", "晚上想吃火鍋")

            all_msgs = memory.list_all()
            self.assertEqual(len(all_msgs), 3)
            self.assertIn("id", all_msgs[0])

            search_res = memory.search("火鍋")
            self.assertEqual(len(search_res), 1)
            self.assertEqual(search_res[0]["content"], "晚上想吃火鍋")

            first_id = all_msgs[0]["id"]
            self.assertTrue(memory.delete(first_id))
            self.assertEqual(len(memory.list_all()), 2)
            memory.close()
        finally:
            temp.cleanup()


class WebNewFeaturesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        store = SettingsStore(
            PROJECT_ROOT / "config.toml",
            settings_path=Path(self._temp.name) / "settings.json",
        )
        live = LiveSettings(store)
        bus = StatusBus()
        runtime = RuntimeControl()
        memory = ConversationMemory(Path(self._temp.name) / "flower.db")
        config = store.load_config()
        reminders = ReminderScheduler(Path(self._temp.name) / "reminders.db")
        controller = FlowerController(
            config=config,
            asr=mock.AsyncMock(),
            llm=mock.AsyncMock(),
            tts=mock.AsyncMock(),
            memory=memory,
            aec=mock.MagicMock(),
            live=live,
            bus=bus,
            runtime=runtime,
            reminders=reminders,
        )
        ctx = AppContext(
            store=store,
            live=live,
            bus=bus,
            runtime=runtime,
            controller=controller,
            memory=memory,
        )
        server = WebServer(ctx)
        self.ctx = ctx
        self.client = httpx.AsyncClient(
            transport=ASGITransport(app=server.app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        if self.ctx.memory:
            self.ctx.memory.close()
        if self.ctx.controller and self.ctx.controller.reminders:
            self.ctx.controller.reminders.close()
        self._temp.cleanup()

    async def test_personas_api(self) -> None:
        response = await self.client.get("/api/personas")
        self.assertEqual(response.status_code, 200)
        self.assertIn("personas", response.json())

        select_res = await self.client.post("/api/personas/select", json={"id": "night"})
        self.assertEqual(select_res.status_code, 200)
        self.assertEqual(self.ctx.live.persona_preset, "night")
        self.assertIn("夜間花花", self.ctx.live.persona)

    async def test_reminders_api(self) -> None:
        create_res = await self.client.post(
            "/api/reminders",
            json={"text": "吃維他命", "in_seconds": 300},
        )
        self.assertEqual(create_res.status_code, 200)
        reminder_id = create_res.json()["reminder"]["id"]

        list_res = await self.client.get("/api/reminders")
        self.assertEqual(len(list_res.json()["reminders"]), 1)

        del_res = await self.client.delete(f"/api/reminders/{reminder_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertTrue(del_res.json()["ok"])

    async def test_memory_search_and_delete_api(self) -> None:
        self.ctx.memory.add("user", "明天記得開會")
        self.ctx.memory.add("assistant", "好喔，已經記下來了！")

        search_res = await self.client.get("/api/memory/search?q=開會")
        self.assertEqual(len(search_res.json()["messages"]), 1)

        all_msgs = self.ctx.memory.list_all()
        target_id = all_msgs[0]["id"]
        del_res = await self.client.delete(f"/api/memory/{target_id}")
        self.assertTrue(del_res.json()["ok"])
        self.assertEqual(len(self.ctx.memory.list_all()), 1)

    async def test_poke_action(self) -> None:
        response = await self.client.post("/api/action", json={"action": "poke"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("reply", response.json())


if __name__ == "__main__":
    unittest.main()
