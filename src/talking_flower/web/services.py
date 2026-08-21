"""後端服務層：AEC 校準、測試朗讀/對話、CosyVoice 熱載與重啟。

自 web.py（god module）拆出；路由層透過 services 實例呼叫。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import subprocess
import sys

import httpx

from ..controller import FlowerController
from .context import AppContext


LOGGER = logging.getLogger(__name__)


class WebServices:
    """依附 AppContext 的非同步服務集合。"""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    async def run_aec_calibration(self) -> None:
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
        else:
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
        finally:
            self.ctx.live.manual_busy = False
            self.ctx.bus.publish({"type": "calibration", "state": "end"})

    async def run_test_tts(self, text: str) -> None:
        controller: FlowerController | None = self.ctx.controller
        try:
            if controller is None:
                return
            await controller.tts.begin_turn()
            await controller.tts.speak(text)
        except Exception:
            LOGGER.exception("測試語音失敗")
        finally:
            self.ctx.live.manual_busy = False
            if controller is not None:
                await controller.tts.end_turn()

    async def run_test_llm(self, text: str, speak: bool) -> None:
        controller = self.ctx.controller
        try:
            if controller is not None:
                await controller.text_turn(text, speak=speak)
        except Exception:
            LOGGER.exception("測試 LLM 失敗")
        finally:
            self.ctx.live.manual_busy = False

    def _cosyvoice_base_url(self) -> str | None:
        base_url = str(self.ctx.store.value("tts.base_url")).rstrip("/")
        return base_url if "50000" in base_url else None

    async def hot_swap_speaker(self) -> bool:
        base_url = self._cosyvoice_base_url()
        if base_url is None:
            return False
        root: Path = self.ctx.store.project_root
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

    async def reload_cosyvoice_style(self) -> bool:
        base_url = self._cosyvoice_base_url()
        if base_url is None:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{base_url}/reload")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def restart_cosyvoice(self) -> None:
        root: Path = self.ctx.store.project_root
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
