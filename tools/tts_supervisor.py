"""TTS server 守護程序：健康輪詢、崩潰自動拉起、log 收集。

用法：
    python tools/tts_supervisor.py --backend kokoro            # 前景守護
    python tools/tts_supervisor.py --backend cosyvoice --once  # 只檢查/拉起一次後離開

取代手動跑 start-*.ps1 的模式：supervisor 每 10 秒打 /health，
連續失敗 3 次就重跑 start-{backend}.ps1；stdout/stderr 由 ps1
既有的 RedirectStandardOutput 收進 data/{backend}.*.log。
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError


LOGGER = logging.getLogger("tts-supervisor")

BACKENDS = {
    "kokoro": {"port": 50001, "health": "http://127.0.0.1:50001/health"},
    "cosyvoice": {"port": 50000, "health": "http://127.0.0.1:50000/health"},
}


def health_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (URLError, OSError):
        return False


def run_start_script(root: Path, backend: str) -> bool:
    script = root / f"start-{backend}.ps1"
    if not script.is_file():
        LOGGER.error("找不到 %s", script)
        return False
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=str(root),
            capture_output=True,
            timeout=180,
        )
        ok = proc.returncode == 0
        if not ok:
            tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
            LOGGER.error("start-%s.ps1 失敗：%s", backend, tail[-1] if tail else f"exit {proc.returncode}")
        return ok
    except Exception as error:
        LOGGER.exception("執行 start-%s.ps1 異常：%s", backend, error)
        return False


def supervise(root: Path, backend: str, *, interval: float = 10.0, max_failures: int = 3, once: bool = False) -> int:
    info = BACKENDS[backend]
    url = info["health"]
    failures = 0
    LOGGER.info("守護 %s TTS（%s），每 %.0f 秒檢查一次", backend, url, interval)
    while True:
        if health_ok(url):
            failures = 0
        else:
            failures += 1
            LOGGER.warning("%s 健康檢查失敗 %d/%d", backend, failures, max_failures)
            if failures >= max_failures:
                LOGGER.warning("連續 %d 次失敗，嘗試重新拉起 %s", max_failures, backend)
                # 先停再啟，避免殘留半死程序佔 port
                stop_script = root / f"stop-{backend}.ps1"
                if stop_script.is_file():
                    try:
                        subprocess.run(
                            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(stop_script)],
                            cwd=str(root),
                            capture_output=True,
                            timeout=60,
                        )
                    except Exception:
                        LOGGER.exception("stop-%s.ps1 失敗（忽略）", backend)
                if run_start_script(root, backend):
                    failures = 0
                    LOGGER.info("%s 已重新拉起", backend)
                else:
                    failures = max_failures - 1  # 下輪再試
        if once:
            return 0 if failures == 0 else 1
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS server 守護程序")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="kokoro")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--interval", type=float, default=10.0, help="健康檢查間隔秒數")
    parser.add_argument("--max-failures", type=int, default=3, help="連續失敗幾次才重啟")
    parser.add_argument("--once", action="store_true", help="只檢查/拉起一次後離開")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    raise SystemExit(supervise(args.project_root, args.backend, interval=args.interval, max_failures=args.max_failures, once=args.once))


if __name__ == "__main__":
    main()
