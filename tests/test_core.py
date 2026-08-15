from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from talking_flower.asr import normalize_transcript
from talking_flower.aec import create_echo_canceller
from talking_flower.config import load_config
from talking_flower.llm import SpeechChunker
from talking_flower.memory import ConversationMemory
from talking_flower.vad import UtteranceSegmenter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AsrTests(unittest.TestCase):
    def test_normalizes_to_traditional_chinese(self) -> None:
        self.assertEqual(normalize_transcript("欢迎大家来体验"), "歡迎大家來體驗")


class ConfigTests(unittest.TestCase):
    def test_volt_one_is_configured(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        self.assertIn("Volt 1", config.audio.input_device)
        self.assertEqual(config.audio.input_hostapi, "Windows WASAPI")
        self.assertEqual(config.audio.sample_rate, 48000)
        self.assertEqual(config.tts.backend, "kokoro")
        self.assertEqual(config.tts.voice, "zf_001")
        self.assertEqual(config.tts.speed, 0.9)
        self.assertEqual(config.tts.sample_rate, 24000)
        self.assertFalse(config.interaction.barge_in_enabled)

    def test_webrtc_aec3_processes_twenty_ms_frame(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        aec = create_echo_canceller(config.aec, config.audio.sample_rate, PROJECT_ROOT)
        try:
            frame = np.zeros(config.audio.sample_rate // 50, dtype=np.float32)
            processed = aec.process_capture(frame)
            self.assertEqual(processed.shape, frame.shape)
            self.assertTrue(np.isfinite(processed).all())
        finally:
            aec.close()


class ChunkerTests(unittest.TestCase):
    def test_chunks_on_chinese_sentence_end(self) -> None:
        chunker = SpeechChunker(minimum_chars=4, maximum_chars=20)
        self.assertEqual(chunker.feed("歡迎回來。今天"), ["歡迎回來。"])
        self.assertEqual(chunker.finish(), ["今天"])

    def test_does_not_split_an_incomplete_long_sentence(self) -> None:
        chunker = SpeechChunker(minimum_chars=4, maximum_chars=10)
        partial = "這是一句很長，但還沒有真正說完的話"
        self.assertEqual(chunker.feed(partial), [])
        self.assertEqual(chunker.feed("。"), [partial + "。"])


class VadTests(unittest.TestCase):
    def test_detects_synthetic_utterance(self) -> None:
        config = load_config(PROJECT_ROOT / "config.toml")
        vad_config = replace(config.vad, backend="energy")
        vad = UtteranceSegmenter(
            vad_config,
            sample_rate=config.audio.asr_sample_rate,
            block_ms=config.audio.block_ms,
        )
        frame_size = int(config.audio.asr_sample_rate * config.audio.block_ms / 1000)
        silence = np.zeros(frame_size, dtype=np.float32)
        speech = np.full(frame_size, 0.05, dtype=np.float32)
        completed = None
        calibration_frames = int(config.vad.calibration_ms / config.audio.block_ms)
        for frame in [silence] * calibration_frames + [speech] * 30 + [silence] * 45:
            maybe, _ = vad.push(frame)
            if maybe is not None:
                completed = maybe
        self.assertIsNotNone(completed)
        self.assertGreater(len(completed), frame_size * 30)


class MemoryTests(unittest.TestCase):
    def test_recent_messages_are_chronological(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = ConversationMemory(Path(directory) / "test.db")
            memory.add("user", "一")
            memory.add("assistant", "二")
            self.assertEqual(
                memory.recent(1),
                [{"role": "user", "content": "一"}, {"role": "assistant", "content": "二"}],
            )
            memory.close()


if __name__ == "__main__":
    unittest.main()
