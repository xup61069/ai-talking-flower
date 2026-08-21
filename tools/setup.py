#!/usr/bin/env python3
"""一鍵安裝：建環境、裝依賴、下載模型。

用法（Windows PowerShell 建議用 uv）：
    uv run tools/setup.py              # 完整安裝
    uv run tools/setup.py --check      # 僅檢查，不下載
    uv run tools/setup.py --models kokoro  # 只拉指定模型

模型清單來自 config.toml / tools 腳本中的預設值；下載後可直接 run.cmd。
"""
from __future__ import annotations

import argparse
import hashlib
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("setup")

# 模型清單： (name, url, dest_relative, sha256_or_None)
# - kokoro 模型由 Kokoro-82M-v1.1 官方發布取得（此處以佔位 URL，需填入實際 huggingface 鏈接）
# - Paraformer 由 funasr 自動下載，此處僅校驗 funasr 可用
# - CosyVoice/Matcha 需手動依其 README 取得，不在此自動拉取
MODELS: list[tuple[str, str, str, str | None]] = [
    # 範例：實際 URL 請替換為你驗證過的 huggingface 連結
    # ("kokoro-82M-v1.1-zh", "https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/resolve/main/model.bin", "models/kokoro/model.bin", None),
]


def check_uv() -> bool:
    return shutil.which("uv") is not None


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    LOG.info("$ %s", " ".join(cmd))
    return subprocess.run(cmd, check=False, **kwargs)


def ensure_venv() -> bool:
    if not check_uv():
        LOG.warning("未偵測到 uv，改用 pip；建議安裝 https://docs.astral.sh/uv/")
        proc = run([sys.executable, "-m", "pip", "install", "-e", ".[ui]"])
        return proc.returncode == 0
    proc = run(["uv", "sync", "--extra", "ui"])
    if proc.returncode != 0:
        LOG.error("uv sync 失敗")
        return False
    # 重型依賴按需
    for extra in ("asr", "vad"):
        if (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8").find(f'"{extra}"') != -1:
            run(["uv", "sync", "--extra", extra])
    return True


def verify_imports() -> bool:
    ok = True
    for mod in ("httpx", "numpy", "opencc", "sounddevice", "fastapi", "uvicorn", "scipy", "soxr"):
        try:
            __import__(mod.replace("-", "_"))
            LOG.info("  %s: OK", mod)
        except ImportError:
            LOG.warning("  %s: 未安裝", mod)
            ok = False
    return ok


def download_model(name: str, url: str, dest: str, sha256: str | None) -> bool:
    dest_path = PROJECT_ROOT / dest
    if dest_path.exists() and dest_path.stat().st_size > 0:
        LOG.info("  %s 已存在，跳過", name)
        return True
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("下載 %s → %s", name, dest_path)
    try:
        urllib.request.urlretrieve(url, dest_path)
    except Exception as error:
        LOG.error("  下載失敗：%s", error)
        return False
    if sha256:
        actual = hashlib.sha256(dest_path.read_bytes()).hexdigest()
        if actual.lower() != sha256.lower():
            LOG.error("  SHA256 不符：預期 %s，實際 %s", sha256, actual)
            dest_path.unlink(missing_ok=True)
            return False
    LOG.info("  %s 完成", name)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="花花 一鍵安裝")
    parser.add_argument("--check", action="store_true", help="僅檢查環境，不下載模型")
    parser.add_argument("--models", nargs="*", choices=["kokoro", "paraformer", "all"], default=["all"])
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    LOG.info("專案根：%s", PROJECT_ROOT)
    LOG.info("Python：%s", sys.version.split()[0])

    if not ensure_venv():
        LOG.error("環境建置失敗")
        raise SystemExit(1)

    if not verify_imports():
        LOG.warning("部分依賴未齊，仍可先啟動 run.cmd 測試")

    if args.check:
        LOG.info("檢查完成（--check，不下載模型）")
        return

    selected = set(args.models)
    if "all" in selected:
        selected = {"kokoro", "paraformer"}

    if "paraformer" in selected:
        LOG.info("Paraformer 由 funasr 首次執行時自動下載（無需手動）")

    if "kokoro" in selected and MODELS:
        for name, url, dest, sha in MODELS:
            if "kokoro" not in name.lower():
                continue
            download_model(name, url, dest, sha)

    LOG.info("完成！執行 ./run.cmd 即可啟動花花")
    if not (PROJECT_ROOT / "native" / "webrtc-apm.dll").exists():
        LOG.warning("提示：native/webrtc-apm.dll 缺失，執行 ./tools/fetch_native.ps1 取得 AEC 支援")


if __name__ == "__main__":
    main()
