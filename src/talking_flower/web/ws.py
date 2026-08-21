"""WebSocket 事件流：/api/ws。"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from .context import AppContext
from .context import token_hash


def register_ws(app, ctx: AppContext) -> None:
    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        # Token 認證：web.auth_token 非空時需帶 X-Auth-Token header 或 ?token=
        expected_hash = str(ctx.store.value("web.auth_token") or "").strip()
        if expected_hash:
            token = (
                websocket.headers.get("x-auth-token", "")
                or websocket.query_params.get("token", "")
            )
            if not token or token_hash(token) != expected_hash:
                await websocket.close(code=4401)
                return
        await websocket.accept()
        queue = await ctx.bus.subscribe()
        try:
            for event in ctx.bus.history():
                await websocket.send_json(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    await websocket.send_json(event)
                except asyncio.TimeoutError:
                    await websocket.send_json({"type": "ping"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            ctx.bus.unsubscribe(queue)
