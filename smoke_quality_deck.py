from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, List


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
    return bool(re.fullmatch(r"\d+", str(s or "").strip()))


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


def _is_meaningful_line(s: str) -> bool:
    low = re.sub(r"\s+", " ", str(s or "").strip()).lower()
    if not low:
        return False
    wc = len(re.findall(r"[a-zA-Z]+", low))
    if wc < 4:
        return False
    if re.fullmatch(r"(?i)point\s*\d+", low):
        return False
    if _looks_step_placeholder(low):
        return False
    return True


def _check_slides(slides: List[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []

    for idx, sl in enumerate(slides or [], start=1):
        if not isinstance(sl, dict):
            warnings.append(f"Slide {idx}: non-dict slide")
            continue

        t = _ctype(sl)
        c = _content(sl)

        if t in {"title", "agenda", "thank_you", "cta"}:
            continue

        if t == "timeline":
            tl = c.get("timeline")
            if not isinstance(tl, list) or len(tl) < 4:
                warnings.append(f"Slide {idx}: timeline has <4 items")
                continue
            for j, it in enumerate(tl[:6], start=1):
                if not isinstance(it, dict):
                    warnings.append(f"Slide {idx}: timeline item {j} not an object")
                    continue
                lab = str(it.get("label") or it.get("title") or "").strip()
                desc = str(it.get("description") or "").strip()
                if not lab or _is_numeric_label(lab):
                    warnings.append(f"Slide {idx}: timeline item {j} label is missing/placeholder")
                if len(re.findall(r"[a-zA-Z]+", desc)) < 4:
                    warnings.append(f"Slide {idx}: timeline item {j} description too short")
            continue

        if t == "key_takeaways":
            bullets = _as_list(c.get("bullets"))
            if len(bullets) < 4:
                warnings.append(f"Slide {idx}: key_takeaways has <4 items")
            no_support = sum(1 for b in bullets if ("—" not in b and ":" not in b))
            if bullets and no_support > max(1, int(len(bullets) * 0.5)):
                warnings.append(f"Slide {idx}: key_takeaways lacks supporting clauses")
            continue

        if t == "swot":
            for k in ("strengths", "weaknesses", "opportunities", "threats"):
                q = c.get(k)
                pts = _as_list((q or {}).get("points")) if isinstance(q, dict) else []
                if len(pts) < 2:
                    warnings.append(f"Slide {idx}: swot quadrant '{k}' has <2 bullets")
            continue

        if t in {"cards", "card", "card_style", "process_timeline_alt"}:
            cards = _as_list(c.get("cards"))
            if len(cards) < 4:
                warnings.append(f"Slide {idx}: cards has <4 items")
            else:
                placeholder = sum(1 for x in cards if _looks_step_placeholder(x))
                if placeholder >= max(2, int(len(cards) * 0.6)):
                    warnings.append(f"Slide {idx}: cards look like placeholder steps")
            continue

        # Generic list density gate
        bullets = _as_list(c.get("bullets"))
        meaningful = [b for b in bullets if _is_meaningful_line(b)]
        if bullets and len(meaningful) < 3:
            warnings.append(f"Slide {idx}: <3 meaningful bullets")

    return warnings


def main() -> int:
    api_key = os.getenv("CHATGPT_API_TOKEN") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("smoke_quality_deck: missing OPENAI_API_KEY/CHATGPT_API_TOKEN; skipping")
        return 2

    from app.services.ai_content import create_deck_slides

    title = "Economic Changes: Executive Briefing"
    topic = (
        "Economic changes in 2024–2026 and implications for businesses. "
        "Include inflation/interest rates, labor markets, supply chains, technology adoption, and policy impacts."
    )

    slides, errors = create_deck_slides(
        title=title,
        subtitle=None,
        presenter=None,
        date=None,
        theme_name="SLEEK",
        input_text=topic,
        num_slides=10,
        deck_id="smoke",
        user_id=None,
    )

    if errors or not slides:
        print("smoke_quality_deck: generation failed", errors)
        return 1

    warnings = _check_slides(slides)
    if warnings:
        for w in warnings:
            print("WARN:", w)
        print(f"smoke_quality_deck: FAILED with {len(warnings)} warnings")
        return 1

    print("smoke_quality_deck: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
