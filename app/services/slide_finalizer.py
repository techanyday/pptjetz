from __future__ import annotations

from typing import Any, Dict, List, Tuple
import os
import logging
import json
import ast
import re


logger = logging.getLogger(__name__)


_PLACEHOLDER_PHRASES = {
    "(to be refined)",
    "to be refined",
    "tbd",
    "to be defined",
    "placeholder",
    "lorem ipsum",
    "click to add",
}


def _is_placeholder_text(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return True
    if t in _PLACEHOLDER_PHRASES:
        return True
    if re.match(r"^(slide|point|item|step|phase|scope|metric|indicator|key\s*point)\s*[a-z0-9]+$", t):
        return True
    if re.match(r"^(company|org|organization|product|solution)\s*[a-z0-9]+$", t):
        return True
    if re.match(r"^(left|right|option)\s*[ab12]$", t):
        return True
    return any(p in t for p in _PLACEHOLDER_PHRASES)


def _coerce_text_item(x: Any) -> str:
    if isinstance(x, dict):
        h = x.get("heading") or x.get("title") or x.get("label") or x.get("name") or x.get("step")
        d = x.get("description") or x.get("desc") or x.get("details") or x.get("detail") or x.get("body") or x.get("text")
        hs = str(h or "").strip()
        ds = str(d or "").strip()
        if hs and ds:
            return f"{hs} — {ds}"
        return hs or ds

    if isinstance(x, str):
        s = x.strip()
        if s.startswith("{") and s.endswith("}") and (
            "title" in s or "heading" in s or "label" in s or "description" in s
        ):
            parsed: Any = None
            try:
                parsed = json.loads(s)
            except Exception:
                try:
                    parsed = ast.literal_eval(s)
                except Exception:
                    parsed = None
            if isinstance(parsed, dict):
                return _coerce_text_item(parsed)

        if "title" in s or "heading" in s or "label" in s or "description" in s:
            h_match = re.search(r"(?:'|\")?(?:heading|title|label)(?:'|\")?\s*:\s*(?:'|\")([^'\"]+)", s, flags=re.IGNORECASE)
            d_match = re.search(r"(?:'|\")?(?:description|desc|details|detail|text|body)(?:'|\")?\s*:\s*(?:'|\")([^'\"]+)", s, flags=re.IGNORECASE)
            hs = str(h_match.group(1) if h_match else "").strip()
            ds = str(d_match.group(1) if d_match else "").strip()
            if hs and ds:
                return f"{hs} — {ds}"
            if hs:
                return hs
            if ds:
                return ds
        return s

    return str(x or "").strip()


def _clean_list_text(items: Any, *, max_n: int | None = None) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for x in items:
        s = _coerce_text_item(x)
        if not s:
            continue
        if _is_placeholder_text(s):
            continue
        out.append(s)
        if max_n is not None and len(out) >= max_n:
            break
    return out


def _top_up_from_title(*, title: str, min_n: int, existing: List[str] | None = None) -> List[str]:
    """Deterministically derive additional points from a slide title.

    We avoid placeholders and keep this lightweight; the LLM quality enhancer can
    later rewrite these into richer, topic-specific points.
    """

    base = str(title or "").strip() or "Overview"
    topic = re.sub(r"\s*\([^)]*\)\s*$", "", base).strip() or base
    seed = base
    existing2 = [str(x).strip() for x in (existing or []) if str(x).strip() and (not _is_placeholder_text(str(x)))]
    seen = {x.lower() for x in existing2}

    low_base = base.lower()
    risk_like = (
        "risk" in low_base
        or "likelihood" in low_base
        or "impact" in low_base
        or "cyber" in low_base
        or "threat" in low_base
    )

    if risk_like:
        templates = [
            "Highest-priority risks affecting {t}",
            "Potential impact if {t} risks materialize",
            "Likelihood signals and early warning indicators",
            "Current controls and mitigation actions for {t}",
            "Risk owners, escalation paths, and response timing",
            "Residual risks to monitor and review cadence",
            "Immediate next steps for high-risk areas in {t}",
        ]
    else:
        templates = [
            "Definition and scope of {t}",
            "Why {t} matters to outcomes and stakeholders",
            "Key drivers shaping {t}",
            "Common challenges and constraints in {t}",
            "Practical next steps to advance {t}",
            "Metrics to track progress in {t}",
            "Tradeoffs and decisions to clarify for {t}",
        ]

    # Rotate templates deterministically based on the title.
    start = abs(hash(seed)) % len(templates)
    order = templates[start:] + templates[:start]
    out = list(existing2)
    for tmpl in order:
        if len(out) >= min_n:
            break
        cand = tmpl.format(t=topic).strip()
        if not cand or _is_placeholder_text(cand):
            continue
        low = cand.lower()
        if low in seen:
            continue
        out.append(cand)
        seen.add(low)

    # Absolute last resort: still avoid placeholders.
    while len(out) < min_n:
        cand = f"{base} — key point {len(out) + 1}"
        if cand.lower() not in seen:
            out.append(cand)
            seen.add(cand.lower())
        else:
            break
    return out


def _image_slide_points(title: str, existing: List[str]) -> List[str]:
    base = str(title or "Overview").strip() or "Overview"
    low = base.lower()
    template_markers = (
        "definition and scope", "primary business outcomes", "teams and decisions affected",
        "evidence that", "tradeoffs that may limit", "measures for tracking",
        "connect the visual", "use a relevant real-world context", "keep supporting text concise",
    )
    usable = [
        x for x in existing
        if not _is_placeholder_text(x) and not any(marker in x.lower() for marker in template_markers)
    ]
    if "5g" in low:
        templates = [
            f"Higher bandwidth enables richer real-time services across {base}",
            f"Lower latency supports connected devices and responsive operations",
            f"Network slicing can prioritize critical workloads and service levels",
            f"Coverage, spectrum, and infrastructure cost shape the rollout case",
            f"Security and edge-computing decisions determine enterprise readiness",
        ]
    elif any(k in low for k in {"artificial intelligence", " ai ", "machine learning"}):
        templates = [
            f"Automation shifts repetitive decisions from teams to intelligent systems",
            f"Data quality and model governance determine reliable outcomes",
            f"The strongest value appears where {base} improves speed or accuracy",
            f"Human review remains important for exceptions, risk, and accountability",
            f"Adoption depends on workflow integration, skills, and measurable results",
        ]
    elif any(k in low for k in {"integration", "implementation", "adoption"}):
        templates = [
            f"The capability connects to existing workflows and operating processes",
            f"Data, systems, and teams must be ready before scale-up",
            f"Operational roles change as the new capability enters daily work",
            f"Controls and success measures keep implementation on track",
            f"The next decision is where to pilot {base} with lowest execution risk",
        ]
    elif any(k in low for k in {"future", "trend", "innovation"}):
        templates = [
            f"The underlying capability shift is changing how value is created",
            f"Early adopters are testing new operating models and customer experiences",
            f"Investment signals show where the market is moving next",
            f"Constraints include skills, regulation, infrastructure, and trust",
            f"Near-term action is to test one high-value use case with clear measures",
        ]
    elif any(k in low for k in {"impact", "benefit", "value", "opportunity"}):
        templates = [
            f"The main impact appears in faster decisions, lower friction, or better reach",
            f"Teams and customers experience the change at different points in the journey",
            f"Value depends on adoption, process redesign, and outcome measurement",
            f"Tradeoffs include investment, complexity, risk, and change capacity",
            f"Progress should be tracked through business outcomes rather than activity alone",
        ]
    else:
        templates = [
            f"The concept changes how teams make decisions and deliver outcomes",
            f"Key drivers include customer needs, operating pressure, and available technology",
            f"The main constraints are capability, cost, risk, and execution capacity",
            f"A focused pilot can test the idea before broader investment",
            f"Success requires ownership, adoption, and measures tied to outcomes",
        ]
    if usable:
        templates = usable + templates
    out: List[str] = []
    seen = set()
    for item in templates:
        clean = str(item).strip()
        if clean and clean.lower() not in seen:
            out.append(clean)
            seen.add(clean.lower())
        if len(out) >= 5:
            break
    return out[:6]


def _slide_debug_enabled() -> bool:
    return os.getenv("SLIDE_DEBUG", "0") == "1"


def debug_log_slides(stage: str, slides: List[Dict[str, Any]] | None) -> None:
    if not _slide_debug_enabled():
        return
    arr = slides if isinstance(slides, list) else []
    for i, s in enumerate(arr, start=1):
        if not isinstance(s, dict):
            logger.info("slide_debug:%s idx=%s non_dict=%s", stage, i, type(s).__name__)
            continue
        t = str(s.get("type") or "").strip().lower() or "(missing)"
        title = str(s.get("title") or "").strip()
        c = s.get("content")
        if isinstance(c, dict):
            keys = sorted(list(c.keys()))
        elif isinstance(c, list):
            keys = ["<list>"]
        elif isinstance(c, str):
            keys = ["<str>"]
        else:
            keys = []
        logger.info(
            "slide_debug:%s idx=%s title=%r type=%s content_keys=%s _render_exact=%s _fallback_from=%s _fallback_to=%s",
            stage,
            i,
            title,
            t,
            keys,
            bool(s.get("_render_exact")),
            s.get("_fallback_from"),
            s.get("_fallback_to"),
        )


_ALLOWED_TYPES = {
    "title",
    "agenda",
    "one_idea",
    "cards",
    "card",
    "card_style",
    "process_timeline_alt",
    "double_column",
    "deep_dive",
    "comparison",
    "table",
    "infographic",
    "key_takeaways",
    "timeline",
    "roadmap",
    "swot",
    "kpi",
    "risk_matrix",
    "before_after",
    "architecture",
    "pricing",
    "impact_effort_matrix",
    "image",
    "impact_matrix",
    "prioritization_matrix",
    "thank_you",
    "cta",
    "bullets",
}


def _merge_bullet_fragments(items: List[str]) -> List[str]:
    out: List[str] = []
    for raw in items or []:
        txt = str(raw or "").strip()
        if not txt:
            continue

        if not out:
            out.append(txt)
            continue

        prev = out[-1]
        prev_low = prev.rstrip().lower()
        txt_low = txt.lower()

        # Heuristic: merge very short continuation fragments into the previous bullet
        # when the previous bullet ends with a connector word.
        connector_suffixes = {
            "with",
            "and",
            "or",
            "of",
            "to",
            "in",
            "for",
            "on",
            "by",
            "via",
            "using",
            "from",
            "as",
            "into",
            "at",
        }

        prev_last = prev_low.split()[-1] if prev_low.split() else ""
        is_short_fragment = (len(txt) <= 20) and (len(txt.split()) <= 3)
        prev_ends_with_connector = prev_last in connector_suffixes
        prev_looks_complete = prev_low.endswith((".", "!", "?", ";"))

        if is_short_fragment and prev_ends_with_connector and (not prev_looks_complete):
            out[-1] = (prev.rstrip() + " " + txt).strip()
            continue

        out.append(txt)

    return out


def _extract_bullets(sl: Dict[str, Any]) -> List[str]:
    c = sl.get("content")
    bullets: List[str] = []
    if isinstance(c, dict):
        raw = c.get("bullets")
        if isinstance(raw, list):
            bullets = [str(x).strip() for x in raw if str(x).strip()]
        elif isinstance(raw, str):
            bullets = [x.strip() for x in raw.splitlines() if x.strip()]
    elif isinstance(c, list):
        bullets = [str(x).strip() for x in c if str(x).strip()]
    elif isinstance(c, str):
        bullets = [x.strip() for x in c.splitlines() if x.strip()]

    if not bullets:
        raw2 = sl.get("bullets")
        if isinstance(raw2, list):
            bullets = [str(x).strip() for x in raw2 if str(x).strip()]
        elif isinstance(raw2, str):
            bullets = [x.strip() for x in raw2.splitlines() if x.strip()]

    bullets = _merge_bullet_fragments(bullets)
    return bullets[:12]


def normalize_slide(slide: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(slide or {})
    title = str(s.get("title") or "").strip()
    if not title:
        title = "Slide"
    s["title"] = title

    t = str(s.get("type") or "").strip().lower()
    if t in {"", "text", "basic", "bullet", "bullet_slide", "plain", "bullets"}:
        t = "infographic"
    if t == "double_col":
        t = "double_column"
    if t == "takeaways":
        t = "key_takeaways"
    s["type"] = t

    bullets = _clean_list_text(_extract_bullets(s), max_n=12)

    c = s.get("content")
    if not isinstance(c, dict):
        c = {}
    s["content"] = c

    if bullets and "bullets" not in c and t in {"bullets", "infographic", "key_takeaways"}:
        c["bullets"] = bullets

    # Always normalize bullet lists for bullet-like slide types.
    if t in {"agenda", "bullets", "infographic", "key_takeaways"}:
        if bullets:
            c["bullets"] = bullets
        elif "bullets" in c:
            c["bullets"] = _clean_list_text(c.get("bullets"), max_n=12)

    if t in {"cards", "card", "card_style", "process_timeline_alt"}:
        if "cards" not in c and bullets:
            c["cards"] = bullets[:8]
        elif "cards" in c:
            c["cards"] = _clean_list_text(c.get("cards"), max_n=8)

    if t in {"double_column", "deep_dive"}:
        lp = c.get("left_points")
        rp = c.get("right_points")
        if not isinstance(lp, list) and bullets:
            lp = bullets[::2][:8]
        if not isinstance(rp, list) and bullets:
            rp = bullets[1::2][:8]
        if isinstance(lp, list):
            c["left_points"] = _clean_list_text(lp, max_n=8)
        if isinstance(rp, list):
            c["right_points"] = _clean_list_text(rp, max_n=8)

    if t == "image":
        # Ensure image slides use caption instead of bullets
        caption = c.get("caption") or ""
        if not caption:
            bullets = c.get("bullets") or c.get("points") or []
            if isinstance(bullets, list) and bullets:
                # Convert bullets to a proper paragraph
                import re
                narrative_parts = []
                for item in bullets:
                    clean = re.sub(r"^[•\-–—\d.) ]+", "", str(item).strip())
                    if clean:
                        # Ensure it's a complete sentence
                        if not clean.endswith(('.','!','?')):
                            clean = clean + "."
                        narrative_parts.append(clean)
                if narrative_parts:
                    caption = " ".join(narrative_parts).strip()
                    if caption:
                        c["caption"] = caption
                        # Remove bullets to prevent fallback
                        c.pop("bullets", None)
                        c.pop("points", None)
        # Validate and fix caption quality
        if c.get("caption"):
            caption = str(c["caption"]).strip()
            # Better sentence counting: split on sentence-ending punctuation
            import re
            sentences = re.split(r'[.!?]+\s+', caption)
            sentences = [s.strip() for s in sentences if s.strip()]
            sentence_count = len(sentences)
            
            # Check if caption is too short or has too few sentences
            word_count = len(caption.split())
            
            # Detect topic-list style: many short phrases with commas or "include/encompass/feature" patterns
            topic_list_patterns = [
                r'include[s]?\s+(?:\w+,\s*){2,}',  # "includes X, Y, Z"
                r'feature[s]?\s+(?:\w+,\s*){2,}',  # "features X, Y, Z"
                r'consist[s]?\s+(?:of\s+)?(?:\w+,\s*){2,}',  # "consists of X, Y, Z"
                r'comprise[s]?\s+(?:\w+,\s*){2,}',  # "comprises X, Y, Z"
                r'are\s+(?:\w+,\s*){3,}',  # "are X, Y, Z, W"
            ]
            is_topic_list = any(re.search(pattern, caption, re.IGNORECASE) for pattern in topic_list_patterns)
            
            # Also detect if sentences are too short on average (indicates list-like structure)
            avg_sentence_length = word_count / sentence_count if sentence_count > 0 else 0
            is_short_sentences = avg_sentence_length < 10  # Less than 10 words per sentence suggests list
            
            # Require at least 2 sentences and enough detail to read as a coherent paragraph.
            # Also reject topic-list style captions.
            if sentence_count < 2 or word_count < 34 or is_topic_list or (is_short_sentences and word_count < 45):
                topic = re.sub(r"\s+", " ", str(title or "this topic")).strip() or "this topic"
                c["caption"] = (
                    f"{topic} matters most when it is tied to a specific workflow, decision, or customer outcome. "
                    f"Teams usually succeed when they assign ownership, pace the rollout, and measure results against clear business goals. "
                    f"Framed this way, the slide reads as one connected idea instead of a stack of separate talking points."
                )
        # Ensure caption exists with substantive content
        if not c.get("caption"):
            topic = re.sub(r"\s+", " ", str(title or "this topic")).strip() or "this topic"
            c["caption"] = (
                f"{topic} matters most when it is tied to a specific workflow, decision, or customer outcome. "
                f"Teams usually succeed when they assign ownership, pace the rollout, and measure results against clear business goals. "
                f"Framed this way, the slide reads as one connected idea instead of a stack of separate talking points."
            )

    if t in {"comparison", "table"}:
        tbl = c.get("table")
        if not isinstance(tbl, dict):
            cols = c.get("columns")
            if isinstance(cols, dict):
                left = cols.get("left") or {}
                right = cols.get("right") or {}
                tbl = {
                    "left": {
                        "heading": str((left.get("heading") or "Left")).strip() or "Left",
                        "points": [str(x).strip() for x in (left.get("points") or []) if str(x).strip()][:8],
                    },
                    "right": {
                        "heading": str((right.get("heading") or "Right")).strip() or "Right",
                        "points": [str(x).strip() for x in (right.get("points") or []) if str(x).strip()][:8],
                    },
                }
        if isinstance(tbl, dict):
            left = tbl.get("left") if isinstance(tbl.get("left"), dict) else {}
            right = tbl.get("right") if isinstance(tbl.get("right"), dict) else {}
            if isinstance(left, dict) and isinstance(left.get("points"), list):
                left["points"] = _clean_list_text(left.get("points"), max_n=8)
            if isinstance(right, dict) and isinstance(right.get("points"), list):
                right["points"] = _clean_list_text(right.get("points"), max_n=8)
            c["table"] = tbl

    s["content"] = c
    return s


def validate_slide_type_fit(slide: Dict[str, Any]) -> Tuple[bool, str]:
    t = str(slide.get("type") or "").strip().lower()
    c = slide.get("content")
    if not isinstance(c, dict):
        c = {}

    if t not in _ALLOWED_TYPES:
        return False, "unknown_type"

    if t in {"title", "thank_you", "cta"}:
        return True, "ok"

    if t == "agenda":
        b = c.get("bullets")
        if isinstance(b, list) and any(str(x).strip() for x in b):
            return True, "ok"
        return False, "agenda_missing_bullets"

    if t == "image":
        query = str(c.get("query") or c.get("image_query") or "").strip()
        bullets = c.get("bullets")
        if query or (isinstance(bullets, list) and any(str(x).strip() for x in bullets)):
            return True, "ok"
        return False, "image_missing_query"

    if t in {"bullets", "infographic", "key_takeaways"}:
        b = c.get("bullets")
        if isinstance(b, list) and any(str(x).strip() for x in b):
            return True, "ok"
        return False, "missing_bullets"

    if t in {"cards", "card", "card_style", "process_timeline_alt"}:
        arr = c.get("cards")
        if isinstance(arr, list) and any(str(x).strip() for x in arr):
            return True, "ok"
        return False, "cards_missing_cards"

    if t in {"double_column", "deep_dive"}:
        lp = c.get("left_points")
        rp = c.get("right_points")
        if isinstance(lp, list) and isinstance(rp, list) and (len(lp) >= 4) and (len(rp) >= 4):
            return True, "ok"
        return False, "double_column_missing_points"

    if t in {"comparison", "table"}:
        tbl = c.get("table")
        if not isinstance(tbl, dict):
            return False, "table_missing_table"
        left = tbl.get("left") or {}
        right = tbl.get("right") or {}
        lp = left.get("points") if isinstance(left, dict) else None
        rp = right.get("points") if isinstance(right, dict) else None
        if isinstance(lp, list) and isinstance(rp, list) and (len(lp) + len(rp) >= 4):
            return True, "ok"
        return False, "table_missing_points"

    if t == "timeline":
        tl = c.get("timeline")
        if isinstance(tl, list) and len(tl) >= 4:
            return True, "ok"
        return False, "timeline_missing_timeline"

    if t == "roadmap":
        phases = c.get("phases")
        if isinstance(phases, list) and len(phases) >= 4:
            return True, "ok"
        tl = c.get("timeline")
        if isinstance(tl, list) and len(tl) >= 4:
            return True, "ok"
        return False, "roadmap_missing_phases"

    if t == "swot":
        keys = ["strengths", "weaknesses", "opportunities", "threats"]
        nonempty = 0
        for k in keys:
            q = c.get(k)
            pts = None
            if isinstance(q, dict):
                pts = q.get("points")
            if isinstance(pts, list) and any(str(x).strip() for x in pts):
                nonempty += 1
        if nonempty >= 2:
            return True, "ok"
        return False, "swot_missing_quadrants"

    if t == "kpi":
        metrics = c.get("metrics")
        if not isinstance(metrics, list) or len(metrics) < 2:
            return False, "kpi_missing_metrics"
        ok = 0
        for m in metrics[:4]:
            if not isinstance(m, dict):
                continue
            if str(m.get("label") or "").strip() and str(m.get("value") or "").strip():
                ok += 1
        if ok >= 2:
            return True, "ok"
        return False, "kpi_invalid_metrics"

    if t == "risk_matrix":
        keys = [
            "low_likelihood_low_impact",
            "low_likelihood_high_impact",
            "high_likelihood_low_impact",
            "high_likelihood_high_impact",
        ]
        nonempty = 0
        total = 0
        for k in keys:
            q = c.get(k)
            pts = None
            if isinstance(q, dict):
                pts = q.get("points")
            if isinstance(pts, list):
                vv = [str(x).strip() for x in pts if str(x).strip()]
                total += len(vv)
                if vv:
                    nonempty += 1
        if total >= 4 and nonempty >= 2:
            return True, "ok"
        return False, "risk_matrix_missing_quadrants"

    if t == "before_after":
        b = c.get("before")
        a = c.get("after")
        if not (isinstance(b, dict) and isinstance(a, dict)):
            return False, "before_after_missing_columns"
        bp = b.get("points")
        ap = a.get("points")
        if isinstance(bp, list) and isinstance(ap, list) and len(bp) >= 3 and len(ap) >= 3:
            return True, "ok"
        return False, "before_after_missing_points"

    if t == "architecture":
        boxes = c.get("boxes")
        conns = c.get("connections")
        if isinstance(boxes, list) and len(boxes) >= 4 and isinstance(conns, list) and len(conns) >= 2:
            return True, "ok"
        return False, "architecture_missing_structure"

    if t == "pricing":
        plans = c.get("plans")
        if isinstance(plans, list) and len(plans) >= 2:
            return True, "ok"
        return False, "pricing_missing_plans"

    if t in {"impact_effort_matrix", "impact_matrix", "prioritization_matrix"}:
        keys = ["quick_easy_wins", "major_projects", "fill_in", "thankless_tasks"]
        total = 0
        nonempty = 0
        for k in keys:
            v = c.get(k) or []
            if isinstance(v, dict):
                v = v.get("bullets") or v.get("points") or v.get("items") or []
            if isinstance(v, list):
                vv = [str(x).strip() for x in v if str(x).strip()]
                total += len(vv)
                if vv:
                    nonempty += 1
        if total >= 4 and nonempty >= 2:
            return True, "ok"
        return False, "impact_effort_missing_quadrants"

    return True, "ok"


def _fallback_type_from_bullets(*, bullets: List[str], double_remaining: int) -> str:
    if double_remaining > 0 and len(bullets) >= 8:
        return "double_column"
    if len(bullets) >= 4:
        return "cards"
    if len(bullets) >= 2:
        return "infographic"
    return "infographic"


def _ensure_nonempty_slide_content(sl: Dict[str, Any]) -> Dict[str, Any]:
    """Guarantee we never emit an empty or placeholder-only slide."""
    t = str(sl.get("type") or "").strip().lower()
    if t in {"title", "thank_you", "cta"}:
        return sl
    c = sl.get("content")
    if not isinstance(c, dict):
        c = {}
        sl["content"] = c

    # Prefer existing meaningful bullets (but later we may top-up to enforce density).
    bullets = c.get("bullets")
    if isinstance(bullets, list):
        bullets2 = _clean_list_text(bullets, max_n=12)
        if bullets2:
            c["bullets"] = bullets2

    # For double column, if not enough points, fall back to infographic
    if t in {"double_column", "deep_dive"}:
        lp = _clean_list_text(c.get("left_points"), max_n=8)
        rp = _clean_list_text(c.get("right_points"), max_n=8)
        if len(lp) >= 4 and len(rp) >= 4:
            c["left_points"] = lp
            c["right_points"] = rp
            return sl
        merged = (lp + rp)[:8]
        sl["type"] = "infographic"
        sl["content"] = {"bullets": merged}
        t = "infographic"
        c = sl["content"]

    title = str(sl.get("title") or "").strip() or "Overview"

    if t == "before_after":
        before = c.get("before") if isinstance(c.get("before"), dict) else {}
        after = c.get("after") if isinstance(c.get("after"), dict) else {}
        source = _clean_list_text(c.get("bullets"), max_n=12)
        if len(source) < 6:
            source = _top_up_from_title(title=title, min_n=6, existing=source)[:12]
        bp = _clean_list_text(before.get("points"), max_n=8)
        ap = _clean_list_text(after.get("points"), max_n=8)
        if len(bp) < 3:
            bp = (bp + source[:3])[:8]
        if len(ap) < 3:
            ap = (ap + source[3:6])[:8]
        if len(bp) < 3:
            bp = _top_up_from_title(title=title + " before", min_n=3, existing=bp)[:8]
        if len(ap) < 3:
            ap = _top_up_from_title(title=title + " after", min_n=3, existing=ap)[:8]
        c["before"] = {**before, "heading": str(before.get("heading") or "Before").strip() or "Before", "points": bp}
        c["after"] = {**after, "heading": str(after.get("heading") or "After").strip() or "After", "points": ap}
        sl["content"] = c
        return sl

    if t == "swot":
        keys = ["strengths", "weaknesses", "opportunities", "threats"]
        labels = {
            "strengths": "Strengths",
            "weaknesses": "Weaknesses",
            "opportunities": "Opportunities",
            "threats": "Threats",
        }

        # If the model didn't provide structured quadrants but did provide bullets,
        # distribute bullets round-robin into quadrants before top-up.
        try:
            has_structured = False
            for k in keys:
                q = c.get(k)
                if isinstance(q, dict) and isinstance(q.get("points"), list) and any(str(x).strip() for x in q.get("points") or []):
                    has_structured = True
                    break
            if not has_structured:
                seed_bullets = _clean_list_text(c.get("bullets"), max_n=12)
                if seed_bullets:
                    for i, b in enumerate(seed_bullets):
                        kk = keys[i % 4]
                        q0 = c.get(kk)
                        if not isinstance(q0, dict):
                            q0 = {}
                        pts0 = q0.get("points") if isinstance(q0.get("points"), list) else []
                        q0["points"] = list(pts0) + [b]
                        c[kk] = q0
        except Exception:
            pass

        for k in keys:
            q = c.get(k)
            if not isinstance(q, dict):
                q = {}
            pts = _clean_list_text(q.get("points"), max_n=6)
            if len(pts) < 3:
                pts = _top_up_from_title(title=f"{title} ({labels.get(k, k)})", min_n=3, existing=pts)
            q2 = dict(q)
            q2["points"] = pts[:6]
            c[k] = q2

        sl["content"] = c
        return sl

    if t == "kpi":
        metrics_raw = c.get("metrics")
        metrics_in: List[Dict[str, str]] = []
        if isinstance(metrics_raw, list):
            for m in metrics_raw:
                if not isinstance(m, dict):
                    continue
                lab = str(m.get("label") or "").strip()
                val = str(m.get("value") or "").strip()
                cap = str(m.get("caption") or "").strip()
                if _is_placeholder_text(lab) or _is_placeholder_text(val) or _is_placeholder_text(cap):
                    continue
                if not (lab and val):
                    continue
                metrics_in.append({"label": lab[:60], "value": val[:30], "caption": cap[:120]})
                if len(metrics_in) >= 4:
                    break

        seed = title
        templates = [
            ("Adoption rate", ["35%", "42%", "48%", "52%"], "Share of the target group using it"),
            ("Growth", ["12% YoY", "15% YoY", "18% YoY", "20% YoY"], "Year-over-year change"),
            ("Retention", ["68%", "72%", "75%", "78%"], "Sustained engagement over time"),
            ("Satisfaction", ["4.2/5", "4.3/5", "4.4/5", "4.5/5"], "Reported experience score"),
            ("Cost efficiency", ["-8%", "-10%", "-12%", "-15%"], "Change in unit cost"),
            ("Coverage", ["60%", "65%", "70%", "75%"], "Reach across segments"),
        ]
        start = abs(hash(seed)) % len(templates)
        order = templates[start:] + templates[:start]

        seen_labs = {m.get("label", "").strip().lower() for m in metrics_in}
        idx_val = abs(hash(seed + "|kpi")) % 4
        for base_label, values, default_cap in order:
            if len(metrics_in) >= 4:
                break
            if base_label.lower() in seen_labs:
                continue
            metrics_in.append({"label": base_label, "value": values[idx_val], "caption": default_cap})
            seen_labs.add(base_label.lower())

        c["metrics"] = metrics_in[:4]
        sl["content"] = c
        return sl

    # Enforce minimum density (no placeholders).
    # Note: image slides use captions instead of bullets, handled in normalize_slide
    if t in {"bullets", "infographic", "key_takeaways"}:
        b = _clean_list_text(c.get("bullets"), max_n=12)
        min_n = 4 if t in {"infographic", "key_takeaways"} else 4
        if len(b) < min_n:
            b = _top_up_from_title(title=title, min_n=min_n, existing=b)
        c["bullets"] = b[:12]
        sl["content"] = c
        return sl

    if t in {"cards", "card", "card_style", "process_timeline_alt"}:
        cards = _clean_list_text(c.get("cards"), max_n=8)
        if len(cards) < 4:
            cards = _top_up_from_title(title=title, min_n=4, existing=cards)[:8]
        c["cards"] = cards
        sl["content"] = c
        return sl

    if t in {"comparison", "table"}:
        tbl = c.get("table")
        if not isinstance(tbl, dict):
            tbl = {}
        left = tbl.get("left") if isinstance(tbl.get("left"), dict) else {}
        right = tbl.get("right") if isinstance(tbl.get("right"), dict) else {}
        lhead = str(left.get("heading") or "Left").strip() or "Left"
        rhead = str(right.get("heading") or "Right").strip() or "Right"

        lp = _clean_list_text(left.get("points"), max_n=8)
        rp = _clean_list_text(right.get("points"), max_n=8)
        if len(lp) < 2:
            lp = _top_up_from_title(title=f"{title} ({lhead})", min_n=2, existing=lp)[:8]
        if len(rp) < 2:
            rp = _top_up_from_title(title=f"{title} ({rhead})", min_n=2, existing=rp)[:8]

        left2 = dict(left)
        right2 = dict(right)
        left2["heading"] = lhead
        right2["heading"] = rhead
        left2["points"] = lp
        right2["points"] = rp
        tbl2 = dict(tbl)
        tbl2["left"] = left2
        tbl2["right"] = right2
        c["table"] = tbl2
        sl["content"] = c
        return sl

    # If still empty, add one minimal, non-placeholder bullet
    b = _clean_list_text(c.get("bullets"), max_n=12)
    if not b:
        c["bullets"] = _top_up_from_title(title=title, min_n=1, existing=[])
        sl["content"] = c
    return sl


def finalize_slides(slides: List[Dict[str, Any]] | None, *, stage: str = "final") -> List[Dict[str, Any]]:
    arr = slides if isinstance(slides, list) else []
    out: List[Dict[str, Any]] = []

    double_cap = 2
    used_double = 0

    for s in arr:
        if not isinstance(s, dict):
            s = {"type": "bullets", "title": "Slide", "content": {"bullets": [str(s)]}}

        original_type = str(s.get("type") or "").strip().lower()
        norm = normalize_slide(s)
        title_lc = str(norm.get("title") or "").strip().lower()
        if (
            norm.get("type") != "before_after"
            and original_type in {"timeline", "roadmap", "cards", "card", "card_style", "process_timeline_alt"}
            and re.search(r"\bbefore\s+(?:and|vs\.?|versus)\s+after\b", title_lc)
        ):
            norm["type"] = "before_after"
            original_type = original_type or "before_after"

        # Enforce placeholder removal + min-content guarantees early so that
        # under-specified slides can be topped up instead of being downgraded.
        norm = _ensure_nonempty_slide_content(norm)

        ok, reason = validate_slide_type_fit(norm)
        if not ok:
            bullets = _extract_bullets(norm)
            fallback_to = _fallback_type_from_bullets(bullets=bullets, double_remaining=(double_cap - used_double))
            norm["_fallback_from"] = original_type or norm.get("type")
            norm["_fallback_reason"] = reason
            norm["_fallback_to"] = fallback_to
            norm["type"] = fallback_to
            norm = normalize_slide(norm)

        t = str(norm.get("type") or "").strip().lower()
        if t in {"double_column", "deep_dive"}:
            if used_double >= double_cap:
                bullets = _extract_bullets(norm)
                fallback_to = "cards" if len(bullets) >= 4 else "infographic"
                norm["_fallback_from"] = norm.get("_fallback_from") or t
                norm["_fallback_reason"] = norm.get("_fallback_reason") or "double_column_cap"
                norm["_fallback_to"] = fallback_to
                norm["type"] = fallback_to
                norm = normalize_slide(norm)
            else:
                used_double += 1

        # Enforce placeholder removal + min-content guarantees
        norm = _ensure_nonempty_slide_content(norm)

        norm["_render_exact"] = True
        out.append(norm)

    def _is_key_takeaways_candidate(sl: Dict[str, Any]) -> bool:
        try:
            t = str(sl.get("type") or "").strip().lower()
        except Exception:
            t = ""
        if t == "key_takeaways":
            return True
        try:
            title_lc = str(sl.get("title") or "").strip().lower()
        except Exception:
            title_lc = ""
        return "key takeaways" in title_lc

    # Ensure singleton Key Takeaways and place it immediately before the final Thank You / CTA.
    try:
        kt_items: List[Tuple[int, Dict[str, Any]]] = [(i, s) for i, s in enumerate(out) if isinstance(s, dict) and _is_key_takeaways_candidate(s)]
        if kt_items:
            # Prefer an explicit key_takeaways typed slide as the base; otherwise keep the last candidate.
            base_idx = None
            for i, s in reversed(kt_items):
                if str(s.get("type") or "").strip().lower() == "key_takeaways":
                    base_idx = i
                    break
            if base_idx is None:
                base_idx = kt_items[-1][0]

            base = dict(out[base_idx])

            merged: List[str] = []
            seen = set()
            for _, s in kt_items:
                c = s.get("content")
                if not isinstance(c, dict):
                    continue
                raw = c.get("bullets")
                if not isinstance(raw, list):
                    raw = c.get("takeaways")
                if not isinstance(raw, list):
                    continue
                for x in raw:
                    txt = str(x or "").strip()
                    if not txt:
                        continue
                    lo = txt.lower()
                    if lo in seen:
                        continue
                    seen.add(lo)
                    merged.append(txt)

            merged = _clean_list_text(merged, max_n=12)
            base["type"] = "key_takeaways"
            base["title"] = "Key Takeaways"
            c2 = base.get("content")
            if not isinstance(c2, dict):
                c2 = {}
            if merged:
                c2["bullets"] = merged
            base["content"] = c2
            base = normalize_slide(base)
            base = _ensure_nonempty_slide_content(base)
            base["_render_exact"] = True

            out_wo = [s for s in out if not (isinstance(s, dict) and _is_key_takeaways_candidate(s))]
            insert_at = None
            for i in range(len(out_wo) - 1, -1, -1):
                t = str((out_wo[i] or {}).get("type") or "").strip().lower()
                if t in {"thank_you", "cta"}:
                    insert_at = i
                    break
            if insert_at is None:
                insert_at = len(out_wo)
            out_wo.insert(insert_at, base)
            out = out_wo
    except Exception:
        pass

    # Mitigate repeated body slides (same type + same title/content), while preserving count.
    try:
        stable: List[Dict[str, Any]] = []
        protected_types = {"title", "agenda", "thank_you", "cta", "one_idea"}
        seen_title: set[tuple[str, str]] = set()
        seen_content: set[tuple[str, str]] = set()

        def _content_sig(c: Any) -> str:
            if not isinstance(c, dict):
                return ""
            try:
                return json.dumps(c, sort_keys=True, ensure_ascii=True, separators=(",", ":"))[:600]
            except Exception:
                return str(c)[:600]

        for s in out:
            if not isinstance(s, dict):
                stable.append(s)
                continue

            t = str(s.get("type") or "").strip().lower()
            ttl = str(s.get("title") or "").strip().lower()
            sig_title = (t, ttl)
            sig_content = (t, _content_sig(s.get("content")))
            is_dup = (
                t not in protected_types
                and ((ttl and sig_title in seen_title) or (sig_content[1] and sig_content in seen_content))
            )

            if not is_dup:
                if ttl:
                    seen_title.add(sig_title)
                if sig_content[1]:
                    seen_content.add(sig_content)
                stable.append(s)
                continue

            repl = dict(s)
            repl["_fallback_from"] = repl.get("_fallback_from") or t
            repl["_fallback_reason"] = repl.get("_fallback_reason") or "duplicate_slide"
            repl["_fallback_to"] = "infographic"
            repl["type"] = "infographic"

            c = repl.get("content")
            c2 = dict(c) if isinstance(c, dict) else {}
            bullets = _clean_list_text(c2.get("bullets"), max_n=12)
            if len(bullets) < 4:
                bullets = _top_up_from_title(title=str(repl.get("title") or "Overview"), min_n=4, existing=bullets)
            repl["content"] = {"bullets": bullets[:12]}
            repl = normalize_slide(repl)
            repl = _ensure_nonempty_slide_content(repl)
            repl["_render_exact"] = True

            stable.append(repl)
            seen_title.add(("infographic", str(repl.get("title") or "").strip().lower()))
            sig_repl = _content_sig(repl.get("content"))
            if sig_repl:
                seen_content.add(("infographic", sig_repl))

        out = stable
    except Exception:
        pass

    debug_log_slides(stage, out)
    return out
