from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .services import (
    parse_youtube_id,
    get_captions,
    download_audio_wav,
    transcribe_wav,
    join_segments,
)

router = APIRouter(prefix="/api", tags=["transcriber"])

# --- Simple in-memory token bucket rate limiter ---
@dataclass
class Bucket:
    tokens: float
    last: float

RATE_CAP = 10  # max tokens
REFILL_SECONDS = 60 * 10  # 10 minutes to fully refill
RATE_PER_SEC = RATE_CAP / REFILL_SECONDS
_buckets: Dict[str, Bucket] = {}


def _take(ip: str) -> bool:
    now = time.time()
    b = _buckets.get(ip)
    if not b:
        _buckets[ip] = Bucket(tokens=RATE_CAP - 1, last=now)
        return True
    # refill
    elapsed = max(0.0, now - b.last)
    b.tokens = min(RATE_CAP, b.tokens + elapsed * RATE_PER_SEC)
    b.last = now
    if b.tokens >= 1.0:
        b.tokens -= 1.0
        return True
    return False


@router.get("/health", include_in_schema=False)
async def health() -> Dict[str, str]:
    return {"ok": "true"}


@router.post("/transcribe")
async def transcribe(request: Request) -> JSONResponse:
    try:
        data: Dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    ip = request.client.host if request.client else "unknown"
    if not _take(ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a bit and try again.")

    url = (data.get("url") or "").strip()
    prefer_captions = bool(data.get("preferCaptions", True))
    tos_ok = bool(data.get("authorized", False))

    if not url:
        raise HTTPException(status_code=422, detail="YouTube URL is required")
    if not tos_ok:
        raise HTTPException(status_code=400, detail="You must confirm authorization to process this video")

    yt_id = parse_youtube_id(url)
    if not yt_id:
        raise HTTPException(status_code=422, detail="Invalid YouTube URL")

    segments = None
    # Try captions first
    if prefer_captions:
        segments = get_captions(yt_id)

    text = None
    if segments:
        text = join_segments(segments)
    else:
        # Fallback: download and transcribe
        wav_path = download_audio_wav(url, yt_id)
        try:
            asr_segments = transcribe_wav(wav_path)
            text = join_segments(asr_segments)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
                # remove temp dir as well
                wav_dir = wav_path.parent
                for p in wav_dir.iterdir():
                    try:
                        p.unlink()
                    except Exception:
                        pass
                try:
                    wav_dir.rmdir()
                except Exception:
                    pass
            except Exception:
                pass

    if not text:
        raise HTTPException(status_code=500, detail="Failed to obtain captions or transcription")

    word_count = len([w for w in text.split() if w.strip()])
    return JSONResponse({"ok": True, "videoId": yt_id, "text": text, "wordCount": word_count})
