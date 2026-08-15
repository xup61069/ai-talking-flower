from __future__ import annotations

import argparse
import logging
import threading
import time
from typing import Iterator

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from kokoro import KModel, KPipeline
from pydantic import BaseModel, Field


LOGGER = logging.getLogger("flower-kokoro")
REPO_ID = "hexgrad/Kokoro-82M-v1.1-zh"
SAMPLE_RATE = 24_000

app = FastAPI(title="AI Talking Flower Kokoro TTS")
_pipeline: KPipeline | None = None
_device = "unknown"
_render_lock = threading.Lock()


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    voice: str = "zf_001"
    speed: float = Field(default=0.9, ge=0.6, le=1.4)


def _stream_pcm(request: SpeechRequest) -> Iterator[bytes]:
    if _pipeline is None:
        raise RuntimeError("Kokoro 尚未載入")

    started = time.perf_counter()
    first_chunk_at: float | None = None
    audio_samples = 0
    with _render_lock, torch.inference_mode():
        for result in _pipeline(request.text, voice=request.voice, speed=request.speed):
            if result.audio is None:
                continue
            audio = result.audio
            if isinstance(audio, torch.Tensor):
                audio = audio.detach().cpu().numpy()
            waveform = np.asarray(audio, dtype=np.float32).reshape(-1)
            if waveform.size == 0:
                continue
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter() - started
            audio_samples += waveform.size
            pcm = (np.clip(waveform, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            yield pcm

    elapsed = time.perf_counter() - started
    duration = audio_samples / SAMPLE_RATE
    LOGGER.info(
        "generated chars=%d first_chunk=%.3f seconds=%.3f audio=%.3f rtf=%.3f voice=%s",
        len(request.text),
        first_chunk_at or 0.0,
        elapsed,
        duration,
        elapsed / duration if duration else 0.0,
        request.voice,
    )


@app.get("/health")
def health() -> dict[str, str | int | bool]:
    return {
        "status": "ok" if _pipeline is not None else "loading",
        "engine": "kokoro-82m-v1.1-zh",
        "device": _device,
        "sample_rate": SAMPLE_RATE,
        "streaming": True,
    }


@app.post("/v1/tts")
def speech(request: SpeechRequest) -> StreamingResponse:
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Kokoro 尚未載入")
    return StreamingResponse(
        _stream_pcm(request),
        media_type="audio/L16",
        headers={
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Audio-Format": "pcm_s16le",
            "X-Channels": "1",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50001)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    global _pipeline, _device
    _device = args.device
    LOGGER.info("loading %s on %s", REPO_ID, _device)
    model = KModel(repo_id=REPO_ID).to(_device).eval()

    # 中文管線遇到拉丁字母時，交由英文音素管線處理。
    english = KPipeline(lang_code="a", repo_id=REPO_ID, model=False)

    def english_phonemes(text: str) -> str:
        return next(english(text)).phonemes

    _pipeline = KPipeline(
        lang_code="z",
        repo_id=REPO_ID,
        model=model,
        en_callable=english_phonemes,
    )
    for _ in _stream_pcm(SpeechRequest(text="嗨，我准备好了。", voice="zf_001", speed=0.9)):
        pass
    LOGGER.info("ready")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
