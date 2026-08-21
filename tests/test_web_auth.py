from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from httpx import ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.bus import RuntimeControl, StatusBus
from talking_flower.settings import LiveSettings, SettingsStore
from talking_flower.web import AppContext, WebServer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOKEN = "my-secret-token"
TOKEN_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()


class WebAuthTests(unittest.IsolatedAsyncioTestCase):
    def _make_server(self, token_hash: str):
        self._temp = tempfile.TemporaryDirectory()
        settings_path = Path(self._temp.name) / "settings.json"
        if token_hash:
            settings_path.write_text(
                '{"web.auth_token": "%s"}' % token_hash, encoding="utf-8"
            )
        store = SettingsStore(
            PROJECT_ROOT / "config.toml",
            settings_path=settings_path,
        )
        live = LiveSettings(store)
        ctx = AppContext(store=store, live=live, bus=StatusBus(), runtime=RuntimeControl())
        server = WebServer(ctx)
        client = httpx.AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test")
        return client, store

    async def test_no_token_configured_allows_all(self) -> None:
        client, _ = self._make_server("")
        try:
            response = await client.get("/api/status")
            self.assertEqual(response.status_code, 200)
        finally:
            await client.aclose()
            self._temp.cleanup()

    async def test_missing_token_rejected_401(self) -> None:
        client, _ = self._make_server(TOKEN_HASH)
        try:
            response = await client.get("/api/status")
            self.assertEqual(response.status_code, 401)
        finally:
            await client.aclose()
            self._temp.cleanup()

    async def test_wrong_token_rejected_401(self) -> None:
        client, _ = self._make_server(TOKEN_HASH)
        try:
            response = await client.get("/api/status", headers={"X-Auth-Token": "wrong"})
            self.assertEqual(response.status_code, 401)
        finally:
            await client.aclose()
            self._temp.cleanup()

    async def test_correct_token_header_accepted(self) -> None:
        client, _ = self._make_server(TOKEN_HASH)
        try:
            response = await client.get("/api/status", headers={"X-Auth-Token": TOKEN})
            self.assertEqual(response.status_code, 200)
        finally:
            await client.aclose()
            self._temp.cleanup()

    async def test_correct_token_query_param_accepted(self) -> None:
        client, _ = self._make_server(TOKEN_HASH)
        try:
            response = await client.get(f"/api/status?token={TOKEN}")
            self.assertEqual(response.status_code, 200)
        finally:
            await client.aclose()
            self._temp.cleanup()

    async def test_static_ui_not_blocked_by_token(self) -> None:
        """UI 靜態頁不擋（登入靠 prompt 輸入 token），只有 /api/* 需認證。"""
        client, _ = self._make_server(TOKEN_HASH)
        try:
            # 非 /api/ 路徑不受 middleware 影響（此處無 static mount，404 但非 401）
            response = await client.get("/")
            self.assertNotEqual(response.status_code, 401)
        finally:
            await client.aclose()
            self._temp.cleanup()

    async def test_token_setting_in_specs(self) -> None:
        client, store = self._make_server("")
        try:
            paths = {spec["path"] for spec in store.as_payload()}
            self.assertIn("web.auth_token", paths)
        finally:
            await client.aclose()
            self._temp.cleanup()


if __name__ == "__main__":
    unittest.main()
