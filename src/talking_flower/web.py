from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import subprocess
import sys

import fastapi
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
import uvicorn

from .bus import BusLogHandler, RuntimeControl, StatusBus
from .controller import FlowerController
from .memory import ConversationMemory
from .personas import get_persona_by_id, list_personas
from .settings import LiveSettings, SettingsStore, SPEC_BY_PATH


LOGGER = logging.getLogger(__name__)


@dataclass
class AppContext:
    store: SettingsStore
    live: LiveSettings
    bus: StatusBus
    runtime: RuntimeControl
    controller: FlowerController | None = None
    memory: ConversationMemory | None = None
    restart_note: str = field(default="")


class WebServer:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.app = FastAPI(title="AI 閒聊花花 控制台")
        self._register()

    def _register(self) -> None:
        app = self.app
        ctx = self.ctx

        @app.get("/api/status")
        async def status() -> dict:
            controller = ctx.controller
            return {
                "state": controller.state.value if controller is not None else "未啟動",
                "busy": controller is not None and controller._turn_task is not None,
                "listening": ctx.live.listening,
                "name": ctx.store.value("app.name"),
                "persona": getattr(ctx.live, "persona_preset", "energetic"),
                "tts_backend": ctx.store.value("tts.backend"),
                "restart_note": ctx.restart_note,
            }

        @app.websocket("/api/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
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

        @app.get("/api/settings")
        async def settings() -> dict:
            return {"settings": ctx.store.as_payload()}

        @app.put("/api/settings")
        async def settings_update(payload: dict) -> dict:
            paths = payload.get("paths")
            if not isinstance(paths, dict):
                return JSONResponse({"ok": False, "reason": "需要 paths 物件"}, status_code=400)
            applied_live: list[str] = []
            needs_restart: list[str] = []
            errors: list[str] = []
            for path, value in paths.items():
                spec = SPEC_BY_PATH.get(path)
                if spec is None:
                    errors.append(path)
                    continue
                try:
                    ctx.store.set(path, value)
                except (TypeError, ValueError) as error:
                    errors.append(f"{path}: {error}")
                    continue
                if spec.apply == "live":
                    applied_live.append(path)
                    if path == "aec.delay_ms":
                        if ctx.controller is not None:
                            ctx.controller.apply_live(path, value)
                    else:
                        ctx.live.set(path, value)
                else:
                    needs_restart.append(path)
            if needs_restart and ctx.runtime is not None:
                ctx.runtime.request_restart()
            return {
                "ok": not errors,
                "applied_live": applied_live,
                "needs_restart": needs_restart,
                "errors": errors,
            }

        @app.get("/api/devices")
        async def devices() -> dict:
            import sounddevice as sd

            items = []
            for index, device in enumerate(sd.query_devices()):
                inputs = int(device["max_input_channels"])
                outputs = int(device["max_output_channels"])
                if inputs <= 0 and outputs <= 0:
                    continue
                hostapi = sd.query_hostapis(device["hostapi"])["name"]
                items.append(
                    {
                        "index": index,
                        "name": str(device["name"]),
                        "hostapi": str(hostapi),
                        "inputs": inputs,
                        "outputs": outputs,
                        "default_samplerate": int(device["default_samplerate"]),
                    }
                )
            return {"devices": items}

        @app.post("/api/action")
        async def action(payload: dict) -> dict:
            name = payload.get("action")
            if name in {"test_tts", "test_llm"}:
                controller = ctx.controller
                if ctx.live.manual_busy or (
                    controller is not None and controller._turn_task is not None
                ):
                    return {"ok": False, "reason": "花花正在忙，請等一下"}
            if name == "pause":
                ctx.live.listening = False
                ctx.bus.publish({"type": "paused"})
                return {"ok": True}
            if name == "resume":
                ctx.live.listening = True
                ctx.bus.publish({"type": "resumed"})
                return {"ok": True}
            if name == "test_tts":
                text = str(payload.get("text") or "嗨，我準備好了。")
                ctx.live.manual_busy = True
                asyncio.create_task(self._run_test_tts(text))
                return {"ok": True}
            if name == "test_llm":
                text = str(payload.get("text") or "跟我說一句話")
                speak = bool(payload.get("speak", True))
                ctx.live.manual_busy = True
                asyncio.create_task(self._run_test_llm(text, speak))
                return {"ok": True}
            if name == "clear_memory":
                if ctx.memory is not None:
                    ctx.memory.clear()
                if ctx.controller is not None:
                    ctx.controller.clear_summary()
                return {"ok": True}
            if name == "export_memory":
                if ctx.memory is None:
                    return {"ok": False, "reason": "記憶尚未開啟"}
                return {"ok": True, "messages": ctx.memory.list_all()}
            if name == "restart":
                ctx.restart_note = str(payload.get("note", ""))
                ctx.runtime.request_restart()
                return {"ok": True}
            if name == "reload_cosyvoice_style":
                ok = await self._reload_cosyvoice_style()
                return {"ok": ok, "reason": "" if ok else "無法連上 CosyVoice server"}
            if name == "restart_cosyvoice":
                asyncio.create_task(self._restart_cosyvoice())
                return {"ok": True}
            if name == "calibrate_aec":
                asyncio.create_task(self._run_aec_calibration())
                return {"ok": True}
            if name == "poke":
                if ctx.controller is not None:
                    reply = await ctx.controller.poke()
                    return {"ok": True, "reply": reply}
                return {"ok": True, "reply": "在呢～"}
            return JSONResponse({"ok": False, "reason": f"未知動作：{name}"}, status_code=400)

        @app.get("/api/personas")
        async def personas_list() -> dict:
            return {
                "personas": list_personas(),
                "active": getattr(ctx.live, "persona_preset", "energetic"),
            }

        @app.post("/api/personas/select")
        async def personas_select(payload: dict) -> dict:
            persona_id = str(payload.get("id", "")).strip()
            preset = get_persona_by_id(persona_id)
            if preset is None:
                return JSONResponse({"ok": False, "reason": "找不到指定的性格預設"}, status_code=404)
            ctx.store.set("llm.persona", preset.persona)
            ctx.store.set("llm.temperature", preset.temperature)
            ctx.store.set("llm.top_p", preset.top_p)
            ctx.store.set("tts.speed", preset.speed)
            if preset.idle_prompt:
                ctx.store.set("idle_chat.prompt", preset.idle_prompt)
            ctx.store.set("profile.persona_preset", preset.id)
            ctx.live.set("llm.persona", preset.persona)
            ctx.live.set("llm.temperature", preset.temperature)
            ctx.live.set("llm.top_p", preset.top_p)
            ctx.live.set("tts.speed", preset.speed)
            if preset.idle_prompt:
                ctx.live.set("idle_chat.prompt", preset.idle_prompt)
            ctx.live.set("profile.persona_preset", preset.id)
            ctx.bus.publish({"type": "persona_changed", "id": preset.id, "name": preset.name})
            return {"ok": True, "id": preset.id, "name": preset.name}

        @app.get("/api/reminders")
        async def reminders_list() -> dict:
            if ctx.controller is None or ctx.controller.reminders is None:
                return {"reminders": []}
            return {"reminders": ctx.controller.reminders.list_all()}

        @app.post("/api/reminders")
        async def reminders_create(payload: dict) -> dict:
            text = str(payload.get("text", "")).strip()
            in_seconds = float(payload.get("in_seconds", 60))
            if not text:
                return JSONResponse({"ok": False, "reason": "提醒文字不能為空"}, status_code=400)
            if ctx.controller is None or ctx.controller.reminders is None:
                return JSONResponse({"ok": False, "reason": "提醒服務尚未就緒"}, status_code=503)
            reminder = ctx.controller.reminders.add(text, in_seconds)
            ctx.bus.publish({"type": "reminder_added", "reminder": reminder.to_dict()})
            return {"ok": True, "reminder": reminder.to_dict()}

        @app.delete("/api/reminders/{reminder_id}")
        async def reminders_delete(reminder_id: int) -> dict:
            if ctx.controller is None or ctx.controller.reminders is None:
                return JSONResponse({"ok": False, "reason": "提醒服務尚未就緒"}, status_code=503)
            ok = ctx.controller.reminders.delete(int(reminder_id))
            return {"ok": ok}

        @app.get("/api/memory")
        async def memory() -> dict:
            if ctx.memory is None:
                return {"messages": []}
            return {"messages": ctx.memory.list_all()}

        @app.get("/api/memory/search")
        async def memory_search(q: str = "") -> dict:
            if ctx.memory is None:
                return {"messages": []}
            return {"messages": ctx.memory.search(q)}

        @app.delete("/api/memory/{message_id}")
        async def memory_delete(message_id: int) -> dict:
            if ctx.memory is None:
                return JSONResponse({"ok": False, "reason": "記憶尚未就緒"}, status_code=503)
            ok = ctx.memory.delete(int(message_id))
            return {"ok": ok}

        @app.get("/api/voices")
        async def voices() -> dict:
            root = ctx.store.project_root
            manifest = root / "voice" / "candidates" / "TTS-SCDuFSC" / "voices.json"
            active_path = root / "voice" / "active.json"
            items: list[dict] = []
            if manifest.is_file():
                try:
                    parsed = json.loads(manifest.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    parsed = []
                for index, item in enumerate(parsed):
                    items.append(
                        {
                            "index": index + 1,
                            "voice": item.get("voice", ""),
                            "file": item.get("file", ""),
                            "transcript": item.get("transcript", ""),
                        }
                    )
            active = None
            if active_path.is_file():
                try:
                    active = json.loads(active_path.read_text(encoding="utf-8")).get("name")
                except (json.JSONDecodeError, OSError):
                    pass
            return {"voices": items, "active": active}

        @app.post("/api/voices")
        async def voices_select(payload: dict) -> dict:
            index = int(payload.get("index", 0))
            root = ctx.store.project_root
            manifest = root / "voice" / "candidates" / "TTS-SCDuFSC" / "voices.json"
            active_path = root / "voice" / "active.json"
            if index == 0:
                if active_path.is_file():
                    active_path.unlink()
                asyncio.create_task(self._restart_cosyvoice())
                return {"ok": True, "voice": "官方示範音色"}
            if not manifest.is_file():
                return JSONResponse({"ok": False, "reason": "找不到 voices.json"}, status_code=404)
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
            if not 1 <= index <= len(parsed):
                return JSONResponse({"ok": False, "reason": "音色編號無效"}, status_code=400)
            item = parsed[index - 1]
            active_path.write_text(
                json.dumps(
                    {
                        "name": item["voice"],
                        "prompt_file": f"voice/candidates/TTS-SCDuFSC/{item['file']}",
                        "transcript": item["transcript"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            asyncio.create_task(self._restart_cosyvoice())
            return {"ok": True, "voice": item["voice"]}

        @app.get("/api/voice-ref")
        async def voice_ref() -> dict:
            root = ctx.store.project_root
            active_path = root / "voice" / "active.json"
            info: dict = {"name": None, "prompt_file": None, "prompt_text": None}
            if active_path.is_file():
                try:
                    info.update(json.loads(active_path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    pass
            return {"ok": True, **info}

        @app.post("/api/voice-ref")
        async def voice_ref_update(payload: dict) -> dict:
            name = str(payload.get("name", "")).strip() or "自訂參考音檔"
            data_url = str(payload.get("data_url", ""))
            transcript = str(payload.get("transcript", "")).strip()
            if "," not in data_url:
                return JSONResponse({"ok": False, "reason": "音檔資料無效"}, status_code=400)
            if not transcript:
                return JSONResponse({"ok": False, "reason": "請填寫逐字稿"}, status_code=400)
            root = ctx.store.project_root
            try:
                wav_bytes = base64.b64decode(data_url.split(",", 1)[1])
            except ValueError:
                return JSONResponse({"ok": False, "reason": "音檔資料無法解碼"}, status_code=400)
            if len(wav_bytes) < 44:
                return JSONResponse({"ok": False, "reason": "音檔太小，不是有效的 WAV"}, status_code=400)
            ref_path = root / "voice" / "reference.wav"
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            ref_path.write_bytes(wav_bytes)
            active_path = root / "voice" / "active.json"
            active_path.write_text(
                json.dumps(
                    {
                        "name": name,
                        "prompt_file": "voice/reference.wav",
                        "prompt_text": transcript,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            hot = await self._hot_swap_speaker()
            return {"ok": True, "voice": name, "hot_swapped": hot}

        @app.get("/api/style")
        async def style() -> dict:
            path = ctx.store.project_root / "voice" / "style.txt"
            return {"style": path.read_text(encoding="utf-8-sig") if path.is_file() else ""}

        @app.put("/api/style")
        async def style_update(payload: dict) -> dict:
            text = str(payload.get("style", ""))
            path = ctx.store.project_root / "voice" / "style.txt"
            path.write_text(text, encoding="utf-8")
            await self._reload_cosyvoice_style()
            return {"ok": True}

    async def _run_aec_calibration(self) -> None:
        store = self.ctx.store
        script = store.project_root / "tools" / "calibrate_aec.py"
        self.ctx.bus.publish(
            {"type": "log", "message": "AEC 校準開始：會播放 3.5 秒掃頻音，請保持安靜…"}
        )
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    str(script),
                    "--in-device",
                    str(store.value("audio.input_device")),
                    "--in-hostapi",
                    str(store.value("audio.input_hostapi")),
                    "--out-device",
                    str(store.value("audio.output_device")),
                    "--out-hostapi",
                    str(store.value("audio.output_hostapi")),
                ],
                cwd=str(store.project_root),
                capture_output=True,
                timeout=60,
            )
        except Exception as error:
            LOGGER.exception("AEC 校準失敗")
            self.ctx.bus.publish({"type": "log", "message": f"AEC 校準失敗：{error}"})
            return
        delay_ms: int | None = None
        for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
            if line.startswith("delay_ms="):
                delay_ms = int(line.split("=", 1)[1])
                break
        if delay_ms is None:
            detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            self.ctx.bus.publish(
                {"type": "log", "message": f"AEC 校準失敗：{detail[-1] if detail else '未知原因'}"}
            )
            return
        store.set("aec.delay_ms", delay_ms)
        if self.ctx.controller is not None:
            self.ctx.controller.apply_live("aec.delay_ms", delay_ms)
        self.ctx.bus.publish(
            {"type": "calibration", "delay_ms": delay_ms, "message": f"AEC 延遲已設為 {delay_ms} ms"}
        )

    async def _run_test_tts(self, text: str) -> None:
        controller = self.ctx.controller
        try:
            if controller is None:
                return
            tts = controller.tts
            await tts.begin_turn()
            await tts.speak(text)
        except Exception:
            LOGGER.exception("測試語音失敗")
        finally:
            self.ctx.live.manual_busy = False
            if controller is not None:
                await controller.tts.end_turn()

    async def _run_test_llm(self, text: str, speak: bool) -> None:
        controller = self.ctx.controller
        try:
            if controller is not None:
                await controller.text_turn(text, speak=speak)
        except Exception:
            LOGGER.exception("測試 LLM 失敗")
        finally:
            self.ctx.live.manual_busy = False

    async def _hot_swap_speaker(self) -> bool:
        base_url = str(self.ctx.store.value("tts.base_url")).rstrip("/")
        if "50000" not in base_url:
            return False
        root = self.ctx.store.project_root
        wav = root / "voice" / "reference.wav"
        active = root / "voice" / "active.json"
        if not wav.is_file() or not active.is_file():
            return False
        try:
            data = json.loads(active.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{base_url}/speaker",
                    json={"wav": str(wav), "text": str(data.get("prompt_text", ""))},
                )
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _reload_cosyvoice_style(self) -> bool:
        base_url = str(self.ctx.store.value("tts.base_url")).rstrip("/")
        if "50000" not in base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{base_url}/reload")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _restart_cosyvoice(self) -> None:
        root = self.ctx.store.project_root
        for name in ("stop-cosyvoice.ps1", "start-cosyvoice.ps1"):
            script = root / name
            if not script.is_file():
                continue
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                    ],
                    cwd=str(root),
                    capture_output=True,
                    timeout=150,
                )
            except Exception:
                LOGGER.exception("執行 %s 失敗", name)

    async def serve(self, host: str, port: int) -> None:
        if host == "0.0.0.0":
            LOGGER.warning("Web 控制台綁定到 0.0.0.0：/api/action 可執行 PowerShell、/api/voice-ref 可寫檔，請勿暴露到公網，建議加 token 鑑權")
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
