from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys

import numpy as np

from .aec import create_echo_canceller
from .asr import create_recognizer
from .audio import list_audio_devices, resolve_device
from .config import Config, load_config
from .controller import FlowerController
from .llm import LlamaCppClient
from .memory import ConversationMemory
from .tts import create_tts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 閒聊花花")
    parser.add_argument("--config", default="config.toml", help="設定檔路徑")
    parser.add_argument("--check", action="store_true", help="檢查裝置與 llama-server")
    parser.add_argument("--list-devices", action="store_true", help="列出音訊裝置")
    parser.add_argument("--text", help="略過麥克風，直接測試一輪文字對話")
    parser.add_argument("--no-speak", action="store_true", help="文字測試時不要朗讀")
    parser.add_argument("--load-asr", action="store_true", help="搭配 --check 載入 ASR 模型")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def build_controller(config: Config) -> tuple[FlowerController, LlamaCppClient, ConversationMemory]:
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
        tts=create_tts(config.tts, config.audio, aec),
        memory=memory,
        aec=aec,
    )
    return controller, llm, memory


async def check(config: Config, *, load_asr: bool) -> int:
    print("設定檔：OK")
    index = resolve_device(
        config.audio.input_device,
        config.audio.input_hostapi,
        input_device=True,
    )
    print(f"麥克風：OK（裝置 {index}，{config.audio.input_device}）")
    llm = LlamaCppClient(config.llm)
    try:
        healthy = await llm.health()
    finally:
        await llm.close()
    print(f"llama-server：{'OK' if healthy else '無法連線'}")
    if not healthy:
        return 1
    tts = create_tts(config.tts, config.audio)
    try:
        tts_healthy = await tts.health()
    finally:
        await tts.close()
    print(f"CosyVoice：{'OK' if tts_healthy else '無法連線'}")
    if not tts_healthy:
        return 1
    aec = create_echo_canceller(config.aec, config.audio.sample_rate, config.project_root)
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


async def run(args: argparse.Namespace, config: Config) -> int:
    if args.check:
        return await check(config, load_asr=args.load_asr)

    controller, llm, memory = build_controller(config)
    try:
        if args.text is not None:
            print(f"{config.app.name}：", end="", flush=True)
            await controller.text_turn(args.text, speak=not args.no_speak)
        else:
            print("花花已啟動。按 Ctrl+C 停止。")
            await controller.run()
    finally:
        await llm.close()
        await controller.tts.close()
        controller.aec.close()
        memory.close()
    return 0


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
        raise SystemExit(asyncio.run(run(args, config)))
    except KeyboardInterrupt:
        print("\n花花已停止。")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
