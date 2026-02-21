from __future__ import annotations

from typing import Any, Dict, List, Tuple
import os
import logging


logger = logging.getLogger(__name__)


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
    "impact_matrix",
    "prioritization_matrix",
    "thank_you",
    "cta",
    "bullets",
}


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

    return bullets[:12]


def normalize_slide(slide: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(slide or {})
    title = str(s.get("title") or "").strip()
    if not title:
        title = "Slide"
    s["title"] = title

    t = str(s.get("type") or "").strip().lower()
    if t in {"", "text", "basic", "bullet", "bullet_slide", "plain"}:
        t = "bullets"
    if t == "double_col":
        t = "double_column"
    if t == "takeaways":
        t = "key_takeaways"
    s["type"] = t

    bullets = _extract_bullets(s)

    c = s.get("content")
    if not isinstance(c, dict):
        c = {}
    s["content"] = c

    if bullets and "bullets" not in c and t in {"bullets", "infographic", "key_takeaways"}:
        c["bullets"] = bullets

    if t in {"cards", "card", "card_style", "process_timeline_alt"}:
        if "cards" not in c and bullets:
            c["cards"] = bullets[:8]

    if t in {"double_column", "deep_dive"}:
        lp = c.get("left_points")
        rp = c.get("right_points")
        if not isinstance(lp, list) and bullets:
            lp = bullets[::2][:8]
        if not isinstance(rp, list) and bullets:
            rp = bullets[1::2][:8]
        if isinstance(lp, list):
            c["left_points"] = [str(x).strip() for x in lp if str(x).strip()][:8]
        if isinstance(rp, list):
            c["right_points"] = [str(x).strip() for x in rp if str(x).strip()][:8]

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
        if isinstance(lp, list) and isinstance(rp, list) and (len(lp) + len(rp) >= 4):
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
    if double_remaining > 0 and len(bullets) >= 4:
        return "double_column"
    if len(bullets) >= 4:
        return "cards"
    if len(bullets) >= 2:
        return "infographic"
    return "bullets"


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

        norm["_render_exact"] = True
        out.append(norm)

    debug_log_slides(stage, out)
    return out
