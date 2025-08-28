from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.core.config import settings


def _themes_palette() -> dict[str, dict[str, str]]:
    """Central palette map for themes used in prompts and preview."""
    return {
        "Ocean Breeze": {
            "primary": "#0077B6",
            "secondary": "#48CAE4",
            "accent": "#90E0EF",
            "background": "#E9F5FF",  # softer, more aesthetic
            "text": "#0F2233",
        },
        "Sunset Horizon": {
            "primary": "#FF6B6B",
            "secondary": "#4ECDC4",
            "accent": "#FFE66D",
            "background": "#FFF7EF",  # warm neutral
            "text": "#1A2E35",
        },
        "Midnight Surf": {
            "primary": "#0F3460",
            "secondary": "#16213E",
            "accent": "#533483",
            "background": "#0B132B",  # deep navy
            "text": "#FFFFFF",
        },
        "Tropical Wave": {
            "primary": "#2A9D8F",
            "secondary": "#E9C46A",
            "accent": "#F4A261",
            "background": "#FAFEFB",  # airy white-green
            "text": "#264653",
        },
        "Arctic Winds": {
            "primary": "#A8DADC",
            "secondary": "#457B9D",
            "accent": "#E63946",
            "background": "#ECF8F6",  # cooler mint
            "text": "#1D3557",
        },
    }


def build_prompt(
    *,
    title: str,
    subtitle: str | None,
    presenter: str | None,
    date: str | None,
    theme_name: str,
    input_text: str,
    num_slides: int,
) -> list[dict[str, str]]:
    """Return ChatGPT messages for strict JSON slide generation (no fallback)."""
    theme = _themes_palette().get(theme_name) or _themes_palette()["Ocean Breeze"]

    system = (
        "You are a senior presentation strategist and slide writer. Output ONLY valid JSON (no comments, no prose). "
        "Return a JSON array of slide objects. Do not wrap or prefix with Markdown fences. "
        "Follow the schema strictly and content rules below."
    )

    user_payload: Dict[str, Any] = {
        "task": "Generate a PowerPoint slide deck content strictly as JSON.",
        "constraints": {
            "output_format": "json_array_only",
            "no_fallback": True,
            "max_tokens": 1800,
            "bullets_word_count_range": [4, 14],
        },
        "presentation": {
            "title": title or "Untitled Presentation",
            "subtitle": subtitle or "",
            "presenter": presenter or "",
            "date": date or "",
            "theme": {
                "name": theme_name,
                "palette": theme,
            },
            "input_text": input_text or "",
            "requested_slide_count": int(max(3, num_slides)),
        },
        "structure_rules": {
            "adaptive_allocation": "Always allocate 3 core slides (Title, Agenda, Thank You). Use remaining (N - 3) for modules in priority order; avoid repetition; maintain narrative flow.",
            "core_slides": [
                "Title Slide: includes title, optional subtitle, presenter, date",
                "Agenda Slide: single-column bullets of main sections/topics (>=6 items if possible)",
                "Thank You / Q&A Slide: closing message, optional contact/CTA",
            ],
            "modules_priority": [
                "Timeline / Background",
                "Topic Overview",
                "Double Column Bullet (2–3 variations max)",
                "Tabular Comparison",
                "Infographic / Visual Summary",
                "Call to Action / Key Takeaways",
            ],
        },
        "schema": {
            "slide_object": {
                "type": "object",
                "required": ["type", "title"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "title",
                            "agenda",
                            "bullets",
                            "double_column",
                            "table",
                            "timeline",
                            "overview",
                            "infographic",
                            "key_takeaways",
                            "thank_you",
                        ],
                    },
                    "title": {"type": "string", "minLength": 1},
                    "subtitle": {"type": "string"},
                    "presenter": {"type": "string"},
                    "date": {"type": "string"},
                    "content": {
                        "type": "object",
                        "properties": {
                            "bullets": {"type": "array", "items": {"type": "string"}},
                            "left_points": {"type": "array", "items": {"type": "string"}},
                            "right_points": {"type": "array", "items": {"type": "string"}},
                            "table": {
                                "type": "object",
                                "properties": {
                                    "left": {
                                        "type": "object",
                                        "properties": {
                                            "heading": {"type": "string"},
                                            "points": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["heading", "points"],
                                    },
                                    "right": {
                                        "type": "object",
                                        "properties": {
                                            "heading": {"type": "string"},
                                            "points": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["heading", "points"],
                                    },
                                },
                                "required": ["left", "right"],
                            },
                            "timeline": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                            "visual_hint": {"type": "string"},
                            "columns": {"type": "object"}
                        },
                    },
                },
            }
        },
        "instructions": [
            "Generate exactly N slides as per requested_slide_count.",
            "Core slides must be positions 1 (title), 2 (agenda), and last (thank_you).",
            "Title must be concise, compelling, and derived from the input_text. Do NOT repeat the input verbatim; rewrite as a strong presentation title.",
            "Agenda must be derived from the content and reflect the main sections/topics you will cover. Do NOT use generic labels like 'Timeline / Background', 'Double Column Bullet', or names of slide types.",
            "Bullets must be non-duplicative, 4–14 words, and informative.",
            "If N is small, prefer overview or infographic over multiple bullet variations.",
            "Double column slides should use left_points and right_points; 6+ items per column when possible.",
            "Tabular Comparison uses content.table with left/right headings and >=6 points per column when possible.",
            "Infographic uses a concise visual_hint plus a few bullets if helpful.",
            "Key Takeaways: succinct bullets summarizing actionable insights.",
            "Thank You: include optional contact info or CTA in bullets.",
            "Return ONLY a JSON array of slides; no markdown, no narrative.",
            "Avoid placeholder-like phrasing (e.g., 'Click to add ...') and avoid slide-type names as content.",
        ],
    }

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _json_dump(user_payload)},
        {
            "role": "assistant",
            "content": _json_dump([
                {
                    "type": "title",
                    "title": "Example",
                    "subtitle": "",
                    "presenter": "",
                    "date": "",
                    "content": {},
                },
                {
                    "type": "agenda",
                    "title": "Agenda",
                    "content": {"bullets": ["Section 1", "Section 2", "Section 3", "Section 4", "Section 5", "Section 6"]},
                },
                {
                    "type": "thank_you",
                    "title": "Thank You",
                    "content": {"bullets": ["Questions and discussion welcome", "Contact: example@example.com"]},
                },
            ]),
        },
    ]


def create_deck_slides(
    *,
    title: str,
    subtitle: str | None,
    presenter: str | None,
    date: str | None,
    theme_name: str,
    input_text: str,
    num_slides: int,
) -> tuple[List[Dict[str, Any]], list[str]]:
    """Call ChatGPT 3.5 Turbo and return (slides_json, errors). No fallback."""
    # Lazy import to keep startup light
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [], [f"OpenAI SDK not available: {exc}"]

    api_key = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not api_key:
        return [], ["CHATGPT_API_TOKEN / OPENAI_API_KEY is missing"]

    client = OpenAI(api_key=api_key)
    messages = build_prompt(
        title=title,
        subtitle=subtitle,
        presenter=presenter,
        date=date,
        theme_name=theme_name,
        input_text=input_text,
        num_slides=num_slides,
    )

    try:
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=messages,  # type: ignore[arg-type]
            temperature=0.3,
            max_tokens=1800,
        )
        content = (resp.choices[0].message.content or "").strip()
        # Log short snippet for diagnostics
        try:
            print("AI raw length=", len(content), "snippet=", content[:400].replace("\n", " "))
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        return [], [f"ChatGPT API error: {exc}"]

    slides, err = _parse_json_array(content)
    if err:
        return [], [err]
    if not isinstance(slides, list):
        return [], ["AI did not return a JSON array"]
    return slides, []


# ----------------------
# Utilities
# ----------------------

def _json_dump(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _parse_json_array(raw: str) -> Tuple[List[Dict[str, Any]] | None, str | None]:
    import json
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, list):
        return None, "Top-level JSON must be an array"
    # Shallow sanitize items to ensure dicts
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            out.append(item)
        else:
            out.append({"type": "bullets", "title": f"Slide {i+1}", "content": {"bullets": [str(item)]}})
    return out, None

