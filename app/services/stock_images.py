from __future__ import annotations

import hashlib
import logging
import os
import json
import re
from pathlib import Path
from typing import Any, Iterable

import requests


PEXELS_ENDPOINT = "https://api.pexels.com/v1/search"
logger = logging.getLogger(__name__)
_SESSION_USED_IMAGE_URLS: set[str] = set()


def _safe_query(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:180]


def _extension(content_type: str, url: str) -> str:
    ctype = str(content_type or "").lower()
    if "png" in ctype:
        return ".png"
    if "webp" in ctype:
        return ".webp"
    if "gif" in ctype:
        return ".gif"
    if "jpeg" in ctype or "jpg" in ctype:
        return ".jpg"
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"


def _download_image(image_url: str, output_dir: Path, query: str) -> str | None:
    try:
        response = requests.get(image_url, timeout=20, headers={"User-Agent": "PPTJetX/1.0"})
        response.raise_for_status()
        content = response.content
        if len(content) < 2048:
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{query}|{image_url}".encode("utf-8")).hexdigest()[:20]
        path = output_dir / f"stock_{digest}{_extension(response.headers.get('content-type', ''), image_url)}"
        path.write_bytes(content)
        return str(path)
    except Exception:
        return None


def fetch_pexels_image(query: str, *, used_urls: Iterable[str] = ()) -> dict[str, Any] | None:
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        return None
    clean_query = _safe_query(query)
    used = {str(x).strip() for x in used_urls if str(x).strip()}
    used.update(_SESSION_USED_IMAGE_URLS)
    try:
        response = requests.get(
            PEXELS_ENDPOINT,
            params={"query": clean_query, "orientation": "landscape", "size": "large", "per_page": 20},
            timeout=15,
            headers={"Authorization": api_key, "User-Agent": "PPTJetX/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Pexels image lookup failed for query=%r: %s", clean_query, exc)
        return None
    photos = payload.get("photos") if isinstance(payload, dict) else None
    if not isinstance(photos, list):
        return None
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        src = photo.get("src") if isinstance(photo.get("src"), dict) else {}
        image_url = str(src.get("large2x") or src.get("large") or src.get("original") or "").strip()
        if not image_url.startswith(("http://", "https://")) or image_url in used:
            continue
        photographer = str(photo.get("photographer") or "").strip() or None
        _SESSION_USED_IMAGE_URLS.add(image_url)
        return {
            "image_url": image_url,
            "thumbnail_url": str(src.get("medium") or "").strip() or None,
            "source_url": str(photo.get("url") or image_url).strip(),
            "source_name": "Pexels",
            "creator": photographer,
            "license": "Pexels License",
            "license_url": "https://www.pexels.com/license/",
            "attribution": f"Photo by {photographer} on Pexels" if photographer else "Pexels",
            "query": clean_query,
        }
    return None


def resolve_stock_image(
    query: str,
    *,
    output_dir: str | os.PathLike[str],
    used_urls: Iterable[str] = (),
) -> dict[str, Any] | None:
    output_path = Path(output_dir)
    history_path = output_path.parent / ".stock_image_history.json"
    persisted_used: set[str] = set()
    try:
        raw_history = json.loads(history_path.read_text(encoding="utf-8"))
        if isinstance(raw_history, list):
            persisted_used = {str(x).strip() for x in raw_history if str(x).strip()}
    except Exception:
        pass
    result = fetch_pexels_image(query, used_urls=set(used_urls) | persisted_used)
    if not result:
        return None
    local_path = _download_image(result["image_url"], output_path, result["query"])
    if not local_path:
        return None
    image_url = str(result.get("image_url") or "").strip()
    if image_url:
        persisted_used.add(image_url)
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(json.dumps(sorted(persisted_used), indent=2), encoding="utf-8")
        except Exception:
            pass
    result["local_path"] = local_path
    return result
