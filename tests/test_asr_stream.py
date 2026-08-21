from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.asr import FunASRStreamingRecognizer, StreamingSession
from talking_flower.commands import CommandResult
from talking_flower.config import load_config
from talking_flower.controller import FlowerController

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeModel:
    """模擬 FunASR 串流模型：累積式輸出，記錄 (樣本數, is_final) 呼叫。"""

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces  # 每次呼叫吐出的「增量」；對外呈現為累積全文
        self.calls: list[tuple[int, bool]] = []
        self._index = 0

    def generate(self, *, input, cache, is_final, **kwargs):
        self.calls.append((len(input), bool(is_final)))
        if self._index < len(self.pieces):
            self._index += 1
        cache["seen"] = True
        cumulative = "".join(self.pieces[: self._index])
        # 串流 paraformer 行為：非 final 回傳目前累積、final 回傳全文
        text = cumulative if not is_final or self._index >= len(self.pieces) else cumulative
        return [{"text": text}]


def _make_recognizer() -> FunASRStreamingRecognizer:
    config = load_config(PROJECT_ROOT / "config.toml").asr
    recognizer = FunASRStreamingRecognizer(config)
    return recognizer


class StreamingSessionTests(unittest.TestCase):
    FRAME = np.full(320, 0.01, dtype=np.float32)  # 20ms @16k

    def test_feed_buffers_until_stride(self) -> None:
        r = _make_recognizer()
        r._model = FakeModel(["你好"])
        session = StreamingSession(r)
        for _ in range(29):  # 29*320 = 9280 < 9600 stride
            session.feed(self.FRAME)
        self.assertEqual(len(r._model.calls), 0)
        session.feed(self.FRAME)  # 第 30 幀湊滿 9600
        self.assertEqual(len(r._model.calls), 1)
        self.assertEqual(r._model.calls[0], (9600, False))
        self.assertEqual(session.text, "你好")

    def test_finish_flushes_remainder_with_is_final(self) -> None:
        r = _make_recognizer()
        r._model = FakeModel(["你好", "世界"])
        session = StreamingSession(r)
        for _ in range(30):
            session.feed(self.FRAME)
        tail = np.full(1600, 0.01, dtype=np.float32)  # noqa: F841 尾段示意（實際由 buffer 承載）
        result = session.finish()
        # finish 只推論緩衝殘餘（tail 由 controller 餵入或此處直接給 buffer）
        self.assertEqual(r._model.calls[-1][1], True)
        self.assertEqual(result, "你好世界")
        # 重置後乾淨
        self.assertEqual(session.text, "")
        self.assertEqual(len(session._buffer), 0)

    def test_finish_empty_buffer_still_finalizes_cache(self) -> None:
        r = _make_recognizer()
        r._model = FakeModel(["嗨"])
        session = StreamingSession(r)
        for _ in range(30):
            session.feed(self.FRAME)
        # 再湊一輪使 buffer 清空
        for _ in range(30):
            session.feed(self.FRAME)
        result = session.finish()
        last_len, last_final = r._model.calls[-1]
        self.assertTrue(last_final)
        self.assertGreater(last_len, 0)
        self.assertEqual(result, "嗨")

    def test_prefix_merge_semantics(self) -> None:
        # pieces 為「增量」：串流模型每次回傳累積全文（增量 join）
        r = _make_recognizer()
        r._model = FakeModel(["今天天氣", "真好"])
        session = StreamingSession(r)
        session.feed(np.concatenate([self.FRAME] * 30))
        self.assertEqual(session.text, "今天天氣")
        session.feed(np.concatenate([self.FRAME] * 30))
        self.assertEqual(session.text, "今天天氣真好")


class StreamControllerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _make_controller(self) -> FlowerController:
        config = load_config(PROJECT_ROOT / "config.toml")
        live = mock.MagicMock()
        live.wake_word = ""
        live.asr_streaming = True
        live.persona_preset = "energetic"
        live.listening = True
        live.manual_busy = False
        bus = mock.MagicMock()
        temp = tempfile.TemporaryDirectory()
        ctrl = FlowerController(
            config=config,
            asr=mock.MagicMock(),
            llm=mock.AsyncMock(),
            tts=mock.MagicMock(),
            memory=mock.MagicMock(),
            aec=mock.MagicMock(),
            live=live,
            bus=bus,
            reminders=mock.MagicMock(),
        )
        ctrl._temp_dir = temp
        ctrl.tts.begin_turn = mock.AsyncMock()
        ctrl.tts.end_turn = mock.AsyncMock()
        ctrl.tts.speak = mock.AsyncMock()
        return ctrl

    async def test_stream_text_skips_batch_transcribe(self) -> None:
        ctrl = self._make_controller()
        try:
            # 直達指令攔截，避免觸及 LLM
            cmd = CommandResult(handled=True, reply="現在時間是十四點喔！", action="time_query")
            ctrl.commander.try_execute = mock.MagicMock(return_value=cmd)
            await ctrl._handle_utterance(b"\x00" * 3200, stream_text="現在幾點")
            # 不應呼叫批次轉錄
            ctrl.asr.transcribe.assert_not_called()
            # 應以串流文字作為使用者語句並朗讀回覆
            cmd_call = ctrl.commander.try_execute.call_args
            self.assertEqual(cmd_call.args[0], "現在幾點")
            ctrl.tts.speak.assert_called_once()
            self.assertIn("現在時間是", ctrl.tts.speak.call_args[0][0])
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    async def test_partial_publish_respects_throttle(self) -> None:
        ctrl = self._make_controller()
        try:
            fake_asr = mock.MagicMock()
            fake_asr._streaming_supported = True
            session = mock.MagicMock()
            texts = iter(["你", "你好", "你好世界"])
            session.feed.side_effect = lambda frame: next(texts)
            fake_asr.create_stream.return_value = session
            ctrl.asr = fake_asr
            frame = np.zeros(320, dtype=np.float32)

            await ctrl._feed_stream_frame(frame, active=True)
            self.assertEqual(ctrl.bus.publish.call_count, 1)
            first_evt = ctrl.bus.publish.call_args[0][0]
            self.assertEqual(first_evt["type"], "asr_partial")
            self.assertEqual(first_evt["text"], "你")

            # 節流窗口內（<0.25s）第二次變化不發布
            await ctrl._feed_stream_frame(frame, active=True)
            self.assertEqual(ctrl.bus.publish.call_count, 1)

            # 強制窗口過期後才發布新文字（第三次 feed 回傳「你好世界」）
            ctrl._partial_last_pub -= 1.0
            await ctrl._feed_stream_frame(frame, active=True)
            self.assertEqual(ctrl.bus.publish.call_count, 2)
            self.assertEqual(ctrl.bus.publish.call_args_list[1][0][0]["text"], "你好世界")

            # 非 active 不發布
            ctrl._partial_last_pub -= 1.0
            await ctrl._feed_stream_frame(frame, active=False)
            self.assertEqual(ctrl.bus.publish.call_count, 2)
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    async def test_disabled_streaming_creates_no_session(self) -> None:
        ctrl = self._make_controller()
        try:
            ctrl.live.asr_streaming = False
            fake_asr = mock.MagicMock()
            ctrl.asr = fake_asr
            await ctrl._feed_stream_frame(np.zeros(320, dtype=np.float32), active=True)
            fake_asr.create_stream.assert_not_called()
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()

    async def test_unsupported_recognizer_skips_streaming(self) -> None:
        ctrl = self._make_controller()
        try:
            fake_asr = mock.MagicMock(spec=["load", "transcribe"])  # 無 _streaming_supported
            ctrl.asr = fake_asr
            await ctrl._feed_stream_frame(np.zeros(320, dtype=np.float32), active=True)
            self.assertIsNone(ctrl._stream_session)
        finally:
            ctrl.reminders.close()
            ctrl._temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
