#!/usr/bin/env python3
"""花花 Discord 看板娘橋接（骨架）。

將 Discord 文字頻道的對話轉給花花的大腦，回覆貼回頻道；
語音頻道可選：偵測到使用者發言時拉取音訊送 ASR（需額外 ffmpeg）。

用法：
    pip install \"ai-talking-flower[discord]\"
    set DISCORD_TOKEN=你的 Bot Token
    set DISCORD_GUILD_ID=（可選，限制伺服器）
    python tools/discord_bridge.py --channel-id 123456789 --llm-base-url http://127.0.0.1:8080/v1

此骨架已可跑文字橋接；語音需額外實作 AudioSource（見 TODO）。
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

LOGGER = logging.getLogger("flower-discord")

try:
    import discord  # type: ignore
    from discord.ext import commands  # type: ignore

    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False


HELP_TEXT = """花花 Discord 指令：
!flower <內容>  直接與花花對話（走直達指令 + LLM）
!flower_poke   戳戳花花
!flower_status 查看花花狀態
"""


def check_available() -> bool:
    if not _DISCORD_AVAILABLE:
        LOGGER.error("discord.py 未安裝：pip install \"ai-talking-flower[discord]\"")
        return False
    if not os.getenv("DISCORD_TOKEN"):
        LOGGER.error("請設定 DISCORD_TOKEN 環境變數")
        return False
    return True


def run_discord_bot(channel_id: int | None, llm_base_url: str) -> None:
    if not check_available():
        raise SystemExit(1)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    # 延遲載入花花核心（避免在無麥克風機器上初始化音訊）
    from talking_flower.config import load_config
    from talking_flower.controller import FlowerController
    from talking_flower.llm import LlamaCppClient
    from talking_flower.memory import ConversationMemory
    from talking_flower.settings import SettingsStore
    from talking_flower.tts import create_tts as _create_tts  # noqa: F401（保留供語音擴充）

    store = SettingsStore(PROJECT_ROOT / "config.toml")
    config = store.load_config()
    # Discord 模式覆寫 LLM 位址
    if llm_base_url:
        import dataclasses

        config = dataclasses.replace(config, llm=dataclasses.replace(config.llm, base_url=llm_base_url))

    llm = LlamaCppClient(config.llm)
    memory = ConversationMemory(PROJECT_ROOT / "data" / "discord_memory.db")

    # 簡化 controller：不接 AEC/Audio，僅用 LLM+Memory+VoiceCommander
    # 真正語音版需再接入 AudioInput，見 TODO
    from talking_flower.aec import BypassEchoCanceller
    from talking_flower.asr import create_recognizer  # noqa: F401（語音擴充用）

    dummy_aec = BypassEchoCanceller(config.audio.sample_rate)

    @bot.event
    async def on_ready():
        LOGGER.info("Discord 已上線：%s (id=%s)", bot.user, bot.user.id if bot.user else "?")
        LOGGER.info(HELP_TEXT)

    @bot.command(name="flower")
    async def flower_cmd(ctx, *, content: str = ""):
        if channel_id and ctx.channel.id != channel_id:
            return
        content = content.strip()
        if not content:
            await ctx.reply(HELP_TEXT)
            return
        async with ctx.typing():
            # 先走直達指令快路徑（0ms），否則走 LLM
            from talking_flower.controller import FlowerController as _FC

            # 輕量 controller 僅為借用 VoiceCommander 與 persona
            temp_ctrl = _FC(
                config=config,
                asr=None,  # type: ignore[arg-type]
                llm=llm,
                tts=None,  # type: ignore[arg-type]
                memory=memory,
                aec=dummy_aec,
            )
            from talking_flower.bus import StatusBus  # noqa: F401
            from talking_flower.skills import load_builtin_skills
            from talking_flower.commands import VoiceCommander

            # 確保技能已載入
            load_builtin_skills()
            commander = VoiceCommander()
            # 人造 controller 殼供技能使用（僅需 live/bus/store/reminders）
            fake_ctrl = temp_ctrl
            result = commander.try_execute(content, fake_ctrl)
            if result.handled:
                await ctx.reply(result.reply)
                memory.add("user", content)
                memory.add("assistant", result.reply)
                return
            # LLM 串流
            history = memory.recent(config.llm.recent_turns)
            memory.add("user", content)
            persona = config.llm.persona
            chunks: list[str] = []
            async for token in llm.stream_reply(
                content, history, persona=persona,
                temperature=config.llm.temperature, top_p=config.llm.top_p, max_tokens=config.llm.max_tokens,
            ):
                chunks.append(token)
            reply = "".join(chunks).strip() or "（花花暫時想不到要說什麼）"
            memory.add("assistant", reply)
            # Discord 2000 字限制
            for i in range(0, len(reply), 1900):
                await ctx.reply(reply[i : i + 1900])

    @bot.command(name="flower_poke")
    async def poke_cmd(ctx):
        if channel_id and ctx.channel.id != channel_id:
            return
        from talking_flower.personas import PERSONA_PRESETS
        import random

        preset = PERSONA_PRESETS[0]
        await ctx.reply(random.choice(preset.poke_replies))

    @bot.command(name="flower_status")
    async def status_cmd(ctx):
        await ctx.reply(f"LLM: {config.llm.base_url} | 記憶 {memory.count()} 則")

    bot.run(os.environ["DISCORD_TOKEN"])


def main() -> None:
    parser = argparse.ArgumentParser(description="花花 Discord 橋接")
    parser.add_argument("--channel-id", type=int, default=0, help="限制頻道 ID（0=全部）")
    parser.add_argument("--llm-base-url", default=os.getenv("LLM_BASE_URL") or "http://127.0.0.1:8080/v1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    channel = args.channel_id or (int(os.getenv("DISCORD_CHANNEL_ID", "0") or 0) or None)
    run_discord_bot(channel, args.llm_base_url)


if __name__ == "__main__":
    main()
