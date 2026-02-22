from __future__ import annotations

from typing import Any, Dict, List, Tuple
import os
import logging


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
    return any(p in t for p in _PLACEHOLDER_PHRASES)


def _clean_list_text(items: Any, *, max_n: int | None = None) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for x in items:
        s = str(x or "").strip()
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
    seed = base
    existing2 = [str(x).strip() for x in (existing or []) if str(x).strip() and (not _is_placeholder_text(str(x)))]
    seen = {x.lower() for x in existing2}

    templates = [
        "Definition and scope (what it covers)",
        "Why it matters (impact on outcomes and stakeholders)",
        "Key drivers and contributing factors",
        "Common pitfalls, risks, and constraints",
        "Practical next steps and near-term actions",
        "Metrics to track progress and success",
        "Tradeoffs and decisions to make explicit",
    ]

    # Rotate templates deterministically based on the title.
    start = abs(hash(seed)) % len(templates)
    order = templates[start:] + templates[:start]
    out = list(existing2)
    for tmpl in order:
        if len(out) >= min_n:
            break
        cand = tmpl.format(t=base).strip()
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

    debug_log_slides(stage, out)
    return out
