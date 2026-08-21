from __future__ import annotations

import unittest
import numpy as np

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.audio import BlockResampler


class ResampleContinuityTests(unittest.TestCase):
    def test_block_resampler_chunked_vs_oneshot(self) -> None:
        """soxr 有狀態 vs 一次性重採樣，RMS 誤差應 <1%（鎖住 R2-2 行為）。"""
        try:
            import soxr  # noqa: F401
        except ImportError:
            self.skipTest("soxr 未安裝，跳過連續性測試")

        sr, tgt = 48000, 16000
        # 1 秒 440 Hz 正弦，連續重採樣應與一次性誤差極小
        t = np.arange(sr, dtype=np.float32) / sr
        tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)

        # 一次性對照
        import soxr

        ref = soxr.resample(tone, sr, tgt)

        # 逐塊（20 ms = 960 樣本 @48k）
        resampler = BlockResampler(sr, tgt)
        chunk = sr // 50  # 20ms
        outs = []
        for i in range(0, len(tone), chunk):
            outs.append(resampler.process(tone[i : i + chunk]))
        # 排空 soxr 延遲
        tail = resampler.flush()
        if len(tail):
            outs.append(tail)
        joined = np.concatenate(outs) if outs else np.array([], dtype=np.float32)
        # 對齊長度：允許 soxr 延遲差異，取重疊區
        min_len = min(len(ref), len(joined))
        # 去掉前後 100 樣本的邊界瞬態
        trim = 100
        if min_len > trim * 2:
            a = joined[trim : min_len - trim]
            b = ref[trim : min_len - trim]
        else:
            a = joined[:min_len]
            b = ref[:min_len]
        rms = float(np.sqrt(np.mean((a - b) ** 2)))
        sig_rms = float(np.sqrt(np.mean(b**2)) + 1e-9)
        rel = rms / sig_rms
        self.assertLess(rel, 0.01, f"逐塊 vs 一次性 RMS 誤差 {rel:.4f} 超過 1%")

    def test_block_resampler_no_nan(self) -> None:
        r = BlockResampler(48000, 16000)
        total = 0
        for _ in range(50):
            frame = np.random.randn(960).astype(np.float32) * 0.1
            out = r.process(frame)
            self.assertTrue(np.isfinite(out).all())
            total += len(out)
        # soxr 有延遲，首塊可能 0，但總輸出應接近 50*320
        tail = r.flush()
        total += len(tail)
        self.assertGreater(total, 50 * 300)

    def test_pcm_player_resampler_fallback_not_wrong_rate(self) -> None:
        """soxr 失敗應降級而非回傳原 24k 當 48k（R3-1）。"""
        from talking_flower.tts import _PcmPlayer
        from talking_flower.aec import BypassEchoCanceller
        import numpy as np

        player = _PcmPlayer.__new__(_PcmPlayer)
        player.source_rate = 24000
        player.sample_rate = 48000
        player._volume = 100
        player.live = None
        player.aec = BypassEchoCanceller(48000)
        # 強制 soxr 失敗：替換 resampler 為會拋例外的 mock
        class BadResampler:
            def resample_chunk(self, x, last=False):
                raise RuntimeError("mock soxr failure")

        player._resampler = BadResampler()
        player._resampler_mode = "soxr"
        # 準備一個 480 樣本 24k 塊，正常應輸出 ~960 樣本 48k
        data = (np.sin(np.linspace(0, 10, 480)) * 10000).astype("<i2").tobytes()
        out = player._prepare(data)
        # 若錯誤回傳未重採樣，長度會是 480*2=960；正確重採樣後應為 960*2=1920
        self.assertEqual(len(out), 960 * 2, "降級後應 via interp 正確重採樣到 48k，而非原 24k")
        self.assertEqual(player._resampler_mode, "interp")


if __name__ == "__main__":
    unittest.main()
