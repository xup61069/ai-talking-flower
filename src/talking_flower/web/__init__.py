"""Web 控制台套件：組裝 auth/routes/ws/services 與靜態 UI。

對外介面與舊 web.py 相同：AppContext、WebServer、install_bus_log_handler。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from ..bus import BusLogHandler, StatusBus
from .context import AppContext, token_hash
from .routes import register_routes
from .services import WebServices
from .ws import register_ws


LOGGER = logging.getLogger(__name__)

__all__ = ["AppContext", "WebServer", "install_bus_log_handler"]


class WebServer:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.services = WebServices(ctx)
        self.app = FastAPI(title="AI 閒聊花花 控制台")
        self._register_auth()
        register_routes(self.app, ctx, self.services)
        register_ws(self.app, ctx)

    def _register_auth(self) -> None:
        """Token 認證：web.auth_token 存 SHA256；空字串=本機信任模式不擋。"""
        app = self.app
        ctx = self.ctx

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            expected_hash = str(ctx.store.value("web.auth_token") or "").strip()
            if expected_hash and request.url.path.startswith("/api/"):
                provided = (
                    request.headers.get("X-Auth-Token", "")
                    or request.query_params.get("token", "")
                )
                if not provided or token_hash(provided) != expected_hash:
                    return JSONResponse({"ok": False, "reason": "需要有效的 X-Auth-Token"}, status_code=401)
            return await call_next(request)

    async def serve(self, host: str, port: int) -> None:
        if host == "0.0.0.0":
            LOGGER.warning(
                "Web 控制台綁定到 0.0.0.0：/api/action 可執行 PowerShell、/api/voice-ref 可寫檔，"
                "請設定 web.auth_token 後再對外，切勿無 Token 暴露公網"
            )
        ui_dir = self.ctx.store.project_root / "ui"
        if ui_dir.is_dir():
            self.app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        await server.serve()


def install_bus_log_handler(bus: StatusBus, level: int = logging.INFO) -> None:
    handler = BusLogHandler(bus)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    return handler
