from __future__ import annotations

import asyncio
import datetime
from enum import Enum
import logging
from pathlib import Path
import random
import time

from .aec import EchoCanceller
from .asr import SpeechRecognizer
from .audio import AudioInput, BlockResampler
from .bus import RestartRequired, RuntimeControl, StatusBus
from .commands import VoiceCommander
from .config import Config
from .llm import LlamaCppClient, SpeechChunker
from .memory import ConversationMemory
from .personas import get_persona_by_id, PERSONA_PRESETS
from .reminders import ReminderScheduler
from .settings import LiveSettings
from .tts import TextToSpeech
from .vad import UtteranceSegmenter


LOGGER = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "等待說話"
    LISTENING = "正在聽"
    TRANSCRIBING = "辨識中"
    THINKING = "思考中"
    SPEAKING = "說話中"


class FlowerController:
    def __init__(
        self,
        config: Config,
        asr: SpeechRecognizer,
        llm: LlamaCppClient,
        tts: TextToSpeech,
        memory: ConversationMemory,
        aec: EchoCanceller,
        live: LiveSettings | None = None,
        bus: StatusBus | None = None,
        runtime: RuntimeControl | None = None,
        reminders: ReminderScheduler | None = None,
        store=None,
        metrics=None,
    ) -> None:
        self.config = config
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.memory = memory
        self.aec = aec
        self.live = live
        self.bus = bus
        self.runtime = runtime
        self.store = store
        self.metrics = metrics
        self.reminders = (
            reminders
            if reminders is not None
            else ReminderScheduler(config.project_root / "data" / "reminders.db")
        )
        self.commander = VoiceCommander()
        self.state = State.IDLE
        self._turn_task: asyncio.Task[None] | None = None
        self._last_activity = time.monotonic()
        self._summary_cache = ""
        self._summary_count = 0
        self._frame_count = 0
        self._last_asr_ms: float = 0.0
        self._summary_file = Path(config.project_root) / "data" / "summary.txt"
        self._load_summary_cache()
        self._next_reminder_poll = 0.0
        self._last_reminder_gc = time.monotonic()
        self._aec_error_count = 0
        self._last_aec_warn = 0.0

    def _set_state(self, state: State) -> None:
        if state != self.state:
            self.state = state
            LOGGER.info("狀態：%s", state.value)
            if self.bus is not None:
                self.bus.publish({"type": "state", "state": state.value})

    def _publish(self, event: dict) -> None:
        if self.bus is not None:
            self.bus.publish(event)

    def _publish_audio(self, status) -> None:
        self._frame_count += 1
        if self._frame_count % 5 != 0:
            return
        self._publish(
            {
                "type": "audio",
                "rms": round(status.rms, 5),
                "threshold": round(status.threshold, 5),
                "active": bool(status.active),
            }
        )

    def apply_live(self, path: str, value: object) -> bool:
        if path == "aec.delay_ms" and hasattr(self.aec, "set_delay_ms"):
            self.aec.set_delay_ms(int(value))
            return True
        return False

    async def run(self) -> None:
        await self.asr.load()
        resampler = BlockResampler(
            self.config.audio.sample_rate,
            self.config.audio.asr_sample_rate,
        )
        segmenter = UtteranceSegmenter(
            self.config.vad,
            sample_rate=self.config.audio.asr_sample_rate,
            block_ms=self.config.audio.block_ms,
        )

        async with AudioInput(self.config.audio) as audio:
            if segmenter.backend == "energy" and self.config.vad.calibration_ms:
                LOGGER.info(
                    "先保持安靜 %.1f 秒，正在校準 Volt 1 環境音",
                    self.config.vad.calibration_ms / 1000,
                )
            self._set_state(State.IDLE)
            async for input_frame in audio.frames():
                if self.runtime is not None and self.runtime.restart_requested:
                    raise RestartRequired()

                if self._turn_task is not None and self._turn_task.done():
                    try:
                        self._turn_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        LOGGER.exception("本輪對話失敗")
                        self._publish({"type": "error", "message": "本輪對話失敗"})
                    self._turn_task = None
                    segmenter.reset()
                    audio.drain()
                    self._set_state(State.IDLE)

                if self.live is not None and (not self.live.listening or self.live.manual_busy):
                    continue

                # 定時提醒事項檢查（節流：僅在下次到期時間到達時才查 DB）
                if self.state is State.IDLE and self._turn_task is None:
                    now_mono = time.monotonic()
                    if now_mono >= self._next_reminder_poll:
                        due_list = self.reminders.pop_due()
                        if due_list:
                            self._last_activity = time.monotonic()
                            self._turn_task = asyncio.create_task(self._speak_reminder(due_list[0].text))
                            self._next_reminder_poll = time.monotonic() + 0.5
                            continue
                        # 沒到期則根據 next_due 排程下次檢查
                        try:
                            next_in = self.reminders.next_due_in()
                        except Exception:
                            next_in = None
                        if next_in is None:
                            self._next_reminder_poll = now_mono + 5.0
                        elif next_in > 1.0:
                            self._next_reminder_poll = now_mono + min(next_in, 5.0)
                        else:
                            self._next_reminder_poll = now_mono + 0.5
                    # 定期 GC 舊提醒（>7 天）
                    if now_mono - self._last_reminder_gc > 3600:
                        try:
                            removed = self.reminders.cleanup_old(days=7)
                            if removed:
                                LOGGER.info("已清理 %d 筆舊提醒", removed)
                        except Exception:
                            pass
                        self._last_reminder_gc = now_mono

                # 主動碎碎念：安靜超過 timeout 就主動說一句。
                if (
                    self.live is not None
                    and self.live.idle_chat_enabled
                    and self.live.idle_chat_prompt.strip()
                    and self._turn_task is None
                    and self.state is State.IDLE
                    and time.monotonic() - self._last_activity
                    >= self.live.idle_chat_timeout_s
                ):
                    self._last_activity = time.monotonic()
                    self._turn_task = asyncio.create_task(self._idle_chat())

                # 回答期間仍需持續餵 AEC capture 以維持濾波器收斂，僅丟棄 VAD 輸出。
                # 關閉插話時，回答期間的麥克風音框不送 VAD，但要保持 AEC 狀態。
                if self._turn_task is not None and not self._barge_in:
                    # 維持 AEC 收斂，結果直接丟棄（可觀測錯誤）
                    try:
                        self.aec.process_capture(input_frame)
                    except Exception as e:
                        self._aec_error_count += 1
                        now = time.monotonic()
                        if self._aec_error_count == 1 or now - self._last_aec_warn > 60:
                            LOGGER.warning("AEC process_capture 失敗（已 %d 次）: %s", self._aec_error_count, e)
                            self._last_aec_warn = now
                    continue

                clean_frame = self.aec.process_capture(input_frame)
                frame_16k = resampler.process(clean_frame)
                utterance, status = segmenter.push(frame_16k)
                self._publish_audio(status)

                if (
                    self._barge_in
                    and self._turn_task is not None
                    and status.active
                    and self.state in {State.THINKING, State.SPEAKING}
                ):
                    LOGGER.info("偵測到插話，中止目前回答")
                    self._turn_task.cancel()
                    try:
                        await self._turn_task
                    except asyncio.CancelledError:
                        pass
                    self._turn_task = None
                    self._set_state(State.LISTENING)

                if self._turn_task is not None:
                    continue

                if status.active:
                    self._set_state(State.LISTENING)
                elif self.state == State.LISTENING and utterance is None:
                    self._set_state(State.IDLE)

                if utterance is not None:
                    self._turn_task = asyncio.create_task(self._handle_utterance(utterance))

    @property
    def _barge_in(self) -> bool:
        if self.live is not None:
            return self.live.barge_in_enabled
        return self.config.interaction.barge_in_enabled

    async def text_turn(self, text: str, *, speak: bool = True) -> str:
        history = self.memory.recent(self._recent_turns)
        self.memory.add("user", text)
        return await self._generate(text, history, speak=speak, source="user")

    async def _idle_chat(self) -> None:
        try:
            history = self.memory.recent(self._recent_turns)
            await self._generate(
                self.live.idle_chat_prompt if self.live else self.config.idle_chat.prompt,
                history,
                speak=True,
                source="idle",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("主動碎碎念失敗")

    @property
    def _recent_turns(self) -> int:
        if self.live is not None:
            return self.live.recent_turns
        return self.config.llm.recent_turns

    def _load_summary_cache(self) -> None:
        try:
            if self._summary_file.is_file():
                text = self._summary_file.read_text(encoding="utf-8").strip()
                if text:
                    self._summary_cache = text
                    # 避免剛啟動就重建摘要，將 count 設為當前總數
                    try:
                        self._summary_count = self.memory.count()
                    except Exception:
                        self._summary_count = 0
                    LOGGER.info("已載入持久化摘要（%d 字）", len(text))
        except Exception as error:
            LOGGER.warning("載入摘要失敗：%s", error)

    def _save_summary_cache(self) -> None:
        try:
            self._summary_file.parent.mkdir(parents=True, exist_ok=True)
            self._summary_file.write_text(self._summary_cache, encoding="utf-8")
        except Exception as error:
            LOGGER.warning("寫入摘要失敗：%s", error)

    def clear_summary(self) -> None:
        """清空舊對話摘要快取。"""
        self._summary_cache = ""
        self._summary_count = 0
        try:
            if self._summary_file.is_file():
                self._summary_file.unlink()
        except Exception:
            pass

    def _persona_with_summary(self) -> str:
        persona = (self.live.persona if self.live is not None else "") or self.config.llm.persona
        now = datetime.datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        time_str = now.strftime(f"%Y年%m月%d日 {weekdays[now.weekday()]} %H:%M")
        context_parts = [persona, f"\n目前時間：{time_str}"]
        if self._summary_cache:
            context_parts.append("\n背景（較早的對話摘要）：\n" + self._summary_cache)
        return "\n".join(context_parts)

    async def _generate(self, user_text: str, history: list[dict[str, str]], *, speak: bool, source: str) -> str:
        self._set_state(State.THINKING)
        if self.bus is not None and source == "user":
            self.bus.publish({"type": "user_text", "text": user_text})

        if source == "user":
            count = self.memory.count()
            if count > self._recent_turns * 2 and count - self._summary_count >= 8:
                summary = await self.llm.summarize(self.memory.older_than(self._recent_turns))
                if summary:
                    self._summary_cache = summary
                    self._save_summary_cache()
                    LOGGER.info("已建立較早對話摘要（%d 字）", len(summary))
                self._summary_count = count

        persona = self._persona_with_summary()

        turn_start = time.monotonic()
        ttft_ms: float = 0.0
        ttfa_ms: float = 0.0
        response_parts: list[str] = []
        chunker = SpeechChunker(soft_split=True)
        speech_queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def produce() -> None:
            nonlocal ttft_ms
            first_token = True
            temperature = self._live_value("temperature", self.config.llm.temperature)
            top_p = self._live_value("top_p", self.config.llm.top_p)
            max_tokens = self._live_value("max_tokens", self.config.llm.max_tokens)
            async for token in self.llm.stream_reply(
                user_text,
                history,
                persona=persona,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            ):
                if first_token:
                    ttft_ms = round((time.monotonic() - turn_start) * 1000, 1)
                    first_token = False
                response_parts.append(token)
                print(token, end="", flush=True)
                if self.bus is not None:
                    self.bus.publish({"type": "flower_delta", "text": token})
                if speak:
                    for chunk in chunker.feed(token):
                        await speech_queue.put(chunk)
            if speak:
                for chunk in chunker.finish():
                    await speech_queue.put(chunk)
                await speech_queue.put(None)

        async def consume() -> None:
            nonlocal ttfa_ms
            if not speak:
                return
            player_open = False
            # 真實 TTFA：TTS 首個音訊 bytes 到達時才計時
            ttfa_set = False

            def mark_ttfa():
                nonlocal ttfa_ms, ttfa_set
                if not ttfa_set:
                    ttfa_ms = round((time.monotonic() - turn_start) * 1000, 1)
                    ttfa_set = True

            try:
                while True:
                    chunk = await speech_queue.get()
                    if chunk is None:
                        break
                    self._set_state(State.SPEAKING)
                    if not player_open:
                        await self.tts.begin_turn()
                        player_open = True
                    await self.tts.speak(chunk, on_first_byte=mark_ttfa)
            except asyncio.CancelledError:
                if player_open:
                    self.tts.abort_turn()
                raise
            finally:
                if player_open:
                    await self.tts.end_turn()

        await asyncio.gather(produce(), consume())
        print()
        response = "".join(response_parts).strip()
        if response and source == "user":
            self.memory.add("assistant", response)

        total_turn_ms = round((time.monotonic() - turn_start) * 1000, 1)
        if self.bus is not None:
            self.bus.publish(
                {
                    "type": "metrics",
                    "asr_ms": getattr(self, "_last_asr_ms", 0.0),
                    "ttft_ms": ttft_ms,
                    "ttfa_ms": ttfa_ms,
                    "total_turn_ms": total_turn_ms,
                }
            )
        # 效能歷史：持久化供 sparkline 趨勢
        if self.metrics is not None:
            try:
                self.metrics.add(
                    asr_ms=getattr(self, "_last_asr_ms", 0.0),
                    ttft_ms=ttft_ms,
                    ttfa_ms=ttfa_ms,
                    total_ms=total_turn_ms,
                    source=source,
                )
            except Exception:
                pass

        self._set_state(State.IDLE)
        self._last_activity = time.monotonic()
        return response

    def _live_value(self, name: str, fallback: object) -> object:
        if self.live is None:
            return fallback
        return getattr(self.live, name, fallback)

    async def poke(self) -> str:
        """點擊花花立繪時觸發的互動回覆。"""
        persona_id = getattr(self.live, "persona_preset", "energetic")
        preset = get_persona_by_id(persona_id) or PERSONA_PRESETS[0]
        replies = preset.poke_replies or ["戳我幹嘛啦～花瓣很嫩的耶！"]
        reply = random.choice(replies)
        if self.bus is not None:
            self.bus.publish({"type": "poke", "text": reply})
        if self._turn_task is None and self.state is State.IDLE:
            try:
                self._set_state(State.SPEAKING)
                await self.tts.begin_turn()
                await self.tts.speak(reply)
            finally:
                await self.tts.end_turn()
                self._set_state(State.IDLE)
        return reply

    async def _speak_reminder(self, text: str) -> None:
        """定時提醒事項發聲。"""
        LOGGER.info("觸發定時提醒：%s", text)
        if self.bus is not None:
            self.bus.publish({"type": "reminder", "text": text})
        message = f"提醒時間到囉！{text}"
        try:
            self._set_state(State.SPEAKING)
            await self.tts.begin_turn()
            await self.tts.speak(message)
        finally:
            await self.tts.end_turn()
            self._set_state(State.IDLE)

    async def _handle_utterance(self, utterance) -> None:
        self._set_state(State.TRANSCRIBING)
        asr_start = time.monotonic()
        text = await self.asr.transcribe(utterance, self.config.audio.asr_sample_rate)
        self._last_asr_ms = round((time.monotonic() - asr_start) * 1000, 1)
        if not text:
            LOGGER.info("沒有辨識到文字")
            return
        if self.bus is not None:
            self.bus.publish({"type": "asr_done", "text": text, "asr_ms": self._last_asr_ms})
        self._last_activity = time.monotonic()
        name = self._live_value("name", self.config.app.name)

        # 優先嘗試直達語音指令（時間查詢、設定提醒、切換性格、音量語速調整）
        # 直達指令不寫入 memory，避免污染 LLM 上下文（僅作 TTS 回覆）
        cmd = self.commander.try_execute(text, self)
        if cmd.handled:
            if self.bus is not None:
                self.bus.publish({"type": "user_text", "text": text})
            print(f"\n你：{text}\n{name}：{cmd.reply}", flush=True)
            if self.bus is not None:
                self.bus.publish({"type": "flower_delta", "text": cmd.reply})
            # 真實 TTFA：複用 on_first_byte 機制，避免假 5 ms
            cmd_start = time.monotonic()
            ttfa_ms = 0.0
            ttfa_set = False

            def _mark_cmd_ttfa():
                nonlocal ttfa_ms, ttfa_set
                if not ttfa_set:
                    ttfa_ms = round((time.monotonic() - cmd_start) * 1000, 1)
                    ttfa_set = True

            try:
                self._set_state(State.SPEAKING)
                await self.tts.begin_turn()
                await self.tts.speak(cmd.reply, on_first_byte=_mark_cmd_ttfa)
            finally:
                await self.tts.end_turn()
                self._set_state(State.IDLE)
            total_ms = round((time.monotonic() - cmd_start) * 1000, 1)
            if self.bus is not None:
                self.bus.publish(
                    {
                        "type": "metrics",
                        "asr_ms": self._last_asr_ms,
                        "ttft_ms": 0.0,
                        "ttfa_ms": ttfa_ms,
                        "total_turn_ms": total_ms,
                    }
                )
            return

        # 否則進入常規 LLM 對話回合
        print(f"\n你：{text}\n{name}：", end="", flush=True)
        await self.text_turn(text, speak=True)
