from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.core.config import settings
from datetime import datetime
from app import db
from app.models import LLMCall, Pricing
import os
import logging
import uuid
import requests


MAX_TOKENS_DECK = int(os.getenv("MAX_TOKENS_DECK", "800"))
MAX_TOKENS_HARMONIZE = int(os.getenv("MAX_TOKENS_HARMONIZE", "600"))
MAX_TOKENS_TOPIC_BULLETS = int(os.getenv("MAX_TOKENS_TOPIC_BULLETS", "180"))
MAX_TOKENS_TABLE_HEADERS = int(os.getenv("MAX_TOKENS_TABLE_HEADERS", "96"))
MAX_TOKENS_EXPORT_COMPARISON = int(os.getenv("MAX_TOKENS_EXPORT_COMPARISON", "220"))
MAX_TOKENS_ONE_IDEA_INTRO = int(os.getenv("MAX_TOKENS_ONE_IDEA_INTRO", "96"))
MAX_TOKENS_RETRY = int(os.getenv("MAX_TOKENS_RETRY", "900"))
MAX_TOKENS_QUALITY = int(os.getenv("MAX_TOKENS_QUALITY", "900"))
ENABLE_TABLE_HEADER_AI = os.getenv("ENABLE_TABLE_HEADER_AI", "0") == "1"
ENABLE_LLM_RETRY = os.getenv("ENABLE_LLM_RETRY", "0") == "1"
ENABLE_EXPORT_BULLETS_AI = os.getenv("ENABLE_EXPORT_BULLETS_AI", "1") == "1"
ENABLE_EXPORT_COMPARISON_AI = os.getenv("ENABLE_EXPORT_COMPARISON_AI", "0") == "1"
ENABLE_EXPORT_ONE_IDEA_AI = os.getenv("ENABLE_EXPORT_ONE_IDEA_AI", "1") == "1"
ENABLE_LLM_HARMONIZE = os.getenv("ENABLE_LLM_HARMONIZE", "1") == "1"
ENABLE_LLM_QUALITY_ENHANCER = os.getenv("ENABLE_LLM_QUALITY_ENHANCER", "1") == "1"
QUALITY_MAX_RETRIES = int(os.getenv("QUALITY_MAX_RETRIES", "2"))

MIN_SLIDES = 13
MAX_SLIDES = 15


logger = logging.getLogger(__name__)


def _attach_deck_id(slides: List[Dict[str, Any]], *, deck_id: str | None) -> List[Dict[str, Any]]:
    try:
        if isinstance(slides, list) and slides and isinstance(slides[0], dict):
            did = str(deck_id or "").strip() or uuid.uuid4().hex
            if did:
                slides[0]["_deck_id"] = did
    except Exception:
        pass
    return slides


def choose_slide_count(
    topic: str,
    user_requested_count: int | None = None,
    content_signal: str | None = None,
) -> int:
    """Choose a safe slide count within [MIN_SLIDES..MAX_SLIDES].

    - If user_requested_count provided: clamp it.
    - Else: infer from topic/content length via simple heuristics.
    """
    try:
        req = int(user_requested_count or 0)
    except Exception:
        req = 0

    if req > 0:
        return int(max(MIN_SLIDES, min(MAX_SLIDES, req)))

    base = (content_signal or topic or "").strip()
    # Word-count heuristic (no deps); maps to 13/14/15 under current bounds
    words = [w for w in base.replace("\n", " ").split(" ") if w.strip()]
    wc = len(words)
    # Extra signal: how many line breaks / separators suggests multi-part request
    lines = [ln.strip() for ln in (content_signal or "").splitlines() if ln.strip()]
    lc = len(lines)
    score = wc + (lc * 8)

    if score <= 80:
        return int(MIN_SLIDES)
    if score <= 160:
        return int(max(MIN_SLIDES, 12))
    if score <= 240:
        return int(max(MIN_SLIDES, 13))
    if score <= 330:
        return int(max(MIN_SLIDES, 14))
    return int(MAX_SLIDES)


def quadrant_evidence_detector(text: str) -> tuple[bool, str]:
    """Strictly detect when a 2x2 matrix is explicitly implied."""
    import re

    t = re.sub(r"\s+", " ", str(text or "").strip()).lower()
    if not t:
        return False, "empty"

    # Explicit matrix language
    explicit = [
        "2x2",
        "two by two",
        "quadrant",
        "top-left",
        "top right",
        "bottom-left",
        "bottom right",
        "high impact",
        "low impact",
        "high effort",
        "low effort",
        "high likelihood",
        "low likelihood",
        "risk vs",
        "impact vs",
        "effort vs",
        "priority matrix",
        "prioritization matrix",
    ]
    if any(k in t for k in explicit):
        return True, "explicit_quadrant_language"

    # Structured cues: mentions both axes high/low + axis names
    has_high_low = ("high" in t and "low" in t)
    axis_terms = ["impact", "effort", "ease", "likelihood", "risk", "reward", "urgency", "value"]
    axis_n = sum(1 for a in axis_terms if a in t)
    if has_high_low and axis_n >= 2:
        return True, "axis_high_low_cues"

    return False, "no_quadrant_evidence"


def swot_evidence_detector(text: str) -> tuple[bool, str]:
    """Detect when a SWOT slide is explicitly relevant."""
    import re

    t = re.sub(r"\s+", " ", str(text or "").strip()).lower()
    if not t:
        return False, "empty"

    if "swot" in t or "swot analysis" in t:
        return True, "explicit_swot"

    # If the user mentions the quadrants explicitly, allow SWOT.
    keys = ["strength", "weakness", "opportunit", "threat"]
    hits = sum(1 for k in keys if k in t)
    if hits >= 3:
        return True, "quadrant_terms"

    # Common phrasing: strengths + weaknesses together.
    if ("strength" in t and "weakness" in t) and ("analysis" in t or "assessment" in t or "matrix" in t):
        return True, "strengths_weaknesses_analysis"

    return False, "no_swot_evidence"


def kpi_evidence_detector(topic: str, text: str) -> tuple[bool, str]:
    import re

    blob = re.sub(r"\s+", " ", f"{topic or ''} {text or ''}".strip()).lower()
    if not blob:
        return False, "empty"

    explicit = [
        "kpi",
        "key metrics",
        "metrics",
        "dashboard",
        "scorecard",
        "measure",
        "performance",
        "benchmark",
        "target",
        "roi",
        "adoption rate",
        "cycle time",
        "cost-to-serve",
        "conversion",
        "churn",
        "uptime",
        "sla",
        "throughput",
        "data quality",
    ]
    if any(k in blob for k in explicit):
        return True, "kpi_terms_present"

    return False, "no_kpi_evidence"


def _normalize_kpi_metrics(metrics: Any) -> List[Dict[str, Any]]:
    if not isinstance(metrics, list):
        return []

    out: List[Dict[str, Any]] = []
    for raw in metrics:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or raw.get("name") or raw.get("metric") or "").strip()
        value = str(raw.get("value") or raw.get("display_value") or "").strip()
        if not label or not value:
            continue

        m: Dict[str, Any] = {
            "label": label[:90],
            "value": value[:36],
        }
        caption = str(raw.get("caption") or raw.get("description") or "").strip()
        basis = str(raw.get("basis") or raw.get("comparison") or raw.get("vs") or "").strip()
        period = str(raw.get("period") or raw.get("timeframe") or raw.get("reporting_period") or "").strip()
        scope = str(raw.get("scope") or raw.get("population_scope") or raw.get("geography") or raw.get("region") or "").strip()
        trend = str(raw.get("trend") or "").strip().lower()
        progress = raw.get("progress")

        if caption:
            m["caption"] = caption[:180]
        if basis:
            m["basis"] = basis[:100]
        if period:
            m["period"] = period[:60]
        if scope:
            m["scope"] = scope[:32]
        if trend in {"up", "down", "flat", "increase", "decrease", "improved", "lower", "higher"}:
            m["trend"] = trend
        if progress is not None:
            try:
                m["progress"] = float(progress)
            except Exception:
                pass

        out.append(m)
        if len(out) >= 4:
            break
    return out


def _fetch_managed_kpi_metrics(*, topic: str, input_text: str, deck_id: str | None, user_id: str | None) -> List[Dict[str, Any]]:
    url = str(settings.MANAGED_KPI_API_URL or "").strip()
    if not url:
        return []

    timeout_s = int(getattr(settings, "MANAGED_KPI_API_TIMEOUT_SECONDS", 12) or 12)
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    api_key = str(getattr(settings, "MANAGED_KPI_API_KEY", "") or "").strip()
    auth_header = str(getattr(settings, "MANAGED_KPI_API_AUTH_HEADER", "Authorization") or "Authorization").strip() or "Authorization"
    if api_key:
        if auth_header.lower() == "authorization" and not api_key.lower().startswith("bearer "):
            headers[auth_header] = f"Bearer {api_key}"
        else:
            headers[auth_header] = api_key

    payload = {
        "topic": str(topic or "").strip(),
        "input_text": str(input_text or "").strip(),
        "deck_id": str(deck_id or "").strip(),
        "user_id": str(user_id or "").strip(),
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout_s)
        if resp.status_code >= 400:
            return []
        obj = resp.json()
    except Exception:
        return []

    metrics: Any = None
    if isinstance(obj, dict):
        metrics = obj.get("metrics")
        if not isinstance(metrics, list):
            data_obj = obj.get("data") if isinstance(obj.get("data"), dict) else {}
            metrics = data_obj.get("metrics") or obj.get("kpis") or data_obj.get("kpis")
    elif isinstance(obj, list):
        metrics = obj

    return _normalize_kpi_metrics(metrics)


def _kpi_to_infographic_fallback(slide: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    s = dict(slide or {})
    title = str(s.get("title") or "Key Metrics").strip() or "Key Metrics"
    s["type"] = "infographic"
    s["_fallback_from"] = s.get("_fallback_from") or "kpi"
    s["_fallback_to"] = "infographic"
    s["_fallback_reason"] = reason
    c = s.get("content") if isinstance(s.get("content"), dict) else {}
    bullets = c.get("bullets") if isinstance(c.get("bullets"), list) else []
    if not bullets:
        bullets = [
            f"{title}: KPI data is currently unavailable from the managed KPI API.",
            "Include KPI-focused topic language to request metrics automatically.",
            "Retry generation after KPI API data is refreshed.",
        ]
    s["content"] = {"bullets": [str(x) for x in bullets if str(x).strip()][:6]}
    return s


def _pricing_to_infographic_fallback(slide: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    s = dict(slide or {})
    title = str(s.get("title") or "Pricing Overview").strip() or "Pricing Overview"
    s["type"] = "infographic"
    s["_fallback_from"] = s.get("_fallback_from") or "pricing"
    s["_fallback_to"] = "infographic"
    s["_fallback_reason"] = reason

    c = s.get("content") if isinstance(s.get("content"), dict) else {}
    bullets = c.get("bullets") if isinstance(c.get("bullets"), list) else []
    if not bullets:
        plans = c.get("plans") if isinstance(c.get("plans"), list) else []
        derived: List[str] = []
        for p in plans[:4]:
            if not isinstance(p, dict):
                continue
            nm = str(p.get("name") or "").strip()
            pr = str(p.get("price") or "").strip()
            if nm and pr:
                derived.append(f"{nm}: {pr}")
            elif nm:
                derived.append(nm)
            elif pr:
                derived.append(pr)
        bullets = derived
    if not bullets:
        bullets = [
            f"{title}: pricing slide type is disabled.",
            "Use comparison or infographic to explain options and tradeoffs.",
            "Include qualitative differences and selection criteria.",
        ]
    s["content"] = {"bullets": [str(x) for x in bullets if str(x).strip()][:6]}
    return s


def _apply_disabled_pricing_policy(slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    arr = list(slides or [])
    if not arr:
        return arr

    out: List[Dict[str, Any]] = []
    for s in arr:
        if isinstance(s, dict) and str(s.get("type") or "").strip().lower() == "pricing":
            out.append(_pricing_to_infographic_fallback(s, reason="pricing_disabled"))
        else:
            out.append(s)
    return out


def _apply_managed_kpi_policy(
    slides: List[Dict[str, Any]],
    *,
    topic: str,
    input_text: str,
    deck_id: str | None,
    user_id: str | None,
) -> List[Dict[str, Any]]:
    arr = list(slides or [])
    if not arr:
        return arr

    has_kpi = any(isinstance(s, dict) and str(s.get("type") or "").strip().lower() == "kpi" for s in arr)
    if not has_kpi:
        return arr

    # KPI slide type is disabled for generation. Convert any KPI slide to infographic.
    out_disabled: List[Dict[str, Any]] = []
    for s in arr:
        if isinstance(s, dict) and str(s.get("type") or "").strip().lower() == "kpi":
            out_disabled.append(_kpi_to_infographic_fallback(s, reason="kpi_disabled"))
        else:
            out_disabled.append(s)
    return out_disabled

    allow_kpi, _ = kpi_evidence_detector(topic or "", input_text or "")
    if not allow_kpi:
        out: List[Dict[str, Any]] = []
        for s in arr:
            if isinstance(s, dict) and str(s.get("type") or "").strip().lower() == "kpi":
                out.append(_kpi_to_infographic_fallback(s, reason="kpi_topic_not_relevant"))
            else:
                out.append(s)
        return out

    metrics = _fetch_managed_kpi_metrics(topic=topic, input_text=input_text, deck_id=deck_id, user_id=user_id)
    if len(metrics) < 2:
        out2: List[Dict[str, Any]] = []
        for s in arr:
            if isinstance(s, dict) and str(s.get("type") or "").strip().lower() == "kpi":
                out2.append(_kpi_to_infographic_fallback(s, reason="kpi_api_no_data"))
            else:
                out2.append(s)
        return out2

    out3: List[Dict[str, Any]] = []
    for s in arr:
        if not (isinstance(s, dict) and str(s.get("type") or "").strip().lower() == "kpi"):
            out3.append(s)
            continue
        ss = dict(s)
        content = ss.get("content") if isinstance(ss.get("content"), dict) else {}
        content = dict(content)
        content["metrics"] = metrics
        if not str(content.get("subtitle") or "").strip():
            content["subtitle"] = "Metrics sourced from managed KPI API."
        ss["content"] = content
        out3.append(ss)
    return out3


def build_slide_type_plan(
    *,
    topic: str,
    input_text: str,
    slide_count: int,
    theme_name: str,
) -> tuple[List[str], Dict[str, Any]]:
    """Create a deterministic slide-type plan with coverage + rotation.

    Returns (plan, meta).
    """
    import hashlib

    n = int(max(MIN_SLIDES, min(MAX_SLIDES, slide_count)))
    allow_matrix, matrix_reason = quadrant_evidence_detector(input_text)
    allow_swot, swot_reason = swot_evidence_detector(f"{topic}\n{input_text}")

    # Weekly rotation (deterministic for same topic+week)
    try:
        week_sig = datetime.utcnow().strftime("%G-W%V")
    except Exception:
        week_sig = ""
    seed_src = f"{(topic or '').strip()}|{(theme_name or '').strip()}|{week_sig}".encode("utf-8")
    seed_hex = hashlib.sha256(seed_src).hexdigest()
    seed_int = int(seed_hex[:8], 16)

    # Python's random is deterministic given a seed; safe for planning.
    import random

    rng = random.Random(seed_int)

    # Required skeleton
    plan: List[str] = ["title", "agenda"]

    required_body: List[str] = ["one_idea"]

    # Ensure at least one timeline/process and one comparison in every deck
    required_body.append("timeline")
    required_body.append("comparison")
    if allow_matrix:
        required_body.append("impact_effort_matrix")
    required_body.append("double_column")
    required_body.append("infographic")
    required_body.append("key_takeaways")

    # Optional pool (rotated)
    optional = [
        "cards",
        "risk_matrix",
        "before_after",
        "architecture",
        "roadmap",
    ]
    if allow_swot:
        optional.append("swot")

    rng.shuffle(optional)

    body_slots = max(0, n - 3)  # excluding title/agenda/thank_you
    body: List[str] = []
    for t in required_body:
        if len(body) < body_slots:
            body.append(t)

    for t in optional:
        if len(body) >= body_slots:
            break
        if t not in body:
            body.append(t)

    # Fill any remaining slots by cycling optional types (still deterministic)
    if len(body) < body_slots:
        cycle = list(optional) if optional else ["double_column", "infographic", "cards"]
        if cycle:
            rng.shuffle(cycle)
        i = 0
        while len(body) < body_slots and i < 50:
            cand = cycle[i % len(cycle)]
            # avoid long runs
            if len(body) >= 2 and body[-1] == cand and body[-2] == cand:
                i += 1
                continue
            body.append(cand)
            i += 1

    # No long runs of same type
    for i in range(1, len(body)):
        if body[i] == body[i - 1]:
            # find a later element to swap
            for j in range(i + 1, len(body)):
                if body[j] != body[i]:
                    body[i], body[j] = body[j], body[i]
                    break

    # Closing
    plan = plan + body + ["thank_you"]

    # Distinct-type coverage guardrail
    distinct = len({x for x in plan if x})
    distinct_target = 7 if n <= 10 else (9 if n >= 15 else 8)
    if distinct < distinct_target:
        # Opportunistically replace some duplicates with unused optional types
        extra = ["kpi", "before_after", "cards"] + (["swot"] if allow_swot else [])
        unused = [t for t in (optional + extra) if t not in plan]
        for k in range(2, len(plan) - 1):
            if distinct >= distinct_target or not unused:
                break
            if plan[k] in {"double_column", "infographic"}:
                plan[k] = unused.pop(0)
                distinct = len({x for x in plan if x})

    meta = {
        "allow_matrix": bool(allow_matrix),
        "matrix_reason": matrix_reason,
        "allow_swot": bool(allow_swot),
        "swot_reason": swot_reason,
        "seed": seed_hex[:12],
        "distinct_types": len({x for x in plan if x}),
    }
    if len(plan) < n:
        plan = (plan + ["double_column"] * (n - len(plan)))[:n]
    return plan[:n], meta


def enforce_slide_count_postprocess(
    *,
    client: Any,
    system_msg: str,
    deck_title: str,
    input_text: str,
    slides: List[Dict[str, Any]],
    slide_type_plan: List[str],
    chosen_n: int,
    deck_id: str | None = None,
    user_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Safety net: ensure final deck length equals chosen_n.

    - If too short: ask LLM to generate ONLY the missing slides for remaining plan types.
    - If too long: trim extras safely (never drop title/agenda/thank_you).
    """
    want = int(max(MIN_SLIDES, min(MAX_SLIDES, chosen_n)))
    arr = list(slides or [])
    plan = list(slide_type_plan or [])[:want]

    if len(arr) > want:
        # Keep required anchors
        title_sl = arr[0:1]
        agenda_sl = arr[1:2] if len(arr) >= 2 else []
        one_idea_sl: List[Dict[str, Any]] = []
        body_start = 2
        if len(arr) >= 3 and isinstance(arr[2], dict) and str(arr[2].get("type") or "").strip().lower() == "one_idea":
            one_idea_sl = arr[2:3]
            body_start = 3
        thanks = [s for s in arr if isinstance(s, dict) and str(s.get("type") or "").strip().lower() in {"thank_you", "cta"}]
        thanks_sl = thanks[-1:] if thanks else []
        body = [s for s in arr[body_start:] if s not in thanks_sl]

        # Prefer keeping key_takeaways if present
        keep: List[Dict[str, Any]] = []
        used_types: set[str] = set()
        for s in body:
            if not isinstance(s, dict):
                continue
            t = str(s.get("type") or "").strip().lower()
            if t and t not in used_types:
                keep.append(s)
                used_types.add(t)
        # If still not enough, append remaining in order
        for s in body:
            if len(keep) >= max(0, want - len(title_sl) - len(agenda_sl) - len(thanks_sl)):
                break
            if isinstance(s, dict) and s not in keep:
                keep.append(s)

        prefix = title_sl + agenda_sl + one_idea_sl
        trimmed = (prefix + keep)[: max(0, want - len(thanks_sl))] + thanks_sl
        return trimmed[:want]

    if len(arr) < want:
        def _local_fill_slide(stype: str, idx: int) -> Dict[str, Any]:
            t = str(stype or "infographic").strip().lower() or "infographic"
            topic = str(deck_title or input_text or "Presentation").strip() or "Presentation"
            short_topic = " ".join(topic.split()[:6]).strip() or "Presentation"
            ttl = f"{short_topic} — {idx}"

            if t == "title":
                return {"type": "title", "title": topic, "subtitle": "", "content": {}}
            if t == "agenda":
                return {
                    "type": "agenda",
                    "title": "Agenda",
                    "content": {"bullets": [
                        "Context and objectives",
                        "Current state",
                        "Key insights",
                        "Recommendations",
                        "Next steps",
                    ]},
                }
            if t in {"thank_you", "cta"}:
                return {"type": "thank_you", "title": "Thank You", "content": {}}
            if t in {"cards", "card", "card_style", "process_timeline_alt"}:
                return {"type": "cards", "title": ttl, "content": {"cards": [
                    "Define goal",
                    "Assess baseline",
                    "Execute actions",
                    "Track outcomes",
                ]}}
            if t in {"double_column", "deep_dive"}:
                return {
                    "type": "double_column",
                    "title": ttl,
                    "content": {
                        "left_points": ["Current challenge", "Root causes", "Constraints", "Dependencies"],
                        "right_points": ["Target state", "Action plan", "Owners and timing", "Success metrics"],
                    },
                }
            if t in {"comparison", "table"}:
                return {
                    "type": "comparison",
                    "title": ttl,
                    "content": {
                        "table": {
                            "left": {"heading": "Current", "points": ["Manual", "Slower", "Higher variance"]},
                            "right": {"heading": "Target", "points": ["Automated", "Faster", "More consistent"]},
                        }
                    },
                }
            if t == "timeline":
                return {
                    "type": "timeline",
                    "title": ttl,
                    "content": {
                        "timeline": [
                            {"label": "Phase 1", "description": "Define scope"},
                            {"label": "Phase 2", "description": "Prepare rollout"},
                            {"label": "Phase 3", "description": "Execute"},
                            {"label": "Phase 4", "description": "Optimize"},
                        ]
                    },
                }
            if t == "roadmap":
                return {
                    "type": "roadmap",
                    "title": ttl,
                    "content": {
                        "phases": [
                            {"label": "Q1", "description": "Foundations"},
                            {"label": "Q2", "description": "Pilot"},
                            {"label": "Q3", "description": "Scale"},
                            {"label": "Q4", "description": "Refine"},
                        ]
                    },
                }
            if t == "swot":
                return {
                    "type": "swot",
                    "title": ttl,
                    "content": {
                        "strengths": {"points": ["Differentiated value", "Execution capability"]},
                        "weaknesses": {"points": ["Resource constraints", "Coverage gaps"]},
                        "opportunities": {"points": ["Market demand", "Strategic partnerships"]},
                        "threats": {"points": ["Competitive pressure", "Regulatory change"]},
                    },
                }
            if t == "kpi":
                return {
                    "type": "kpi",
                    "title": ttl,
                    "content": {
                        "metrics": [
                            {"label": "Adoption", "value": "42%", "caption": "Current utilization"},
                            {"label": "Efficiency", "value": "+18%", "caption": "Operational uplift"},
                        ]
                    },
                }
            if t == "risk_matrix":
                return {
                    "type": "risk_matrix",
                    "title": ttl,
                    "content": {
                        "low_likelihood_low_impact": {"points": ["Minor delays"]},
                        "low_likelihood_high_impact": {"points": ["Vendor outage"]},
                        "high_likelihood_low_impact": {"points": ["Scope changes"]},
                        "high_likelihood_high_impact": {"points": ["Resource shortfall"]},
                    },
                }
            if t == "before_after":
                return {
                    "type": "before_after",
                    "title": ttl,
                    "content": {
                        "before": {"heading": "Before", "points": ["Fragmented process", "Long cycle time", "Manual tracking"]},
                        "after": {"heading": "After", "points": ["Standardized workflow", "Faster delivery", "Automated tracking"]},
                    },
                }
            if t == "architecture":
                return {
                    "type": "architecture",
                    "title": ttl,
                    "content": {
                        "boxes": ["Input", "Processing", "Control", "Output"],
                        "connections": ["Input -> Processing", "Processing -> Output"],
                    },
                }
            if t == "pricing":
                return {
                    "type": "infographic",
                    "title": ttl,
                    "content": {
                        "bullets": [
                            "Option A: baseline scope and support",
                            "Option B: expanded capability and service levels",
                            "Tradeoffs: cost, speed, and operational complexity",
                            "Selection criteria aligned to team priorities",
                        ]
                    },
                }
            if t in {"impact_effort_matrix", "impact_matrix", "prioritization_matrix"}:
                return {
                    "type": "impact_effort_matrix",
                    "title": ttl,
                    "content": {
                        "quick_easy_wins": ["Automate repetitive tasks"],
                        "major_projects": ["Platform modernization"],
                        "fill_in": ["Improve documentation"],
                        "thankless_tasks": ["Manual reconciliation"],
                    },
                }
            if t in {"one_idea", "infographic", "key_takeaways", "bullets"}:
                return {
                    "type": "infographic" if t == "bullets" else t,
                    "title": ttl,
                    "content": {"bullets": [
                        "Define the objective and scope",
                        "Highlight critical insights",
                        "Prioritize actions and ownership",
                        "Track results and iterate",
                    ]},
                }
            return {
                "type": "infographic",
                "title": ttl,
                "content": {"bullets": [
                    "Define objective",
                    "Assess current state",
                    "Implement actions",
                    "Measure impact",
                ]},
            }

        missing_k = want - len(arr)
        # Remaining plan types for the missing indices
        remaining_types = plan[len(arr) : len(arr) + missing_k]
        if not remaining_types:
            remaining_types = ["double_column"] * missing_k

        payload = {
            "task": "Generate additional slides to reach the required slide count.",
            "deck_title": (deck_title or "").strip(),
            "input_text": (input_text or "").strip(),
            "existing_slides": arr,
            "missing_slide_types": remaining_types,
            "rules": [
                "Return ONLY a JSON array of slide objects (no prose).",
                "Generate exactly len(missing_slide_types) slides.",
                "Slide types must follow missing_slide_types in order.",
                "Do NOT repeat placeholder titles like 'Slide 1'.",
                "Align to the agenda and deck topic; keep executive-ready density.",
                "Do NOT output impact_effort_matrix unless explicitly implied by input_text.",
            ],
        }
        try:
            resp = client.chat.completions.create(
                model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": _json_dump(payload)},
                ],
                temperature=0.2,
                max_tokens=MAX_TOKENS_RETRY,
            )
            _log_llm_call(
                resp=resp,
                model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
                route="slide_count_fill",
                deck_id=deck_id,
                export_id=deck_id,
                user_id=user_id,
            )
            raw = (resp.choices[0].message.content or "").strip()
            more, err = _parse_json_array(raw)
            if not err and isinstance(more, list) and more:
                arr2 = arr + more[:missing_k]
                if len(arr2) >= want:
                    return arr2[:want]
                arr = arr2
        except Exception:
            pass
        still_missing = want - len(arr)
        if still_missing > 0:
            backfill_types = plan[len(arr) : len(arr) + still_missing]
            if len(backfill_types) < still_missing:
                backfill_types = backfill_types + ["infographic"] * (still_missing - len(backfill_types))
            for i, st in enumerate(backfill_types, start=1):
                arr.append(_local_fill_slide(st, len(arr) + i))
            return arr[:want]
    return arr[:want]


def _enforce_matrix_gating(slides: List[Dict[str, Any]], *, allow_matrix: bool, reason: str) -> List[Dict[str, Any]]:
    """If matrix is disallowed, convert any impact_effort_matrix slides to infographic."""
    import re

    if allow_matrix:
        return slides
    out: List[Dict[str, Any]] = []
    for s in slides or []:
        if not isinstance(s, dict):
            continue
        t = str(s.get("type") or "").strip().lower()
        if t != "impact_effort_matrix":
            out.append(s)
            continue
        c = s.get("content")
        content = c if isinstance(c, dict) else {}
        # Flatten quadrants into bullets
        bullets: List[str] = []
        for k in ("quick_easy_wins", "major_projects", "fill_in", "thankless_tasks"):
            v = content.get(k) or []
            if isinstance(v, list):
                for x in v[:3]:
                    txt = re.sub(r"\s+", " ", str(x or "").strip())
                    if txt:
                        bullets.append(txt)
        if len(bullets) < 3:
            # Fallback to whatever bullets might exist
            b2 = content.get("bullets")
            if isinstance(b2, list):
                bullets += [str(x).strip() for x in b2 if str(x).strip()]
        out.append({
            "type": "infographic",
            "title": s.get("title") or "Priorities",
            "content": {"bullets": bullets[:6]},
            "_matrix_disallowed": True,
            "_matrix_reason": reason,
        })
    return out


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
    chosen_n = choose_slide_count(title or input_text or "", user_requested_count=num_slides, content_signal=input_text)
    slide_type_plan, plan_meta = build_slide_type_plan(
        topic=title or "",
        input_text=input_text or "",
        slide_count=chosen_n,
        theme_name=theme_name or "",
    )
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
            "requested_slide_count": int(chosen_n),
            "slide_type_plan": slide_type_plan,
            "slide_type_plan_meta": plan_meta,
        },
        "structure_rules": {
            "adaptive_allocation": "Always allocate 3 core slides (Title, Agenda, Thank You). Use remaining (N - 3) for modules in priority order; avoid repetition; maintain narrative flow.",
            "core_slides": [
                "Title Slide: includes title, optional subtitle, presenter, date",
                "Agenda Slide: bullets must reflect the exact body slide plan (one bullet per body slide).",
                "Thank You / Q&A Slide: closing message, optional contact/CTA",
            ],
            "modules_priority": [
                "Timeline / Roadmap",
                "Process / Steps (Cards)",
                "Tabular Comparison",
                "SWOT / Risk / Before-After",
                "KPIs / Metrics",
                "Architecture / System Diagram",
                "Prioritization Matrix (Impact vs Effort) when explicitly requested or when the goal is to prioritize initiatives",
                "Double Column Bullet (2–3 variations max)",
                "Infographic / Visual Summary",
                "Key Takeaways / Call to Action",
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
                            "one_idea",
                            "bullets",
                            "cards",
                            "double_column",
                            "comparison",
                            "table",
                            "timeline",
                            "roadmap",
                            "swot",
                            "kpi",
                            "risk_matrix",
                            "before_after",
                            "architecture",
                            "infographic",
                            "key_takeaways",
                            "impact_effort_matrix",
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
                            "cards": {"type": "array", "items": {"type": "string"}},
                            "quick_easy_wins": {"type": "array", "items": {"type": "string"}},
                            "major_projects": {"type": "array", "items": {"type": "string"}},
                            "fill_in": {"type": "array", "items": {"type": "string"}},
                            "thankless_tasks": {"type": "array", "items": {"type": "string"}},
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
                                        "date_range": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                            "phases": {"type": "array"},
                            "strengths": {"type": "object"},
                            "weaknesses": {"type": "object"},
                            "opportunities": {"type": "object"},
                            "threats": {"type": "object"},
                            "metrics": {"type": "array"},
                            "low_likelihood_low_impact": {"type": "object"},
                            "low_likelihood_high_impact": {"type": "object"},
                            "high_likelihood_low_impact": {"type": "object"},
                            "high_likelihood_high_impact": {"type": "object"},
                            "before": {"type": "object"},
                            "after": {"type": "object"},
                            "boxes": {"type": "array"},
                            "connections": {"type": "array"},
                            "plans": {"type": "array"},
                            "visual_hint": {"type": "string"},
                            "columns": {"type": "object"}
                        },
                    },
                },
            }
        },
        "instructions": [
            "Generate EXACTLY N slides where N == requested_slide_count. Do NOT add or remove slides.",
            "You MUST follow presentation.slide_type_plan exactly: slide i must have type slide_type_plan[i] (0-indexed).",
            "Core slides must be positions 1 (title), 2 (agenda), and last (thank_you).",
            "Title must be concise, compelling, and derived from the input_text. Do NOT repeat the input verbatim; rewrite as a strong presentation title. NEVER use generic titles like 'Generated Presentation', 'Presentation', or 'Overview'.",
            "Agenda: YOU must create the agenda (outline) from the topic/input_text; do NOT ask the user for an outline.",
            "Agenda MUST have exactly (N - 3) bullets (one per body slide). Do NOT output fewer or more.",
            "Use agenda bullet i (0-indexed) to map to body slide i+2 (since slides[0]=title, slides[1]=agenda). Use exactly one slide per agenda bullet.",
            "Agenda bullets must be topic-specific and must NOT be generic labels like 'Overview', 'Discussion', or slide-type names.",
            "Matrix gating: Only use type impact_effort_matrix if input_text explicitly implies a 2x2 quadrant prioritization matrix (impact vs effort). If slide_type_plan contains impact_effort_matrix, you must populate all four quadrants with 3-5 items each. If slide_type_plan does NOT contain impact_effort_matrix, you must NOT output it.",
            "Bullets must be non-duplicative, 4–14 words, informative, and TOPIC-SPECIFIC. Avoid generic boilerplate such as 'leveraging X to improve KPIs'.",
            "Write executive-ready content: specific mechanisms, impacts, and tradeoffs. Avoid vague claims (e.g., 'critical', 'accelerated', 'important') unless you add a short 'because/so-what' clause.",
            "For slides after the Agenda, prefer rich visual types (cards, comparison/table, infographic, timeline, roadmap, swot, risk_matrix, before_after, architecture) over plain bullets.",
            "Use type 'bullets' ONLY for the Agenda slide and as a last-resort fallback when no other slide type fits.",
            "COHERENCE & ORDER: All slides after the agenda MUST map to the agenda bullets in sequence (1 slide per agenda bullet; do not group multiple slides under one bullet).",
            "TITLE PARAPHRASING: Each mapped slide's TITLE must closely paraphrase its agenda bullet (share key terms); do not use generic labels.",
            "CONTENT ALIGNMENT: Each mapped slide's CONTENT (bullets/points/table cells) must elaborate the mapped agenda bullet and share key terms.",
            "NO PLACEHOLDERS: Slide titles and subtitles MUST NOT be placeholders or defaults (e.g., 'Timeline', 'Roadmap', 'Process', 'Overview', 'Write your subtitle here', 'Click to add...').",
            "CONTENT-DERIVED TITLES: For every non-core slide (not title/agenda/thank_you), write the slide.title using concrete terms taken from that slide's content fields (e.g., timeline labels, card headings, table headers, architecture box labels).",
            "CONTENT-DERIVED SUBTITLES: If you provide slide.subtitle, it must be a single-line summary derived from that slide's content fields (not generic marketing language).",
            "Architecture slides: the slide.title MUST be topic-specific and derived from the boxes/flow (3–7 words). Do NOT use generic titles like 'Architecture Diagram' or 'System Architecture' without a qualifier.",
            "Timeline/Process/Roadmap slides: you MUST include a NON-EMPTY slide.subtitle as a one-line, topic-specific summary derived from the steps/phases (8–14 words). Prefer phrasing like 'From <first label> to <last label>' or an outcome-oriented summary that includes at least ONE step/phase label.",
            "BAN: Do NOT use marketing filler subtitles such as 'Make a big impact...', 'professional slides', 'our templates', 'best practices', or anything about slides/charts. Subtitles must describe the CONTENT of the slide.",
            "Do not produce slides that are not linked to an agenda item.",
            "Ensure the Thank You slide is always the final slide.",
            "If N is small, prefer infographic/cards/comparison over multiple bullet variations.",
            "Double column slides should use left_points and right_points; 6+ items per column when possible.",
            "Tabular Comparison must be CONTEXTUAL to the topic in input_text. Headings should reflect the actual comparison dimension (e.g., 'Pre-COVID' vs 'During/After COVID', 'Developed Markets' vs 'Emerging Markets'). NEVER use 'Traditional vs AI-Driven' unless the topic is explicitly about AI approaches.",
            "For any 'Key Concepts' or concept-focused slide, provide 6–8 distinct topic-derived terms with concise, non-repetitive definitions. Derive terms from the topic and agenda; avoid umbrella words (e.g., 'Overview', 'Concepts'). Do NOT echo the slide title in the bullet text. Do NOT repeat the same template phrase across bullets.",
            "HARD BANS: Do NOT output placeholder content such as headings 'Indicator A', 'Indicator B', or cell values like 'Point 1', 'Point 2', etc. Replace all such placeholders with specific, topic-derived headings and items.",
            "HARD BANS: Do NOT use placeholder entities like 'Company A', 'Company B', 'Company C', 'Org 1', 'Product X', or similar. If you include case studies/examples, make them concrete and topic-relevant (industry/context + what changed + measurable result).",
            "Infographic uses a concise visual_hint plus a few bullets if helpful.",
            "Key Takeaways: 4–6 takeaways; each must include a short supporting clause (use '—' or ':'), and at least half should be actionable (what to do next).",
            "Thank You: include optional contact info or CTA in bullets.",
            "Cards: use content.cards as an array of 4-8 short stage labels (process/steps).",
            "Comparison/Table: use content.table with left/right objects each containing heading and points (4-8 points).",
            "Timeline: use content.timeline = [{label, description, date_range?}] with 4-6 items.",
            "Timeline intent: labels must be named phases (not '1', '2', '3'). Each description must explain what changes and why it matters.",
            "Roadmap: use content.phases = [{label, description, date_range?}] with 4-6 items (or content.timeline in the same structure if you already used that key).",
            "SWOT: use content.strengths/weaknesses/opportunities/threats each as {points:[...]} with 2-4 points per quadrant when possible; points must clearly match the quadrant intent.",
            "Do NOT output slides of type 'kpi'. Use infographic or comparison instead when discussing metrics.",
            "Do NOT output slides of type 'pricing'. Use comparison or infographic instead when discussing plans or costs.",
            "Risk Matrix: use content.low_likelihood_low_impact/low_likelihood_high_impact/high_likelihood_low_impact/high_likelihood_high_impact each as {points:[...]} with at least 2 non-empty quadrants.",
            "Before/After: use content.before and content.after each as {heading, points:[...]} with >=3 points per side.",
            "Architecture: use content.boxes=[{label, description?}] (4-8) and content.connections=[{from_box,to_box,label?}] referencing existing box labels.",
            "Variety: avoid using the same slide type twice in a row. Use at most 2 double_column slides total.",
            "If (and ONLY if) the user explicitly requests an 'impact vs effort' matrix / 'effort vs impact' matrix / 'prioritization matrix' (2x2) OR the input_text clearly asks to prioritize initiatives/features by impact and effort, include exactly one slide of type 'impact_effort_matrix' and populate content arrays: quick_easy_wins, major_projects, fill_in, thankless_tasks (3–5 items each).",
            "Do NOT use 'impact_effort_matrix' for feature comparison tables or option comparisons. For comparisons, use type 'comparison' or 'table' and provide a real two-column comparison.",
            "Do NOT represent an impact/effort prioritization matrix as 'cards' or 'infographic'. When doing impact vs effort, you MUST use type 'impact_effort_matrix' with the four quadrant arrays.",
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


def _compute_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    try:
        # Find most recent effective pricing for this model
        row = (
            Pricing.query
            .filter(Pricing.model_id == model_id)
            .order_by(Pricing.effective_from.desc())
            .first()
        )
        if not row:
            return 0.0
        return (prompt_tokens/1000.0) * float(row.input_per_1k) + (completion_tokens/1000.0) * float(row.output_per_1k)
    except Exception:
        return 0.0


def _log_llm_call(*, resp: Any, model: str, route: str, deck_id: str | None = None, export_id: str | None = None, user_id: str | None = None) -> None:
    try:
        usage = getattr(resp, 'usage', None) or {}
        prompt_tokens = int(getattr(usage, 'prompt_tokens', getattr(usage, 'input_tokens', 0)) or 0)
        completion_tokens = int(getattr(usage, 'completion_tokens', getattr(usage, 'output_tokens', 0)) or 0)
        total_tokens = int(getattr(usage, 'total_tokens', prompt_tokens + completion_tokens) or 0)
        request_id = getattr(resp, 'id', None) or getattr(resp, 'request_id', None) or ''
        cost = _compute_cost(model, prompt_tokens, completion_tokens)
        rec = LLMCall(
            deck_id=deck_id,
            export_id=export_id or deck_id,
            user_id=user_id,
            route=route,
            model=model,
            request_id=str(request_id),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            created_at=datetime.utcnow(),
        )
        db.session.add(rec)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def create_deck_slides(
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

    chosen_n = choose_slide_count(title or input_text or "", user_requested_count=(num_slides if (num_slides or 0) > 0 else None), content_signal=input_text)
    slide_type_plan, plan_meta = build_slide_type_plan(
        topic=title or "",
        input_text=input_text or "",
        slide_count=chosen_n,
        theme_name=theme_name or "",
    )
    if os.getenv("SLIDE_DEBUG", "0") == "1":
        try:
            logger.info(
                "slide_debug:plan chosen_n=%s distinct=%s allow_matrix=%s reason=%s plan=%s",
                chosen_n,
                plan_meta.get("distinct_types"),
                plan_meta.get("allow_matrix"),
                plan_meta.get("matrix_reason"),
                slide_type_plan,
            )
        except Exception:
            pass

    messages = build_prompt(
        title=title,
        subtitle=subtitle,
        presenter=presenter,
        date=date,
        theme_name=theme_name,
        input_text=input_text,
        num_slides=chosen_n,
    )

    try:
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=messages,  # type: ignore[arg-type]
            temperature=0.2,
            max_tokens=MAX_TOKENS_DECK,
        )
        _log_llm_call(resp=resp, model=settings.CHATGPT_MODEL or "gpt-3.5-turbo", route="generate", deck_id=deck_id, export_id=deck_id, user_id=user_id)
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

    if os.getenv("SLIDE_DEBUG", "0") == "1":
        try:
            import logging
            log = logging.getLogger(__name__)
            for i, s in enumerate(slides, start=1):
                if not isinstance(s, dict):
                    log.info("slide_debug:ai_parsed idx=%s non_dict=%s", i, type(s).__name__)
                    continue
                t = str(s.get("type") or "").strip().lower() or "(missing)"
                title = str(s.get("title") or "").strip()
                c = s.get("content")
                keys = sorted(list(c.keys())) if isinstance(c, dict) else (["<list>"] if isinstance(c, list) else (["<str>"] if isinstance(c, str) else []))
                log.info(
                    "slide_debug:ai_parsed idx=%s title=%r type=%s content_keys=%s _render_exact=%s",
                    i,
                    title,
                    t,
                    keys,
                    bool(s.get("_render_exact")),
                )
        except Exception:
            pass
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
        tmp = _ai_rewrite_table_headers(client, slides)
        slides = tmp
    except Exception:
        pass
    # Post-pass: harmonize titles/content to agenda; remove template phrases
    try:
        slides = _harmonize_titles_to_agenda(slides)
    except Exception:
        pass

    try:
        slides = _clamp_slide_content(slides)
    except Exception:
        pass

    try:
        slides = _apply_managed_kpi_policy(
            slides,
            topic=title or "",
            input_text=input_text or "",
            deck_id=deck_id,
            user_id=user_id,
        )
    except Exception:
        pass

    try:
        slides = _apply_disabled_pricing_policy(slides)
    except Exception:
        pass

    try:
        slides = _sync_agenda_to_slide_titles(slides)
    except Exception:
        pass

    try:
        q_issues = _content_quality_issues(slides)
    except Exception:
        q_issues = []

    if q_issues and ENABLE_LLM_QUALITY_ENHANCER:
        try:
            retries = max(0, min(2, int(QUALITY_MAX_RETRIES or 0)))
        except Exception:
            retries = 2
        for _attempt in range(retries):
            try:
                slides2 = _ai_enhance_content_quality(
                    client=client,
                    system_msg=messages[0]["content"],
                    deck_title=title,
                    input_text=input_text,
                    slides=slides,
                    issues=q_issues,
                    deck_id=deck_id,
                    user_id=user_id,
                )
                if slides2:
                    slides = slides2
                try:
                    slides = _harmonize_titles_to_agenda(slides)
                except Exception:
                    pass
                try:
                    slides = _clamp_slide_content(slides)
                except Exception:
                    pass
                q_issues = _content_quality_issues(slides)
                if not q_issues:
                    break
            except Exception:
                break

    # Safety net: enforce matrix gating and slide count before coherency retry.
    try:
        slides = _enforce_matrix_gating(
            slides,
            allow_matrix=bool(plan_meta.get("allow_matrix")),
            reason=str(plan_meta.get("matrix_reason") or ""),
        )
    except Exception:
        pass
    try:
        slides = enforce_slide_count_postprocess(
            client=client,
            system_msg=messages[0]["content"],
            deck_title=title,
            input_text=input_text,
            slides=slides,
            slide_type_plan=slide_type_plan,
            chosen_n=chosen_n,
            deck_id=deck_id,
            user_id=user_id,
        )
    except Exception:
        pass
    try:
        slides = _clamp_slide_content(slides)
    except Exception:
        pass

    try:
        slides = _apply_managed_kpi_policy(
            slides,
            topic=title or "",
            input_text=input_text or "",
            deck_id=deck_id,
            user_id=user_id,
        )
    except Exception:
        pass

    try:
        slides = _apply_disabled_pricing_policy(slides)
    except Exception:
        pass

    if os.getenv("SLIDE_DEBUG", "0") == "1":
        try:
            types = []
            for s in (slides or [])[:32]:
                if isinstance(s, dict):
                    types.append(str(s.get("type") or "").strip().lower() or "(missing)")
            logger.info("slide_debug:final chosen_n=%s final_n=%s types=%s", chosen_n, len(slides or []), types)
        except Exception:
            pass

    if os.getenv("SLIDE_DEBUG", "0") == "1":
        try:
            import logging
            log = logging.getLogger(__name__)
            for i, s in enumerate(slides, start=1):
                if not isinstance(s, dict):
                    log.info("slide_debug:ai_post idx=%s non_dict=%s", i, type(s).__name__)
                    continue
                t = str(s.get("type") or "").strip().lower() or "(missing)"
                title = str(s.get("title") or "").strip()
                c = s.get("content")
                keys = sorted(list(c.keys())) if isinstance(c, dict) else (["<list>"] if isinstance(c, list) else (["<str>"] if isinstance(c, str) else []))
                log.info(
                    "slide_debug:ai_post idx=%s title=%r type=%s content_keys=%s _render_exact=%s",
                    i,
                    title,
                    t,
                    keys,
                    bool(s.get("_render_exact")),
                )
        except Exception:
            pass
    # Post-check quality and coherency; perform one corrective retry if enabled
    issues, agenda = _quality_issues(slides)
    # Post-pass: AI harmonization of titles/bullets to agenda and topic
    if issues and ENABLE_LLM_HARMONIZE:
        try:
            slides = _ai_harmonize_to_agenda(
                client=client,
                system_msg=messages[0]["content"],
                title=title,
                input_text=input_text,
                slides=slides,
            )
            try:
                slides = _harmonize_titles_to_agenda(slides)
            except Exception:
                pass
            issues, agenda = _quality_issues(slides)
        except Exception:
            pass
    if issues and ENABLE_LLM_RETRY:
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
                num_slides=chosen_n,
                agenda_bullets=agenda,
                issues=issues,
            )
            if fixed:
                try:
                    fixed = _enforce_matrix_gating(
                        fixed,
                        allow_matrix=bool(plan_meta.get("allow_matrix")),
                        reason=str(plan_meta.get("matrix_reason") or ""),
                    )
                except Exception:
                    pass
                try:
                    fixed = enforce_slide_count_postprocess(
                        client=client,
                        system_msg=messages[0]["content"],
                        deck_title=title,
                        input_text=input_text,
                        slides=fixed,
                        slide_type_plan=slide_type_plan,
                        chosen_n=chosen_n,
                        deck_id=deck_id,
                        user_id=user_id,
                    )
                except Exception:
                    pass
                try:
                    fixed = _apply_managed_kpi_policy(
                        fixed,
                        topic=title or "",
                        input_text=input_text or "",
                        deck_id=deck_id,
                        user_id=user_id,
                    )
                except Exception:
                    pass
                try:
                    fixed = _apply_disabled_pricing_policy(fixed)
                except Exception:
                    pass
                fixed = _attach_deck_id(fixed, deck_id=deck_id)
                return fixed, []
        except Exception as exc:  # noqa: BLE001
            return slides, [f"Retry warning: {exc}"]
    slides = _attach_deck_id(slides, deck_id=deck_id)
    return slides, []


def _clamp_slide_content(slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    import re

    def _clamp_words(text: str, *, max_words: int) -> str:
        t = re.sub(r"\s+", " ", str(text or "").strip())
        if not t:
            return ""
        parts = t.split(" ")
        if len(parts) <= max_words:
            return t
        cut = " ".join(parts[:max_words]).rstrip(",.;:- ")
        return (cut + "…") if cut else t

    def _clamp_list(lst: Any, *, max_n: int, max_words: int) -> List[str]:
        if not isinstance(lst, list):
            return []
        out: List[str] = []
        for x in lst:
            s = _clamp_words(str(x or ""), max_words=max_words)
            if s:
                out.append(s)
            if len(out) >= max_n:
                break
        return out

    def _ctype(sl: Dict[str, Any]) -> str:
        return str(sl.get("type") or "").strip().lower()

    out_slides: List[Dict[str, Any]] = []
    for s in (slides or []):
        if not isinstance(s, dict):
            continue
        ss = dict(s)
        t = _ctype(ss)
        c = ss.get("content")
        if not isinstance(c, dict):
            c = {}
        if isinstance(c.get("bullets"), list):
            c["bullets"] = _clamp_list(c.get("bullets"), max_n=10, max_words=24)
        if isinstance(c.get("cards"), list):
            c["cards"] = _clamp_list(c.get("cards"), max_n=8, max_words=10)
        if isinstance(c.get("left_points"), list):
            c["left_points"] = _clamp_list(c.get("left_points"), max_n=8, max_words=18)
        if isinstance(c.get("right_points"), list):
            c["right_points"] = _clamp_list(c.get("right_points"), max_n=8, max_words=18)

        if t in {"timeline", "roadmap"}:
            tl = c.get("timeline")
            if isinstance(tl, list):
                new_tl: List[Dict[str, str]] = []
                for it in tl[:6]:
                    if isinstance(it, dict):
                        lab = _clamp_words(str(it.get("label") or it.get("title") or ""), max_words=7)
                        desc = _clamp_words(str(it.get("description") or ""), max_words=22)
                        dr = _clamp_words(str(it.get("date_range") or ""), max_words=6)
                        obj: Dict[str, str] = {"label": lab, "description": desc}
                        if dr:
                            obj["date_range"] = dr
                        if lab or desc:
                            new_tl.append(obj)
                    else:
                        txt = _clamp_words(str(it or ""), max_words=22)
                        if txt:
                            new_tl.append({"label": txt[:40], "description": ""})
                if new_tl:
                    c["timeline"] = new_tl
            ph = c.get("phases")
            if isinstance(ph, list):
                new_ph: List[Dict[str, str]] = []
                for it in ph[:6]:
                    if isinstance(it, dict):
                        lab = _clamp_words(str(it.get("label") or it.get("title") or ""), max_words=7)
                        desc = _clamp_words(str(it.get("description") or ""), max_words=22)
                        dr = _clamp_words(str(it.get("date_range") or ""), max_words=6)
                        obj2: Dict[str, str] = {"label": lab, "description": desc}
                        if dr:
                            obj2["date_range"] = dr
                        if lab or desc:
                            new_ph.append(obj2)
                if new_ph:
                    c["phases"] = new_ph

        if t == "swot":
            for k in ("strengths", "weaknesses", "opportunities", "threats"):
                q = c.get(k)
                if isinstance(q, dict) and isinstance(q.get("points"), list):
                    q2 = dict(q)
                    q2["points"] = _clamp_list(q2.get("points"), max_n=6, max_words=18)
                    c[k] = q2

        ss["content"] = c
        out_slides.append(ss)
    return out_slides


def _sync_agenda_to_slide_titles(slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure the Agenda reflects the final body slide titles.

    The generator sometimes outputs an agenda that doesn't match the final deck length
    after post-processing (slide count enforcement / gating / fallbacks). This pass
    rewrites agenda bullets to equal the set of body slide titles in order.
    """

    def _ctype(sl: Dict[str, Any]) -> str:
        return str(sl.get("type") or "").strip().lower()

    arr = [s for s in (slides or []) if isinstance(s, dict)]
    if len(arr) < 3:
        return slides

    agenda_idx = next((i for i, s in enumerate(arr) if _ctype(s) == "agenda"), None)
    if agenda_idx is None:
        return slides

    def _is_skip(t: str) -> bool:
        return t in {"title", "agenda", "thank_you"}

    body_titles: List[str] = []
    for s in arr:
        t = _ctype(s)
        if _is_skip(t):
            continue
        title = str(s.get("title") or "").strip()
        if title:
            body_titles.append(title)

    if not body_titles:
        return slides

    ag = dict(arr[agenda_idx])
    c = ag.get("content")
    c2 = dict(c) if isinstance(c, dict) else {}
    c2["bullets"] = body_titles
    ag["content"] = c2
    arr[agenda_idx] = ag
    return arr


def _content_quality_issues(slides: List[Dict[str, Any]]) -> List[str]:
    import re

    def _ctype(sl: Dict[str, Any]) -> str:
        return str(sl.get("type") or "").strip().lower()

    def _content(sl: Dict[str, Any]) -> Dict[str, Any]:
        c = sl.get("content")
        return c if isinstance(c, dict) else {}

    def _as_list(v: Any) -> List[str]:
        if not isinstance(v, list):
            return []
        out: List[str] = []
        for x in v:
            s = re.sub(r"\s+", " ", str(x or "").strip())
            if s:
                out.append(s)
        return out

    def _is_numeric_label(s: str) -> bool:
        s2 = str(s or "").strip()
        return bool(re.fullmatch(r"\d+", s2))

    def _looks_step_placeholder(s: str) -> bool:
        low = re.sub(r"\s+", " ", str(s or "").strip()).lower()
        if not low:
            return True
        if _is_numeric_label(low):
            return True
        if re.fullmatch(r"step\s*\d+", low):
            return True
        if re.fullmatch(r"phase\s*\d+", low):
            return True
        if re.fullmatch(r"stage\s*\d+", low):
            return True
        return False

    def _is_generic_line(line: str) -> bool:
        low = re.sub(r"\s+", " ", str(line or "").strip()).lower()
        if not low:
            return True
        wc = len(re.findall(r"[a-zA-Z]+", low))
        if wc <= 3:
            return True
        if ("—" not in low) and (":" not in low) and wc <= 6:
            if any(w in low for w in ["critical", "important", "significant", "accelerat", "key", "uneven"]):
                return True
        if any(w in low for w in ["leverage", "optimize", "synergy", "streamline", "enhance", "drive kpi", "improve kpi"]):
            return True
        return False

    issues: List[str] = []

    for idx, sl in enumerate(slides or [], start=1):
        if not isinstance(sl, dict):
            continue
        t = _ctype(sl)
        c = _content(sl)

        if t in {"title", "agenda", "thank_you", "cta"}:
            continue

        if t == "timeline":
            tl = c.get("timeline")
            if not isinstance(tl, list) or len(tl) < 4:
                issues.append(f"Slide {idx}: timeline requires 4–6 phases")
            else:
                bad = 0
                for it in tl[:6]:
                    if isinstance(it, dict):
                        lab = str(it.get("label") or it.get("title") or "").strip()
                        desc = str(it.get("description") or "").strip()
                        if not lab or _is_numeric_label(lab):
                            bad += 1
                        if not desc or len(re.findall(r"[a-zA-Z]+", desc)) < 4:
                            bad += 1
                    else:
                        bad += 1
                if bad >= 2:
                    issues.append(f"Slide {idx}: timeline phases need named labels and descriptions")

        if t == "key_takeaways":
            bullets = _as_list(c.get("bullets"))
            if len(bullets) < 4:
                issues.append(f"Slide {idx}: key_takeaways needs 4–6 takeaways")
            else:
                no_support = sum(1 for b in bullets if ("—" not in b and ":" not in b))
                if no_support > max(1, int(len(bullets) * 0.5)):
                    issues.append(f"Slide {idx}: key_takeaways bullets need a supporting clause (— or :)")
                gen = sum(1 for b in bullets if _is_generic_line(b))
                if gen > max(1, int(len(bullets) * 0.4)):
                    issues.append(f"Slide {idx}: key_takeaways too generic")

        if t == "swot":
            for k in ("strengths", "weaknesses", "opportunities", "threats"):
                q = c.get(k)
                pts: List[str] = []
                if isinstance(q, dict):
                    pts = _as_list(q.get("points"))
                if len(pts) < 3:
                    issues.append(f"Slide {idx}: swot quadrant '{k}' needs 3–6 bullets")

        if t in {"bullets", "infographic"}:
            bullets = _as_list(c.get("bullets"))
            if len(bullets) < 3:
                issues.append(f"Slide {idx}: list slide is too scanty")
            else:
                gen = sum(1 for b in bullets if _is_generic_line(b))
                if gen > max(1, int(len(bullets) * 0.4)):
                    issues.append(f"Slide {idx}: bullets are too generic")
                # Timeline placeholders sometimes appear as numbered lists
                stepish = sum(1 for b in bullets if _looks_step_placeholder(b))
                if stepish >= max(2, int(len(bullets) * 0.6)) and ("timeline" in (str(sl.get("title") or "").lower()) or "phase" in " ".join(bullets).lower()):
                    issues.append(f"Slide {idx}: list slide looks like a placeholder timeline")

        if t in {"cards", "card", "card_style", "process_timeline_alt"}:
            cards = _as_list(c.get("cards"))
            if len(cards) < 4:
                issues.append(f"Slide {idx}: cards slide needs 4–8 steps")
            else:
                placeholder = sum(1 for x in cards if _looks_step_placeholder(x))
                if placeholder >= max(2, int(len(cards) * 0.6)):
                    issues.append(f"Slide {idx}: cards contain placeholder step labels")

        if t in {"double_column", "deep_dive"}:
            lp = _as_list(c.get("left_points"))
            rp = _as_list(c.get("right_points"))
            if (len(lp) + len(rp)) < 6:
                issues.append(f"Slide {idx}: double_column should have richer density")

        if t in {"comparison", "table"}:
            tbl = c.get("table")
            left_pts: List[str] = []
            right_pts: List[str] = []
            if isinstance(tbl, dict):
                left = tbl.get("left") if isinstance(tbl.get("left"), dict) else {}
                right = tbl.get("right") if isinstance(tbl.get("right"), dict) else {}
                left_pts = _as_list((left or {}).get("points"))
                right_pts = _as_list((right or {}).get("points"))
            if (len(left_pts) + len(right_pts)) < 4:
                issues.append(f"Slide {idx}: comparison/table needs 4–8 paired points")

    return issues


def _ai_enhance_content_quality(
    *,
    client: Any,
    system_msg: str,
    deck_title: str,
    input_text: str,
    slides: List[Dict[str, Any]],
    issues: List[str],
    deck_id: str | None = None,
    user_id: str | None = None,
) -> List[Dict[str, Any]] | None:
    import json

    sys = system_msg or "You are a senior presentation editor. Output ONLY valid JSON; no prose."
    payload = {
        "task": "Improve content quality to be executive-ready while keeping the deck structure unchanged.",
        "deck_title": (deck_title or "").strip(),
        "input_text": (input_text or "").strip(),
        "issues": list(issues or []),
        "rules": [
            "Return ONLY a JSON array of slides.",
            "Keep the same number of slides, in the same order.",
            "Do NOT change slide types. For each index i, output slide[i].type exactly as the input slide[i].type.",
            "Do NOT add or remove slides.",
            "Improve density and specificity; remove placeholder content.",
            "Avoid generic consultant-speak; be concrete and topic-specific.",
            "Timeline: 4–6 phases with named labels (not numbers) and a short description explaining what changes and why it matters.",
            "Key Takeaways: 4–6 items; each with a supporting clause using '—' or ':'; at least half actionable.",
            "SWOT: each quadrant has 2–4 bullets; ensure bullets match quadrant intent.",
            "List slides: 4–7 bullets typical; avoid 1–2 bullet slides unless it's title/closing.",
            "Bullets must be informative and specific; if you use 'critical/accelerated/important', add a 'because/so-what' clause.",
            "Keep bullets concise (roughly 8–22 words).",
        ],
        "slides": slides,
    }
    try:
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": _json_dump(payload)},
            ],
            temperature=0.2,
            max_tokens=MAX_TOKENS_QUALITY,
        )
        _log_llm_call(
            resp=resp,
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            route="quality_enhancer",
            deck_id=deck_id,
            export_id=deck_id,
            user_id=user_id,
        )
        raw = (resp.choices[0].message.content or "").strip()
        edited, err = _parse_json_array(raw)
        if err or not isinstance(edited, list):
            return None
        if len(edited) != len(slides):
            return None
        for i in range(min(len(edited), len(slides))):
            a = edited[i] if isinstance(edited[i], dict) else {}
            b = slides[i] if isinstance(slides[i], dict) else {}
            if str(a.get("type") or "").strip().lower() != str(b.get("type") or "").strip().lower():
                return None
        return edited
    except Exception:
        return None


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


def derive_topic_bullets(*, deck_title: str, topic: str, input_text: str | None = None, n: int = 5, deck_id: str | None = None, user_id: str | None = None) -> List[str]:
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
            "Each bullet must have two parts: a short headline and a short explainer, separated by an em dash (—) or a colon (:).",
            "Example format: 'AI automates repetitive tasks — improves operational efficiency for teams.'",
            "Headline (before the dash/colon) should be 3–8 words and stand alone as the main idea.",
            "Explainer (after the dash/colon) should be 1 short sentence (or clause) expanding that idea in simple language.",
            "Total length per bullet (headline + explainer) should stay roughly within 8–22 words.",
            "Avoid template phrases and placeholders (e.g., 'Definition and scope', 'Point 1').",
            "Avoid repeating the topic title text verbatim.",
            "Do NOT mention slides, decks, or bullet points; describe the idea itself.",
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
            max_tokens=MAX_TOKENS_TOPIC_BULLETS,
        )
        _log_llm_call(resp=resp, model=settings.CHATGPT_MODEL or "gpt-3.5-turbo", route="derive_topic_bullets", deck_id=deck_id, export_id=deck_id, user_id=user_id)
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


def derive_topic_comparison_table(*, deck_title: str, topic: str, input_text: str | None = None, bullets: List[str] | None = None, deck_id: str | None = None, user_id: str | None = None) -> Dict[str, Any] | None:
    """Ask ChatGPT to generate a paired comparison table for a topic.

    Returns a dict with keys left_heading, right_heading, left_points, right_points,
    or None on failure.
    """
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return None

    api_key = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not api_key:
        return None
    client = OpenAI(api_key=api_key)
    payload = {
        "task": "Design a two-column comparison table with paired rows for a presentation slide.",
        "deck_title": deck_title,
        "topic": topic,
        "context": (input_text or ""),
        "existing_points": list(bullets or []),
        "output_format": {
            "type": "object",
            "properties": {
                "left_heading": {"type": "string"},
                "right_heading": {"type": "string"},
                "left_points": {"type": "array", "items": {"type": "string"}},
                "right_points": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["left_heading", "right_heading", "left_points", "right_points"],
        },
        "rules": [
            "Return ONLY a JSON object matching output_format. No prose, no markdown.",
            "Each row's left and right entries must be logically related (e.g., challenge vs corresponding impact, dimension A vs dimension B).",
            "4–6 rows total is ideal.",
            "Do not use placeholder text like 'Point 1' or generic labels.",
        ],
    }
    try:
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You output ONLY a compact JSON object; no extra text."},
                {"role": "user", "content": _json_dump(payload)},
            ],
            temperature=0.2,
            max_tokens=MAX_TOKENS_EXPORT_COMPARISON,
        )
        _log_llm_call(resp=resp, model=settings.CHATGPT_MODEL or "gpt-3.5-turbo", route="derive_topic_comparison_table", deck_id=deck_id, export_id=deck_id, user_id=user_id)
        import json
        raw = (resp.choices[0].message.content or "").strip()
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return None
        left_h = str(obj.get("left_heading") or "").strip()
        right_h = str(obj.get("right_heading") or "").strip()
        left_pts_raw = obj.get("left_points") or []
        right_pts_raw = obj.get("right_points") or []
        if not isinstance(left_pts_raw, list) or not isinstance(right_pts_raw, list):
            return None
        left_points: List[str] = []
        right_points: List[str] = []
        for x in left_pts_raw:
            s = str(x or "").strip()
            if s:
                left_points.append(s)
            if len(left_points) >= 6:
                break
        for x in right_pts_raw:
            s = str(x or "").strip()
            if s:
                right_points.append(s)
            if len(right_points) >= 6:
                break
        if not left_h or not right_h or not left_points or not right_points:
            return None
        return {
            "left_heading": left_h,
            "right_heading": right_h,
            "left_points": left_points,
            "right_points": right_points,
        }
    except Exception:
        return None


def derive_one_idea_intro(
    *,
    deck_title: str,
    agenda_topics: List[str] | None = None,
    input_text: str | None = None,
    deck_id: str | None = None,
    user_id: str | None = None,
) -> Dict[str, str] | None:
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return None

    api_key = settings.CHATGPT_API_TOKEN or settings.OPENAI_API_KEY
    if not api_key:
        return None

    client = OpenAI(api_key=api_key)
    payload = {
        "task": "Write a one-idea introductory slide from a deck title.",
        "deck_title": (deck_title or "").strip(),
        "agenda_topics": list(agenda_topics or []),
        "context": (input_text or "").strip(),
        "rules": [
            "Return ONLY a JSON object with keys 'headline' and 'explainer'.",
            "Headline: a single clear statement of the core idea; at most 16 words.",
            "Explainer: 1–2 sentences in simple language, at most 45 words total.",
            "Do not mention the slide, deck, presentation, or bullets.",
            "Describe the topic itself and its impact, not what the slide will cover.",
            "No lists, numbering, or emojis.",
        ],
    }
    try:
        resp = client.chat.completions.create(
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You output ONLY a compact JSON object with keys 'headline' and 'explainer'; no prose or markdown.",
                },
                {"role": "user", "content": _json_dump(payload)},
            ],
            temperature=0.25,
            max_tokens=MAX_TOKENS_ONE_IDEA_INTRO,
        )
        _log_llm_call(
            resp=resp,
            model=settings.CHATGPT_MODEL or "gpt-3.5-turbo",
            route="derive_one_idea_intro",
            deck_id=deck_id,
            export_id=deck_id,
            user_id=user_id,
        )
        import json
        import re

        raw = (resp.choices[0].message.content or "").strip()
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return None
        headline = str(obj.get("headline") or "").strip()
        explainer = str(obj.get("explainer") or "").strip()
        if not headline and not explainer:
            return None
        if explainer:
            parts = re.split(r"(?<=[.!?])\s+", explainer.strip())
            parts = [p for p in parts if p]
            explainer = " ".join(parts[:2]).strip()
            if len(explainer) > 260:
                e_trim = explainer[:260]
                explainer = (e_trim.rsplit(" ", 1)[0] or e_trim).rstrip(",.;:- ") + "…"
        if headline and len(headline) > 140:
            h_trim = headline[:140]
            headline = (h_trim.rsplit(" ", 1)[0] or h_trim).rstrip(",.;:- ")
        return {"headline": headline, "explainer": explainer}
    except Exception:
        return None


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

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", str(s or "").strip())

    def _overlap(a: str, b: str) -> int:
        stop = {"the","of","and","to","a","in","on","for","vs","vs.","an"}
        aw = [w.lower() for w in re.findall(r"[a-zA-Z]+", a) if w.lower() not in stop]
        bw = [w.lower() for w in re.findall(r"[a-zA-Z]+", b) if w.lower() not in stop]
        return len(set(aw) & set(bw))

    generic_titles = {
        "overview",
        "key concepts",
        "concepts",
        "a vs b",
        "comparison",
        "generated presentation",
        "timeline",
        "timeline infographic",
        "timeline overview",
        "roadmap",
        "roadmap overview",
        "process",
        "steps",
        "phases",
        "architecture diagram",
        "system architecture",
        "kpis",
        "kpi",
        "metrics",
    }
    template_substrings = [
        "brief explanation to frame relevance",
        "with measurable kpis and owners",
        "key concepts — leveraging",
        "(to be refined)",
        "lorem ipsum",
        "placeholder",
        "generic outline",
    ]

    subtitle_placeholders = [
        "write your subtitle here",
        "click to add",
        "subtitle",
        "(to be refined)",
        "placeholder",
    ]

    subtitle_banned_substrings = [
        "make a big impact",
        "professional slides",
        "slides and charts",
        "slides & charts",
        "our templates",
        "presentation templates",
    ]

    def _is_bad_title(title: str) -> bool:
        low = _norm(title).lower()
        if not low:
            return True
        if low in generic_titles:
            return True
        if re.fullmatch(r"(timeline|roadmap|process|steps|phases?)", low):
            return True
        if "write your" in low or "click to add" in low:
            return True
        if re.fullmatch(r"(slide|slide \d+)", low):
            return True
        return False

    def _is_bad_subtitle(sub: str) -> bool:
        low = _norm(sub).lower()
        if not low:
            return True
        if any(p in low for p in subtitle_placeholders):
            return True
        if any(p in low for p in subtitle_banned_substrings):
            return True
        if len(re.findall(r"[a-zA-Z]+", low)) < 4:
            return True
        return False

    def _pick_labels(sl: Dict[str, Any]) -> List[str]:
        c = sl.get("content") if isinstance(sl.get("content"), dict) else {}
        st = str(sl.get("type") or "").strip().lower()
        labs: List[str] = []
        if st in {"timeline", "roadmap"}:
            raw = c.get("timeline") or c.get("phases")
            if isinstance(raw, list):
                for it in raw[:8]:
                    if isinstance(it, dict):
                        lab = _norm(it.get("label") or it.get("title") or "")
                        if lab:
                            labs.append(lab)
                    else:
                        s0 = _norm(it)
                        if s0:
                            labs.append(s0)
        elif st in {"cards", "card", "card_style", "process_timeline_alt"}:
            raw = c.get("cards")
            if isinstance(raw, list):
                for it in raw[:8]:
                    if isinstance(it, dict):
                        lab = _norm(it.get("heading") or it.get("label") or it.get("title") or it.get("name") or it.get("step") or "")
                        if lab:
                            labs.append(lab)
                            continue
                        desc = _norm(it.get("description") or it.get("desc") or it.get("details") or it.get("text") or "")
                        if desc:
                            labs.append(desc)
                            continue
                    s0 = _norm(it)
                    if s0:
                        labs.append(s0)
        elif st in {"comparison", "table"}:
            tbl = c.get("table") if isinstance(c.get("table"), dict) else {}
            left = tbl.get("left") if isinstance(tbl.get("left"), dict) else {}
            right = tbl.get("right") if isinstance(tbl.get("right"), dict) else {}
            lh = _norm(left.get("heading") or "")
            rh = _norm(right.get("heading") or "")
            if lh:
                labs.append(lh)
            if rh:
                labs.append(rh)
        elif st == "architecture":
            boxes = c.get("boxes")
            if isinstance(boxes, list):
                for b in boxes[:8]:
                    if isinstance(b, dict):
                        lab = _norm(b.get("label") or "")
                        if lab:
                            labs.append(lab)
        return [x for x in labs if x]

    def _limit_words(s: str, max_words: int) -> str:
        parts = [p for p in re.split(r"\s+", _norm(s)) if p]
        if len(parts) <= max_words:
            return " ".join(parts)
        return " ".join(parts[:max_words])

    def _derive_title(sl: Dict[str, Any], agenda_fallback: str) -> str:
        st = str(sl.get("type") or "").strip().lower()
        labs = _pick_labels(sl)
        if st in {"timeline", "roadmap"} and len(labs) >= 2:
            return _limit_words(f"From {labs[0]} to {labs[-1]}", 7)
        if st in {"cards", "card", "card_style", "process_timeline_alt"} and len(labs) >= 2:
            return _limit_words(f"Key Steps: {labs[0]} to {labs[-1]}", 7)
        if st in {"comparison", "table"} and len(labs) >= 2:
            return _limit_words(f"{labs[0]} vs {labs[1]}", 7)
        if st == "architecture" and len(labs) >= 2:
            return _limit_words(f"{labs[0]} to {labs[-1]}", 7)
        return _limit_words(agenda_fallback, 8) or agenda_fallback

    def _derive_subtitle(sl: Dict[str, Any]) -> str:
        st = str(sl.get("type") or "").strip().lower()
        labs = _pick_labels(sl)
        n = len(labs)
        if st in {"timeline", "roadmap"} and n >= 2:
            return _limit_words(f"From {labs[0]} to {labs[-1]} across {max(4, min(6, n))} phases with clear deliverables", 14)
        if st in {"cards", "card", "card_style", "process_timeline_alt"} and n >= 2:
            return _limit_words(f"From {labs[0]} to {labs[-1]} with owners, outputs, and checkpoints at each step", 14)
        if st in {"comparison", "table"} and n >= 2:
            return _limit_words(f"How {labs[0]} differs from {labs[1]} across impacts, risks, and effort", 14)
        if st == "architecture" and n >= 2:
            return _limit_words(f"How components interact from {labs[0]} through {labs[-1]} across the full flow", 14)
        return ""

    for s in slides:
        stype = s.get("type")
        if stype in {"title","agenda","thank_you"}:
            # Clean agenda bullets of template phrases
            if stype == "agenda":
                bullets = s.get("content", {}).get("bullets", []) or []
                clean = [b for b in bullets if not any(p in (b or "").lower() for p in template_substrings)]
                s.setdefault("content", {})["bullets"] = clean
            continue
        if stype in {"impact_effort_matrix", "impact_matrix", "prioritization_matrix"}:
            continue
        title = (s.get("title") or "").strip()
        # Map to best agenda bullet
        best_ov, best_i = max((( _overlap(title, a), i) for i, a in enumerate(agenda)), default=(0, 0))
        if _is_bad_title(title) or best_ov == 0:
            s["title"] = _derive_title(s, agenda[best_i])

        sub0 = _norm(s.get("subtitle") or "")
        if stype in {"timeline", "roadmap", "cards", "card", "card_style", "process_timeline_alt"}:
            if stype in {"timeline", "roadmap"}:
                labs = _pick_labels(s)
                sub_low = sub0.lower()
                has_label_overlap = False
                try:
                    has_label_overlap = any((lab or "").lower() in sub_low for lab in (labs or [])[:6] if str(lab or "").strip())
                except Exception:
                    has_label_overlap = False
                if _is_bad_subtitle(sub0) or (labs and not has_label_overlap and ("from " not in sub_low)):
                    s["subtitle"] = _derive_subtitle(s)
            else:
                labs = _pick_labels(s)
                sub_low = sub0.lower()
                has_label_overlap = False
                try:
                    has_label_overlap = any((lab or "").lower() in sub_low for lab in (labs or [])[:6] if str(lab or "").strip())
                except Exception:
                    has_label_overlap = False
                if _is_bad_subtitle(sub0) or (labs and not has_label_overlap and ("from " not in sub_low)):
                    s["subtitle"] = _derive_subtitle(s)
        else:
            if sub0 and _is_bad_subtitle(sub0):
                s["subtitle"] = ""
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
        max_tokens=MAX_TOKENS_HARMONIZE,
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
        # First try deterministic heuristic pair
        h = _heuristic_pair(title, agenda)
        if h:
            table.setdefault("left", {})["heading"] = h[0]
            table.setdefault("right", {})["heading"] = h[1]
            s["title"] = f"{h[0]} vs {h[1]}"
            content["table"] = table
            s["content"] = content
            changed = True
            continue
        if not ENABLE_TABLE_HEADER_AI:
            continue
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
            max_tokens=MAX_TOKENS_TABLE_HEADERS,
        )
        import json
        try:
            obj = json.loads((resp.choices[0].message.content or "").strip())
            new_left = (obj.get("left") or lhead).strip()
            new_right = (obj.get("right") or rhead).strip()
            if new_left and new_right:
                table.setdefault("left", {})["heading"] = new_left
                table.setdefault("right", {})["heading"] = new_right
                s["title"] = f"{new_left} vs {new_right}"
                content["table"] = table
                s["content"] = content
                changed = True
        except Exception:
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
        max_tokens=MAX_TOKENS_RETRY,
    )
    content = (resp.choices[0].message.content or "").strip()
    slides, err = _parse_json_array(content)
    if err or not isinstance(slides, list):
        return None
    return slides

