from __future__ import annotations

import hashlib
import threading
from typing import Any, Iterable

from app.services.stock_images import download_image_bytes, search_pexels_candidates


_CACHE: dict[str, bytes] = {}
_CACHE_LOCK = threading.Lock()


def _query_words(value: str) -> set[str]:
    return {word.lower() for word in str(value or "").split() if len(word) > 2}


def _orientation(frame_w_in: float, frame_h_in: float) -> str:
    if frame_h_in > frame_w_in * 1.12:
        return "portrait"
    if frame_w_in > frame_h_in * 1.12:
        return "landscape"
    return "square"


def _score(candidate: dict[str, Any], query: str, frame_w_in: float, frame_h_in: float) -> float:
    width, height = float(candidate.get("width") or 0), float(candidate.get("height") or 0)
    if not width or not height:
        return -1000
    target_ratio = frame_w_in / max(frame_h_in, 0.1)
    ratio_score = max(0.0, 30.0 - abs((width / height) - target_ratio) * 25.0)
    resolution_score = min(width / max(frame_w_in * 120, 1), height / max(frame_h_in * 120, 1)) * 10
    overlap = len(_query_words(query) & _query_words(candidate.get("alt", "")))
    return ratio_score + min(resolution_score, 20.0) + overlap * 4


def _cache_key(url: str, frame_w_in: float, frame_h_in: float) -> str:
    return hashlib.sha256(f"{url}|{frame_w_in:.3f}|{frame_h_in:.3f}".encode()).hexdigest()


def get_slide_photo(
    image_spec: Any,
    frame_w_in: float,
    frame_h_in: float,
    used_ids: set[str] | Iterable[str],
) -> bytes | None:
    """Return a frame-suitable Pexels image or ``None`` for graceful fallback."""
    if isinstance(image_spec, dict):
        queries = list(image_spec.get("queries") or [])
        style = str(image_spec.get("image_style") or "")
    else:
        queries = list(getattr(image_spec, "queries", None) or [])
        style = str(getattr(image_spec, "image_style", "") or "")
    used = {str(item) for item in used_ids}
    used_target = used_ids if isinstance(used_ids, set) else None
    orientation = _orientation(frame_w_in, frame_h_in)
    for query in queries[:3]:
        full_query = " ".join(part for part in (str(query).strip(), style.strip()) if part)
        candidates = search_pexels_candidates(full_query, orientation=orientation, per_page=15)
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("id")) not in used and str(candidate.get("image_url")) not in used
        ]
        candidates.sort(key=lambda item: _score(item, full_query, frame_w_in, frame_h_in), reverse=True)
        for candidate in candidates:
            key = _cache_key(candidate["image_url"], frame_w_in, frame_h_in)
            with _CACHE_LOCK:
                cached = _CACHE.get(key)
            if cached:
                used.add(str(candidate.get("id")))
                if used_target is not None:
                    used_target.add(str(candidate.get("id")))
                return cached
            content = download_image_bytes(candidate["image_url"], timeout=10)
            if content:
                with _CACHE_LOCK:
                    _CACHE[key] = content
                used.add(str(candidate.get("id")))
                if used_target is not None:
                    used_target.add(str(candidate.get("id")))
                return content
    return None


__all__ = ["get_slide_photo"]
