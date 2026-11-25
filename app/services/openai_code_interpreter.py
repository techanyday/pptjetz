from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import requests

from app import db
from app.core.config import settings
from app.models import LLMCall, Pricing

logger = logging.getLogger(__name__)

OPENAI_API_BASE = os.environ.get("CHATGPT_API_BASE", "https://api.openai.com/v1").rstrip("/")
DEFAULT_OUTPUT_PATH = "/mnt/data/generated_presentation.pptx"
ALLOWED_SOURCE_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".docx",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
}

GENERATION_INSTRUCTIONS = """You are an expert presentation designer and Python PowerPoint developer.
You must use the Python code interpreter tool to generate the presentation file.
Write a complete, self-contained python-pptx script and execute it inside the hosted sandbox.
Create native, editable PowerPoint elements wherever possible.
Use professional typography, colour palettes, spacing, alignment, and visual hierarchy.
Choose layouts that match the content of each slide. Use diagrams, process flows, timelines, tables, charts, comparison layouts, and infographic-style elements when appropriate.
Avoid repetitive slide layouts, excessive text, overcrowded slides, blank slides, and text-only slides.
Respect the requested audience, tone, topic, slide count, and design preferences.
Include a professional cover slide where appropriate.
Use images only when they materially improve the presentation and are available through provided files or standard Python-accessible methods in the sandbox.
Do not fabricate facts, statistics, citations, or sources.
This is a visual presentation, not an outline. Every slide must have deliberate visual design:
- Define a coherent 4-6 colour palette and use coloured backgrounds, panels, accent bars, or cards.
- Use at least 3-5 meaningful visual elements on most slides, such as cards, shapes, diagrams, connectors, timelines, tables, charts, icons made from shapes, or comparison panels.
- Do not make a slide with only a title textbox and one paragraph textbox.
- Use charts or data-like visualizations only for facts present in the brief; otherwise use diagrams, process flows, comparisons, or labelled shape-based infographics.
- Vary layouts across the deck: cover, section divider, cards, two-column comparison, process/timeline, metric or chart slide, and conclusion/next steps when relevant.
- Set explicit fills, line colours, font sizes, and spacing rather than relying on the default PowerPoint theme.
- Keep text concise and make the visual hierarchy obvious at a glance.
For a typical 8-12 slide deck, deliberately include several of these visual compositions when they fit the topic:
- a cover with a full-slide background, accent shape, and title hierarchy;
- a cards or infographic slide with 3-5 labelled cards;
- a process slide with 4-6 nodes and connector arrows;
- a hub-and-spoke slide with one central concept, 4-6 surrounding nodes, and connectors;
- a timeline or roadmap with 4-6 milestones;
- a comparison slide with two or more designed panels;
- a matrix, KPI card, or chart slide when supported by facts in the brief;
- a conclusion slide with visual next-step cards.
Implement these with native python-pptx shapes and connectors, not just text boxes. Define reusable helper functions for cards, nodes, connectors, labels, and section headers so the visual system is consistent. Before saving, inspect the slide collection and ensure content slides contain multiple shapes beyond their title.
The script must save the final presentation to /mnt/data/generated_presentation.pptx.
Do not return the Python source as the final deliverable. Execute it and produce the actual PowerPoint file.
If execution fails, explain the failure clearly. Do not retry with alternate scripts unless explicitly instructed.
"""


class OpenAICodeInterpreterError(RuntimeError):
    pass


@dataclass
class GeneratedPresentationResult:
    deck_title: str
    pptx_bytes: bytes
    model: str
    request_id: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    token_cost_usd: float
    code_interpreter_cost_usd: float
    total_cost_usd: float
    container_id: str | None
    openai_file_id: str | None
    uploaded_file_ids: list[str]
    raw_response: dict[str, Any]


def _resolve_model() -> str:
    return (
        getattr(settings, "OPENAI_CODE_INTERPRETER_MODEL", None)
        or os.environ.get("OPENAI_CODE_INTERPRETER_MODEL")
        or "gpt-4o"
    )


def _resolve_api_key() -> str:
    token = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not token:
        raise OpenAICodeInterpreterError("Missing API token: set CHATGPT_API_TOKEN or OPENAI_API_KEY")
    return token


def _resolve_timeout() -> int:
    raw = getattr(settings, "OPENAI_CODE_INTERPRETER_TIMEOUT_SECONDS", None) or os.environ.get(
        "OPENAI_CODE_INTERPRETER_TIMEOUT_SECONDS", "240"
    )
    try:
        return max(30, int(raw))
    except Exception:
        return 240


def _resolve_connect_timeout() -> int:
    raw = getattr(settings, "OPENAI_CODE_INTERPRETER_CONNECT_TIMEOUT_SECONDS", None) or os.environ.get(
        "OPENAI_CODE_INTERPRETER_CONNECT_TIMEOUT_SECONDS", "30"
    )
    try:
        return max(5, int(raw))
    except Exception:
        return 30


def _resolve_max_output_tokens() -> int:
    raw = getattr(settings, "OPENAI_CODE_INTERPRETER_MAX_OUTPUT_TOKENS", None) or os.environ.get(
        "OPENAI_CODE_INTERPRETER_MAX_OUTPUT_TOKENS", "18000"
    )
    try:
        return max(2000, min(int(raw), 30000))
    except Exception:
        return 18000


def _resolve_memory_limit() -> str:
    return (
        getattr(settings, "OPENAI_CODE_INTERPRETER_MEMORY_LIMIT", None)
        or os.environ.get("OPENAI_CODE_INTERPRETER_MEMORY_LIMIT")
        or "4g"
    )


def _resolve_tool_cost() -> float:
    raw = getattr(settings, "OPENAI_CODE_INTERPRETER_TOOL_COST_USD", None)
    if raw is None:
        raw = os.environ.get("OPENAI_CODE_INTERPRETER_TOOL_COST_USD", "0")
    try:
        return max(0.0, float(raw))
    except Exception:
        return 0.0


def _resolve_max_source_files() -> int:
    raw = getattr(settings, "OPENAI_CODE_INTERPRETER_MAX_SOURCE_FILES", None)
    if raw is None:
        raw = os.environ.get("OPENAI_CODE_INTERPRETER_MAX_SOURCE_FILES", "5")
    try:
        return max(0, int(raw))
    except Exception:
        return 5


def _resolve_max_source_file_bytes() -> int:
    raw = getattr(settings, "OPENAI_CODE_INTERPRETER_MAX_SOURCE_FILE_BYTES", None)
    if raw is None:
        raw = os.environ.get("OPENAI_CODE_INTERPRETER_MAX_SOURCE_FILE_BYTES", str(15 * 1024 * 1024))
    try:
        return max(1024, int(raw))
    except Exception:
        return 15 * 1024 * 1024


def _resolve_max_pptx_bytes() -> int:
    raw = getattr(settings, "OPENAI_CODE_INTERPRETER_MAX_PPTX_BYTES", None)
    if raw is None:
        raw = os.environ.get("OPENAI_CODE_INTERPRETER_MAX_PPTX_BYTES", str(25 * 1024 * 1024))
    try:
        return max(1024, int(raw))
    except Exception:
        return 25 * 1024 * 1024


def _coerce_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "to_dict_recursive", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                data = fn()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    try:
        return json.loads(json.dumps(obj, default=lambda x: getattr(x, "__dict__", str(x))))
    except Exception:
        return {}


def _iter_dicts(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item)


def _extract_container_artifacts(response_dict: dict[str, Any]) -> tuple[str | None, str | None]:
    container_id = None
    file_id = None
    for item in _iter_dicts(response_dict):
        item_type = str(item.get("type") or "").strip()
        container_id = container_id or item.get("container_id") or item.get("container")
        if item_type == "container_file_citation":
            file_id = file_id or item.get("file_id") or item.get("container_file_id")
        elif item_type == "code_interpreter_call":
            for output_key in ("results", "outputs"):
                outputs = item.get(output_key)
                if not isinstance(outputs, list):
                    continue
                for output in outputs:
                    if not isinstance(output, dict):
                        continue
                    files = output.get("files")
                    if isinstance(files, list):
                        for generated_file in files:
                            if not isinstance(generated_file, dict):
                                continue
                            filename = str(
                                generated_file.get("filename")
                                or generated_file.get("name")
                                or ""
                            ).lower()
                            if filename.endswith(".pptx") or not file_id:
                                file_id = file_id or generated_file.get("file_id") or generated_file.get("id")

        filename = str(item.get("filename") or item.get("name") or "").lower()
        if filename.endswith(".pptx"):
            file_id = file_id or item.get("file_id") or item.get("id")
    return str(container_id) if container_id else None, str(file_id) if file_id else None


def _extract_text_output(response_dict: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in _iter_dicts(response_dict):
        if str(item.get("type") or "") == "output_text":
            t = str(item.get("text") or "").strip()
            if t:
                texts.append(t)
    return "\n".join(texts).strip()


def _http_json(
    method: str,
    path: str,
    *,
    token: str,
    timeout: int,
    stream: bool = False,
    json_body: dict[str, Any] | None = None,
    data: Any = None,
    files: Any = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    url = f"{OPENAI_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    if extra_headers:
        headers.update(extra_headers)
    if json_body is not None:
        headers.setdefault("Content-Type", "application/json")
    # A hosted Code Interpreter request can take several minutes, but the
    # initial TCP connection should not be held hostage by a stale socket.
    request_timeout = (_resolve_connect_timeout(), timeout)
    resp = None
    last_connection_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                timeout=request_timeout,
                stream=stream,
                json=json_body,
                data=data,
                files=files,
            )
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_connection_error = exc
            if attempt >= 2:
                raise OpenAICodeInterpreterError(
                    f"Could not connect to OpenAI at {OPENAI_API_BASE} after 3 attempts: {exc}"
                ) from exc
            time.sleep(1.0 * (attempt + 1))
    if resp is None:
        raise OpenAICodeInterpreterError(
            f"Could not connect to OpenAI at {OPENAI_API_BASE}: {last_connection_error}"
        )
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        detail = resp.text[:4000]
        try:
            payload = resp.json()
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                err = payload["error"]
                message = str(err.get("message") or detail)
                raise OpenAICodeInterpreterError(f"OpenAI request failed: {message}") from exc
        except OpenAICodeInterpreterError:
            raise
        except Exception:
            pass
        raise OpenAICodeInterpreterError(f"OpenAI request failed: {exc} – {detail}") from exc
    return resp


def _list_container_files(container_id: str, *, token: str, timeout: int) -> list[dict[str, Any]]:
    resp = _http_json("GET", f"/containers/{container_id}/files", token=token, timeout=timeout)
    data = resp.json()
    if isinstance(data, dict):
        files = data.get("data") or data.get("files") or []
        if isinstance(files, list):
            return [f for f in files if isinstance(f, dict)]
    return []


def _download_container_file(container_id: str, file_id: str, *, token: str, timeout: int) -> bytes:
    resp = _http_json(
        "GET",
        f"/containers/{container_id}/files/{file_id}/content",
        token=token,
        timeout=timeout,
        stream=True,
    )
    body = resp.content
    if not body:
        raise OpenAICodeInterpreterError("OpenAI returned an empty file for the generated presentation")
    return body


def validate_pptx_bytes(data: bytes) -> None:
    if not data:
        raise OpenAICodeInterpreterError("Generated PowerPoint file is empty")
    if len(data) > _resolve_max_pptx_bytes():
        raise OpenAICodeInterpreterError("Generated PowerPoint file exceeds the configured size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                raise OpenAICodeInterpreterError("Generated file is not a valid PowerPoint package")
    except OpenAICodeInterpreterError:
        raise
    except Exception as exc:
        raise OpenAICodeInterpreterError(f"Generated file is not a valid PPTX: {exc}") from exc


def validate_visual_design_bytes(data: bytes) -> None:
    """Reject an apparently valid but text-only deck before download."""
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            slide_names = sorted(
                name
                for name in zf.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
            if not slide_names:
                raise OpenAICodeInterpreterError("Generated PowerPoint contains no slides")

            rich_slides = 0
            coloured_slides = 0
            for name in slide_names:
                xml = zf.read(name).decode("utf-8", errors="ignore")
                visual_elements = (
                    xml.count("<p:sp>")
                    + xml.count("<p:graphicFrame>")
                    + xml.count("<p:pic>")
                    + xml.count("<p:cxnSp>")
                )
                if visual_elements >= 3:
                    rich_slides += 1
                if xml.count("<a:solidFill>") + xml.count("<a:gradFill>") >= 2:
                    coloured_slides += 1

            minimum_rich = max(2, (len(slide_names) + 1) // 2)
            minimum_coloured = max(2, (len(slide_names) + 2) // 3)
            if rich_slides < minimum_rich or coloured_slides < minimum_coloured:
                raise OpenAICodeInterpreterError(
                    "Generated PowerPoint passed file validation but does not contain enough visual design "
                    f"(rich slides {rich_slides}/{len(slide_names)}, coloured slides {coloured_slides}/{len(slide_names)})"
                )
    except OpenAICodeInterpreterError:
        raise
    except Exception as exc:
        raise OpenAICodeInterpreterError(f"Could not inspect PowerPoint visual design: {exc}") from exc


def _compute_token_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    try:
        row = Pricing.query.filter(Pricing.model_id == model_id).order_by(Pricing.effective_from.desc()).first()
        if not row:
            return 0.0
        return (prompt_tokens / 1000.0) * float(row.input_per_1k) + (completion_tokens / 1000.0) * float(row.output_per_1k)
    except Exception:
        return 0.0


def _log_usage(*, model: str, route: str, deck_id: str | None, user_id: str | None, request_id: str, prompt_tokens: int, completion_tokens: int, total_tokens: int, total_cost_usd: float) -> None:
    try:
        rec = LLMCall(
            deck_id=deck_id,
            export_id=deck_id,
            user_id=user_id,
            route=route,
            model=model,
            request_id=request_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=total_cost_usd,
            created_at=datetime.utcnow(),
        )
        db.session.add(rec)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _build_user_prompt(*, title: str, subtitle: str | None, presenter: str | None, date: str | None, theme_name: str, input_text: str, num_slides: int, source_file_names: list[str]) -> str:
    requested_count = num_slides if num_slides and num_slides > 0 else 10
    bits = [
        f"Presentation title: {title or 'Generate an appropriate presentation title'}",
        f"Requested slide count: {requested_count}",
        f"Theme preference: {theme_name or 'Use a professional modern theme'}",
        f"Subtitle: {subtitle or 'None provided'}",
        f"Presenter: {presenter or 'None provided'}",
        f"Date: {date or 'None provided'}",
        "Primary user brief:",
        input_text.strip() or "Create a professional presentation based on the title.",
        "Requirements:",
        "- Use python-pptx and save the final file to /mnt/data/generated_presentation.pptx.",
        "- Respect the requested slide count as closely as possible.",
        "- Use native editable PowerPoint elements wherever practical.",
        "- Avoid slides with overflowing text or objects outside the slide bounds.",
        "- Use varied slide layouts and apply professional spacing, hierarchy, and visual polish.",
        "- Use charts, tables, timelines, process diagrams, comparisons, or infographic elements where they improve understanding.",
        "- Do not invent facts or citations.",
        "- Do not create a deck where most slides consist only of one title and one body textbox.",
        "- For a 10-slide deck, include at minimum: one card/infographic slide, one process-flow slide, one hub-and-spoke or architecture slide, one timeline/roadmap slide, one comparison or matrix slide, and one visual conclusion slide.",
        "- For fewer slides, combine these compositions rather than omitting visual structure.",
        "- After building the deck, run a self-check in the script: each content slide must contain at least 3 non-title visual shapes and the deck must use explicit fills or accent colours on most slides. Add shapes before saving if the check fails.",
    ]
    if source_file_names:
        bits.extend(
            [
                "Source files have been provided in the sandbox. Read them and preserve their terminology and factual content.",
                f"Available source files: {', '.join(source_file_names)}",
            ]
        )
    return "\n".join(bits).strip()


def _normalize_source_paths(source_files: Any) -> list[Path]:
    out: list[Path] = []
    if not isinstance(source_files, list):
        return out
    max_files = _resolve_max_source_files()
    max_bytes = _resolve_max_source_file_bytes()
    for raw in source_files[:max_files]:
        value = raw.get("path") if isinstance(raw, dict) else raw
        path_str = str(value or "").strip()
        if not path_str:
            continue
        path = Path(path_str).expanduser()
        if not path.exists() or not path.is_file():
            raise OpenAICodeInterpreterError(f"Source file not found: {path}")
        if path.suffix.lower() not in ALLOWED_SOURCE_EXTENSIONS:
            raise OpenAICodeInterpreterError(f"Unsupported source file type: {path.name}")
        if path.stat().st_size > max_bytes:
            raise OpenAICodeInterpreterError(f"Source file exceeds size limit: {path.name}")
        out.append(path)
    return out


def _upload_source_files(*, token: str, timeout: int, paths: list[Path]) -> list[str]:
    uploaded: list[str] = []
    for path in paths:
        try:
            with path.open("rb") as f:
                resp = _http_json(
                    "POST",
                    "/files",
                    token=token,
                    timeout=timeout,
                    data={"purpose": "assistants"},
                    files={"file": (path.name, f)},
                )
                payload = resp.json()
        except Exception as exc:
            raise OpenAICodeInterpreterError(f"Failed to upload source file '{path.name}': {exc}") from exc
        file_id = payload.get("id") if isinstance(payload, dict) else None
        if not file_id:
            raise OpenAICodeInterpreterError(f"OpenAI did not return a file id for '{path.name}'")
        uploaded.append(str(file_id))
    return uploaded


def generate_presentation_with_code_interpreter(
    *,
    title: str,
    subtitle: str | None,
    presenter: str | None,
    date: str | None,
    theme_name: str,
    input_text: str,
    num_slides: int,
    deck_id: str | None = None,
    user_id: str | None = None,
    source_files: Any = None,
) -> GeneratedPresentationResult:
    token = _resolve_api_key()
    model = _resolve_model()
    timeout = _resolve_timeout()
    memory_limit = _resolve_memory_limit()
    max_output_tokens = _resolve_max_output_tokens()
    tool_cost = _resolve_tool_cost()

    source_paths = _normalize_source_paths(source_files)
    uploaded_file_ids = _upload_source_files(token=token, timeout=timeout, paths=source_paths) if source_paths else []

    container_cfg: dict[str, Any] = {"type": "auto", "memory_limit": memory_limit}
    if uploaded_file_ids:
        container_cfg["file_ids"] = uploaded_file_ids

    user_prompt = _build_user_prompt(
        title=title,
        subtitle=subtitle,
        presenter=presenter,
        date=date,
        theme_name=theme_name,
        input_text=input_text,
        num_slides=num_slides,
        source_file_names=[p.name for p in source_paths],
    )

    try:
        response = _http_json(
            "POST",
            "/responses",
            token=token,
            timeout=timeout,
            json_body={
                "model": model,
                "tools": [{"type": "code_interpreter", "container": container_cfg}],
                "instructions": GENERATION_INSTRUCTIONS,
                "input": user_prompt,
                "max_output_tokens": max_output_tokens,
            },
        )
        response_dict = response.json()
    except Exception as exc:
        raise OpenAICodeInterpreterError(f"OpenAI Responses API error: {exc}") from exc

    request_id = str(response_dict.get("id") or "")
    usage = response_dict.get("usage") or {}
    prompt_tokens = int((usage.get("input_tokens") if isinstance(usage, dict) else 0) or (usage.get("prompt_tokens") if isinstance(usage, dict) else 0) or 0)
    completion_tokens = int((usage.get("output_tokens") if isinstance(usage, dict) else 0) or (usage.get("completion_tokens") if isinstance(usage, dict) else 0) or 0)
    total_tokens = int((usage.get("total_tokens") if isinstance(usage, dict) else 0) or (prompt_tokens + completion_tokens))

    container_id, file_id = _extract_container_artifacts(response_dict)
    if not container_id:
        raise OpenAICodeInterpreterError(
            _extract_text_output(response_dict) or "Code Interpreter did not return a container id or generated file reference"
        )
    if not file_id:
        files = _list_container_files(container_id, token=token, timeout=timeout)
        pptx_candidates = [
            f for f in files
            if str(f.get("filename") or f.get("name") or "").lower().endswith(".pptx")
        ]
        chosen = pptx_candidates[0] if pptx_candidates else (files[0] if files else None)
        if not chosen:
            raise OpenAICodeInterpreterError("Code Interpreter completed but no generated presentation file was found")
        file_id = str(chosen.get("id") or chosen.get("file_id") or "")
    if not file_id:
        raise OpenAICodeInterpreterError("Generated presentation file id could not be determined")

    body = _download_container_file(container_id, file_id, token=token, timeout=timeout)
    validate_pptx_bytes(body)
    validate_visual_design_bytes(body)

    token_cost = _compute_token_cost(model, prompt_tokens, completion_tokens)
    total_cost = token_cost + tool_cost
    _log_usage(
        model=model,
        route="generate_code_interpreter",
        deck_id=deck_id,
        user_id=user_id,
        request_id=request_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
    )

    deck_title = (title or "Generated Presentation").strip() or "Generated Presentation"
    return GeneratedPresentationResult(
        deck_title=deck_title,
        pptx_bytes=body,
        model=model,
        request_id=request_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        token_cost_usd=token_cost,
        code_interpreter_cost_usd=tool_cost,
        total_cost_usd=total_cost,
        container_id=container_id,
        openai_file_id=file_id,
        uploaded_file_ids=uploaded_file_ids,
        raw_response=response_dict,
    )
