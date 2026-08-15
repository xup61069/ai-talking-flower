from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="量測 TTS server 的首音延遲與 RTF")
    parser.add_argument("--url", default="http://127.0.0.1:50001/v1/tts")
    parser.add_argument("--text", default="好啦，这次我会等整句声音全部生成完成，再一次顺顺地说给你听。")
    parser.add_argument("--voice", default="zf_001")
    parser.add_argument("--speed", type=float, default=0.9)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    payload = {"text": args.text, "voice": args.voice, "speed": args.speed}
    with httpx.Client(timeout=120.0) as client:
        for index in range(args.repeat):
            started = time.perf_counter()
            first_chunk_at = None
            received = 0
            with client.stream("POST", args.url, json=payload) as response:
                response.raise_for_status()
                sample_rate = int(response.headers.get("X-Sample-Rate", "24000"))
                for chunk in response.iter_bytes():
                    if first_chunk_at is None:
                        first_chunk_at = time.perf_counter() - started
                    received += len(chunk) - len(chunk) % 2
            total = time.perf_counter() - started
            duration = received / 2 / sample_rate
            print(
                f"[{index + 1}] first_chunk={first_chunk_at or 0.0:.3f}s "
                f"total={total:.3f}s audio={duration:.3f}s "
                f"rtf={total / duration if duration else 0.0:.3f} "
                f"rate={sample_rate}Hz bytes={received}"
            )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
