"""Minimal ChatGPT 3.5 Turbo client using the Chat Completions API.

Reads the API token and model from app.core.config.settings.
Returns the assistant's message content as a string.
"""
from __future__ import annotations

import os
import requests
from typing import List, Dict

from app.core.config import settings

API_BASE = os.environ.get("CHATGPT_API_BASE", "https://api.openai.com/v1")

class ChatGPTError(RuntimeError):
    pass


def chat_completion(
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    timeout: int = 60,
) -> str:
    token = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not token:
        raise ChatGPTError("Missing API token: set CHATGPT_API_TOKEN (preferred) or OPENAI_API_KEY in .env")

    url = f"{API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.CHATGPT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        masked = token[:6] + "..." if isinstance(token, str) and len(token) >= 6 else "<short>"
        print(f"Debug - ChatGPT request model={settings.CHATGPT_MODEL} msg_count={len(messages)} token_present=True token_head={masked}")
    except Exception:
        pass
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:  # noqa: TRY003
        # Surface API errors clearly to caller/UI
        detail = resp.text
        raise ChatGPTError(f"ChatGPT API error: {exc} – {detail}") from exc

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        raise ChatGPTError(f"Unexpected ChatGPT response format: {data}") from exc
