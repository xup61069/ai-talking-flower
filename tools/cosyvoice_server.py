from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import numpy as np
from pydantic import BaseModel
import uvicorn


LOGGER = logging.getLogger("flower-cosyvoice")
app = FastAPI(title="AI Talking Flower CosyVoice")

_model = None
_sample_rate = 24_000
_prompt_text = ""
_prompt_wav = ""
_style_instruction = ""
_style_file: Path | None = None
_speaker_id = "flower"
_token_hop_len = 25
_flow_steps = 10
_inference_lock = threading.Lock()


class SpeechRequest(BaseModel):
    text: str
    speed: float = 1.0


def _stream_pcm(text: str, speed: float) -> Iterator[bytes]:
    if _model is None:
        raise RuntimeError("CosyVoice 尚未載入")

    started = time.perf_counter()
    first_chunk_at: float | None = None
    audio_samples = 0
    with _inference_lock:
        # CosyVoice 會在一次串流內逐步放大 token_hop_len；常駐服務必須在每句
        # 開始前恢復訓練使用的 25，否則後續短句會愈來愈晚才吐出第一段音訊。
        _model.model.token_hop_len = _token_hop_len
        _model.model.token_max_hop_len = 4 * _token_hop_len
        if _style_instruction:
            outputs = _model.inference_instruct2(
                text,
                _style_instruction,
                _prompt_wav,
                zero_shot_spk_id=_speaker_id,
                stream=True,
                speed=speed,
            )
        else:
            outputs = _model.inference_zero_shot(
                text,
                _prompt_text,
                _prompt_wav,
                zero_shot_spk_id=_speaker_id,
                stream=True,
                speed=speed,
            )
        for output in outputs:
            samples = output["tts_speech"].detach().float().cpu().numpy().reshape(-1)
            if samples.size == 0:
                continue
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter() - started
            audio_samples += samples.size
            pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            yield pcm

    elapsed = time.perf_counter() - started
    duration = audio_samples / _sample_rate
    LOGGER.info(
        "generated chars=%d first_chunk=%.3f seconds=%.3f audio=%.3f rtf=%.3f",
        len(text),
        first_chunk_at or 0.0,
        elapsed,
        duration,
        elapsed / duration if duration else 0.0,
    )


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok" if _model is not None else "loading",
        "sample_rate": _sample_rate,
        "speaker": _speaker_id,
        "style": bool(_style_instruction),
        "token_hop_len": _token_hop_len,
        "flow_steps": _flow_steps,
        "streaming": True,
    }


@app.post("/reload")
def reload_style() -> dict[str, object]:
    """熱載 voice/style.txt，不必重啟整個 server。"""
    if _model is None:
        raise HTTPException(status_code=503, detail="CosyVoice 尚未載入")
    if _style_file is None:
        return {"ok": True, "style": False, "reason": "沒有 style 檔"}
    with _inference_lock:
        _style_instruction = _style_file.read_text(encoding="utf-8-sig").strip()
        try:
            _model.remove_zero_shot_spk(_speaker_id)
        except Exception:
            pass
        speaker_prompt = _style_instruction or _prompt_text
        _model.add_zero_shot_spk(speaker_prompt, _prompt_wav, _speaker_id)
    LOGGER.info("已熱載 style.txt：%d 字", len(_style_instruction))
    return {"ok": True, "style": bool(_style_instruction)}


@app.post("/v1/tts")
def synthesize(request: SpeechRequest) -> StreamingResponse:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不可為空")
    if not 0.5 <= request.speed <= 2.0:
        raise HTTPException(status_code=400, detail="speed 必須介於 0.5 到 2.0")
    if _model is None:
        raise HTTPException(status_code=503, detail="CosyVoice 尚未載入")
    return StreamingResponse(
        _stream_pcm(text, request.speed),
        media_type="audio/L16",
        headers={
            "X-Sample-Rate": str(_sample_rate),
            "X-Audio-Format": "pcm_s16le",
            "X-Channels": "1",
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cosyvoice-root", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--prompt-wav", type=Path, required=True)
    parser.add_argument(
        "--prompt-text",
        default="You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。",
    )
    parser.add_argument("--style-file", type=Path)
    parser.add_argument(
        "--flow-steps",
        type=int,
        default=6,
        choices=range(4, 11),
        metavar="4-10",
        help="DiT flow-matching steps; 6 is the low-latency default, 10 is original quality",
    )
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50000)
    return parser.parse_args()


def main() -> None:
    global _model, _sample_rate, _prompt_text, _prompt_wav, _style_instruction, _style_file, _flow_steps

    args = parse_args()
    root = args.cosyvoice_root.resolve()
    model_dir = args.model_dir.resolve()
    prompt_wav = args.prompt_wav.resolve()
    style_file = args.style_file.resolve() if args.style_file else None
    if not model_dir.is_dir():
        raise FileNotFoundError(f"找不到 CosyVoice 模型：{model_dir}")
    if not prompt_wav.is_file():
        raise FileNotFoundError(f"找不到聲音參考檔：{prompt_wav}")

    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    LOGGER.info("載入 CosyVoice3：%s", model_dir)
    _model = AutoModel(model_dir=str(model_dir), fp16=True)
    _flow_steps = args.flow_steps
    flow_decoder = _model.model.flow.decoder
    original_flow_forward = flow_decoder.forward

    def low_latency_flow_forward(*forward_args, **forward_kwargs):
        forward_kwargs["n_timesteps"] = _flow_steps
        return original_flow_forward(*forward_args, **forward_kwargs)

    flow_decoder.forward = low_latency_flow_forward
    _sample_rate = int(_model.sample_rate)
    _prompt_text = args.prompt_text
    _prompt_wav = str(prompt_wav)
    if style_file:
        if not style_file.is_file():
            raise FileNotFoundError(f"找不到聲音風格檔：{style_file}")
        _style_file = style_file
        _style_instruction = style_file.read_text(encoding="utf-8-sig").strip()
    speaker_prompt = _style_instruction or _prompt_text
    _model.add_zero_shot_spk(speaker_prompt, _prompt_wav, _speaker_id)

    # 單使用者常駐服務保留 CUDA 快取，可避免每句結束後重新配置大量記憶體。
    import torch

    torch.set_float32_matmul_precision("high")
    torch.cuda.empty_cache = lambda: None
    if not args.no_warmup:
        LOGGER.info("執行一次語音暖機")
        for _ in _stream_pcm("嗨，我準備好了。", 1.0):
            pass
    LOGGER.info("CosyVoice3 就緒：%d Hz，flow steps=%d", _sample_rate, _flow_steps)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
