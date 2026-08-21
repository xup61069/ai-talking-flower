from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys
import webbrowser

import numpy as np

from .aec import create_echo_canceller
from .asr import create_recognizer
from .audio import list_audio_devices, resolve_device
from .bus import RestartRequired, RuntimeControl, StatusBus
from .config import Config, load_config
from .controller import FlowerController
from .llm import LlamaCppClient
from .memory import ConversationMemory
from .settings import LiveSettings, SettingsStore
from .tts import create_tts


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 閒聊花花")
    parser.add_argument("--config", default="config.toml", help="設定檔路徑")
    parser.add_argument("--check", action="store_true", help="檢查裝置與 llama-server")
    parser.add_argument("--list-devices", action="store_true", help="列出音訊裝置")
    parser.add_argument("--text", help="略過麥克風，直接測試一輪文字對話")
    parser.add_argument("--no-speak", action="store_true", help="文字測試時不要朗讀")
    parser.add_argument("--load-asr", action="store_true", help="搭配 --check 載入 ASR 模型")
    parser.add_argument("--no-web", action="store_true", help="不要啟動網頁控制台")
    parser.add_argument("--host", default="127.0.0.1", help="網頁控制台監聽位址")
    parser.add_argument("--port", type=int, default=7860, help="網頁控制台埠號")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def build_controller(
    config: Config,
    *,
    live: LiveSettings | None = None,
    bus: StatusBus | None = None,
    runtime: RuntimeControl | None = None,
    store: SettingsStore | None = None,
) -> tuple[FlowerController, LlamaCppClient, ConversationMemory]:
    llm = LlamaCppClient(config.llm)
    memory = ConversationMemory(config.app.database)
    aec = create_echo_canceller(
        config.aec,
        config.audio.sample_rate,
        config.project_root,
    )
    controller = FlowerController(
        config=config,
        asr=create_recognizer(config.asr),
        llm=llm,
        tts=create_tts(config.tts, config.audio, aec, live),
        memory=memory,
        aec=aec,
        live=live,
        bus=bus,
        runtime=runtime,
        store=store,
    )
    return controller, llm, memory


async def check(config: Config, *, load_asr: bool) -> int:
    print("設定檔：OK")
    try:
        index = resolve_device(
            config.audio.input_device,
            config.audio.input_hostapi,
            input_device=True,
        )
    except Exception as error:
        print(f"麥克風：找不到（{error}）")
        return 1
    print(f"麥克風：OK（裝置 {index}，{config.audio.input_device}）")
    llm = LlamaCppClient(config.llm)
    try:
        healthy = await llm.health()
    finally:
        await llm.close()
    print(f"llama-server：{'OK' if healthy else '無法連線'}")
    if not healthy:
        return 1
    try:
        tts = create_tts(config.tts, config.audio)
    except (RuntimeError, ValueError) as error:
        print(f"TTS：{error}")
        return 1
    try:
        tts_healthy = await tts.health()
    finally:
        await tts.close()
    label = {"kokoro": "Kokoro", "cosyvoice": "CosyVoice", "windows_sapi": "Windows SAPI"}.get(
        config.tts.backend, config.tts.backend
    )
    print(f"{label}：{'OK' if tts_healthy else '無法連線'}")
    if not tts_healthy:
        return 1
    try:
        aec = create_echo_canceller(
            config.aec,
            config.audio.sample_rate,
            config.project_root,
        )
    except Exception as error:
        print(f"WebRTC AEC3：{error}")
        return 1
    try:
        frame_size = config.audio.sample_rate * config.audio.block_ms // 1000
        processed = aec.process_capture(np.zeros(frame_size, dtype=np.float32))
        aec_healthy = len(processed) == frame_size
    finally:
        aec.close()
    print(f"WebRTC AEC3：{'OK' if aec_healthy else '處理失敗'}")
    if not aec_healthy:
        return 1
    if load_asr:
        recognizer = create_recognizer(config.asr)
        await recognizer.load()
        print(f"ASR：OK（{config.asr.model}）")
    return 0


async def run(args: argparse.Namespace, config_path: Path) -> int:
    if args.check:
        store = SettingsStore(config_path)
        return await check(store.load_config(), load_asr=args.load_asr)

    store = SettingsStore(config_path)
    live = LiveSettings(store)
    bus = StatusBus()
    runtime = RuntimeControl()

    ctx = None
    web_task: asyncio.Task[None] | None = None
    if not args.no_web and args.text is None:
        from .web import AppContext, WebServer, install_bus_log_handler

        ctx = AppContext(store=store, live=live, bus=bus, runtime=runtime)
        install_bus_log_handler(bus)
        server = WebServer(ctx)
        bus.attach_loop(asyncio.get_running_loop())
        web_task = asyncio.create_task(server.serve(host=args.host, port=args.port))
        asyncio.create_task(_open_browser_after(args.host, args.port, 1.0))

    try:
        while True:
            controller, llm, memory = build_controller(
                store.load_config(),
                live=live,
                bus=bus,
                runtime=runtime,
                store=store,
            )
            if ctx is not None:
                ctx.controller, ctx.llm, ctx.memory = controller, llm, memory
            try:
                if args.text is not None:
                    print(f"{controller.config.app.name}：", end="", flush=True)
                    await controller.text_turn(args.text, speak=not args.no_speak)
                    return 0
                print("花花已啟動。網頁控制台：", f"http://{args.host}:{args.port}")
                await controller.run()
                return 0
            except RestartRequired:
                LOGGER.info("以新設定重建管線")
                continue
            finally:
                await llm.close()
                await controller.tts.close()
                controller.aec.close()
                memory.close()
    finally:
        if web_task is not None:
            web_task.cancel()
            try:
                await web_task
            except (asyncio.CancelledError, Exception):
                pass


async def _open_browser_after(host: str, port: int, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        webbrowser.open(f"http://{host}:{port}")
    except Exception:
        pass


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args()
    if args.list_devices:
        print(list_audio_devices())
        return

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    config = load_config(config_path)
    configure_logging(config.app.log_level)
    try:
        raise SystemExit(asyncio.run(run(args, config_path)))
    except KeyboardInterrupt:
        print("\n花花已停止。")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
