from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talking_flower.audio import resolve_device  # noqa: E402


LOGGER = logging.getLogger("flower-calibrate")


def _resolve(name: str, hostapi: str, *, input_device: bool) -> int | None:
    if not name:
        return None
    return resolve_device(name, hostapi, input_device=input_device)


def delay_samples(
    reference: np.ndarray,
    recorded: np.ndarray,
    max_lag: int,
    *,
    top_n: int = 1,
) -> tuple[int, list[tuple[int, float]]]:
    def envelope(x: np.ndarray, win: int) -> np.ndarray:
        kernel = np.ones(win) / win
        return np.convolve(np.abs(x - x.mean()), kernel, mode="same")

    win = 96  # 2 ms 平滑窗；包絡相關不受喇叭頻率響應與相位失真影響
    eref = envelope(reference, win)
    erec = envelope(recorded, win)
    n = len(erec)
    ref_pad = np.zeros(n)
    ref_pad[: len(eref)] = eref
    corr = np.fft.irfft(np.fft.rfft(ref_pad) * np.conj(np.fft.rfft(erec)))
    corr[max_lag:] = -np.inf  # 只搜尋 0..max_lag 的正延遲（喇叭聲音延遲到達麥克風）
    peak_val = float(np.max(corr))
    top: list[tuple[int, float]] = []
    scanned = 0
    for lag in range(0, max_lag):
        if corr[lag] >= 0.5 * peak_val:
            strength = float(corr[lag])
            top.append((lag, strength))
            scanned += 1
            if scanned >= top_n:
                break
    if not top:
        return -1, []
    # 直接路徑先到達：取第一個明顯峰值，而非最高峰值
    return top[0][0], top


def calibrate(
    *,
    input_name: str,
    input_hostapi: str,
    output_name: str,
    output_hostapi: str,
    duration: float,
) -> int:
    sample_rate = 48000
    input_index = _resolve(input_name, input_hostapi, input_device=True)
    output_index = _resolve(output_name, output_hostapi, input_device=False)

    if input_index is None:
        input_index = sd.default.device[0]
    if output_index is None:
        output_index = sd.default.device[1]

    total = int(sample_rate * (duration + 0.5))
    time_axis = np.arange(total) / sample_rate
    f0, f1 = 200.0, 4000.0
    phase = 2.0 * np.pi * (f0 * time_axis + (f1 - f0) * time_axis**2 / (2.0 * (duration + 0.5)))
    fade = np.ones(total, dtype=np.float32)
    fade_n = int(0.05 * sample_rate)
    fade[:fade_n] = np.linspace(0.0, 1.0, fade_n)
    fade[-fade_n:] = np.linspace(1.0, 0.0, fade_n)
    # 前 1 秒靜音當標尺：避免兩條 stream 啟動時差污染測量
    lead = int(1.0 * sample_rate)
    reference = np.zeros(total, dtype=np.float32)
    reference[lead:] = 0.5 * np.sin(phase)[: total - lead] * fade[: total - lead]
    chirp_start = lead

    LOGGER.info(
        "播放掃頻（%.0f Hz→%.0f Hz，%.1f 秒）至 %s，並從 %s 錄音，請保持安靜",
        f0,
        f1,
        duration + 0.5,
        sd.query_devices(output_index)["name"],
        sd.query_devices(input_index)["name"],
    )
    # 用兩條獨立 stream（InputStream + OutputStream）配 callback，與真實管線
    # 的做法一致。不要用 sd.rec/sd.play/sd.wait：實測 sd.rec 的緩衝會交替
    # 出現「全零／垃圾值」——callback 沒寫入就結束；duplex Stream 則因
    # 輸入（WASAPI Volt）與輸出（預設 MME N300）不同 hostapi 而被拒。
    recorded = np.zeros(total, dtype=np.float32)
    write_pos = 0
    rec_pos = 0

    def play_cb(outdata, frames, time_info, status):
        nonlocal write_pos
        if write_pos < total:
            outdata[:, 0] = reference[write_pos : write_pos + frames]
            write_pos += frames
        else:
            outdata[:, 0] = 0.0

    def rec_cb(indata, frames, time_info, status):
        nonlocal rec_pos
        if rec_pos < total:
            n = min(frames, total - rec_pos)
            recorded[rec_pos : rec_pos + n] = indata[:n, 0]
            rec_pos += n

    with sd.OutputStream(
        device=output_index,
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        callback=play_cb,
        latency="high",
    ), sd.InputStream(
        device=input_index,
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        callback=rec_cb,
        latency="low",
    ):
        sd.sleep(int(1000 * (duration + 1.0)))

    mic = recorded.astype(np.float32)
    peak = float(np.max(np.abs(mic)))
    rms = float(np.sqrt(np.mean(np.square(mic), dtype=np.float64)))
    LOGGER.info("錄音診斷：peak=%.4f rms=%.4f", peak, rms)
    if peak < 1e-4:
        raise RuntimeError("麥克風完全沒有收到聲音；請確認喇叭有開、音量夠大")

    lag, top = delay_samples(reference, mic, int(sample_rate * 0.5), top_n=5)
    for candidate, strength in top:
        LOGGER.info("候選延遲：%d ms（強度 %.3f）", round(candidate / sample_rate * 1000), strength)
    if lag < 0:
        raise RuntimeError("找不到明顯的延遲峰值；請提高喇叭音量或降低環境噪音")
    # 峰值位置即為輸出→輸入的總延遲（含 stream 啟動時差，與 app 真實路徑一致）
    delay_ms = round(lag / sample_rate * 1000 / 5) * 5
    if lag < chirp_start:
        LOGGER.warning("峰值早於掃頻起點（%d ms），stream 啟動時差為負；仍以峰值位置為準", round(lag / sample_rate * 1000))
    LOGGER.info("估計延遲：%d ms（峰值在 %d ms）", delay_ms, round(lag / sample_rate * 1000))
    return delay_ms


def main() -> None:
    parser = argparse.ArgumentParser(description="量測喇叭播放到麥克風收音的延遲（AEC delay_ms）")
    parser.add_argument("--in-device", default="", help="麥克風裝置名稱（空 = 預設）")
    parser.add_argument("--in-hostapi", default="", help="麥克風介面")
    parser.add_argument("--out-device", default="", help="輸出裝置名稱（空 = 預設）")
    parser.add_argument("--out-hostapi", default="", help="輸出介面")
    parser.add_argument("--duration", type=float, default=3.0, help="掃頻秒數")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        delay_ms = calibrate(
            input_name=args.in_device,
            input_hostapi=args.in_hostapi,
            output_name=args.out_device,
            output_hostapi=args.out_hostapi,
            duration=args.duration,
        )
    except Exception as error:
        LOGGER.error("校準失敗：%s", error)
        raise SystemExit(1) from error
    print(f"delay_ms={delay_ms}")


if __name__ == "__main__":
    main()
