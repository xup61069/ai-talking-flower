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
    """以掃頻互相關估計 delay，修正相關方向並使用線性相關（零填充）。
    優先使用原始波形相關（chirp 具尖銳自相關），回退至包絡相關以抗頻響失真。
    """

    n = len(recorded)
    N = 1
    while N < 2 * n:
        N <<= 1
    # 線性互相關：rec * conj(ref) 使正 lag 對應 rec 延後（符合實體「播放→收音」）
    corr_raw_full = np.fft.irfft(np.fft.rfft(recorded, n=N) * np.conj(np.fft.rfft(reference, n=N)), n=N)
    corr_raw = corr_raw_full[:n].copy()
    if max_lag < len(corr_raw):
        corr_raw[max_lag:] = -np.inf
    peak_raw = float(np.max(corr_raw)) if len(corr_raw) else float("-inf")
    # 若原始相關有明顯尖峰則採用之（> 5× 均值則視為清晰）
    use_raw = False
    if np.isfinite(peak_raw) and peak_raw > 0:
        mean_abs = float(np.mean(np.abs(corr_raw[:max_lag]))) if max_lag > 0 else 0.0
        if mean_abs > 0 and peak_raw / (mean_abs + 1e-9) > 5.0:
            use_raw = True
    if use_raw:
        # 在原始相關中取最高峰附近的最早明顯峰（抗多徑：直接音先到）
        peak_val = peak_raw
        # 以 0.5*peak 為閾值，從 0 往後掃描，取第一個達標者即為直接路徑
        # 但對 raw 相關，遠離峰值的 0 區值遠小於 0.5*peak，故不會誤判為 0
        top: list[tuple[int, float]] = []
        scanned = 0
        for lag in range(0, min(max_lag, len(corr_raw))):
            if corr_raw[lag] >= 0.5 * peak_val:
                strength = float(corr_raw[lag])
                top.append((lag, strength))
                scanned += 1
                if scanned >= top_n:
                    break
        if top:
            return top[0][0], top
        # 回退至最大值位置
        lag = int(np.argmax(corr_raw[:max_lag]))
        return lag, [(lag, float(corr_raw[lag]))]

    # 回退：包絡相關（抗頻響）
    def envelope(x: np.ndarray, win: int) -> np.ndarray:
        kernel = np.ones(win) / win
        return np.convolve(np.abs(x - x.mean()), kernel, mode="same")

    win = 96  # 2 ms 平滑窗；包絡相關不受喇叭頻率響應與相位失真影響
    eref = envelope(reference, win)
    erec = envelope(recorded, win)
    ref_pad = np.zeros(n)
    ref_pad[: len(eref)] = eref
    corr_full = np.fft.irfft(np.fft.rfft(erec, n=N) * np.conj(np.fft.rfft(ref_pad, n=N)), n=N)
    corr = corr_full[:n].copy()
    if max_lag < len(corr):
        corr[max_lag:] = -np.inf  # 只搜尋 0..max_lag 的正延遲
    peak_val = float(np.max(corr))
    if not np.isfinite(peak_val) or peak_val <= 0:
        return -1, []
    top = []
    scanned = 0
    for lag in range(0, min(max_lag, len(corr))):
        if corr[lag] >= 0.5 * peak_val:
            strength = float(corr[lag])
            top.append((lag, strength))
            scanned += 1
            if scanned >= top_n:
                break
    if not top:
        return -1, []
    return top[0][0], top


def estimate_erle(reference: np.ndarray, recorded: np.ndarray, delay: int) -> float:
    """粗略估計 ERLE (Echo Return Loss Enhancement) dB，回傳 0..inf。"""
    if delay < 0 or delay >= len(recorded):
        return 0.0
    # 對齊後比較能量
    ref_aligned = np.zeros_like(recorded)
    copy_len = min(len(reference), len(recorded) - delay)
    if copy_len > 0:
        ref_aligned[delay : delay + copy_len] = reference[:copy_len]
    # 僅在參考有能量的段計算
    mask = np.abs(ref_aligned) > 1e-4
    if np.sum(mask) < 100:
        return 0.0
    power_rec = float(np.mean(np.square(recorded[mask])))
    power_res = float(np.mean(np.square(recorded[mask] - ref_aligned[mask] * 0.5)))  # 粗略 0.5 增益
    if power_res <= 1e-9:
        return 40.0
    erle = 10.0 * np.log10(max(power_rec, 1e-9) / max(power_res, 1e-9))
    return float(np.clip(erle, 0.0, 40.0))


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
    erle_db = estimate_erle(reference, mic, lag)
    LOGGER.info("ERLE 粗估：%.1f dB（>10 dB 表示回音路徑清晰）", erle_db)
    if lag < chirp_start:
        LOGGER.warning("峰值早於掃頻起點（%d ms），stream 啟動時差為負；仍以峰值位置為準", round(lag / sample_rate * 1000))
    LOGGER.info("估計延遲：%d ms（峰值在 %d ms，ERLE %.1f dB）", delay_ms, round(lag / sample_rate * 1000), erle_db)
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
