"""REST 路由層：/api/* 全部端點。

自 web.py（god module）拆出；僅做 HTTP 進出與參數驗證，業務委派 services。
"""
from __future__ import annotations

import asyncio
import base64
import json

from fastapi.responses import JSONResponse

from ..personas import get_persona_by_id, list_personas
from ..settings import SPEC_BY_PATH
from .context import AppContext
from .services import WebServices


def register_routes(app, ctx: AppContext, services: WebServices) -> None:
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
            asyncio.create_task(services.run_test_tts(text))
            return {"ok": True}
        if name == "test_llm":
            text = str(payload.get("text") or "跟我說一句話")
            speak = bool(payload.get("speak", True))
            ctx.live.manual_busy = True
            asyncio.create_task(services.run_test_llm(text, speak))
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
            ok = await services.reload_cosyvoice_style()
            return {"ok": ok, "reason": "" if ok else "無法連上 CosyVoice server"}
        if name == "restart_cosyvoice":
            asyncio.create_task(services.restart_cosyvoice())
            return {"ok": True}
        if name == "calibrate_aec":
            if ctx.live.manual_busy:
                return {"ok": False, "reason": "忙碌中，請稍後再試"}
            ctx.live.manual_busy = True
            ctx.bus.publish({"type": "calibration", "state": "start", "message": "校準中，暫停聆聽..."})
            asyncio.create_task(services.run_aec_calibration())
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

    @app.get("/api/metrics/history")
    async def metrics_history(limit: int = 50) -> dict:
        metrics = getattr(ctx.controller, "metrics", None) if ctx.controller else None
        if metrics is None:
            return {"history": [], "summary": {}}
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 50
        return {"history": metrics.recent(limit), "summary": metrics.summary(max(limit, 100))}

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

    @app.get("/api/memory/vector-search")
    async def memory_vector_search(q: str = "", limit: int = 5) -> dict:
        if ctx.memory is None:
            return {"messages": []}
        if not q.strip():
            return {"messages": []}
        try:
            limit = max(1, min(int(limit), 20))
        except (TypeError, ValueError):
            limit = 5
        try:
            results = ctx.memory.search_vector(q, limit=limit)
        except Exception:
            results = []
        return {"messages": results}

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
            asyncio.create_task(services.restart_cosyvoice())
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
        asyncio.create_task(services.restart_cosyvoice())
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
        hot = await services.hot_swap_speaker()
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
        await services.reload_cosyvoice_style()
        return {"ok": True}
