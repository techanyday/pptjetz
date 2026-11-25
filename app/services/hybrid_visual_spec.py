from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

import requests

from app.core.config import settings
from app import db
from app.models import LLMCall, Pricing
from app.services.hybrid_pptx_renderer import render_visual_spec

API_BASE = os.environ.get("CHATGPT_API_BASE", "https://api.openai.com/v1").rstrip("/")

HYBRID_INSTRUCTIONS = """You are an expert presentation information designer.
Return only a JSON array of slide objects for a local python-pptx renderer.
Do not return markdown, Python code, or narrative.

Choose slide types dynamically based on the topic, audience, and user brief.
Do not use a fixed sequence and do not use the same type repeatedly unless the
content genuinely requires it. The allowed slide types are:
title, agenda, card_style, cards, double_column, deep_dive, comparison, table,
infographic, key_takeaways, timeline, roadmap, swot, kpi, risk_matrix,
before_after, architecture, pricing, impact_effort_matrix, impact_matrix,
prioritization_matrix, image, thank_you.

This is a visual slide specification, not an essay. Use concise content and
choose varied structures: cards, process flows, timelines, roadmaps, hub-and-spoke
architecture, comparisons, KPI panels, risk matrices, and infographics.
Use charts or metrics only when supported by the brief. Do not invent facts,
statistics, citations, or sources. Prefer native editable shapes and concise
labels over long paragraphs.

Use these content shapes so the local renderer can draw them:
- cards/card_style/infographic/key_takeaways: {"cards": ["Label: short explanation", "..."]} or {"bullets": [...]}
- process/roadmap: {"steps": [{"label": "...", "description": "..."}]}
- timeline: {"events": [{"label": "...", "description": "..."}]}
- architecture: {"boxes": [{"label": "...", "description": "..."}], "connections": [["A", "B"]]}
- comparison: {"columns": [{"heading": "...", "points": ["...", "..."]}, ...]}
- double_column/deep_dive/before_after: {"columns": [{"heading": "...", "points": ["...", "..."]}, ...]}
- table: {"headers": ["..."], "rows": [["...", "..."]]}
- kpi: {"metrics": [{"label": "...", "value": "...", "caption": "..."}]}
- risk_matrix: {"risks": [{"label": "...", "impact": "...", "likelihood": "..."}]}
- pricing: {"tiers": [{"name": "...", "price": "...", "features": ["..."]}]}
- impact_effort_matrix/impact_matrix/prioritization_matrix: {"items": [{"label": "...", "impact": "...", "effort": "..."}]}
"""


class HybridVisualSpecError(RuntimeError):
    pass


def _token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        pricing = Pricing.query.filter(Pricing.model_id == model).order_by(Pricing.effective_from.desc()).first()
        if pricing:
            return (input_tokens / 1000.0) * float(pricing.input_per_1k) + (output_tokens / 1000.0) * float(pricing.output_per_1k)
    except Exception:
        pass
    return 0.0


def _log_usage(meta: dict[str, Any], *, deck_id: str | None, user_id: str | None) -> float:
    cost = _token_cost(meta["model"], meta["prompt_tokens"], meta["completion_tokens"])
    try:
        db.session.add(
            LLMCall(
                deck_id=deck_id,
                export_id=deck_id,
                user_id=user_id,
                route="generate_hybrid_visual_spec",
                model=meta["model"],
                request_id=meta["request_id"],
                prompt_tokens=meta["prompt_tokens"],
                completion_tokens=meta["completion_tokens"],
                total_tokens=meta["total_tokens"],
                cost_usd=cost,
                created_at=datetime.utcnow(),
            )
        )
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
    return cost


def _token() -> str:
    token = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not token:
        raise HybridVisualSpecError("Missing API token: set CHATGPT_API_TOKEN or OPENAI_API_KEY")
    return token


def _model() -> str:
    return (
        os.getenv("PPTJET_HYBRID_MODEL")
        or getattr(settings, "PPTJET_HYBRID_MODEL", None)
        or "gpt-4o-mini"
    )


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _output_text(data: dict[str, Any]) -> str:
    chunks = []
    for item in _walk(data):
        if item.get("type") == "output_text" and str(item.get("text") or "").strip():
            chunks.append(str(item["text"]).strip())
    return "\n".join(chunks).strip()


def _parse_json(text: str) -> list[dict[str, Any]]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise HybridVisualSpecError(f"Hybrid visual specification was not valid JSON: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise HybridVisualSpecError("Hybrid visual specification did not contain slides")
    slides = [item for item in value if isinstance(item, dict)]
    if len(slides) < 3:
        raise HybridVisualSpecError("Hybrid visual specification contained too few valid slides")
    return slides


def _prompt(*, title: str, subtitle: str | None, theme_name: str, input_text: str, num_slides: int) -> str:
    count = num_slides if num_slides and num_slides > 0 else 10
    return "\n".join(
        [
            f"Topic/title: {title or 'Create an appropriate title'}",
            f"Requested slides: {count}",
            f"Theme preference: {theme_name or 'modern professional'}",
            f"Subtitle: {subtitle or 'none'}",
            "Brief:",
            input_text or "Create a useful professional presentation about the topic.",
            "Return a complete deck. Include a title and conclusion. Select the slide types that best fit the topic; do not force every type or a fixed order. For 8 or more slides, include at least four different visual structures beyond plain bullets, selected from the allowed type list.",
            "Each slide object must have type, title, and content. Keep each content field concise.",
        ]
    )


def generate_visual_spec(
    *,
    title: str,
    subtitle: str | None,
    theme_name: str,
    input_text: str,
    num_slides: int,
    deck_id: str | None = None,
    user_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    token = _token()
    timeout = max(30, int(os.getenv("PPTJET_HYBRID_TIMEOUT_SECONDS", "90")))
    request_body = {
        "model": _model(),
        "instructions": HYBRID_INSTRUCTIONS,
        "input": _prompt(
            title=title,
            subtitle=subtitle,
            theme_name=theme_name,
            input_text=input_text,
            num_slides=num_slides,
        ),
        "max_output_tokens": int(os.getenv("PPTJET_HYBRID_MAX_OUTPUT_TOKENS", "6000")),
    }
    response = None
    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(
                f"{API_BASE}/responses",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
                json=request_body,
                timeout=(20, timeout),
            )
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = exc
            if attempt >= 2:
                raise HybridVisualSpecError(
                    f"Could not connect to OpenAI at {API_BASE} after 3 attempts: {exc}"
                ) from exc
            time.sleep(1.0 * (attempt + 1))
    if response is None:
        raise HybridVisualSpecError(f"Could not connect to OpenAI at {API_BASE}: {last_error}")
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise HybridVisualSpecError(f"Hybrid Responses API error: {response.text[:2000]}") from exc
    data = response.json()
    slides = _parse_json(_output_text(data))
    usage = data.get("usage") or {}
    meta = {
        "provider": "hybrid_visual_spec",
        "model": _model(),
        "request_id": str(data.get("id") or ""),
        "prompt_tokens": int(usage.get("input_tokens") or 0),
        "completion_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
    meta["token_cost_usd"] = _log_usage(meta, deck_id=deck_id, user_id=user_id)
    meta["total_cost_usd"] = meta["token_cost_usd"]
    meta["rendered_pptx_bytes"] = render_visual_spec(slides, theme_name=theme_name)
    return slides, meta
