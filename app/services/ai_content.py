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
        "Follow the schema strictly and the content rules below. Prioritize topic-aware, specific language over generic phrasing."
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
            "Default: generate N slides as per requested_slide_count. If the agenda items cannot be covered coherently in N, you MAY add up to 5 additional slides (N+1..N+5). Do not add fluff; only add slides that improve clarity and alignment to the agenda.",
            "Core slides must be positions 1 (title), 2 (agenda), and last (thank_you).",
            "Title must be concise, compelling, and derived from the input_text. Do NOT repeat the input verbatim; rewrite as a strong presentation title. NEVER use generic titles like 'Generated Presentation', 'Presentation', or 'Overview'.",
            "Agenda: YOU must create the agenda (outline) from the topic/input_text; do NOT ask the user for an outline. It must reflect the main sections/topics you will cover and avoid generic labels like slide-type names.",
            "Bullets must be non-duplicative, 4–14 words, informative, and TOPIC-SPECIFIC. Avoid generic boilerplate such as 'leveraging X to improve KPIs'.",
            "COHERENCE & ORDER: All slides after the agenda MUST map to the agenda bullets in sequence. Use contiguous blocks per agenda item (you may use 1+ slides per item, but do not reorder).",
            "TITLE PARAPHRASING: Each mapped slide's TITLE must closely paraphrase its agenda bullet (share key terms); do not use generic labels.",
            "CONTENT ALIGNMENT: Each mapped slide's CONTENT (bullets/points/table cells) must elaborate the mapped agenda bullet and share key terms.",
            "Do not produce slides that are not linked to an agenda item.",
            "Ensure the Thank You slide is always the final slide, even if you add extra slides beyond N.",
            "If N is small, prefer overview or infographic over multiple bullet variations.",
            "Double column slides should use left_points and right_points; 6+ items per column when possible.",
            "Tabular Comparison must be CONTEXTUAL to the topic in input_text. Headings should reflect the actual comparison dimension (e.g., 'Pre-COVID' vs 'During/After COVID', 'Developed Markets' vs 'Emerging Markets'). NEVER use 'Traditional vs AI-Driven' unless the topic is explicitly about AI approaches.",
            "For any 'Key Concepts' or concept-focused slide, provide 6–8 distinct topic-derived terms with concise, non-repetitive definitions. Derive terms from the topic and agenda; avoid umbrella words (e.g., 'Overview', 'Concepts'). Do NOT echo the slide title in the bullet text. Do NOT repeat the same template phrase across bullets.",
            "HARD BANS: Do NOT output placeholder content such as headings 'Indicator A', 'Indicator B', or cell values like 'Point 1', 'Point 2', etc. Replace all such placeholders with specific, topic-derived headings and items.",
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
            temperature=0.2,
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
    # Ensure a strong, non-generic Title slide at index 0 using AI refinement if needed
    try:
        slides = _ensure_title_slide(
            slides,
            client=client,
            requested_title=title or "",
            subtitle=subtitle or "",
            presenter=presenter or "",
            date=date or "",
            input_text=input_text or "",
        )
    except Exception:
        # Non-fatal; continue with raw slides
        pass
    # Post-pass: rewrite generic table headers contextually with AI and sync titles
    try:
        slides = _ai_rewrite_table_headers(client, slides)
    except Exception as _:
        pass
    # Post-pass: AI harmonization of titles/bullets to agenda and topic
    try:
        slides = _ai_harmonize_to_agenda(
            client=client,
            system_msg=messages[0]["content"],
            title=title,
            input_text=input_text,
            slides=slides,
        )
    except Exception:
        pass
    # Post-pass: harmonize titles/content to agenda; remove template phrases
    try:
        slides = _harmonize_titles_to_agenda(slides)
    except Exception:
        pass
    # Post-check quality and coherency; perform one corrective retry if needed
    issues, agenda = _quality_issues(slides)
    if issues:
        try:
            fixed = _retry_with_agenda(
                client=client,
                system_msg=messages[0]["content"],  # same system policy
                title=title,
                subtitle=subtitle or "",
                presenter=presenter or "",
                date=date or "",
                theme_name=theme_name,
                input_text=input_text,
                num_slides=num_slides,
                agenda_bullets=agenda,
                issues=issues,
            )
            if fixed:
                return fixed, []
        except Exception as exc:  # noqa: BLE001
            return slides, [f"Retry warning: {exc}"]
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


def _is_generic_presentation_title(title: str) -> bool:
    """Detect generic or placeholder deck titles that shouldn't ship."""
    t = (title or "").strip().lower()
    return t in {
        "generated presentation",
        "presentation",
        "overview",
        "untitled presentation",
        "presentation title",
        "example",
        "agenda",
    }


def _ai_generate_deck_title(*, client: Any, requested_title: str, input_text: str) -> str:
    """Ask ChatGPT for a concise, topic-derived, production-ready deck title.

    Returns an empty string on failure so caller can fallback.
    """
    try:
        from openai import OpenAI  # type: ignore  # noqa: F401
    except Exception:
        # If SDK missing, just signal failure
        return ""

    try:
        sys = (
            "You output ONLY a concise deck title text. No quotes, no markdown, no extra text."
        )
        user_payload = {
            "task": "Generate a compelling, topic-specific presentation title.",
            "requested_title": (requested_title or "").strip(),
            "input_text": (input_text or "").strip(),
            "rules": [
                "12 words max, 80 characters max.",
                "Do NOT repeat input verbatim; paraphrase into a proper title.",
                "Avoid generic titles like 'Generated Presentation', 'Presentation', 'Overview'.",
                "Use key terms from the topic where appropriate.",
            ],
        }
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": _json_dump(user_payload)},
            ],
            temperature=0.2,
            max_tokens=64,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Clean up any accidental quoting/whitespace
        s = raw.strip().strip("\"' ")
        # Clip to safety bound
        return s[:80]
    except Exception:
        return ""


def _ensure_title_slide(
    slides: List[Dict[str, Any]],
    *,
    client: Any | None,
    requested_title: str,
    subtitle: str,
    presenter: str,
    date: str,
    input_text: str,
) -> List[Dict[str, Any]]:
    """Ensure slides[0] is a title slide with a non-generic title.

    - If a title slide exists but the title is generic/empty, replace its title.
    - If no title slide exists, insert one at index 0 with a generated title.
    """
    slides_norm = list(slides or [])

    # Find first title slide if any
    def _ctype(sl: Dict[str, Any]) -> str:
        return (sl.get("type") or "").strip().lower()

    t_idx = next((i for i, s in enumerate(slides_norm) if _ctype(s) == "title"), None)
    current_title = ""
    if t_idx is not None:
        current_title = (slides_norm[t_idx].get("title") or "").strip()

    # Decide final title
    final_title = (requested_title or "").strip()
    if not final_title or _is_generic_presentation_title(final_title):
        if client is not None:
            ai_title = _ai_generate_deck_title(client=client, requested_title=final_title, input_text=input_text)
            if ai_title and not _is_generic_presentation_title(ai_title):
                final_title = ai_title
    # As a last resort, try existing title from slides
    if (not final_title or _is_generic_presentation_title(final_title)) and current_title:
        if not _is_generic_presentation_title(current_title):
            final_title = current_title
    # If still empty, heuristically derive from input_text
    if not final_title:
        txt = (input_text or "").strip()
        import re
        sent = re.split(r"(?<=[.!?])\s+", txt)[:1]
        guess = (sent[0] if sent else txt)[:80]
        final_title = guess.strip() or "Presentation"

    # Build/update the title slide
    title_slide = {
        "type": "title",
        "title": final_title.strip(),
        "subtitle": subtitle or presenter or "",
        "presenter": presenter or "",
        "date": date or "",
        "content": {},
    }

    if t_idx is None:
        slides_norm.insert(0, title_slide)
    else:
        # Update and move to index 0 if needed
        slides_norm[t_idx].update(title_slide)
        if t_idx != 0:
            s = slides_norm.pop(t_idx)
            slides_norm.insert(0, s)

    return slides_norm


def derive_topic_bullets(*, deck_title: str, topic: str, input_text: str | None = None, n: int = 5) -> List[str]:
    """Ask ChatGPT to derive concise, topic-specific bullets for a topic title.
    Returns up to n bullets; on failure, returns an empty list.
    """
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return []

    api_key = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not api_key:
        return []
    client = OpenAI(api_key=api_key)
    payload = {
        "task": "Derive concise, presentation-ready bullets for a topic.",
        "deck_title": deck_title,
        "topic": topic,
        "context": (input_text or ""),
        "rules": [
            "Return ONLY a JSON array of strings. No prose, no markdown.",
            "Each bullet should be 5–14 words, informative, and topic-specific.",
            "Avoid template phrases and placeholders (e.g., 'Definition and scope', 'Point 1').",
            "Avoid repeating the topic title text verbatim.",
            "No numbering or trailing punctuation-only entries.",
        ],
        "max_items": int(max(3, min(6, n))),
    }
    try:
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You output ONLY a JSON array of strings; no extra text."},
                {"role": "user", "content": _json_dump(payload)},
            ],
            temperature=0.2,
            max_tokens=300,
        )
        import json
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        if isinstance(data, list):
            out = []
            for x in data:
                s = str(x or "").strip()
                if s:
                    out.append(s)
                if len(out) >= payload["max_items"]:
                    break
            return out
    except Exception:
        return []
    return []


def _quality_issues(slides: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Detect placeholder/generic content and agenda misalignment (order + content).
    Returns (issues, agenda_bullets).
    """
    issues: List[str] = []
    agenda: List[str] = []
    deck_title = ""
    # capture agenda bullets
    invalid_agenda = False
    for s in slides:
        if s.get("type") == "title" and not deck_title:
            deck_title = (s.get("title") or "").strip()
        if s.get("type") == "agenda":
            raw = [b.strip() for b in s.get("content", {}).get("bullets", []) if isinstance(b, str)]
            # Remove placeholder-like agenda bullets
            bad_agenda = {"tbd", "to be refined", "to be defined", "(to be refined)", "placeholder", "section", "section n"}
            agenda = [b for b in raw if b and b.lower() not in bad_agenda]
            if (len(agenda) < 5) or (len(agenda) != len(raw)):
                invalid_agenda = True
            break
    # simple helpers
    def _has_placeholder_points(points: List[str]) -> bool:
        import re
        for p in points:
            if re.fullmatch(r"(?i)point\s*\d+", p.strip()):
                return True
        return False

    def _word_overlap(a: str, b: str) -> int:
        import re
        stop = {"the","of","and","to","a","in","on","for","vs","vs.","an"}
        aw = [w.lower() for w in re.findall(r"[a-zA-Z]+", a) if w.lower() not in stop]
        bw = [w.lower() for w in re.findall(r"[a-zA-Z]+", b) if w.lower() not in stop]
        return len(set(aw) & set(bw))

    # Extract topic keywords from deck title and any agenda text
    import re
    corpus_text = f"{deck_title} {' '.join(agenda)}"
    keywords = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", corpus_text)]
    keywords = list({w for w in keywords if w not in {"concepts","overview","agenda","thank","you","traditional","driven"}})

    matched_agenda_indices: List[int] = []
    # scan slides
    for idx, s in enumerate(slides):
        stype = s.get("type")
        title = (s.get("title") or "").strip()
        # Normalize content to a dict; the AI may sometimes emit a list/null
        _raw = s.get("content")
        content = _raw if isinstance(_raw, dict) else {}
        # Table placeholders
        if stype == "table":
            tbl = content.get("table") or {}
            if not isinstance(tbl, dict):
                tbl = {}
            left = (tbl.get("left") or {}) if isinstance(tbl.get("left"), dict) else {}
            right = (tbl.get("right") or {}) if isinstance(tbl.get("right"), dict) else {}
            lhead = (left.get("heading") or "").strip().lower()
            rhead = (right.get("heading") or "").strip().lower()
            lpts = left.get("points") or []
            rpts = right.get("points") or []
            if lhead in {"indicator a","a"} or rhead in {"indicator b","b"} or _has_placeholder_points(lpts) or _has_placeholder_points(rpts):
                issues.append(f"Slide {idx+1}: table placeholders detected")
        # Generic template phrasing detection
        bullets = content.get("bullets") or []
        templ_phrases = [
            "brief explanation to frame relevance",
            "with measurable kpis and owners",
            "key concepts — leveraging",
            "(to be refined)",
            "lorem ipsum",
            "to be refined",
            "placeholder",
            "generic outline",
        ]
        if any(any(p in (b or "").lower() for p in templ_phrases) for b in bullets):
            issues.append(f"Slide {idx+1}: template-like bullet phrasing detected")
        # Flag generic concept slide titles
        if (s.get("type") in {"bullets","overview"}) and title.lower() in {"key concepts","concepts","overview"}:
            issues.append(f"Slide {idx+1}: generic slide title '{title}'")
        # Flag generic comparison title
        if title.strip().lower() in {"a vs b","a versus b","option a vs option b"}:
            issues.append(f"Slide {idx+1}: generic comparison title detected")
        # Flag generic presentation title
        if stype == "title" and title.lower() in {"generated presentation","presentation","overview","untitled presentation"}:
            issues.append(f"Slide {idx+1}: generic presentation title '{title}' - must derive from topic")
        # Agenda alignment check (skip first two and last)
        if agenda and idx > 1 and idx < len(slides) - 1 and stype not in {"title","agenda","thank_you"}:
            # pick best matching agenda bullet by title overlap
            overlaps = [(_word_overlap(title, a), i) for i, a in enumerate(agenda)]
            best_overlap, best_i = max(overlaps, default=(0, -1))
            if best_overlap <= 1:  # require more than 1 shared term to be safe
                issues.append(f"Slide {idx+1}: title not aligned with agenda")
            else:
                matched_agenda_indices.append(best_i)
                # Content alignment: some bullet/point text should share words with matched agenda bullet
                body_chunks: List[str] = []
                if isinstance(content.get("bullets"), list):
                    body_chunks += [str(x) for x in content.get("bullets")]
                if isinstance(content.get("left_points"), list):
                    body_chunks += [str(x) for x in content.get("left_points")]
                if isinstance(content.get("right_points"), list):
                    body_chunks += [str(x) for x in content.get("right_points")]
                tbl = content.get("table") or {}
                body_chunks += (tbl.get("left", {}) or {}).get("points", []) or []
                body_chunks += (tbl.get("right", {}) or {}).get("points", []) or []
                agenda_text = agenda[best_i]
                if body_chunks:
                    best_body_overlap = max((_word_overlap(agenda_text, b) for b in body_chunks), default=0)
                    if best_body_overlap == 0:
                        issues.append(f"Slide {idx+1}: content not aligned with mapped agenda item '{agenda_text}'")
                # Topic keyword alignment: at least one chunk should include a topic keyword
                if body_chunks and keywords:
                    def _has_kw(line: str) -> bool:
                        low = (line or "").lower()
                        return any(k in low for k in keywords[:12])
                    if not any(_has_kw(b) for b in body_chunks):
                        issues.append(f"Slide {idx+1}: content lacks topic-specific terminology")

    # Order monotonicity check: matched agenda indices should be non-decreasing
    if matched_agenda_indices and any(matched_agenda_indices[i] < matched_agenda_indices[i-1] for i in range(1, len(matched_agenda_indices))):
        issues.append("Slide order does not follow agenda sequence")

    if invalid_agenda:
        issues.append("Agenda contains placeholders or too few items; regenerate agenda from topic and remap slides")
    return issues, agenda


def _harmonize_titles_to_agenda(slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lightweight deterministic cleanup to align titles/content to agenda and drop template phrases."""
    # Extract agenda
    agenda: List[str] = []
    for s in slides:
        if s.get("type") == "agenda":
            agenda = [b.strip() for b in s.get("content", {}).get("bullets", []) if isinstance(b, str)]
            break
    if not agenda:
        return slides

    import re
    def _overlap(a: str, b: str) -> int:
        stop = {"the","of","and","to","a","in","on","for","vs","vs.","an"}
        aw = [w.lower() for w in re.findall(r"[a-zA-Z]+", a) if w.lower() not in stop]
        bw = [w.lower() for w in re.findall(r"[a-zA-Z]+", b) if w.lower() not in stop]
        return len(set(aw) & set(bw))

    generic_titles = {"overview","key concepts","concepts","a vs b","comparison","generated presentation"}
    template_substrings = [
        "brief explanation to frame relevance",
        "with measurable kpis and owners",
        "key concepts — leveraging",
        "(to be refined)",
        "lorem ipsum",
        "placeholder",
        "generic outline",
    ]

    for s in slides:
        stype = s.get("type")
        if stype in {"title","agenda","thank_you"}:
            # Clean agenda bullets of template phrases
            if stype == "agenda":
                bullets = s.get("content", {}).get("bullets", []) or []
                clean = [b for b in bullets if not any(p in (b or "").lower() for p in template_substrings)]
                s.setdefault("content", {})["bullets"] = clean
            continue
        title = (s.get("title") or "").strip()
        # Map to best agenda bullet
        best_ov, best_i = max((( _overlap(title, a), i) for i, a in enumerate(agenda)), default=(0, 0))
        if title.lower() in generic_titles or best_ov == 0:
            s["title"] = agenda[best_i]
        # Remove template-like bullets
        content = s.get("content") or {}
        if isinstance(content.get("bullets"), list):
            content["bullets"] = [
                b for b in content["bullets"]
                if not any(p in (b or "").lower() for p in template_substrings)
            ]
        # Remove 'Point N' style entries from any points arrays
        for key in ("left_points","right_points"):
            if isinstance(content.get(key), list):
                content[key] = [x for x in content[key] if not re.fullmatch(r"(?i)point\s*\d+", str(x).strip())]
        tbl = content.get("table") or {}
        for side in ("left","right"):
            pts = (tbl.get(side) or {}).get("points") or []
            if isinstance(pts, list):
                tbl.setdefault(side, {})["points"] = [x for x in pts if not re.fullmatch(r"(?i)point\s*\d+", str(x).strip())]
        if tbl:
            content["table"] = tbl
        s["content"] = content
    return slides


def _ai_harmonize_to_agenda(
    *,
    client: Any,
    system_msg: str | None = None,
    title: str,
    input_text: str,
    slides: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Use ChatGPT to rewrite any generic slide titles and bullets to align with the AI-generated agenda.
    - Keeps slide count and slide types; only rewrites title and content fields.
    - Ensures each slide maps to nearest agenda bullet in order, paraphrasing titles and aligning bullets.
    - Replaces any placeholder or template phrases with topic-derived specifics.
    """
    import json
    agenda: List[str] = []
    for s in slides:
        if s.get("type") == "agenda":
            agenda = [b for b in (s.get("content", {}).get("bullets", []) or []) if isinstance(b, str)]
            break
    if not agenda:
        return slides

    sys = system_msg or (
        "You are a senior presentation editor. Output ONLY valid JSON; no prose."
    )
    payload = {
        "task": "Harmonize slide titles and content to agenda/order and replace placeholders.",
        "topic_title": title,
        "input_text": input_text,
        "agenda": agenda,
        "rules": [
            "Keep the same number of slides and types; modify only title, subtitle, and content fields.",
            "For slides after agenda, map to the closest agenda bullet in sequence; titles must paraphrase that bullet.",
            "Bullets/points/table cells must elaborate the mapped agenda bullet using topic-specific terms.",
            "Delete or rewrite any placeholder or template phrases (e.g., '(to be refined)', 'brief explanation to frame relevance', 'with measurable KPIs and owners', 'Point 1').",
            "Comparison headers must reflect the true comparison dimension; avoid 'Traditional vs AI-Driven' unless the topic is explicitly AI.",
        ],
        "slides": slides,
        "output": "Return the full JSON array of slides, edited in place.",
    }
    resp = client.chat.completions.create(
        model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": _json_dump(payload)},
        ],
        temperature=0.2,
        max_tokens=1800,
    )
    content = (resp.choices[0].message.content or "").strip()
    edited, err = _parse_json_array(content)
    if not err and isinstance(edited, list):
        return edited
    return slides


def _ai_rewrite_table_headers(client: Any, slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect generic/irrelevant comparison headers and ask ChatGPT to propose topic-appropriate ones.
    Updates slide titles to "A vs B" when both headers exist.
    """
    # Extract topic and agenda for context
    title = ""
    agenda: List[str] = []
    for s in slides:
        if s.get("type") == "title" and not title:
            title = (s.get("title") or "").strip()
        if s.get("type") == "agenda" and not agenda:
            agenda = [b.strip() for b in s.get("content", {}).get("bullets", []) if isinstance(b, str)]

    def _is_generic(h: str) -> bool:
        h = (h or "").strip().lower()
        return h in {"option a", "option b", "a", "b", "left", "right", "traditional", "ai-driven", "ai driven", "ai"}

    changed = False

    def _heuristic_pair(topic: str, agenda: List[str]) -> tuple[str, str] | None:
        t = (topic or "").lower()
        joined = " ".join(agenda).lower()
        text = f"{t} {joined}"
        if any(k in text for k in ["drug", "substance", "opioid", "addiction"]):
            return ("Risk Factors", "Protective Factors")
        if any(k in text for k in ["health", "disease", "care", "clinic", "hospital"]):
            return ("Symptoms", "Interventions")
        if any(k in text for k in ["education", "learning", "school", "students", "curriculum"]):
            return ("Traditional Methods", "Modern Strategies")
        if any(k in text for k in ["market", "finance", "economy", "pricing", "growth"]):
            return ("Challenges", "Opportunities")
        if any(k in text for k in ["policy", "regulation", "compliance", "governance"]):
            return ("Current State", "Proposed Reforms")
        if any(k in text for k in ["covid", "pandemic", "crisis"]):
            return ("Pre-Crisis", "Post-Crisis")
        return ("Option A", "Option B")  # safe, will be improved by later passes if possible
    for s in slides:
        if s.get("type") != "table":
            continue
        content = s.get("content") or {}
        table = content.get("table") or {}
        left = (table.get("left") or {})
        right = (table.get("right") or {})
        lhead = (left.get("heading") or "").strip()
        rhead = (right.get("heading") or "").strip()
        if not (_is_generic(lhead) or _is_generic(rhead)):
            continue
        # Prepare prompt for AI rewrite
        prompt = {
            "task": "Rewrite comparison headers to reflect the true dimension of comparison for the topic.",
            "topic": title,
            "agenda": agenda,
            "existing": {"left": lhead, "right": rhead},
            "examples": [
                {"bad": ["Traditional", "AI-Driven"], "good": ["Manual Reporting", "Real-time Dashboards"]},
                {"bad": ["Option A", "Option B"], "good": ["Developed Markets", "Emerging Markets"]},
                {"bad": ["Approach 1", "Approach 2"], "good": ["Pre-COVID", "Post-COVID"]},
            ],
            "output_format": {"type": "object", "properties": {"left": {"type": "string"}, "right": {"type": "string"}}, "required": ["left", "right"]},
            "rules": [
                "Use concise, topic-specific labels (2–4 words).",
                "Do not mention AI unless the topic clearly centers on AI.",
                "Avoid placeholders like Option A/B, Traditional/Modern, Approach 1/2.",
            ],
        }
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You output only compact JSON objects; no prose, no markdown."},
                {"role": "user", "content": _json_dump(prompt)},
            ],
            temperature=0.2,
            max_tokens=128,
        )
        import json
        try:
            obj = json.loads((resp.choices[0].message.content or "").strip())
            new_left = (obj.get("left") or lhead).strip()
            new_right = (obj.get("right") or rhead).strip()
            if new_left and new_right:
                table.setdefault("left", {})["heading"] = new_left
                table.setdefault("right", {})["heading"] = new_right
                # Refresh title to A vs B when appropriate
                s["title"] = f"{new_left} vs {new_right}"
                content["table"] = table
                s["content"] = content
                changed = True
        except Exception:
            # If parsing fails, try deterministic heuristic fallback
            h = _heuristic_pair(title, agenda)
            if h and (lhead.lower() in {"option a","a","traditional","left"} or rhead.lower() in {"option b","b","ai-driven","ai driven","right"}):
                table.setdefault("left", {})["heading"] = h[0]
                table.setdefault("right", {})["heading"] = h[1]
                s["title"] = f"{h[0]} vs {h[1]}"
                content["table"] = table
                s["content"] = content
                changed = True
    # Final safeguard: apply heuristic if headers remain generic anywhere
    if not changed:
        for s in slides:
            if s.get("type") != "table":
                continue
            content = s.get("content") or {}
            table = content.get("table") or {}
            left = (table.get("left") or {})
            right = (table.get("right") or {})
            lhead = (left.get("heading") or "").strip()
            rhead = (right.get("heading") or "").strip()
            if _is_generic(lhead) or _is_generic(rhead):
                h = _heuristic_pair(title, agenda)
                table.setdefault("left", {})["heading"] = h[0]
                table.setdefault("right", {})["heading"] = h[1]
                s["title"] = f"{h[0]} vs {h[1]}"
                content["table"] = table
                s["content"] = content
    return slides


def _retry_with_agenda(
    *,
    client: Any,
    system_msg: str,
    title: str,
    subtitle: str,
    presenter: str,
    date: str,
    theme_name: str,
    input_text: str,
    num_slides: int,
    agenda_bullets: List[str],
    issues: List[str],
) -> List[Dict[str, Any]] | None:
    """Single corrective retry with explicit agenda alignment and placeholder bans."""
    # If provided agenda is weak/empty, explicitly instruct to rebuild it
    force_regen_agenda = not agenda_bullets or len(agenda_bullets) < 5
    agenda_directive = (
        "Regenerate a coherent agenda with 6–8 topic-specific bullets derived from input_text; avoid placeholders. "
        "Then map all subsequent slides to those bullets in order. "
        if force_regen_agenda
        else f"Use this agenda IN ORDER: {agenda_bullets}. Map all subsequent slides to these bullets in order. "
    )
    repair_user = (
        "Regenerate the entire deck as a JSON array following the same schema. "
        "Fix the following issues: " + "; ".join(issues) + ". "
        "Strict requirements: "
        f"(1) {agenda_directive}Use contiguous blocks per agenda item (you may use 1+ slides per agenda bullet, but do not reorder). "
        "(2) TITLE SLIDE: Use a compelling, topic-specific title derived from the input text. NEVER use 'Generated Presentation' or generic titles. "
        "(3) Each mapped slide's TITLE must paraphrase its agenda bullet closely (share key terms); do not use generic labels. "
        "(4) Each mapped slide's CONTENT (bullets/points/table cells) must elaborate the mapped agenda bullet, sharing key terms. "
        "(5) Replace any placeholders with specific, topic-derived content; never use 'Indicator A/B' or 'Point N'. "
        "(6) Tabular Comparison headings must reflect the true comparison dimension for the topic (never 'Traditional vs AI-Driven' unless explicitly about AI). "
        "Return ONLY the JSON array."
    )
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": _json_dump({
            "task": "Repair deck with strict agenda alignment and no placeholders",
            "presentation": {
                "title": title, "subtitle": subtitle, "presenter": presenter, "date": date,
                "theme": {"name": theme_name},
                "input_text": input_text,
                "requested_slide_count": int(max(3, num_slides)),
            },
            "agenda_bullets": agenda_bullets,
            "notes": repair_user,
        })},
    ]
    resp = client.chat.completions.create(
        model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
        messages=messages,  # type: ignore[arg-type]
        temperature=0.2,
        max_tokens=1800,
    )
    content = (resp.choices[0].message.content or "").strip()
    slides, err = _parse_json_array(content)
    if err or not isinstance(slides, list):
        return None
    return slides

