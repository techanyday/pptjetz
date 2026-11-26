from __future__ import annotations

import json
import logging
import os
import re
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import requests

from app.core.config import settings
from .schema import EDITORIAL_LAYOUTS, DeckSpec, EditorialSlide
from .variety import seeded_plan


API_BASE = os.environ.get("CHATGPT_API_BASE", "https://api.openai.com/v1").rstrip("/")
DEFAULT_DEADLINE_SECONDS = 150
BANNED_PHRASES = (
    "key idea",
    "practical implication",
    "key section of the presentation",
    "lorem",
    "use a sourced measure",
    "second sourced measure",
    "editorial brief",
    "learn about",
    "explore the fundamentals",
    "we will discuss",
    "this slide",
    "key takeaways",
)

_NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w])(?:\d+(?:[.,]\d+)*(?:\s*%|\s*[x×])?|\d+\s*[-–]\s*\d+)(?![\w])",
    re.IGNORECASE,
)
_NUMBER_WORD_RE = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"hundred|thousand|million|billion|first|second|third|fourth|fifth)\b",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)
FALLBACK_COUNT = 0
_PHOTO_LAYOUTS = {"cover_photo_block", "split_photo_cards", "stack_with_photo", "photo_overlay", "numbered_list_photo", "image_grid", "quote_photo"}

LAYOUT_ALIASES = {
    "title": "cover_photo_block",
    "title_slide": "cover_photo_block",
    "cover": "cover_photo_block",
    "cards": "split_photo_cards",
    "card_style": "split_photo_cards",
    "agenda": "numbered_columns",
    "thank_you": "closing_actions",
    "closing": "closing_actions",
    "roadmap": "timeline",
    "image": "photo_overlay",
    "photo": "photo_overlay",
    "photo_layout": "photo_overlay",
    "photo_overlay_layout": "photo_overlay",
    "stats": "stat_pair_banner",
    "stat": "stat_pair_banner",
    "diagram": "loop_diagram",
    "process": "loop_diagram",
    "list": "numbered_list_photo",
    "hub-and-spoke": "hub_spoke",
}


class EditorialBriefError(RuntimeError):
    pass


def _token() -> str:
    token = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not token:
        raise EditorialBriefError("Missing API token for Editorial generation")
    return token


def _model() -> str:
    return os.getenv("EDITORIAL_MODEL") or getattr(settings, "EDITORIAL_MODEL", None) or getattr(settings, "PPTJET_HYBRID_MODEL", None) or "gpt-4o-mini"


def _output_text(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("output_text", "text", "value"):
            if isinstance(payload.get(key), str) and payload[key].strip():
                return payload[key].strip()
        if payload.get("type") in {"output_text", "text"} and isinstance(payload.get("text"), str):
            return payload["text"].strip()
        chunks = []
        for key in ("output", "content", "message", "response"):
            text = _output_text(payload.get(key))
            if text:
                chunks.append(text)
        if chunks:
            return "\n".join(chunks)
        for value in payload.values():
            text = _output_text(value)
            if text:
                chunks.append(text)
        return "\n".join(chunks)
    if isinstance(payload, list):
        return "\n".join(filter(None, (_output_text(item) for item in payload)))
    if isinstance(payload, str):
        return payload
    return ""


def _parse_object(payload: Any) -> dict[str, Any]:
    text = _output_text(payload).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip().startswith("```") else lines[1:])
    if not text:
        raise EditorialBriefError("Editorial response did not contain output text")
    try:
        value = json.loads(text)
    except Exception as exc:
        # Models sometimes add one short sentence before an otherwise valid
        # object despite the JSON-only instruction.
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
            except Exception:
                raise EditorialBriefError(f"Editorial response was not valid JSON: {exc}") from exc
        else:
            raise EditorialBriefError(f"Editorial response was not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise EditorialBriefError("Editorial response must be a JSON object")
    return value


def request_json(
    *,
    instructions: str,
    input_text: str,
    max_output_tokens: int,
    deadline: float,
) -> dict[str, Any]:
    if time.monotonic() >= deadline:
        raise EditorialBriefError("Editorial generation deadline exceeded")
    response = None
    last_error: Exception | None = None
    json_input = input_text if "json" in input_text else f"{input_text}\nReturn valid json only."
    for attempt in range(3):
        remaining = max(5, int(deadline - time.monotonic()))
        try:
            response = requests.post(
                f"{API_BASE}/responses",
                headers={
                    "Authorization": f"Bearer {_token()}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
                json={
                    "model": _model(),
                    "instructions": instructions,
                    "input": json_input,
                    # Responses API structured output uses text.format. The
                    # top-level response_format field belongs to Chat Completions.
                    "text": {"format": {"type": "json_object"}},
                    "max_output_tokens": max_output_tokens,
                },
                timeout=(20, min(remaining, 90)),
            )
            response.raise_for_status()
            return _parse_object(response.json())
        except requests.HTTPError as exc:
            detail = ""
            if response is not None:
                try:
                    detail = response.text[:1200]
                except Exception:
                    pass
            last_error = EditorialBriefError(f"OpenAI Editorial API error: {detail or exc}")
            if attempt == 2 or time.monotonic() >= deadline:
                break
            time.sleep(attempt + 1)
        except (requests.RequestException, EditorialBriefError, ValueError) as exc:
            last_error = exc
            if attempt == 2 or time.monotonic() >= deadline:
                break
            time.sleep(attempt + 1)
    raise EditorialBriefError(f"Editorial JSON request failed: {last_error}")


def create_brief(
    *,
    topic: str,
    source_text: str,
    slide_count: int,
    audience: str | None,
    deadline: float,
) -> dict[str, Any]:
    instructions = """Create an editorial presentation brief as JSON only.
Return: title, subtitle, audience, thesis, style_guide, and slides.
Also return evidence: a list of claim, value, and source entries copied only from source_text.
Never invent statistics or numeric claims. If source_text contains no numbers, use no numeric
figures or number words in the presentation. Every numeric value must appear verbatim in source_text.
Write slide content as substantive topic material, not presentation instructions. Do not use
phrases such as "learn about", "explore the fundamentals", "understand", "this slide",
"we will discuss", or "key takeaways" as the main content. Each slide must state a specific
claim, mechanism, example, contrast, consequence, or action grounded in the topic or source_text.
Each slide must contain only layout and action_title at this stage.
Use the allowed editorial layouts and vary them: no adjacent duplicate layouts,
no layout more than twice unless the deck exceeds 14 slides. Use photo layouts
on at least 60 percent of slides. Include a statistic, diagram, and table or
comparison only when supported by the source. Never invent statistics."""
    slide_count = min(30, max(10, int(slide_count or 10)))
    prompt = json.dumps(
        {
            "topic": topic,
            "source_text": source_text[:6000],
            "requested_slide_count": slide_count,
            "audience": audience or "not specified",
        },
        ensure_ascii=False,
    )
    return request_json(
        instructions=instructions,
        input_text=prompt,
        max_output_tokens=3500,
        deadline=deadline,
    )


def _slide_prompt(brief: dict[str, Any], index: int, slide: dict[str, Any], neighbors: str) -> str:
    return json.dumps(
        {
            "brief": brief,
            "slide_index": index,
            "slide_plan": slide,
            "neighbor_titles": neighbors,
            "source_text": str(brief.get("source_text") or "")[:6000],
            "evidence": brief.get("evidence") or [],
            "rule": "Fill exactly the requested layout fields. Include 40-120 words of speaker notes and an optional 1-3 word section label. Do not invent numbers, number words, statistics, or sources. Every number must be present in source_text.",
        },
        ensure_ascii=False,
    )


def _layout_contract(layout: str) -> str:
    contracts = {
        "cover_photo_block": "fields={title, subtitle, footer_line}; also include eyebrow",
        "split_photo_cards": "fields={cards:[{eyebrow, heading, body},{eyebrow, heading, body}], takeaway}",
        "stack_with_photo": "fields={rows:[{label, detail} x4-6], arrow_caption}",
        "loop_diagram": "fields={steps:[3-5 concise strings]}",
        "photo_overlay": "fields={title, lines:[1-2 strings]}",
        "stat_pair_banner": "fields={stats:[{value, caption},{value, caption}], banner_heading, banner_body}",
        "table": "fields={columns:[3-5 strings], rows:[3-6 arrays matching columns]}",
        "numbered_columns": "fields={items:[{heading, body} x3-5]}",
        "color_block_list": "fields={block_eyebrow, big_phrase, block_body, points:[{heading, detail} x3]}",
        "numbered_list_photo": "fields={items:[{heading, detail} x3-5]}",
        "quote": "fields={quote, attribution}",
        "section_break": "fields={phrase}",
        "comparison": "fields={columns:[{heading, points:[4-6 substantive statements, each 6-14 words]} x2-3]}",
        "timeline": "fields={items:[{label, detail} x2-6]}",
        "hub_spoke": "fields={center, spokes:[{heading, body} x3-6]}",
        "closing_actions": "fields={actions:[{heading, detail} x3-5]}",
        "big_number": "fields={value, sentence}",
        "image_grid": "fields={captions:[3-4 meaningful visual captions of 4-10 words]}",
        "process_chevrons": "fields={steps:[3-5 short steps], step_details:[one-line explanation for each step]}",
        "quote_photo": "fields={quote, attribution}",
        "bar_chart": "fields={labels:[2-6], values:[2-6], source}",
        "matrix_2x2": "fields={quadrants:[4 labels], quadrant_details:[one concise explanation for each quadrant]}",
    }
    contract = contracts.get(layout, "fields must contain the complete layout-specific object")
    if layout in _PHOTO_LAYOUTS:
        contract += "; image={queries:[2-3 concrete searches of 2-6 words], alt_text}"
    return contract


def _normalize_layout(value: Any) -> str:
    layout = str(value or "").strip().lower()
    normalized = LAYOUT_ALIASES.get(layout, layout)
    return normalized if normalized in EDITORIAL_LAYOUTS else "cover_photo_block"


def _cut_words(value: Any, limit: int) -> str:
    words = str(value or "").strip().split()
    return " ".join(words[:limit])


def _coerce_fields(layout: str, fields: Any) -> dict[str, Any]:
    fields = fields if isinstance(fields, dict) else {}
    limits = {
        "cover_photo_block": {"title": 10, "subtitle": 22, "footer_line": 22},
        "split_photo_cards": {"takeaway": 20},
        "stack_with_photo": {"arrow_caption": 4},
        "photo_overlay": {"title": 8},
        "stat_pair_banner": {"banner_heading": 5, "banner_body": 28},
        "color_block_list": {"block_eyebrow": 3, "big_phrase": 4, "block_body": 24},
        "section_break": {"phrase": 12},
    }
    nested_limits = {
        "cover_photo_block": {"title": 10, "subtitle": 22, "footer_line": 22},
        "split_photo_cards": {"eyebrow": 2, "heading": 4, "body": 35, "takeaway": 20},
        "stack_with_photo": {"label": 3, "detail": 5, "arrow_caption": 4},
        "loop_diagram": {"steps": 9},
        "photo_overlay": {"title": 8, "lines": 20},
        "stat_pair_banner": {"value": 4, "caption": 18, "banner_heading": 5, "banner_body": 28},
        "table": {"columns": 8, "rows": 8},
        "numbered_columns": {"heading": 3, "body": 20},
        "color_block_list": {"block_eyebrow": 3, "big_phrase": 4, "block_body": 24, "heading": 4, "detail": 18},
        "numbered_list_photo": {"heading": 6, "detail": 12},
        "comparison": {"heading": 8, "points": 12},
        "timeline": {"label": 5, "detail": 18},
        "hub_spoke": {"center": 8, "heading": 3, "body": 20},
        "closing_actions": {"heading": 6, "detail": 12},
        "big_number": {"value": 3, "sentence": 22},
        "image_grid": {"captions": 8},
        "process_chevrons": {"steps": 8, "step_details": 18},
        "quote_photo": {"quote": 28, "attribution": 8},
        "bar_chart": {"labels": 5, "source": 24},
        "matrix_2x2": {"quadrants": 8, "quadrant_details": 18},
    }
    active_limits = nested_limits.get(layout, {})
    char_limits = {
        "detail": 120, "body": 140, "block_body": 160, "sentence": 180,
        "caption": 140, "heading": 60, "quote": 300, "attribution": 100,
        "label": 40, "value": 20, "source": 240,
    }
    def walk(value, key=""):
        if isinstance(value, str):
            return _cut_words(value[:char_limits.get(key, 240)], active_limits.get(key, 120))
        if isinstance(value, list):
            max_items = {"cards": 2, "rows": 6, "steps": 5, "lines": 2, "stats": 2, "columns": 5, "items": 5, "points": 6, "actions": 5, "spokes": 6}.get(key, 6)
            return [walk(item, key) for item in value[:max_items]]
        if isinstance(value, dict):
            return {child_key: walk(child_value, child_key) for child_key, child_value in value.items()}
        return value
    output = {}
    for key, value in fields.items():
        if key not in limits.get(layout, {}) and key not in {
            "cards", "rows", "steps", "lines", "stats", "columns", "items",
            "points", "actions", "center", "spokes", "quote", "attribution", "step_details", "quadrant_details",
            "value", "sentence", "captions", "quadrants", "labels", "source",
            "banner_heading", "banner_body",
        }:
            continue
        if key in limits.get(layout, {}):
            output[key] = _cut_words(value, limits[layout][key])
        elif isinstance(value, list):
            output[key] = walk(value, key)
        elif isinstance(value, dict):
            output[key] = walk(value, key)
        else:
            output[key] = value
    return output


def coerce_slide(raw: Any, layout: str) -> dict[str, Any]:
    """Trim model output to the Editorial contract without inventing content."""
    raw = raw if isinstance(raw, dict) else {}
    normalized = _normalize_layout(raw.get("layout") or layout)
    output = {"layout": normalized}
    for key in ("action_title", "variant", "section", "eyebrow", "so_what", "source", "notes"):
        if key in raw and raw[key] is not None:
            limit = {"action_title": 12, "variant": 4, "section": 3, "eyebrow": 3, "so_what": 20, "source": 32, "notes": 120}.get(key, 120)
            char_limit = {"action_title": 100, "variant": 40, "section": 30, "eyebrow": 30, "so_what": 160, "source": 240, "notes": 900}.get(key, 240)
            output[key] = _cut_words(str(raw[key])[:char_limit], limit)
    if isinstance(raw.get("image"), dict):
        image = raw["image"]
        queries = [_cut_words(query, 6) for query in image.get("queries", [])[:3] if str(query).strip()]
        if len(queries) >= 2:
            output["image"] = {"queries": queries, "alt_text": str(image.get("alt_text") or "").strip()[:180]}
    output["fields"] = _coerce_fields(normalized, raw.get("fields"))
    return output


def lint_slide(
    slide: EditorialSlide,
    *,
    seen_sentences: set[str] | None = None,
    source_text: str = "",
) -> list[str]:
    findings: list[str] = []
    data = slide.model_dump()
    visible_data = {key: value for key, value in data.items() if key not in {"notes", "source"}}
    serialized = json.dumps(visible_data, ensure_ascii=False).lower()
    for phrase in BANNED_PHRASES:
        if phrase in serialized:
            findings.append(f"banned filler phrase: {phrase}")
    if slide.layout == "stat_pair_banner":
        for stat in slide.fields.stats:
            if not str(stat.value).strip() or str(stat.value).strip() == "—":
                findings.append("stat value is empty")
    if seen_sentences is not None:
        copy_data = {key: value for key, value in data.items() if key not in {"notes", "image"}}
        sentences = re.split(r"(?<=[.!?])\s+", json.dumps(copy_data, ensure_ascii=False).lower())
        for sentence in sentences:
            normalized = " ".join(sentence.split())
            if len(normalized.split()) >= 8 and normalized in seen_sentences:
                findings.append("duplicate sentence across slides")
            elif len(normalized.split()) >= 8:
                seen_sentences.add(normalized)
    for key, value in data.items():
        if key != "image" and value == "":
            findings.append(f"empty field: {key}")
    if source_text:
        findings.extend(_numeric_content_findings(slide, source_text))
    else:
        findings.extend(_numeric_content_findings(slide, ""))
    return findings


def _number_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _NUMBER_TOKEN_RE.finditer(text):
        digits = re.findall(r"\d+", match.group(0))
        tokens.update(digits)
    tokens.update(match.group(0).lower() for match in _NUMBER_WORD_RE.finditer(text))
    return tokens


def _numeric_content_findings(slide: EditorialSlide, source_text: str) -> list[str]:
    """Reject numeric claims that are not grounded in the user's source."""
    if not source_text.strip():
        allowed: set[str] = set()
    else:
        allowed = _number_tokens(source_text)
    data = slide.model_dump()
    visible_parts: list[str] = []

    def collect(value: Any, key: str = "") -> None:
        if isinstance(value, str):
            if key not in {"layout", "variant", "number", "index", "step", "position"}:
                visible_parts.append(value)
        elif isinstance(value, dict):
            for child_key, child_value in value.items():
                collect(child_value, str(child_key))
        elif isinstance(value, list):
            for child_value in value:
                collect(child_value, key)

    collect(data)
    found = _number_tokens(" ".join(visible_parts))
    unsupported = sorted(found - allowed)
    return [f"unsupported numeric claim: {token}" for token in unsupported]


def _build_slide(
    *,
    brief: dict[str, Any],
    plan: list[dict[str, Any]],
    index: int,
    deadline: float,
    request: Callable[..., dict[str, Any]],
) -> EditorialSlide | None:
    current = plan[index]
    neighbors = ", ".join(
        str(item.get("action_title", ""))
        for item in plan[max(0, index - 1) : min(len(plan), index + 2)]
        if item is not current
    )
    target_layout = _normalize_layout(current.get("layout"))
    raw = request(
        instructions=(
            "Fill one strict Editorial slide object as json. Return exactly one top-level object "
            "with these keys: layout, action_title, notes, and fields. The top-level layout must "
            f"be exactly {target_layout!r}. Do not return brief, slide_plan, slide_index, neighbor_titles, "
            f"markdown, or a wrapper named slide. Required contract: {_layout_contract(target_layout)}."
        ),
        input_text=_slide_prompt(brief, index, current, neighbors),
        max_output_tokens=2200,
        deadline=deadline,
    )
    def prepare(value):
        candidate = coerce_slide(_unwrap_slide(value), target_layout)
        if current.get("variant") and not candidate.get("variant"):
            candidate["variant"] = current["variant"]
        if target_layout in _PHOTO_LAYOUTS and not candidate.get("image"):
            subject = " ".join(str(candidate.get("action_title") or current.get("action_title") or "editorial story").split()[:4])
            style = str((brief.get("style_guide") or {}).get("image_style") or "natural light candid")
            candidate["image"] = {
                "queries": [f"{subject} people making", "people making natural light"],
                "alt_text": f"Editorial photograph about {subject}",
            }
        return candidate
    try:
        slide = _validate_slide(prepare(raw))
    except Exception as first_error:
        logger.warning("Editorial slide validation failed index=%s layout=%s error=%s", index, target_layout, first_error)
        repair = request(
            instructions=(
                "Repair this Editorial slide json. Return exactly one top-level object with "
                f"layout={target_layout!r}, action_title, notes, and fields. Do not wrap it "
                f"inside another object and do not include brief or slide_plan. Required contract: "
                f"{_layout_contract(target_layout)}."
            ),
            input_text=json.dumps({"candidate": raw, "error": str(first_error), "required_layout": target_layout}, ensure_ascii=False),
            max_output_tokens=2200,
            deadline=deadline,
        )
        try:
            slide = _validate_slide(prepare(repair))
        except Exception as repair_error:
            logger.warning("Editorial slide repair failed index=%s layout=%s error=%s", index, target_layout, repair_error)
            simpler = request(
                instructions=(
                    "Regenerate this slide as valid json using only one of these simpler layouts: "
                    "numbered_columns, closing_actions, quote. Return a complete top-level "
                    "Editorial slide object with action_title, notes, and fields."
                ),
                input_text=json.dumps({
                    "claim": current.get("action_title"),
                    "thesis": brief.get("thesis"),
                    "source_text": str(brief.get("source_text") or "")[:6000],
                    "error": str(repair_error),
                }, ensure_ascii=False),
                max_output_tokens=2200,
                deadline=deadline,
            )
            try:
                slide = _validate_slide(coerce_slide(_unwrap_slide(simpler), "numbered_columns"))
            except Exception as final_error:
                logger.error("Editorial slide dropped index=%s layout=%s error=%s", index, target_layout, final_error)
                if os.getenv("EDITORIAL_STRICT", "").strip() == "1":
                    raise
                return None
    findings = lint_slide(slide)
    if findings:
        logger.warning("Editorial slide lint failed index=%s layout=%s findings=%s", index, slide.layout, findings)
        if os.getenv("EDITORIAL_STRICT", "").strip() == "1":
            raise EditorialBriefError(f"Editorial slide {index + 1} failed lint: {'; '.join(findings)}")
        return None
    return slide


def _validate_slide(raw: dict[str, Any]) -> EditorialSlide:
    from .schema import EditorialSlideAdapter

    return EditorialSlideAdapter.validate_python([raw])[0]


def _unwrap_slide(raw: dict[str, Any]) -> dict[str, Any]:
    """Recover a slide object when a model adds a single response wrapper."""
    candidate = raw
    for _ in range(3):
        if not isinstance(candidate, dict):
            return {}
        if "layout" in candidate:
            candidate["layout"] = _normalize_layout(candidate.get("layout"))
            return candidate
        nested = next(
            (candidate[key] for key in ("slide", "data", "result", "output") if isinstance(candidate.get(key), dict)),
            None,
        )
        if nested is None:
            return candidate
        candidate = nested
    return candidate


def _clean_style_guide(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    allowed = {"palette_hint", "image_style", "mood", "theme_id"}
    return {
        key: str(value[key]).strip()
        for key in allowed
        if isinstance(value.get(key), str) and value[key].strip()
    }


def _is_cyclic_content(content_signal: str) -> bool:
    text = re.sub(r"\s+", " ", str(content_signal or "").lower())
    return any(
        phrase in text
        for phrase in (
            "feedback loop",
            "closed loop",
            "cyclic",
            "cycle",
            "circular",
            "recurring",
            "recursive",
            "iterative",
            "flywheel",
            "reinforcing loop",
        )
    )


def _normalize_slide_plan(
    plan: list[Any],
    evidence: list[Any] | None = None,
    content_signal: str = "",
) -> list[dict[str, Any]]:
    """Turn model layout suggestions into a varied, schema-valid plan."""
    preferred = [
        "cover_photo_block",
        "split_photo_cards",
        "stack_with_photo",
        "loop_diagram",
        "photo_overlay",
        "stat_pair_banner",
        "table",
        "numbered_columns",
        "color_block_list",
        "numbered_list_photo",
        "quote",
        "comparison",
        "timeline",
        "hub_spoke",
        "closing_actions",
    ]
    seed = int(os.getenv("EDITORIAL_SEED", "0") or 0)
    rng = random.Random(seed + len(plan) * 7919)
    middle = preferred[1:-1]
    rng.shuffle(middle)
    preferred = [preferred[0], *middle, preferred[-1]]
    normalized: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    evidence = evidence or []
    numeric_evidence = sum(bool(re.search(r"\d", str(item.get("value", "")))) for item in evidence if isinstance(item, dict))
    requested_layouts = {_normalize_layout(item.get("layout")) for item in plan if isinstance(item, dict)}
    force_unique = len(plan) <= 10 and len(requested_layouts) <= max(1, len(plan) // 2)
    for index, item in enumerate(plan):
        item = item if isinstance(item, dict) else {}
        requested = _normalize_layout(item.get("layout"))
        if requested == "loop_diagram" and not _is_cyclic_content(content_signal):
            requested = "process_chevrons"
        if requested == "stat_pair_banner" and numeric_evidence < 2:
            requested = "numbered_columns"
        if requested == "table" and not (len(evidence) >= 3 or item.get("tabular") or item.get("comparable_items")):
            requested = "comparison"
        layout = requested
        previous = normalized[-1]["layout"] if normalized else None
        if layout == previous or counts.get(layout, 0) >= (1 if force_unique else (3 if len(plan) > 14 else 2)):
            layout = next(
                (candidate for candidate in preferred if candidate != previous and counts.get(candidate, 0) == 0),
                next(candidate for candidate in preferred if candidate != previous),
            )
        normalized.append(
            {
                **item,
                "layout": layout,
                "action_title": str(item.get("action_title") or item.get("title") or "Core implication").strip(),
            }
        )
        counts[layout] = counts.get(layout, 0) + 1
    if normalized:
        normalized[0]["layout"] = "cover_photo_block"
        normalized[-1]["layout"] = "closing_actions"
        # Enforce an image-led deck instead of relying on the model to honor
        # the photo-layout request in the brief.
        photo_target = 1 + ((max(0, len(normalized) - 2) * 3 + 4) // 5)
        photo_count = sum(item["layout"] in _PHOTO_LAYOUTS for item in normalized)
        photo_pool = [
            "split_photo_cards",
            "photo_overlay",
            "numbered_list_photo",
            "image_grid",
            "quote_photo",
        ]
        used_layouts = {item["layout"] for item in normalized}
        photo_pool = [layout for layout in photo_pool if layout not in used_layouts]
        for item in normalized[1:-1]:
            if photo_count >= photo_target:
                break
            if item["layout"] in _PHOTO_LAYOUTS or not photo_pool:
                continue
            item["layout"] = photo_pool.pop(0)
            photo_count += 1
    return normalized


def build_editorial_spec(
    *,
    topic: str,
    source_text: str = "",
    slide_count: int = 10,
    audience: str | None = None,
    deadline_seconds: int = DEFAULT_DEADLINE_SECONDS,
    request: Callable[..., dict[str, Any]] = request_json,
) -> DeckSpec:
    deadline = time.monotonic() + min(max(10, deadline_seconds), DEFAULT_DEADLINE_SECONDS)
    brief = create_brief(
        topic=topic,
        source_text=source_text,
        slide_count=slide_count,
        audience=audience,
        deadline=deadline,
    ) if request is request_json else request(
        instructions="Create an Editorial brief as JSON.",
        input_text=json.dumps({"topic": topic, "source_text": source_text, "slide_count": slide_count}),
        max_output_tokens=3500,
        deadline=deadline,
    )
    if isinstance(brief, dict):
        brief["source_text"] = source_text[:6000]
    effective_count = min(30, max(10, int(slide_count or 10)))
    plan = brief.get("slides")
    if not isinstance(plan, list) or not plan:
        raise EditorialBriefError("Editorial brief did not contain a slide plan")
    evidence = brief.get("evidence") if isinstance(brief.get("evidence"), list) else []
    if request is request_json:
        seed_value = os.getenv("EDITORIAL_SEED")
        seed = int(seed_value) if seed_value and seed_value.lstrip("-").isdigit() else None
        arc_name, seeded = seeded_plan(topic, effective_count, seed)
        brief["arc"] = arc_name
        actual_seed = seed if seed is not None else abs(hash((topic, arc_name, time.time_ns()))) % (2**32)
        brief["_planner_seed"] = actual_seed
        logger.info("Editorial planner topic=%r arc=%s seed=%s", topic, arc_name, actual_seed)
        plan = [
            {
                **(plan[index] if index < len(plan) and isinstance(plan[index], dict) else {}),
                "layout": layout,
                "role": role,
                "variant": variant,
            }
            for index, (role, layout, variant) in enumerate(seeded)
            ]
    plan = _normalize_slide_plan(plan[:effective_count], evidence, f"{topic}\n{source_text}")
    while len(plan) < effective_count:
        index = len(plan)
        plan.insert(-1 if plan else 0, {
            "layout": "numbered_columns",
            "action_title": "Evidence changes the next decision",
        })
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_build_slide, brief=brief, plan=plan, index=index, deadline=deadline, request=request)
            for index in range(len(plan))
        ]
        generated = [future.result() for future in futures]
    slides = []
    seen_sentences: set[str] = set()
    for index, slide in enumerate(generated):
        if slide is None:
            continue
        findings = lint_slide(slide, seen_sentences=seen_sentences, source_text=source_text)
        if findings:
            logger.warning("Editorial slide dropped after deck lint index=%s layout=%s findings=%s", index, slide.layout, findings)
            if os.getenv("EDITORIAL_STRICT", "").strip() == "1":
                raise EditorialBriefError(f"Editorial slide {index + 1} failed deck lint: {findings}")
            continue
        slides.append(slide)
    if not slides:
        raise EditorialBriefError("Editorial generation produced no valid slides")
    notes = slides[0].notes
    if slides[0].layout != "cover_photo_block":
        cover_raw = {
            "layout": "cover_photo_block",
            "action_title": brief["thesis"],
            "eyebrow": "Opening claim",
            "notes": notes,
            "fields": {
                "title": brief.get("title") or topic,
                "subtitle": brief.get("subtitle") or brief["thesis"],
                "footer_line": brief.get("audience") or audience or "Editorial briefing",
            },
        }
        try:
            slides.insert(0, _validate_slide(coerce_slide(cover_raw, "cover_photo_block")))
        except Exception:
            logger.exception("Unable to construct required Editorial cover")
    if slides[-1].layout != "closing_actions":
        closing_raw = {
            "layout": "closing_actions",
            "action_title": "The next decision starts with this claim",
            "notes": notes,
            "fields": {
                "actions": [
                    {"heading": "Choose", "detail": brief["thesis"]},
                    {"heading": "Test", "detail": "Turn the claim into a visible next step."},
                    {"heading": "Learn", "detail": "Use the result to refine the decision."},
                ],
            },
        }
        try:
            slides.append(_validate_slide(coerce_slide(closing_raw, "closing_actions")))
        except Exception:
            logger.exception("Unable to construct required Editorial closing")
    # Preserve the requested deck length even when a model repeatedly returns
    # invalid or duplicate content. A valid slide is cloned with a distinct
    # claim rather than silently shrinking the presentation.
    seed_slides = list(slides)
    while len(slides) < effective_count:
        source = seed_slides[(len(slides) - len(seed_slides)) % len(seed_slides)]
        slides.append(source.model_copy(update={
            "action_title": f"{source.action_title} — continuation {len(slides) + 1}",
        }))
    if slides[-1].layout != "closing_actions":
        slides.pop()
        closing = {
            "layout": "closing_actions",
            "action_title": "The next decision starts with this claim",
            "notes": slides[0].notes,
            "fields": {"actions": [
                {"heading": "Choose", "detail": brief["thesis"]},
                {"heading": "Test", "detail": "Turn the claim into a visible next step."},
                {"heading": "Learn", "detail": "Use the result to refine the decision."},
            ]},
        }
        slides.append(_validate_slide(coerce_slide(closing, "closing_actions")))
    return DeckSpec(
        title=brief["title"],
        subtitle=brief.get("subtitle"),
        audience=brief.get("audience") or audience or "General audience",
        thesis=brief["thesis"],
        arc=brief.get("arc"),
        style_guide={
            **_clean_style_guide(brief.get("style_guide")),
            "theme_id": (
                _clean_style_guide(brief.get("style_guide")).get("theme_id")
                or ["warm_editorial", "midnight_gold", "forest_cream", "slate_teal", "berry_blush", "ocean_coral"][
                    (int(brief.get("_planner_seed", os.getenv("EDITORIAL_SEED", "0") or 0)) + abs(hash(topic))) % 6
                ]
            ),
        },
        slides=slides,
    )
