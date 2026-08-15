from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import httpx
from httpx import ASGITransport

from talking_flower.bus import RuntimeControl, StatusBus
from talking_flower.settings import LiveSettings, SettingsStore
from talking_flower.web import AppContext, WebServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        store = SettingsStore(PROJECT_ROOT / "config.toml")
        store.settings_path = Path(self._temp.name) / "settings.json"
        live = LiveSettings(store)
        bus = StatusBus()
        runtime = RuntimeControl()
        ctx = AppContext(store=store, live=live, bus=bus, runtime=runtime)
        server = WebServer(ctx)
        self.ctx = ctx
        self.client = httpx.AsyncClient(
            transport=ASGITransport(app=server.app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self._temp.cleanup()

    async def test_status(self) -> None:
        response = await self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["listening"], True)

    async def test_settings_payload(self) -> None:
        response = await self.client.get("/api/settings")
        settings = response.json()["settings"]
        paths = {item["path"] for item in settings}
        self.assertIn("tts.volume", paths)
        self.assertIn("llm.persona", paths)
        self.assertIn("idle_chat.enabled", paths)
        self.assertGreater(len(settings), 30)

    async def test_live_update_no_restart(self) -> None:
        response = await self.client.put(
            "/api/settings",
            json={"paths": {"tts.volume": 80, "llm.temperature": 1.1}},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(set(body["applied_live"]), {"tts.volume", "llm.temperature"})
        self.assertEqual(body["needs_restart"], [])
        self.assertFalse(self.ctx.runtime.restart_requested)
        self.assertEqual(self.ctx.live.volume, 80)

    async def test_restart_update_sets_flag(self) -> None:
        response = await self.client.put(
            "/api/settings",
            json={"paths": {"tts.backend": "cosyvoice"}},
        )
        body = response.json()
        self.assertEqual(body["needs_restart"], ["tts.backend"])
        self.assertTrue(self.ctx.runtime.restart_requested)

    async def test_invalid_path(self) -> None:
        response = await self.client.put("/api/settings", json={"paths": {"xxx.yyy": 1}})
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIn("xxx.yyy", body["errors"])

    async def test_devices(self) -> None:
        response = await self.client.get("/api/devices")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()["devices"], list)

    async def test_memory_and_voices(self) -> None:
        response = await self.client.get("/api/memory")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()["messages"], list)
        response = await self.client.get("/api/voices")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()["voices"], list)

    async def test_actions(self) -> None:
        response = await self.client.post("/api/action", json={"action": "pause"})
        self.assertTrue(response.json()["ok"])
        self.assertFalse(self.ctx.live.listening)
        response = await self.client.post("/api/action", json={"action": "resume"})
        self.assertTrue(response.json()["ok"])
        response = await self.client.post("/api/action", json={"action": "unknown_xyz"})
        self.assertEqual(response.status_code, 400)

    async def test_voice_ref_roundtrip(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            config = Path(temp.name) / "config.toml"
            config.write_bytes((PROJECT_ROOT / "config.toml").read_bytes())
            store = SettingsStore(config)
            store.settings_path = Path(temp.name) / "settings.json"
            live = LiveSettings(store)
            bus = StatusBus()
            runtime = RuntimeControl()
            ctx = AppContext(store=store, live=live, bus=bus, runtime=runtime)
            client = httpx.AsyncClient(
                transport=ASGITransport(app=WebServer(ctx).app),
                base_url="http://test",
            )
            wav = b"RIFF" + b"\x00" * 100
            data_url = "data:audio/wav;base64," + __import__("base64").b64encode(wav).decode()
            response = await client.post(
                "/api/voice-ref",
                json={"name": "測試聲線", "data_url": data_url, "transcript": "今天天氣真好。"},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertTrue(body["ok"])
            active = Path(temp.name) / "voice" / "active.json"
            self.assertTrue(active.is_file())
            self.assertIn("reference.wav", active.read_text(encoding="utf-8"))
            self.assertTrue((Path(temp.name) / "voice" / "reference.wav").is_file())
            response = await client.get("/api/voice-ref")
            info = response.json()
            self.assertEqual(info["name"], "測試聲線")
            self.assertEqual(info["prompt_text"], "今天天氣真好。")
            await client.aclose()
        finally:
            temp.cleanup()

    async def test_voice_ref_requires_transcript(self) -> None:
        response = await self.client.post(
            "/api/voice-ref",
            json={"name": "x", "data_url": "data:audio/wav;base64,AAAA", "transcript": "  "},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
