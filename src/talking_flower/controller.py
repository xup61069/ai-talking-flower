from __future__ import annotations

import asyncio
from enum import Enum
import logging

from .aec import EchoCanceller
from .asr import SpeechRecognizer
from .audio import AudioInput, BlockResampler
from .config import Config
from .llm import LlamaCppClient, SpeechChunker
from .memory import ConversationMemory
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
    ) -> None:
        self.config = config
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.memory = memory
        self.aec = aec
        self.state = State.IDLE
        self._turn_task: asyncio.Task[None] | None = None

    def _set_state(self, state: State) -> None:
        if state != self.state:
            self.state = state
            LOGGER.info("狀態：%s", state.value)

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
                if self._turn_task is not None and self._turn_task.done():
                    try:
                        self._turn_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        LOGGER.exception("本輪對話失敗")
                    self._turn_task = None
                    segmenter.reset()
                    audio.drain()
                    self._set_state(State.IDLE)

                # 關閉插話時，回答期間直接丟棄麥克風音框。回答結束後上方會再清空佇列，
                # 避免喇叭回音被當成下一句使用者語音。
                if self._turn_task is not None and not self.config.interaction.barge_in_enabled:
                    continue

                clean_frame = self.aec.process_capture(input_frame)
                frame_16k = resampler.process(clean_frame)
                utterance, status = segmenter.push(frame_16k)

                if (
                    self.config.interaction.barge_in_enabled
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

    async def text_turn(self, text: str, *, speak: bool = True) -> str:
        history = self.memory.recent(self.config.llm.recent_turns)
        self.memory.add("user", text)
        self._set_state(State.THINKING)
        response_parts: list[str] = []
        chunker = SpeechChunker()
        speech_queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def produce() -> None:
            async for token in self.llm.stream_reply(text, history):
                response_parts.append(token)
                print(token, end="", flush=True)
                if speak:
                    for chunk in chunker.feed(token):
                        await speech_queue.put(chunk)
            if speak:
                for chunk in chunker.finish():
                    await speech_queue.put(chunk)
                await speech_queue.put(None)

        async def consume() -> None:
            if not speak:
                return
            player_open = False
            try:
                while True:
                    chunk = await speech_queue.get()
                    if chunk is None:
                        break
                    self._set_state(State.SPEAKING)
                    if not player_open:
                        await self.tts.begin_turn()
                        player_open = True
                    await self.tts.speak(chunk)
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
        if response:
            self.memory.add("assistant", response)
        self._set_state(State.IDLE)
        return response

    async def _handle_utterance(self, utterance) -> None:
        self._set_state(State.TRANSCRIBING)
        text = await self.asr.transcribe(utterance, self.config.audio.asr_sample_rate)
        if not text:
            LOGGER.info("沒有辨識到文字")
            return
        print(f"\n你：{text}\n{self.config.app.name}：", end="", flush=True)
        await self.text_turn(text, speak=True)
