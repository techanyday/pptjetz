from __future__ import annotations

from typing import Any, Dict, List, Tuple
from datetime import date
import json
import os
import random
import re
import ast
import math
import colorsys
import hashlib
from pptx import Presentation  # type: ignore
from pptx.util import Inches, Pt
from .content_synthesis import synthesize_key_concepts, is_placeholder, BANNED
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE, MSO_ANCHOR  # type: ignore
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE as MsoShape, MSO_CONNECTOR_TYPE  # type: ignore
from pptx.enum.dml import MSO_LINE_DASH_STYLE  # type: ignore
from pptx.dml.color import RGBColor  # type: ignore
from .slide6_takeaways_chevrons import render_takeaways_chevrons

SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5

ENABLE_SEMANTIC_CARDS_ROUTER = os.getenv("ENABLE_SEMANTIC_CARDS_ROUTER", "0") == "1"

def _start_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, 1)


def timeline_date_range_labels(*, today: date | None = None, total_months: int = 6, phases: int = 3) -> List[str]:
    t = today or date.today()
    phases = max(1, int(phases or 1))
    total_months = max(phases, int(total_months or 6))
    months_per_phase = max(1, total_months // phases)
    start = _start_of_month(t)
    out: List[str] = []
    for i in range(phases):
        p0 = _add_months(start, i * months_per_phase)
        p1 = _add_months(start, (i + 1) * months_per_phase - 1)
        out.append(f"{p0.strftime('%b %Y')} – {p1.strftime('%b %Y')}")
    return out

# --- Color utilities ---
def _is_dark_rgb(rgb: Tuple[int, int, int]) -> bool:
    r, g, b = rgb
    # Relative luminance approximation
    return (0.2126*r + 0.7152*g + 0.0722*b) < 140

def _clamp(x: int) -> int:
    return max(0, min(255, x))

def _shade(rgb: Tuple[int, int, int], delta: int) -> Tuple[int, int, int]:
    r, g, b = rgb
    return (_clamp(r + delta), _clamp(g + delta), _clamp(b + delta))


def _blend_rgb(a: Tuple[int, int, int], b: Tuple[int, int, int], alpha: float) -> Tuple[int, int, int]:
    aa = max(0.0, min(1.0, float(alpha)))
    r = int(round(a[0] * (1.0 - aa) + b[0] * aa))
    g = int(round(a[1] * (1.0 - aa) + b[1] * aa))
    bb = int(round(a[2] * (1.0 - aa) + b[2] * aa))
    return (_clamp(r), _clamp(g), _clamp(bb))


def _rgb_to_hls(rgb: Tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = rgb
    return colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)


def _hls_to_rgb(h: float, l: float, s: float) -> Tuple[int, int, int]:
    rr, gg, bb = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return (_clamp(int(round(rr * 255))), _clamp(int(round(gg * 255))), _clamp(int(round(bb * 255))))


def _rgb_dist(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    return float(abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]))

# Simple theme palettes
# Each theme maps to: bg, text, primary, secondary
THEME_PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "SLEEK": {
        # Subtle cool gray instead of pure white to match Sleek theme intent
        "bg": (242, 245, 249),
        "text": (33, 37, 41),
        "primary": (41, 98, 255),   # deep blue
        "secondary": (235, 242, 255),
        "muted": (71, 85, 105),
    },
    "PULSE": {
        "bg": (255, 255, 255),
        "text": (24, 24, 27),
        "primary": (220, 38, 38),   # red
        "secondary": (254, 226, 226),
        "muted": (75, 85, 99),
    },
    "MINIMAL FLUX": {
        "bg": (255, 255, 255),
        "text": (31, 41, 55),
        "primary": (99, 102, 241),  # indigo
        "secondary": (238, 242, 255),
        "muted": (71, 85, 105),
    },
    "BLUE HORIZON": {
        "bg": (250, 253, 255),
        "text": (15, 23, 42),
        "primary": (2, 132, 199),   # sky
        "secondary": (219, 234, 254),
        "muted": (71, 85, 105),
    },

    "PRIME_MERIDIAN": {
        "bg": (248, 245, 239),
        "text": (28, 28, 28),
        "primary": (46, 58, 89),
        "secondary": (234, 228, 217),
        "muted": (107, 100, 92),
    },
}

# Global rotating index for summary template selection (round-robin)
SUMMARY_STYLE_INDEX: int = 0
TIMELINE_STYLE_INDEX: int = 0
# Persisted across deck generations so timeline styles rotate between runs
PROCESS_STYLE_INDEX: int = 0
# Rotate color scheme for double-column bullet squares across presentations
DOUBLE_SCHEME_INDEX: int = 0
# Rotate bullet circle color per deck generation (1..5), shared across layouts
BULLET_COLOR_GEN_INDEX: int = 0
# Track if any "takeaways" slide (including at-a-glance) has already been rendered
TAKEAWAYS_RENDERED: bool = False
TAKEAWAYS_STYLE_GEN_INDEX: int = 0
COMPARISON_STYLE_GEN_INDEX: int = 0
IMPACT_MAP_VARIANT_INDEX: int = 0


class VariantRotator:
    def __init__(self) -> None:
        self._seed: str | None = None
        self._family_idx: Dict[str, int] = {}

    def reset(self, seed: str | None = None) -> None:
        self._seed = (str(seed) if seed is not None else None)
        self._family_idx = {}

    def _offset_for_family(self, family: str, pool_len: int) -> int:
        if not pool_len:
            return 0
        if not self._seed:
            return 0
        h = hashlib.md5((self._seed + "|" + str(family)).encode("utf-8"), usedforsecurity=False).hexdigest()
        return int(h[:8], 16) % pool_len

    def choose_variant(self, family: str, pool: List[str], forced: str | None = None) -> str:
        fam = str(family or "").strip() or "default"
        opts = [str(x) for x in (pool or []) if str(x).strip()]
        if not opts:
            return ""

        idx = self._family_idx.get(fam)
        if idx is None:
            idx = self._offset_for_family(fam, len(opts))

        forced_norm = str(forced or "").strip() or None
        if forced_norm and forced_norm in opts:
            chosen = forced_norm
        else:
            chosen = opts[int(idx) % len(opts)]

        self._family_idx[fam] = int(idx) + 1
        return chosen

    def family_index(self, family: str) -> int:
        fam = str(family or "").strip() or "default"
        return int(self._family_idx.get(fam, 0))


VARIANT_ROTATOR = VariantRotator()


def reset_variant_rotation(seed: str | None = None) -> None:
    VARIANT_ROTATOR.reset(seed=seed)


def choose_variant(family: str, pool: List[str], forced: str | None = None) -> str:
    return VARIANT_ROTATOR.choose_variant(family=family, pool=pool, forced=forced)


def _choose_impact_map_variant(*, content: Dict[str, Any] | None, has_roles: bool, index: int) -> str:
    c = content or {}
    raw_variant = str(c.get("impact_map_variant") or c.get("variant") or "").strip().lower()
    variant_map = {
        "classic": "classic",
        "radial": "radial",
        "pyramid": "pyramid",
        "two_sided": "two_sided",
        "two-sided": "two_sided",
        "before_after": "two_sided",
        "before-after": "two_sided",
        "swimlane": "swimlane",
    }
    forced = variant_map.get(raw_variant)

    pool: List[str] = ["classic", "radial", "pyramid", "two_sided"]
    if has_roles:
        pool.append("swimlane")

    if forced and forced in pool:
        return forced
    return pool[int(index) % len(pool)]

def _mark_takeaways_rendered():
    global TAKEAWAYS_RENDERED
    TAKEAWAYS_RENDERED = True


# --- Topic-aware Background/Evolution heuristics ---
def _derive_evolution_stages_for_topic(topic: str, agenda: List[str], intro: str) -> List[str]:
    """Return 3–5 neutral, topic-aware evolution labels.
    Avoid AI-biased labels like 'Manual → Assisted' unless clearly relevant.
    """
    text = " ".join([str(topic or ""), intro or ""] + [str(a) for a in (agenda or [])]).lower()
    # Clear AI/automation contexts
    if any(k in text for k in ["ai", "automation", "automate", "ml", "machine learning"]):
        return ["Manual → Assisted", "Rules-Based → Data-Driven", "Predictive → Adaptive"]
    # Mental health / wellbeing
    if any(k in text for k in ["mental health", "wellbeing", "well-being", "well being", "psycholog", "awareness"]):
        return ["Awareness & Stigma", "Access & Support", "Community & Policy"]
    # Education / training
    if any(k in text for k in ["education", "learning", "curriculum", "training", "school", "students"]):
        return ["Foundational Literacy", "Blended Learning", "Personalized & Inclusive"]
    # Marketing / campaigns
    if any(k in text for k in ["marketing", "campaign", "brand", "growth", "funnel", "channels"]):
        return ["Brand Awareness", "Acquisition", "Retention & Advocacy"]
    # Healthcare / public health
    if any(k in text for k in ["healthcare", "public health", "screening", "care", "clinic"]):
        return ["Screening", "Early Intervention", "Integrated Care"]
    # Policy / government / public sector
    if any(k in text for k in ["policy", "government", "public sector", "regulation", "compliance"]):
        return ["Policy Drafting", "Pilot Programs", "Nationwide Rollout"]
    # COVID / crisis
    if any(k in text for k in ["covid", "pandemic", "lockdown", "recovery", "resilience"]):
        return ["Pre-COVID", "Crisis Response", "Recovery & Resilience"]
    # Regional / market evolution
    if any(k in text for k in ["market", "region", "expansion", "entry"]):
        return ["Entry", "Expansion", "Maturity"]
    # Generic neutral fallback
    return ["Foundational Phase", "Growth & Adoption", "Maturity"]

# Guard: never render titles like "Concept 1/2/..."
TITLE_BANNED_RE = re.compile(r"^\s*concepts?\s*\d+\s*$", re.I)

def _theme(name: str | None) -> Dict[str, Tuple[int, int, int]]:
    key = (name or "SLEEK").strip().upper()
    return THEME_PALETTES.get(key, THEME_PALETTES["SLEEK"])  # default

# Fixed titles per slide index
TITLE_MAP: Dict[int, str] = {
    1: "Title",
    2: "Agenda",
    3: "Background",
    4: "Key Concepts",
    5: "Comparison",
    6: "Key Takeaways",
    7: "Thank You",
}

def title_for(pos: int) -> str:
    return TITLE_MAP.get(pos, f"Slide {pos}")

def slide_heading(pos: int) -> str:
    # Only slide 10 may be Thank You
    return "Thank You" if pos == 10 else title_for(pos)


def _add_textbox(slide, x, y, w, h, text: str, *, font_size_pt: int = 24, bold=False, align="left", color: Tuple[int, int, int] | None = None, word_wrap: bool = False, auto_fit: bool = False):
    left, top, width, height = map(Inches, (x, y, w, h))
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.clear()
    # Optional text behaviors for long text handling
    if word_wrap:
        tf.word_wrap = True
    if auto_fit:
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text or ""
    run.font.size = Pt(font_size_pt)
    run.font.bold = bool(bold)
    if color is not None:
        run.font.color.rgb = RGBColor(*color)
    if align == "center":
        p.alignment = PP_ALIGN.CENTER
    elif align == "right":
        p.alignment = PP_ALIGN.RIGHT
    else:
        p.alignment = PP_ALIGN.LEFT
    return box


def _split_head_tail(txt: str) -> Tuple[str, str]:
    t = (txt or "").strip()
    if not t:
        return "", ""
    import re as _re
    # 1) Prefer explicit separators the AI is instructed to use
    #    (em dash, en dash, colon, semicolon, or a standalone hyphen with spaces).
    #    This avoids splitting on hyphens inside words such as "AI-powered".
    m = _re.search(r"[:;\u2013\u2014]|(?<=\s)-(?!\s*$)(?=\s)", t)
    if m and m.start() > 0:
        head = t[:m.start()].strip()
        # drop the matched separator itself and trim remainder
        tail = t[m.start() + 1 :].lstrip()
        # ignore meaningless tails that are too short
        if tail and len(tail.strip()) >= 3:
            return head, tail

    # 2) Fallback: first 4 words become the headline, remainder the explainer.
    parts = t.split()
    head = " ".join(parts[:4])
    tail = " ".join(parts[4:])
    return head, tail


def _add_bullets(
    slide,
    x,
    y,
    w,
    h,
    bullets: List[str],
    *,
    font_size_pt: int = 20,
    min_items: int = 0,
    synth_fill: str = "TBD",
    color: Tuple[int, int, int] | None = None,
    word_wrap: bool = True,
    auto_fit: bool = False,
    line_spacing: float = 1.45,
    bold_first_phrase: bool = False,
    space_before_pt: int = 2,
    space_after_pt: int = 12,
):
    """Add a bullet list textbox.

    Layout, margins, and geometry are fixed; we only control typography
    and colors. The `color` parameter is used for both bullet markers and
    body text so bullets never fall back to random theme defaults.
    """
    items = [str(s).strip() for s in (bullets or []) if str(s).strip()]
    while len(items) < min_items:
        items.append(synth_fill)

    left, top, width, height = map(Inches, (x, y, w, h))
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.clear()
    if word_wrap:
        tf.word_wrap = True
    if auto_fit:
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE

    first = True
    for txt in items:
        if first:
            p = tf.paragraphs[0]
            first = False
        else:
            p = tf.add_paragraph()
        p.clear()

        # Bullet marker run (glyph only; color set below)
        r0 = p.add_run()
        r0.text = "• "

        # Split into headline + explainer
        head, tail = _split_head_tail(txt)
        headline = head or txt
        explainer = tail

        # Headline on first line (bold)
        r1 = p.add_run()
        r1.text = headline

        # Configure paragraph spacing and font for headline
        p.level = 0
        try:
            p.space_before = Pt(space_before_pt)
            p.space_after = Pt(2)  # tight gap before explainer
            p.line_spacing = line_spacing
        except Exception:
            pass

        # Explainer as a separate paragraph immediately underneath
        if explainer:
            p2 = tf.add_paragraph()
            p2.text = explainer
            p2.level = 1
            try:
                p2.space_before = Pt(0)
                p2.space_after = Pt(space_after_pt)
                p2.line_spacing = line_spacing
            except Exception:
                pass

    # hub-and-spoke side-by-side renderers are defined inside build_pptx_from_slides
    # so they can access prs, blank, pal and helper functions.

    return box


def _add_table(slide, x, y, w, h, rows: int, cols: int):
    left, top, width, height = map(Inches, (x, y, w, h))
    table_shape = slide.shapes.add_table(rows, cols, left, top, width, height)
    return table_shape.table


def _render_circles_row(slide, title: str, labels: List[str], *, pal: Dict[str, Tuple[int, int, int]], y_in: float, circle_d_in: float, margin_in: float, numbered: bool):
    # Title (locked)
    _add_textbox(slide, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=pal["primary"])

    n = min(5, max(4, len(labels)))
    circle_d = circle_d_in
    margin = margin_in
    gap = max(0.3, (SLIDE_W_IN - 2 * margin - n * circle_d) / max(1, (n - 1)))
    y = y_in
    for i, lab in enumerate(labels[:n]):
        x = margin + i * (circle_d + gap)
        shape = slide.shapes.add_shape(MsoShape.OVAL, Inches(x), Inches(y), Inches(circle_d), Inches(circle_d))
        shape.line.color.rgb = RGBColor(*pal["primary"])
        if numbered:
            shape.text_frame.text = str(i + 1)
            for p in shape.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(20)
                    r.font.bold = True
                    r.font.color.rgb = RGBColor(*pal["primary"])
                p.alignment = PP_ALIGN.CENTER


# --- Agenda (Slide 2) Zebra Renderer (Auto-fit with overflow/columns) ---
def _clean_agenda_items(items: List[str], limit: int = 32) -> List[str]:
    seen = set()
    out: List[str] = []
    for s in items or []:
        t = (str(s or "").strip())
        if not t or t.lower() in {"tbd", "-", "•"}:
            continue
        lo = t.lower()
        if lo in seen:
            continue
        seen.add(lo)
        out.append(t)
        if len(out) >= limit:
            break
    return out

def _clean_agenda_items_any(items: List[Any], limit: int = 32) -> List[Any]:
    seen = set()
    out: List[Any] = []
    for it in items or []:
        # Preserve structured agenda items. Some upstream flows can stringify dicts;
        # normalize those back into dicts so title/description extraction is reliable.
        try:
            if isinstance(it, str):
                s = it.strip()
                if s.startswith("{") and ("title" in s or "description" in s):
                    s_norm = (
                        s.replace("“", '"')
                        .replace("”", '"')
                        .replace("’", "'")
                        .replace("‘", "'")
                    )
                    probe = s_norm
                    if "}" in probe:
                        probe = probe[: probe.rfind("}") + 1]
                    try:
                        import ast as _ast

                        obj = _ast.literal_eval(probe)
                        if isinstance(obj, dict):
                            it = obj
                    except Exception:
                        try:
                            obj2 = json.loads(probe)
                            if isinstance(obj2, dict):
                                it = obj2
                        except Exception:
                            pass
        except Exception:
            pass
        head_txt, _tail_txt = _agenda_head_desc(it)
        t = str(head_txt or "").strip()
        if not t or t.lower() in {"tbd", "-", "•"}:
            continue
        lo = t.lower()
        if lo in seen:
            continue
        seen.add(lo)
        out.append(it)
        if len(out) >= limit:
            break
    return out

def _agenda_title(slide, title: str, *, pal: Dict[str, Tuple[int, int, int]]):
    # White title on dark backgrounds; keep primary on light themes
    title_rgb = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]
    _add_textbox(slide, 1.0, 0.9, 9.0, 0.95, title, font_size_pt=44, bold=True, color=title_rgb, word_wrap=True, auto_fit=True)
    return 0.95  # inches

def _agenda_accent(slide, top_in: float, height_in: float, *, pal: Dict[str, Tuple[int, int, int]]):
    bar_left = 1.0 - 0.35
    h = max(0.5, height_in)
    bar = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(bar_left), Inches(top_in), Inches(0.12), Inches(h))
    # Subtle gray on dark backgrounds; lighter secondary on light themes
    if _is_dark_rgb(pal.get("bg", (255,255,255))):
        bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(96, 102, 112)
    else:
        sec = pal.get("secondary", pal.get("text", (120,120,120)))
        bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*sec)
    bar.line.fill.background()

def _agenda_subtitle(slide, y_in: float, subtitle: str, *, pal: Dict[str, Tuple[int, int, int]]):
    sub = str(subtitle or "").strip()
    if not sub:
        return 0.0
    _add_textbox(
        slide,
        1.0,
        y_in,
        11.3,
        0.45,
        sub,
        font_size_pt=16,
        color=pal.get("muted", pal.get("text", (71, 85, 105))),
        word_wrap=True,
        auto_fit=True,
    )
    return 0.45

def _agenda_head_desc(item: Any) -> Tuple[str, str]:
    try:
        if isinstance(item, dict):
            head_txt = str(item.get("title") or item.get("label") or item.get("heading") or "").strip()
            tail_txt = str(item.get("description") or item.get("details") or item.get("text") or "").strip()
            if head_txt:
                tail_txt = str(tail_txt or "").strip()
                if tail_txt and not re.search(r"[A-Za-z0-9]", tail_txt):
                    tail_txt = ""
                return head_txt, _shorten(tail_txt, 96) if tail_txt else ""
    except Exception:
        pass

    # If the agenda item is a dict-like string (common when upstream JSON/dicts
    # are stringified), attempt to parse it and re-run extraction.
    try:
        txt_probe = str(item or "").strip()
        # Normalize smart quotes so dict-like strings are parseable and match regex patterns.
        txt_probe = (
            txt_probe.replace("“", '"')
            .replace("”", '"')
            .replace("’", "'")
            .replace("‘", "'")
        )
        # Be tolerant of dict-like strings that may have extra characters or not strictly end with '}'.
        # Try to isolate the first JSON/dict object and parse it.
        if txt_probe.startswith("{") and ("title" in txt_probe or "description" in txt_probe):
            import ast as _ast
            probe = txt_probe
            if "}" in probe:
                probe = probe[: probe.rfind("}") + 1]
            try:
                obj = _ast.literal_eval(probe)
                if isinstance(obj, dict):
                    return _agenda_head_desc(obj)
            except Exception:
                pass
            try:
                obj2 = json.loads(probe)
                if isinstance(obj2, dict):
                    return _agenda_head_desc(obj2)
            except Exception:
                pass

            # Regex fallback for cases where the string is almost-a-dict but not parseable
            # (e.g., mixed quotes/formatting). This prevents placeholder-looking output.
            try:
                m_title = re.search(r"(?:'title'|\"title\")\s*:\s*(?:'([^']+)'|\"([^\"]+)\")", probe)
                if not m_title:
                    # Handle malformed key/value where ':' is missing after the title key.
                    m_title = re.search(r"(?:'title'|\"title\")\s*(?:,|\s)+\s*(?:'([^']+)'|\"([^\"]+)\")", probe)

                m_desc = re.search(r"(?:'description'|\"description\")\s*:\s*(?:'([^']+)'|\"([^\"]+)\")", probe)
                if not m_desc:
                    m_desc = re.search(r"(?:'desc'|\"desc\")\s*:\s*(?:'([^']+)'|\"([^\"]+)\")", probe)

                title_val = (m_title.group(1) or m_title.group(2)) if m_title else ""
                desc_val = (m_desc.group(1) or m_desc.group(2)) if m_desc else ""
                title_val = str(title_val or "").strip()
                desc_val = str(desc_val or "").strip()
                if title_val:
                    if desc_val and not re.search(r"[A-Za-z0-9]", desc_val):
                        desc_val = ""
                    return title_val, _shorten(desc_val, 96) if desc_val else ""
            except Exception:
                pass
    except Exception:
        pass

    txt = str(item or "").strip()
    if not txt:
        return "", ""
    head, tail = _split_head_tail(txt)
    head_txt = (head or txt).strip()
    tail_txt = (tail or "").strip()

    if tail_txt:
        tail_words = re.findall(r"[A-Za-z0-9]+", tail_txt)
        if len(tail_words) <= 1 or len(tail_txt) < 10:
            head_txt = (head_txt + " " + tail_txt).strip()
            tail_txt = ""
    try:
        tail_txt = str(tail_txt or "").strip()
        if tail_txt and not re.search(r"[A-Za-z0-9]", tail_txt):
            tail_txt = ""
        tail_txt = _shorten(tail_txt, 96) if tail_txt else ""
    except Exception:
        tail_txt = tail_txt or ""

    # Final guard: if the "headline" looks like a dict key placeholder, attempt to extract a real title.
    head_probe = str(head_txt or "").strip()
    if (
        head_probe in {"{'title'", '{"title"', "{title"}
        or head_probe.startswith("{'title")
        or head_probe.startswith('{"title')
    ):
        try:
            s0 = str(item or "").strip()
            s0 = (
                s0.replace("“", '"')
                .replace("”", '"')
                .replace("’", "'")
                .replace("‘", "'")
            )
            m_title2 = re.search(r"(?:'title'|\"title\")\s*:\s*(?:'([^']+)'|\"([^\"]+)\")", s0)
            if not m_title2:
                m_title2 = re.search(r"(?:'title'|\"title\")\s*(?:,|\s)+\s*(?:'([^']+)'|\"([^\"]+)\")", s0)

            m_desc2 = re.search(r"(?:'description'|\"description\")\s*:\s*(?:'([^']+)'|\"([^\"]+)\")", s0)
            if not m_desc2:
                m_desc2 = re.search(r"(?:'desc'|\"desc\")\s*:\s*(?:'([^']+)'|\"([^\"]+)\")", s0)
            title2 = (m_title2.group(1) or m_title2.group(2)) if m_title2 else ""
            desc2 = (m_desc2.group(1) or m_desc2.group(2)) if m_desc2 else ""
            title2 = str(title2 or "").strip()
            desc2 = str(desc2 or "").strip()
            if title2:
                if desc2 and not re.search(r"[A-Za-z0-9]", desc2):
                    desc2 = ""
                return title2, _shorten(desc2, 96) if desc2 else ""
        except Exception:
            pass
    return head_txt, tail_txt

def _agenda_step_colors(pal: Dict[str, Tuple[int, int, int]]) -> List[Tuple[int, int, int]]:
    base_primary = pal.get("primary", (59, 130, 246))
    base_secondary = pal.get("secondary", base_primary)
    bg_rgb = pal.get("bg", (255, 255, 255))
    is_dark_bg = _is_dark_rgb(bg_rgb)

    def _ensure_visible(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
        r, g, b = rgb
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        br, bgc, bb = bg_rgb
        lum_bg = 0.2126 * br + 0.7152 * bgc + 0.0722 * bb
        if abs(lum - lum_bg) < 28:
            delta = 34 if is_dark_bg else -34
            return _shade(rgb, delta)
        return rgb

    a = _ensure_visible(base_primary)
    b = _ensure_visible(base_secondary)
    c = _ensure_visible(_shade(base_primary, 22 if is_dark_bg else -22))
    d = _ensure_visible(_shade(base_primary, 12 if is_dark_bg else -12))
    e = _ensure_visible(_shade(base_secondary, 18 if is_dark_bg else -18))
    return [a, b, c, d, e, a]

def _render_agenda_step_squares(slide, items: List[Any], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", subtitle: str | None = None) -> None:
    cleaned = _clean_agenda_items_any(list(items or []), limit=10)
    if not cleaned:
        cleaned = ["Overview", "Approach", "Findings", "Next Steps"]
    n = min(8, max(4, len(cleaned)))
    cleaned = cleaned[:n]
    title_h = _agenda_title(slide, title, pal=pal)
    sub_h = _agenda_subtitle(slide, 0.9 + title_h, subtitle or "", pal=pal)
    top = 0.9 + title_h + (sub_h if sub_h else 0.0) + 0.25
    avail_h = SLIDE_H_IN - 0.8 - top
    STEP_COLS = _agenda_step_colors(pal)

    left_count = n if n <= 4 else (n + 1) // 2
    left_items, right_items = cleaned[:left_count], cleaned[left_count:]
    max_rows = max(len(left_items), len(right_items))

    if n <= 4:
        row_h = max(0.95, min(1.35, (avail_h - (n - 1) * 0.18) / n))
        col_gap = 0.0
        col_w = SLIDE_W_IN - 2.0
        col_xs = [1.0]
        cols_items = [left_items]
    else:
        row_h = max(0.85, min(1.20, (avail_h - (max_rows - 1) * 0.16) / max_rows))
        col_gap = 0.70
        total_w = SLIDE_W_IN - 2.0 - col_gap
        col_w = total_w / 2.0
        col_xs = [1.0, 1.0 + col_w + col_gap]
        cols_items = [left_items, right_items]

    badge_d = 0.72
    badge_x_off = 0.00
    text_dx = 1.55

    for col_idx, col_items in enumerate(cols_items):
        col_x = col_xs[col_idx]
        y = top
        badge_x = col_x + badge_x_off
        text_x = badge_x + text_dx
        text_w = max(1.6, (col_x + col_w) - text_x)
        for j, raw in enumerate(col_items):
            i = (j if col_idx == 0 else (len(cols_items[0]) + j))
            head_txt, tail_txt = _agenda_head_desc(raw)
            col = STEP_COLS[i % len(STEP_COLS)]
            b = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(badge_x), Inches(y + 0.05), Inches(badge_d), Inches(badge_d))
            b.fill.solid(); b.fill.fore_color.rgb = RGBColor(*col)
            b.line.fill.background()
            tfb = b.text_frame; tfb.clear(); tfb.word_wrap = False; tfb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pb = tfb.paragraphs[0]; pb.alignment = PP_ALIGN.CENTER
            rb = pb.add_run(); rb.text = f"{i+1:02d}"; rb.font.size = Pt(24); rb.font.bold = True; rb.font.color.rgb = RGBColor(255, 255, 255)
            y_mid = y + 0.05 + badge_d / 2
            ln = slide.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(badge_x + badge_d), Inches(y_mid), Inches(text_x - 0.15), Inches(y_mid))
            try:
                ln.line.color.rgb = RGBColor(*col)
                ln.line.width = Pt(2)
            except Exception:
                pass
            _add_textbox(slide, text_x, y, text_w, 0.34, head_txt, font_size_pt=18, bold=True, color=pal.get("text", (15, 23, 42)), word_wrap=True, auto_fit=True)
            if tail_txt:
                _add_textbox(slide, text_x, y + 0.36, text_w, max(0.12, row_h - 0.38), tail_txt, font_size_pt=12, color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)
            y += row_h + (0.18 if n <= 4 else 0.16)

def _render_agenda_minimal_rule(slide, items: List[Any], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", subtitle: str | None = None) -> None:
    cleaned = _clean_agenda_items_any(list(items or []), limit=10)
    if not cleaned:
        cleaned = ["Overview", "Approach", "Findings", "Next Steps"]
    n = min(8, max(4, len(cleaned)))
    cleaned = cleaned[:n]
    title_h = _agenda_title(slide, title, pal=pal)
    sub_h = _agenda_subtitle(slide, 0.9 + title_h, subtitle or "", pal=pal)
    top = 0.9 + title_h + (sub_h if sub_h else 0.0) + 0.25
    avail_h = SLIDE_H_IN - 0.8 - top

    left_count = n if n <= 4 else (n + 1) // 2
    left_items, right_items = cleaned[:left_count], cleaned[left_count:]
    max_rows = max(len(left_items), len(right_items))
    gap = 0.18
    if n <= 4:
        cols_items = [left_items]
        col_gap = 0.0
        col_w = SLIDE_W_IN - 2.0
        col_xs = [1.0]
        row_h = max(0.92, min(1.14, (avail_h - (len(left_items) - 1) * gap) / max(1, len(left_items))))
    else:
        cols_items = [left_items, right_items]
        col_gap = 0.70
        total_w = SLIDE_W_IN - 2.0 - col_gap
        col_w = total_w / 2.0
        col_xs = [1.0, 1.0 + col_w + col_gap]
        row_h = max(0.84, min(1.05, (avail_h - (max_rows - 1) * gap) / max_rows))

    rule_col = pal.get("secondary", pal.get("muted", pal.get("text", (148, 163, 184))))
    for col_idx, col_items in enumerate(cols_items):
        col_x = col_xs[col_idx]
        rule_x = col_x + 0.35
        rule_h = (row_h + gap) * len(col_items) - gap
        if rule_h > 0.05:
            rule = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(rule_x), Inches(top + 0.05), Inches(0.05), Inches(rule_h))
            rule.fill.solid(); rule.fill.fore_color.rgb = RGBColor(*rule_col)
            rule.line.fill.background()

        text_x = rule_x + 0.35
        text_w = max(1.6, (col_x + col_w) - text_x)
        y = top
        for j, raw in enumerate(col_items):
            i = (j if col_idx == 0 else (len(cols_items[0]) + j))
            head_txt, tail_txt = _agenda_head_desc(raw)
            num_txt = f"{i+1:02d}."
            _add_textbox(slide, text_x, y, text_w, 0.34, f"{num_txt} {head_txt}", font_size_pt=18, bold=True, color=pal.get("text", (15, 23, 42)), word_wrap=True, auto_fit=True)
            if tail_txt:
                _add_textbox(slide, text_x, y + 0.36, text_w, max(0.12, row_h - 0.38), tail_txt, font_size_pt=12, color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)
            y += row_h + gap

def _render_agenda_stacked_circles(slide, items: List[Any], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", subtitle: str | None = None) -> None:
    cleaned = _clean_agenda_items_any(list(items or []), limit=10)
    if not cleaned:
        cleaned = ["Overview", "Approach", "Findings", "Next Steps"]
    n = min(8, max(4, len(cleaned)))
    cleaned = cleaned[:n]
    title_h = _agenda_title(slide, title, pal=pal)
    sub_h = _agenda_subtitle(slide, 0.9 + title_h, subtitle or "", pal=pal)
    top = 0.9 + title_h + (sub_h if sub_h else 0.0) + 0.30
    avail_h = SLIDE_H_IN - 0.8 - top
    STEP_COLS = _agenda_step_colors(pal)

    left_count = n if n <= 4 else (n + 1) // 2
    left_items, right_items = cleaned[:left_count], cleaned[left_count:]
    max_rows = max(len(left_items), len(right_items))

    if n <= 4:
        col_gap = 0.0
        col_w = SLIDE_W_IN - 2.0
        col_xs = [1.0]
        cols_items = [left_items]
        row_h = max(1.05, min(1.28, (avail_h - (len(left_items) - 1) * 0.10) / max(1, len(left_items))))
        row_gap = 0.10
    else:
        col_gap = 0.80
        total_w = SLIDE_W_IN - 2.0 - col_gap
        col_w = total_w / 2.0
        col_xs = [1.0, 1.0 + col_w + col_gap]
        cols_items = [left_items, right_items]
        row_h = max(0.92, min(1.12, (avail_h - (max_rows - 1) * 0.14) / max_rows))
        row_gap = 0.14

    for col_idx, col_items in enumerate(cols_items):
        col_x = col_xs[col_idx]
        y = top
        cx = col_x + 0.70
        text_x = col_x + 2.45
        text_w = max(1.6, (col_x + col_w) - text_x)
        for j, raw in enumerate(col_items):
            i = (j if col_idx == 0 else (len(cols_items[0]) + j))
            head_txt, tail_txt = _agenda_head_desc(raw)
            col = STEP_COLS[i % len(STEP_COLS)]
            d = min(1.10, max(0.80, row_h - 0.04))
            circ_y = y + max(0.0, (row_h - d) / 2.0)
            circ = slide.shapes.add_shape(MsoShape.OVAL, Inches(cx), Inches(circ_y), Inches(d), Inches(d))
            circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*col)
            circ.line.fill.background()
            tfc = circ.text_frame; tfc.clear(); tfc.word_wrap = False; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
            rc = pc.add_run(); rc.text = f"{i+1:02d}"; rc.font.size = Pt(26); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)
            y_mid = circ_y + d / 2
            ln = slide.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(cx + d), Inches(y_mid), Inches(text_x - 0.15), Inches(y_mid))
            try:
                ln.line.color.rgb = RGBColor(*col)
                ln.line.width = Pt(2)
            except Exception:
                pass
            _add_textbox(slide, text_x, y + 0.05, text_w, 0.34, head_txt, font_size_pt=18, bold=True, color=pal.get("text", (15, 23, 42)), word_wrap=True, auto_fit=True)
            if tail_txt:
                desc_h = max(0.12, row_h - 0.46)
                _add_textbox(slide, text_x, y + 0.41, text_w, desc_h, tail_txt, font_size_pt=12, color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)
            y += row_h + row_gap


def _render_agenda_center_chevrons(slide, items: List[Any], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", subtitle: str | None = None) -> None:
    cleaned = _clean_agenda_items_any(list(items or []), limit=10)
    if not cleaned:
        cleaned = ["Overview", "Approach", "Findings", "Next Steps"]
    n = min(8, max(4, len(cleaned)))
    cleaned = cleaned[:n]

    # Cleaning can reduce the list (dedupe/strip placeholders). Ensure we still have
    # the minimum count expected by this layout (paired rows).
    if len(cleaned) < 4:
        fallback = ["Overview", "Approach", "Findings", "Next Steps"]
        while len(cleaned) < 4 and len(cleaned) < len(fallback):
            cleaned.append(fallback[len(cleaned)])
        while len(cleaned) < 4:
            cleaned.append(cleaned[-1])
    n = len(cleaned)

    title_h = _agenda_title(slide, title, pal=pal)
    sub_h = _agenda_subtitle(slide, 0.9 + title_h, subtitle or "", pal=pal)
    top = 0.9 + title_h + (sub_h if sub_h else 0.0) + 0.30
    avail_h = SLIDE_H_IN - 0.8 - top

    STEP_COLS = _agenda_step_colors(pal)

    # Layout: paired rows (1 left, 2 right), with center dots in between.
    rows = int((n + 1) // 2)
    row_gap = 0.22
    row_h = max(0.78, min(1.05, (avail_h - (rows - 1) * row_gap) / max(1, rows)))

    center_x = SLIDE_W_IN / 2
    side_margin = 0.95
    center_gap = 0.80
    avail_w = SLIDE_W_IN - 2 * side_margin - center_gap
    col_w = avail_w / 2

    left_x = side_margin
    right_x = center_x + center_gap / 2
    bar_w = col_w
    bar_h = max(0.56, row_h * 0.72)

    dot_d = max(0.10, min(0.16, row_h * 0.16))
    dot_col = pal.get("primary", (59, 130, 246))

    circ_d = max(0.42, min(0.62, row_h * 0.58))
    circ_fill = RGBColor(255, 255, 255)
    circ_txt_col = pal.get("text", (15, 23, 42))

    def _bar_text(slide, x_in: float, y_in: float, w_in: float, h_in: float, txt: str, *, color_rgb: Tuple[int, int, int]):
        tf = slide.shapes.add_textbox(Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in)).text_frame
        tf.clear()
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        try:
            from pptx.enum.text import MSO_ANCHOR
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        except Exception:
            pass
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = str(txt or "").strip()
        r.font.size = Pt(16)
        r.font.bold = True
        r.font.color.rgb = RGBColor(*color_rgb)

    for ridx in range(rows):
        left_idx = ridx * 2
        right_idx = left_idx + 1
        y = top + ridx * (row_h + row_gap)
        y_mid = y + row_h / 2
        bar_y = y + max(0.0, (row_h - bar_h) / 2)

        # Center dot per row
        dot = slide.shapes.add_shape(MsoShape.OVAL, Inches(center_x - dot_d / 2), Inches(y_mid - dot_d / 2), Inches(dot_d), Inches(dot_d))
        dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*dot_col)
        dot.line.fill.background()

        if left_idx < n:
            head_txt, _tail_txt = _agenda_head_desc(cleaned[left_idx])
            col = STEP_COLS[left_idx % len(STEP_COLS)]
            bar = slide.shapes.add_shape(MsoShape.RIGHT_ARROW, Inches(left_x), Inches(bar_y), Inches(bar_w), Inches(bar_h))
            bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*col)
            bar.line.fill.background()

            # Number circle near the inner edge
            cx = center_x - center_gap / 2 - circ_d * 0.55
            circ = slide.shapes.add_shape(MsoShape.OVAL, Inches(cx - circ_d / 2), Inches(y_mid - circ_d / 2), Inches(circ_d), Inches(circ_d))
            circ.fill.solid(); circ.fill.fore_color.rgb = circ_fill
            try:
                circ.line.color.rgb = RGBColor(*col)
                circ.line.width = Pt(1.5)
            except Exception:
                pass
            tfc = circ.text_frame
            tfc.clear(); tfc.word_wrap = False; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                from pptx.enum.text import MSO_ANCHOR
                tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
            except Exception:
                pass
            pc = tfc.paragraphs[0]
            pc.alignment = PP_ALIGN.CENTER
            rc = pc.add_run()
            rc.text = f"{left_idx + 1:d}"
            rc.font.size = Pt(18)
            rc.font.bold = True
            rc.font.color.rgb = RGBColor(*circ_txt_col)

            _bar_text(slide, left_x + 0.25, bar_y, bar_w - 0.70, bar_h, head_txt, color_rgb=(255, 255, 255))

        if right_idx < n:
            head_txt, _tail_txt = _agenda_head_desc(cleaned[right_idx])
            col = STEP_COLS[right_idx % len(STEP_COLS)]
            bar = slide.shapes.add_shape(MsoShape.LEFT_ARROW, Inches(right_x), Inches(bar_y), Inches(bar_w), Inches(bar_h))
            bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*col)
            bar.line.fill.background()

            cx = center_x + center_gap / 2 + circ_d * 0.55
            circ = slide.shapes.add_shape(MsoShape.OVAL, Inches(cx - circ_d / 2), Inches(y_mid - circ_d / 2), Inches(circ_d), Inches(circ_d))
            circ.fill.solid(); circ.fill.fore_color.rgb = circ_fill
            try:
                circ.line.color.rgb = RGBColor(*col)
                circ.line.width = Pt(1.5)
            except Exception:
                pass
            tfc = circ.text_frame
            tfc.clear(); tfc.word_wrap = False; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                from pptx.enum.text import MSO_ANCHOR
                tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
            except Exception:
                pass
            pc = tfc.paragraphs[0]
            pc.alignment = PP_ALIGN.CENTER
            rc = pc.add_run()
            rc.text = f"{right_idx + 1:d}"
            rc.font.size = Pt(18)
            rc.font.bold = True
            rc.font.color.rgb = RGBColor(*circ_txt_col)

            _bar_text(slide, right_x + 0.45, bar_y, bar_w - 0.70, bar_h, head_txt, color_rgb=(255, 255, 255))


def _render_agenda_chevron_badges(slide, items: List[Any], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", subtitle: str | None = None) -> None:
    cleaned = _clean_agenda_items_any(list(items or []), limit=10)
    if not cleaned:
        cleaned = ["Overview", "Approach", "Findings", "Next Steps"]
    n = min(8, max(4, len(cleaned)))
    cleaned = cleaned[:n]

    # Cleaning can reduce the list (dedupe/strip placeholders). Ensure we still have
    # the minimum count expected by this layout.
    if len(cleaned) < 4:
        fallback = ["Overview", "Approach", "Findings", "Next Steps"]
        while len(cleaned) < 4 and len(cleaned) < len(fallback):
            cleaned.append(fallback[len(cleaned)])
        while len(cleaned) < 4:
            cleaned.append(cleaned[-1])
    n = len(cleaned)

    title_h = _agenda_title(slide, title, pal=pal)
    sub_h = _agenda_subtitle(slide, 0.9 + title_h, subtitle or "", pal=pal)
    top = 0.9 + title_h + (sub_h if sub_h else 0.0) + 0.35
    avail_h = SLIDE_H_IN - 0.85 - top

    STEP_COLS = _agenda_step_colors(pal)

    left_count = (n + 1) // 2
    left_items = cleaned[:left_count]
    right_items = cleaned[left_count:]
    rows = max(len(left_items), len(right_items))

    col_gap = 0.75
    col_w = (SLIDE_W_IN - 2.0 - col_gap) / 2
    col_xs = [1.0, 1.0 + col_w + col_gap]

    row_gap = 0.28
    row_h = max(0.82, min(1.22, (avail_h - (rows - 1) * row_gap) / max(1, rows)))

    badge_h = max(0.55, min(0.78, row_h * 0.72))
    badge_w = max(0.90, min(1.10, badge_h * 1.55))
    badge_y_off = max(0.0, (row_h - badge_h) / 2)

    text_x_off = badge_w + 0.30
    head_h = 0.34
    tail_h_min = 0.18

    for col_idx, col_items in enumerate([left_items, right_items]):
        col_x = col_xs[col_idx]
        y = top
        for j in range(rows):
            global_idx = (j if col_idx == 0 else (left_count + j))
            if global_idx >= n:
                continue
            raw = col_items[j] if j < len(col_items) else None
            if raw is None:
                continue

            head_txt, tail_txt = _agenda_head_desc(raw)
            col = STEP_COLS[global_idx % len(STEP_COLS)]

            # Chevron badge
            b = slide.shapes.add_shape(MsoShape.CHEVRON, Inches(col_x), Inches(y + badge_y_off), Inches(badge_w), Inches(badge_h))
            b.fill.solid(); b.fill.fore_color.rgb = RGBColor(*col)
            b.line.fill.background()

            tfb = b.text_frame
            tfb.clear(); tfb.word_wrap = False; tfb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                from pptx.enum.text import MSO_ANCHOR
                tfb.vertical_anchor = MSO_ANCHOR.MIDDLE
            except Exception:
                pass
            pb = tfb.paragraphs[0]
            pb.alignment = PP_ALIGN.CENTER
            rb = pb.add_run()
            rb.text = f"{global_idx + 1:02d}"
            rb.font.size = Pt(20)
            rb.font.bold = True
            rb.font.color.rgb = RGBColor(255, 255, 255)

            text_x = col_x + text_x_off
            text_w = max(1.8, (col_x + col_w) - text_x)

            _add_textbox(
                slide,
                text_x,
                y,
                text_w,
                head_h,
                head_txt,
                font_size_pt=16,
                bold=True,
                color=pal.get("text", (15, 23, 42)),
                word_wrap=True,
                auto_fit=True,
            )
            if tail_txt:
                tail_y = y + head_h + 0.10
                tail_h = max(tail_h_min, row_h - (tail_y - y))
                _add_textbox(
                    slide,
                    text_x,
                    tail_y,
                    text_w,
                    tail_h,
                    tail_txt,
                    font_size_pt=11,
                    color=pal.get("muted", pal.get("text", (71, 85, 105))),
                    word_wrap=True,
                    auto_fit=True,
                )

            y += row_h + row_gap

def _render_agenda_split_panel(slide, items: List[Any], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", subtitle: str | None = None) -> None:
    cleaned = _clean_agenda_items_any(list(items or []), limit=10)
    if not cleaned:
        cleaned = ["Overview", "Approach", "Findings", "Next Steps"]
    n = min(8, max(3, len(cleaned)))
    cleaned = cleaned[:n]
    STEP_COLS = _agenda_step_colors(pal)
    panel_col = STEP_COLS[0] if STEP_COLS else pal.get("secondary", (226, 232, 240))
    left_w = 4.65
    panel = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(0.0), Inches(0.0), Inches(left_w), Inches(SLIDE_H_IN))
    panel.fill.solid(); panel.fill.fore_color.rgb = RGBColor(*panel_col)
    panel.line.fill.background()
    try:
        panel.shadow.inherit = False
    except Exception:
        pass
    head_col = pal.get("text", (15, 23, 42))
    title_rgb = head_col
    _add_textbox(slide, 0.55, 3.10, left_w - 0.75, 1.2, title or "Agenda", font_size_pt=56, bold=True, color=title_rgb, word_wrap=True, auto_fit=True)
    if subtitle:
        _add_textbox(slide, 0.55, 4.25, left_w - 0.75, 0.7, str(subtitle), font_size_pt=16, color=head_col, word_wrap=True, auto_fit=True)
    stripe_col = pal.get("bg", (255, 255, 255))
    for k in range(7):
        y0 = 0.55 + k * 0.20
        ln = slide.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(0.45), Inches(y0), Inches(1.55), Inches(y0 + 1.00))
        try:
            ln.line.color.rgb = RGBColor(*stripe_col)
            ln.line.width = Pt(2)
        except Exception:
            pass
    x0 = left_w + 0.65
    top = 1.40
    avail_h = SLIDE_H_IN - 1.1 - top

    left_count = n if n <= 4 else (n + 1) // 2
    left_items, right_items = cleaned[:left_count], cleaned[left_count:]
    max_rows = max(len(left_items), len(right_items))
    row_gap = 0.35
    if n <= 4:
        cols_items = [left_items]
        col_gap = 0.0
    else:
        cols_items = [left_items, right_items]
        col_gap = 0.65

    right_total_w = SLIDE_W_IN - 0.65 - x0
    col_w = right_total_w if n <= 4 else max(2.6, (right_total_w - col_gap) / 2.0)
    col_xs = [x0] if n <= 4 else [x0, x0 + col_w + col_gap]
    row_h = max(1.08, min(1.45, (avail_h - (max_rows - 1) * row_gap) / max_rows))

    for col_idx, col_items in enumerate(cols_items):
        col_x = col_xs[col_idx]
        y = top
        for j, raw in enumerate(col_items):
            i = (j if col_idx == 0 else (len(cols_items[0]) + j))
            head_txt, tail_txt = _agenda_head_desc(raw)
            col = STEP_COLS[i % len(STEP_COLS)]
            dash = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(col_x), Inches(y + 0.12), Inches(0.26), Inches(0.05))
            dash.fill.solid(); dash.fill.fore_color.rgb = RGBColor(*col)
            dash.line.fill.background()
            _add_textbox(slide, col_x + 0.32, y, 1.1, 0.55, f"{i+1:02d}", font_size_pt=52, bold=True, color=col, word_wrap=False, auto_fit=True)
            head_x = col_x + 1.55
            head_w = max(1.6, (col_x + col_w) - head_x)
            head_len = len(str(head_txt or "").strip())
            head_h = 0.58 if head_len >= 22 else 0.38
            head_fs = 16 if head_len >= 30 else 18
            head_y = y + 0.08
            _add_textbox(slide, head_x, head_y, head_w, head_h, head_txt, font_size_pt=head_fs, bold=True, color=pal.get("text", (15, 23, 42)), word_wrap=True, auto_fit=True)
            if tail_txt:
                tail_y = head_y + head_h + 0.06
                tail_h = max(0.12, (y + row_h) - tail_y)
                _add_textbox(slide, head_x, tail_y, head_w, tail_h, tail_txt, font_size_pt=12, color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)
            y += row_h + row_gap

def _render_agenda_rows_column(slide, items: List[str], origin_x_in: float, origin_y_in: float, col_w_in: float, row_h_in: float, gap_in: float, *, pal: Dict[str, Tuple[int, int, int]]):
    y = origin_y_in
    zebra_rgb = pal.get("secondary") or pal["bg"]
    for i, label in enumerate(items):
        if i % 2 == 0:
            bg = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(origin_x_in), Inches(y - 0.08), Inches(col_w_in), Inches(row_h_in))
            bg.fill.solid(); bg.fill.fore_color.rgb = RGBColor(*zebra_rgb); bg.line.fill.background()
        tb = slide.shapes.add_textbox(Inches(origin_x_in + 0.18), Inches(y), Inches(col_w_in - 0.18), Inches(row_h_in)).text_frame
        tb.word_wrap = True
        tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        p = tb.paragraphs[0]
        p.text = label
        scale = float(row_h_in / 0.90)
        size_pt = max(16, int(22 * scale))
        p.font.size = Pt(size_pt)
        p.font.color.rgb = RGBColor(*pal["text"]) if isinstance(pal["text"], tuple) else p.font.color.rgb
        y += row_h_in + gap_in
    return y

def _render_agenda_zebra_autofit(prs: Presentation, slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", subtitle: str | None = None, is_cont: bool = False) -> List[str]:
    items = _clean_agenda_items(items, limit=32)
    if not items:
        items = ["Overview", "Discussion", "Q&A", "Next Steps"]

    # Title (do not append '(cont.)' – keep a stable heading across pages)
    title_h = _agenda_title(slide, title, pal=pal)

    sub_h = _agenda_subtitle(slide, 0.9 + title_h, subtitle or "", pal=pal)

    # Layout constants
    row_h_base = 0.90
    row_h_min = 0.62
    gap = 0.12
    top = 0.9 + title_h + (sub_h if sub_h else 0.0) + 0.25
    avail_h = SLIDE_H_IN - 0.8 - top

    def fits_single(n: int, row_h: float) -> bool:
        return (n * row_h + (n - 1) * gap) <= avail_h

    n = len(items)
    row_h = row_h_base
    if not fits_single(n, row_h):
        scale = max(row_h_min / row_h_base, (avail_h - (n - 1) * gap) / (n * row_h_base))
        row_h = max(row_h_min, row_h_base * min(1.0, scale))

    if fits_single(n, row_h):
        if n <= 4:
            col_x = 1.0
            col_w = SLIDE_W_IN - 1.0 - 1.0
            bottom_y = _render_agenda_rows_column(slide, items, col_x, top, col_w, row_h, gap, pal=pal)
            _agenda_accent(slide, top - 0.10, bottom_y - top + 0.20, pal=pal)
            return []

    # Two-column fallback
    left_count = (n + 1) // 2
    left_items, right_items = items[:left_count], items[left_count:]
    col_gap = 0.60
    total_w = SLIDE_W_IN - 1.0 - 1.0 - col_gap
    col_w_half = total_w / 2
    max_rows = max(len(left_items), len(right_items))
    row_h2 = min(row_h_base, max(row_h_min, (avail_h - (max_rows - 1) * gap) / max_rows))
    left_x = 1.0
    right_x = 1.0 + col_w_half + col_gap
    bottom_left = _render_agenda_rows_column(slide, left_items, left_x, top, col_w_half, row_h2, gap, pal=pal)
    bottom_right = _render_agenda_rows_column(slide, right_items, right_x, top, col_w_half, row_h2, gap, pal=pal)
    bottom_y = max(bottom_left, bottom_right)
    _agenda_accent(slide, top - 0.10, bottom_y - top + 0.20, pal=pal)

    # Overflow calculation
    max_fit = int((avail_h + gap) // (row_h2 + gap))
    overflow: List[str] = []
    if max_rows > max_fit:
        overflow = left_items[max_fit:] + right_items[max_fit:]
    return overflow


# --- Background Timeline (Auto-fit, 1-2 rows with continuation) ---
def _timeline_title(slide, title: str, *, pal: Dict[str, Tuple[int, int, int]]):
    # White on dark backgrounds, primary on light; wrap and auto-fit to avoid overflow
    title_rgb = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]
    _add_textbox(slide, 1.0, 0.9, 10.5, 0.95, title, font_size_pt=42, bold=True, color=title_rgb, word_wrap=True, auto_fit=True)
    return 0.95

def _timeline_baseline(slide, x1_in: float, x2_in: float, y_in: float, *, pal: Dict[str, Tuple[int, int, int]]):
    # STRAIGHT_CONNECTOR1 = 1
    line = slide.shapes.add_connector(1, Inches(x1_in), Inches(y_in), Inches(x2_in), Inches(y_in))
    line.line.color.rgb = RGBColor(*pal.get("secondary", pal["text"]))
    line.line.width = Pt(3)
    try:
        line.shadow.inherit = False; line.shadow.visible = True; line.shadow.blur = 4; line.shadow.distance = 1
    except Exception:
        pass
    return line

def _timeline_node(slide, x_in: float, y_in: float, d_in: float, label: str, number: int, *, pal: Dict[str, Tuple[int, int, int]]):
    d = Inches(d_in)
    # OVAL = 1
    node = slide.shapes.add_shape(1, Inches(x_in), Inches(y_in), d, d)
    # Chip fill (light)
    chip_rgb = pal.get("secondary") or pal["bg"]
    node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*chip_rgb)
    node.line.color.rgb = RGBColor(*pal["primary"])
    try:
        node.shadow.inherit = False; node.shadow.visible = True; node.shadow.blur = 4; node.shadow.distance = 1
    except Exception:
        pass

    tf = node.text_frame; tf.clear()
    pnum = tf.paragraphs[0]; pnum.alignment = PP_ALIGN.CENTER
    r = pnum.add_run(); r.text = str(number); r.font.size = Pt(24); r.font.bold = True; r.font.color.rgb = RGBColor(*pal["primary"])

    # Caption under circle (auto-fit)
    cap_w_in = min(2.55, max(d_in + 0.55, d_in + 0.85))
    cap_x_in = (x_in + d_in / 2) - cap_w_in / 2
    # Clamp within slide safe margins so labels never spill outside the presentation area
    cap_x_in = max(0.7, min(SLIDE_W_IN - 0.7 - cap_w_in, cap_x_in))
    cap_tf = slide.shapes.add_textbox(Inches(cap_x_in), Inches(y_in + d_in + 0.12), Inches(cap_w_in), Inches(0.9)).text_frame
    cap_tf.word_wrap = True
    cap_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    cp = cap_tf.paragraphs[0]; cp.alignment = PP_ALIGN.CENTER
    cp.text = (label or "")
    cp.font.size = Pt(20); cp.font.bold = True; cp.font.color.rgb = RGBColor(*pal["text"]) if isinstance(pal["text"], tuple) else cp.font.color.rgb

def _timeline_render_row(slide, items: List[str], y_in: float, circle_d_in: float, numbered_start: int, *, pal: Dict[str, Tuple[int, int, int]]):
    n = len(items)
    d = circle_d_in
    margin_x = 0.8
    usable = SLIDE_W_IN - 1.0 - 1.0 - 2 * margin_x - n * d
    gap = max(0.35, usable / (n - 1)) if n > 1 else 0.0
    x_left_center = 1.0 + margin_x + d / 2
    x_right_center = x_left_center + (n - 1) * (d + gap)
    _timeline_baseline(slide, x_left_center, x_right_center, y_in + d / 2, pal=pal)
    centers = []
    for i, label in enumerate(items):
        cx = (x_left_center + i * (d + gap)) - d / 2
        _timeline_node(slide, cx, y_in, d, label, numbered_start + i, pal=pal)
        centers.append(cx + d / 2)
    # connector dots between nodes
    dot_d = 0.12
    y_dot = y_in + d / 2 - dot_d / 2
    for i in range(len(centers) - 1):
        mid = (centers[i] + centers[i + 1]) / 2
        dot = slide.shapes.add_shape(MsoShape.OVAL, Inches(mid - dot_d / 2), Inches(y_dot), Inches(dot_d), Inches(dot_d))
        dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(255, 255, 255); dot.line.color.rgb = RGBColor(209, 213, 219)
    return x_right_center + d / 2

def _render_background_timeline_autofit(prs: Presentation, slide, stages: List[str], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Background — Evolution", is_cont: bool = False, subtitle_override: str | None = None) -> List[str]:
    items = _clean_agenda_items(stages, limit=20)
    if len(items) < 3:
        items += ["Foundational Phase", "Tools Adoption", "Scaled Analytics"][: 3 - len(items)]

    th = _timeline_title(slide, title, pal=pal)
    sub = str(subtitle_override or "").strip()
    if sub:
        _add_textbox(slide, 1.0, 0.9 + th, 10.5, 0.50, sub, font_size_pt=18, color=pal.get("muted", (71,85,105)), align="center")
    base_top = 0.9 + th + 0.25 + 0.45
    bottom = SLIDE_H_IN - 0.8
    max_block_h = bottom - base_top

    n_all = len(items)
    n = min(n_all, 8)
    d = 1.4  # larger circle diameter (in)
    row_h = d + 1.00

    if row_h <= max_block_h and n <= 6:
        # Center single-row block vertically between title and bottom area
        needed_h = row_h
        top = base_top + max(0.0, (max_block_h - needed_h) / 2)
        # Background band behind the row
        cont_x, cont_w = 0.7, SLIDE_W_IN - 1.4
        cont_y = top - 0.30
        cont_h = row_h + 0.60
        band = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240,245,255))))
        band.line.color.rgb = RGBColor(229,231,235)
        try:
            band.shadow.inherit = False; band.shadow.visible = True; band.shadow.blur = 12; band.shadow.distance = 3
        except Exception:
            pass
        _timeline_render_row(slide, items[:n], y_in=top, circle_d_in=d, numbered_start=1, pal=pal)
        return items[n:]

    # Two-row fallback
    split = (n + 1) // 2
    top_items, bottom_items = items[:split], items[split:]
    needed_h = row_h * 2 + 0.45
    if needed_h > max_block_h:
        scale = max(0.9 / d, max_block_h / needed_h)
        d = max(0.9, d * scale)
        row_h = d + 0.90

    # Center two-row block vertically
    y_top = base_top + max(0.0, (max_block_h - (row_h * 2 + 0.45)) / 2)
    y_bot = y_top + row_h + 0.45
    # Background container behind two rows
    cont_x, cont_w = 0.7, SLIDE_W_IN - 1.4
    cont_y = y_top - 0.30
    cont_h = (y_bot + row_h) - y_top + 0.30
    band = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
    band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240,245,255))))
    band.line.color.rgb = RGBColor(229,231,235)
    try:
        band.shadow.inherit = False; band.shadow.visible = True; band.shadow.blur = 8; band.shadow.distance = 3
    except Exception:
        pass
    _timeline_render_row(slide, top_items, y_in=y_top, circle_d_in=d, numbered_start=1, pal=pal)
    _timeline_render_row(slide, bottom_items, y_in=y_bot, circle_d_in=d, numbered_start=1 + len(top_items), pal=pal)
    return items[n:]


# --- Key Concepts as Card Grid (Auto-fit, 2 cols + continuation) ---
def _clean_concepts(items: List[str], limit: int = 20) -> List[str]:
    seen = set(); out: List[str] = []
    for s in items or []:
        t = (str(s or "").strip())
        if not t or t.lower() in {"tbd", "(to be refined)", "-", "•"}:
            continue
        lo = t.lower()
        if lo in seen:
            continue
        seen.add(lo)
        out.append(t)
        if len(out) >= limit:
            break
    return out

def _kc_title(slide, title: str, *, pal: Dict[str, Tuple[int, int, int]]):
    _add_textbox(slide, 1.0, 0.9, 9.5, 0.95, title, font_size_pt=42, bold=True, color=pal["primary"])
    return 0.95

def _kc_card(slide, x_in: float, y_in: float, w_in: float, h_in: float, title: str, desc: str, *, pal: Dict[str, Tuple[int, int, int]]):
    card = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in))
    bg_rgb = pal.get("secondary", pal.get("bg", (255, 255, 255)))
    card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*bg_rgb)
    card.line.color.rgb = RGBColor(*bg_rgb)

    # icon bubble
    ico = slide.shapes.add_shape(MsoShape.OVAL, Inches(x_in + 0.18), Inches(y_in + 0.18), Inches(0.36), Inches(0.36))
    ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(*pal["primary"]) ; ico.line.color.rgb = RGBColor(*pal["primary"]) 
    itf = ico.text_frame; itf.clear()
    ip = itf.paragraphs[0]; ip.alignment = PP_ALIGN.CENTER
    ir = ip.add_run(); ir.text = "✓"; ir.font.size = Pt(12)

    # title
    tbox = slide.shapes.add_textbox(Inches(x_in + 0.70), Inches(y_in + 0.12), Inches(w_in - 0.90), Inches(0.40)).text_frame
    p1 = tbox.paragraphs[0]; p1.text = title
    is_dark_card = _is_dark_rgb(bg_rgb)
    title_rgb = (255, 255, 255) if is_dark_card else pal.get("text", (24, 24, 27))
    p1.font.size = Pt(20); p1.font.bold = True; p1.font.color.rgb = RGBColor(*title_rgb)

    # description
    db = slide.shapes.add_textbox(Inches(x_in + 0.70), Inches(y_in + 0.52), Inches(w_in - 0.90), Inches(h_in - 0.66)).text_frame
    db.word_wrap = True; db.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p2 = db.paragraphs[0]; p2.text = desc
    desc_rgb = (235, 244, 255) if is_dark_card else pal.get("muted", pal.get("text", (24, 24, 27)))
    p2.font.size = Pt(14); p2.font.color.rgb = RGBColor(*desc_rgb)

def _render_key_concepts_cards_autofit(prs: Presentation, slide, concepts: List[str], desc_map: Dict[str, str] | None, *, pal: Dict[str, Tuple[int, int, int]], title: str = "Key Concepts", is_cont: bool = False) -> List[str]:
    titles = _clean_concepts(concepts)

    th = _kc_title(slide, title, pal=pal)

    pairs: List[Tuple[str, str]] = []
    for t in titles:
        d = None
        if isinstance(desc_map, dict):
            d = (desc_map.get(t) or "").strip() or None
        if not d:
            d = "Brief explanation to frame relevance and typical impact."
        pairs.append((t, d))

    top = 0.9 + th + 0.25
    avail_h = SLIDE_H_IN - 0.8 - top
    cols = 2
    col_gap = 0.50
    col_w = (SLIDE_W_IN - 1.0 - 1.0 - col_gap) / cols
    rows_try = 3
    card_h = 1.05
    card_h_min = 0.90
    v_gap = 0.22

    def total_h(rows: int, ch: float) -> float:
        return rows * ch + (rows - 1) * v_gap

    ch = card_h
    if total_h(rows_try, ch) > avail_h:
        ch = max(card_h_min, (avail_h - (rows_try - 1) * v_gap) / rows_try)

    rows = rows_try
    capacity = rows * cols
    page_pairs = pairs[:capacity]
    remainder_titles = [t for t, _ in pairs[capacity:]]

    y = top
    for r in range(rows):
        x = 1.0
        for c in range(cols):
            idx = r * cols + c
            if idx >= len(page_pairs):
                break
            t_i, d_i = page_pairs[idx]
            if TITLE_BANNED_RE.match(t_i or ""):
                continue
            _kc_card(slide, x, y, col_w, ch, t_i, d_i, pal=pal)
            x += col_w + col_gap
        y += ch + v_gap

    return remainder_titles

# --- Key Takeaways (Slide 6) Infographic Renderer ---
def _clean_takeaways(items: List[str], limit: int = 5) -> List[str]:
    seen = set(); out: List[str] = []
    for s in items or []:
        t = (str(s or "").strip())
        if not t or t.lower() in {"tbd", "(to be refined)", "-", "•"}:
            continue
        lo = t.lower()
        if lo in seen:
            continue
        seen.add(lo)
        out.append(t)
        if len(out) >= limit:
            break
    return out

def _takeaways_title(slide, title: str, *, pal: Dict[str, Tuple[int, int, int]]):
    # Consistent white title for Key Takeaways
    title_rgb = (255, 255, 255)
    _add_textbox(slide, 1.0, 0.9, 10.0, 0.95, title, font_size_pt=42, bold=True, color=title_rgb, word_wrap=True, auto_fit=True)
    return 0.95

def _takeaway_node(slide, x_in: float, y_in: float, d_in: float, label: str, *, pal: Dict[str, Tuple[int, int, int]]):
    # circle
    shape = slide.shapes.add_shape(MsoShape.OVAL, Inches(x_in), Inches(y_in), Inches(d_in), Inches(d_in))
    shape.fill.background()  # keep slide bg
    shape.line.color.rgb = RGBColor(*pal["primary"])
    # caption inside
    tf = shape.text_frame; tf.clear(); tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    p.text = label
    for r in p.runs:
        r.font.size = Pt(16); r.font.color.rgb = RGBColor(*pal["text"])

def _render_takeaways_infographic_autofit(prs: Presentation, slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Key Takeaways", is_cont: bool = False) -> List[str]:
    items = _clean_takeaways(items, limit=12)  # collect more; we will paginate to 4–5
    th = _takeaways_title(slide, title, pal=pal)

    top = 0.9 + th + 0.25
    bottom = SLIDE_H_IN - 0.8
    avail_h = bottom - top

    # determine n per page (prefer 5, allow 4)
    n_total = len(items)
    n = 5 if n_total >= 5 else max(4, n_total)
    if n == 0:
        items = ["Summary", "Outcomes", "Next Steps", "Owners"]
        n_total = len(items)
        n = 4

    d = 1.6
    needed_h = d + 0.20  # circle only; label inside
    if needed_h > avail_h:
        d = max(1.2, avail_h - 0.20)

    margin_x = 1.0
    usable = SLIDE_W_IN - 1.0 - 1.0 - 2 * margin_x - n * d
    gap = max(0.35, usable / (n - 1)) if n > 1 else 0.0

    y = top
    x_left_center = 1.0 + margin_x + d / 2
    for i in range(n):
        cx = (x_left_center + i * (d + gap)) - d / 2
        _takeaway_node(slide, cx, y, d, items[i], pal=pal)

    remainder = items[n:]
    return remainder

def _set_cell_text(cell, text: str, size: int = 18, bold: bool = False, align_center: bool = False, color: Tuple[int, int, int] | None = None):
    cell.text = text or ""
    for p in cell.text_frame.paragraphs:
        for r in p.runs:
            r.font.size = Pt(size)
            r.font.bold = bool(bold)
            if color is not None:
                r.font.color.rgb = RGBColor(*color)
        if align_center:
            p.alignment = PP_ALIGN.CENTER


def _draw_table_grid(slide, table, *, rgb: Tuple[int, int, int] = (209, 213, 219), weight_pt: float = 1.0) -> None:
    """Draw thin grid lines over the table to ensure inner borders are visible."""
    left, top, total_w, total_h = table.left, table.top, table.width, table.height
    # Vertical boundaries
    x_positions = [left]
    acc = left
    for col in table.columns:
        acc += col.width
        x_positions.append(acc)
    for xv in x_positions:
        ln = slide.shapes.add_connector(1, xv, top, xv, top + total_h)
        ln.line.color.rgb = RGBColor(*rgb)
        ln.line.width = Pt(weight_pt)
    # Horizontal boundaries
    y_positions = [top]
    acc = top
    # Prefer explicit row heights when available; otherwise divide evenly
    explicit = all(getattr(row, 'height', None) for row in table.rows)
    if explicit:
        for row in table.rows:
            acc += row.height
            y_positions.append(acc)
    else:
        step = int(total_h / len(table.rows)) if len(table.rows) else total_h
        for _ in range(len(table.rows)):
            acc += step
            y_positions.append(acc)
    for yv in y_positions:
        ln = slide.shapes.add_connector(1, left, yv, left + total_w, yv)
        ln.line.color.rgb = RGBColor(*rgb)
        ln.line.width = Pt(weight_pt)


def _draw_table_grid_rect(slide, x_in: float, y_in: float, w_in: float, h_in: float, rows: int, cols: int, *, rgb: Tuple[int,int,int] = (209,213,219), weight_pt: float = 1.0) -> None:
    """Draw grid lines using the rectangle metrics used to create the table.
    Use this when we don't have access to the Table shape's coordinates.
    """
    left = Inches(x_in); top = Inches(y_in); total_w = Inches(w_in); total_h = Inches(h_in)
    # Vertical lines (cols + 1 boundaries)
    for i in range(cols + 1):
        xv = left + int(total_w * i / max(1, cols))
        ln = slide.shapes.add_connector(1, xv, top, xv, top + total_h)
        ln.line.color.rgb = RGBColor(*rgb)
        ln.line.width = Pt(weight_pt)
    # Horizontal lines (rows + 1 boundaries)
    for j in range(rows + 1):
        yv = top + int(total_h * j / max(1, rows))
        ln = slide.shapes.add_connector(1, left, yv, left + total_w, yv)
        ln.line.color.rgb = RGBColor(*rgb)
        ln.line.width = Pt(weight_pt)


def build_pptx_from_positions(pos: Dict[int, Dict[str, Any]], out_path: str, *, theme_name: str | None = None) -> str:
    """
    Build a 10-slide deck per user's geometry using python-pptx.
    Expects `pos` dict with numbered slides 1..10 and required keys.
    Returns the saved PPTX path.
    """
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W_IN)
    prs.slide_height = Inches(SLIDE_H_IN)

    blank = prs.slide_layouts[6]
    pal = _theme(theme_name)
    # Title color: white on dark backgrounds, otherwise theme primary
    TITLE_COL = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]
    # Title color: white on dark backgrounds, otherwise theme primary
    TITLE_COL = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]
    # Title color: white on dark backgrounds, otherwise theme primary
    TITLE_COL = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]
    # Title color: white on dark theme backgrounds, else theme primary
    TITLE_COL = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]
    # Title color: white on dark backgrounds, otherwise theme primary
    TITLE_COL = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]
    # Title color rule: white on dark themes, primary on light
    TITLE_COL = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]

    def apply_bg(slide):
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(*pal["bg"])

    # Slide 1 – Title
    s1 = prs.slides.add_slide(blank)
    apply_bg(s1)
    d1 = pos.get(1, {})
    # Slide 1 uses the AI-derived title (do not lock to generic "Title")
    title1 = (d1.get("title") or "Presentation Title").strip() or "Presentation Title"
    _add_textbox(s1, 1.0, 1.0, 11.3, 2.0, title1, font_size_pt=46, bold=True, align="center", color=pal["primary"], word_wrap=True, auto_fit=True)
    sub = d1.get("subtitle") or d1.get("presenter") or ""
    _add_textbox(s1, 1.0, 2.4, 11.3, 0.8, sub, font_size_pt=20, color=pal["text"], align="center", word_wrap=True, auto_fit=True)

    # Slide 2 – Agenda (single slide; ignore overflow items)
    s2 = prs.slides.add_slide(blank)
    apply_bg(s2)
    d2 = pos.get(2, {})
    subtitle2 = str(d2.get("subtitle") or "").strip() or None
    _render_agenda_zebra_autofit(prs, s2, d2.get("bullets", [])[:32], pal=pal, title="Agenda", subtitle=subtitle2, is_cont=False)

    # Slide 3 – Background as Timeline
    s3 = prs.slides.add_slide(blank)
    apply_bg(s3)
    d3 = pos.get(3, {})
    stages: List[str] = []
    intro = (d3.get("paragraph") or "").strip()
    stages += (d3.get("bullets") or d3.get("left_points") or [])
    if not stages and d3.get("right_points"):
        stages = d3["right_points"][:4]
    # Ensure topic-aware seeds if we have < 3 items
    if len(stages) < 3:
        topic1 = (pos.get(1, {}) or {}).get("title") or ""
        agenda = (pos.get(2, {}) or {}).get("bullets") or []
        derived = _derive_evolution_stages_for_topic(topic1, agenda, intro)
        for lab in derived:
            if len(stages) >= 3:
                break
            if isinstance(lab, str) and lab.strip():
                stages.append(lab.strip())

    # Background timeline: render a single slide and ignore any overflow stages
    _render_background_timeline_autofit(prs, s3, stages, pal=pal, title="Background — Evolution", is_cont=False)

    # Slide 4 – Key Concepts as Card Grid
    s4 = prs.slides.add_slide(blank)
    apply_bg(s4)
    d4 = pos.get(4, {})
    # Topic from slide 4, else slide 1 title
    topic4 = (d4.get("topic") or d4.get("title") or (pos.get(1, {}) or {}).get("title") or "General Topic")
    # Aggregate signals from multiple slides to avoid empty synthesis
    bullets4: List[str] = []
    bullets4 += (d4.get("left_points") or [])
    bullets4 += (d4.get("right_points") or [])
    bullets4 += (d4.get("agenda") or [])
    bullets4 += (d4.get("intro_bullets") or [])
    # Pull from Agenda (slide 2)
    d2s = pos.get(2, {}) or {}
    bullets4 += (d2s.get("bullets") or [])
    # Pull from Background (slide 3)
    d3s = pos.get(3, {}) or {}
    bullets4 += (d3s.get("bullets") or [])
    bullets4 += (d3s.get("left_points") or [])
    bullets4 += (d3s.get("right_points") or [])
    if d3s.get("paragraph"):
        bullets4.append(str(d3s.get("paragraph")))
    # Remove obvious guide words
    bullets4 = [b for b in bullets4 if isinstance(b, str) and b.strip()]
    concepts4 = synthesize_key_concepts(str(topic4), bullets4, n=6)
    # Acceptance checks: remove placeholders, enforce length constraints, ban phrases
    def _ok_len(t: str, d: str) -> bool:
        tw = len((t or "").split())
        dw = len((d or "").split())
        return (1 <= tw <= 6) and (10 <= dw <= 18)
    filtered = []
    seen = set()
    for c in concepts4:
        t = (c.get("title") or "").strip()
        d = (c.get("one_liner") or "").strip()
        if not t or not d:
            continue
        lowd = d.lower()
        if any(b in lowd for b in BANNED):
            continue
        if is_placeholder(t) or is_placeholder(d):
            continue
        if not _ok_len(t, d):
            continue
        lo = t.lower()
        if lo in seen:
            continue
        seen.add(lo)
        filtered.append({"title": t, "one_liner": d})
    concepts4 = filtered or concepts4
    titles4 = [c.get("title", "").strip() for c in concepts4 if c.get("title")]
    desc_map4 = {c["title"]: c.get("one_liner", "").strip() for c in concepts4 if c.get("title")}
    # Acceptance guard: ensure non-empty titles by falling back to aggregated signals
    if not titles4:
        fallback = bullets4[:6]
        titles4 = [t for t in fallback if isinstance(t, str) and t.strip()][:6]
        if titles4 and not desc_map4:
            desc_map4 = {t: t for t in titles4}
    # Final safety: derive from topic if still empty (prevents blank slide)
    if not titles4:
        base = str(topic4).strip() or "Overview"
        titles4 = [base]
        if not desc_map4:
            desc_map4 = {base: f"{base} — practical implications, measurable outcomes, and owner alignment across initiatives."}
    # Enforce at least 4 concept cards by borrowing from signals or curated defaults
    curated = [
        "Strategy Alignment", "Workflow Automation", "Data Governance", "Impact Measurement",
        "Change Management", "Risk & Ethics",
    ]
    seen_lower = {t.lower() for t in titles4}
    # harvest additional labels from aggregated bullets first
    for lab in bullets4:
        if len(titles4) >= 4:
            break
        t = str(lab or "").strip()
        if not t or t.lower() in seen_lower or t.lower() in {"tbd", "(to be refined)", "-", "•"}:
            continue
        titles4.append(t); seen_lower.add(t.lower())
        if t not in desc_map4:
            desc_map4[t] = "Brief explanation to frame relevance and typical impact."
    # curated fallback
    for t in curated:
        if len(titles4) >= 4:
            break
        if t.lower() in seen_lower:
            continue
        titles4.append(t); seen_lower.add(t.lower())
        if t not in desc_map4:
            desc_map4[t] = "Brief explanation to frame relevance and typical impact."
    # Key Concepts: render a single card grid slide and drop any overflow concepts
    _render_key_concepts_cards_autofit(prs, s4, titles4, desc_map4, pal=pal, title="Key Concepts", is_cont=False)

    # Slide 5 – Comparison (Table)
    s5 = prs.slides.add_slide(blank)
    apply_bg(s5)
    d5 = pos.get(5, {})
    # headers and rows
    def _clean_points(items: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for s in items or []:
            t = str(s or "").strip()
            if not t or t.lower() in {"tbd", "(to be refined)", "-", "•"}:
                continue
            lo = t.lower()
            if lo in seen:
                continue
            seen.add(lo); out.append(t)
        return out
    left_pts = _clean_points((d5.get("left", {}) or {}).get("points", []))
    right_pts = _clean_points((d5.get("right", {}) or {}).get("points", []))
    # If too thin, synthesize from agenda/background/key-concepts signals
    if len(left_pts) < 3 or len(right_pts) < 3:
        d2s = pos.get(2, {}) or {}
        d3s = pos.get(3, {}) or {}
        d4s = pos.get(4, {}) or {}
        signals: List[str] = []
        signals += (d2s.get("bullets") or [])
        signals += (d3s.get("bullets") or []) + (d3s.get("left_points") or []) + (d3s.get("right_points") or [])
        # prefer key concept titles
        kc_titles: List[str] = []
        for c in (d4s.get("concepts") or []):
            if isinstance(c, dict):
                t = (c.get("title") or "").strip()
                if t:
                    kc_titles.append(t)
        signals = kc_titles + signals
        pool = _clean_points(signals)
        # distribute alternately into left/right until each has 3
        i = 0
        while (len(left_pts) < 3 or len(right_pts) < 3) and i < len(pool):
            target = left_pts if len(left_pts) <= len(right_pts) else right_pts
            target.append(pool[i])
            i += 1
        # curated defaults if still short
        defaults = [
            "Manual hand-offs", "Spreadsheet tracking", "Email-driven updates",
            "Automated pipeline", "Dashboards & alerts", "Predictive insights",
        ]
        j = 0
        while len(left_pts) < 3 and j < len(defaults):
            left_pts.append(defaults[j]); j += 1
        while len(right_pts) < 3 and j < len(defaults):
            right_pts.append(defaults[j]); j += 1
    # clamp number of body rows to 3..6
    body_rows = max(3, min(max(len(left_pts), len(right_pts)), 6))
    total_rows = body_rows + 1  # include header
    table = _add_table(s5, 1.6665, 2.2, 10.0, 3.0, rows=total_rows, cols=2)
    headers = d5.get("headers") or [
        (d5.get("left", {}) or {}).get("heading") or "Option A",
        (d5.get("right", {}) or {}).get("heading") or "Option B",
    ]
    # ensure 2 headers
    if len(headers) < 2:
        headers = (headers + ["Option B"])[:2]

    # Guardrail: if headers are the generic 'Traditional' vs 'AI-Driven' but the topic is NOT about AI,
    # rewrite them to a topic-appropriate pair based on the agenda/topic.
    topic1 = (pos.get(1, {}) or {}).get("title") or ""
    agenda_bullets = (pos.get(2, {}) or {}).get("bullets") or []

    def _needs_ai_traditional_rewrite(hdrs: List[str]) -> bool:
        a = (hdrs[0] or "").strip().lower()
        b = (hdrs[1] or "").strip().lower()
        is_ai_pair = a == "traditional" and b in {"ai-driven", "ai driven", "ai"}
        topic_mentions_ai = "ai" in (topic1 or "").lower()
        agenda_mentions_ai = any("ai" in str(x).lower() for x in (agenda_bullets or []))
        return is_ai_pair and not (topic_mentions_ai or agenda_mentions_ai)

    def _derive_headers_for_topic() -> List[str]:
        text = " ".join([str(topic1)] + [str(x) for x in (agenda_bullets or [])]).lower()
        # Mental health awareness heuristics
        if any(k in text for k in ["mental health", "wellbeing", "well-being", "well being"]):
            # Use a neutral, accurate dimension for awareness topics
            return ["Awareness & Education", "Support & Services"]
        if "marketing" in text or "campaign" in text:
            return ["Organic Channels", "Paid Channels"]
        if "covid" in text or "pandemic" in text:
            return ["Pre-COVID", "Post-COVID"]
        if "markets" in text or "regions" in text:
            return ["Developed Markets", "Emerging Markets"]
        # Fallback neutral labels
        return ["Option A", "Option B"]

    if _needs_ai_traditional_rewrite(headers):
        headers = _derive_headers_for_topic()

    # Smart title derived from headers
    def _clean_label(s: str) -> str:
        t = str(s or "").strip()
        if not t:
            return ""
        t = re.sub(r"^(option|approach|plan|path|model)\s*[:#\-]?\s*", "", t, flags=re.I)
        t = re.sub(r"\b(vs\.?|versus)\b", "", t, flags=re.I)
        t = re.sub(r"\s{2,}", " ", t).strip()
        return t

    a, b = _clean_label(headers[0]), _clean_label(headers[1])
    if a and b and a.lower() != b.lower():
        comp_title = f"{a} vs {b}"
    elif a:
        comp_title = f"{a}: Side-by-Side"
    elif b:
        comp_title = f"{b}: Side-by-Side"
    else:
        base = (pos.get(1, {}) or {}).get("title") or "Comparison"
        comp_title = f"{base}: Alternatives"

    _add_textbox(s5, 1.0, 0.9, 11.3, 1.0, comp_title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)
    # Style header row
    for c in range(2):
        cell = table.cell(0, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(*pal["primary"])
        _set_cell_text(cell, headers[c] or ("Option A" if c == 0 else "Option B"), size=20, bold=True, align_center=True, color=(255, 255, 255))
        # Header thin inner borders
        try:
            from pptx.util import Pt
            g1 = RGBColor(209, 213, 219)  # cool gray
            g2 = RGBColor(229, 231, 235)  # lighter for verticals
            cell.border_bottom.color.rgb = g1
            cell.border_bottom.width = Pt(0.75)
            if c == 0:
                cell.border_left.color.rgb = g2; cell.border_left.width = Pt(0.5)
            cell.border_right.color.rgb = g2; cell.border_right.width = Pt(0.5)
            if c == 1:
                cell.border_right.color.rgb = g2; cell.border_right.width = Pt(0.5)
        except Exception:
            pass
    # rows
    # Body text should be dark against the light table fills across themes
    body_text_rgb_local = (31, 41, 55)
    for r in range(body_rows):
        li = left_pts[r] if r < len(left_pts) else ""
        ri = right_pts[r] if r < len(right_pts) else ""
        _set_cell_text(table.cell(r + 1, 0), str(li), size=18, color=body_text_rgb_local)
        _set_cell_text(table.cell(r + 1, 1), str(ri), size=18, color=body_text_rgb_local)
        if r % 2 == 1:
            for c in range(2):
                table.cell(r + 1, c).fill.solid()
                table.cell(r + 1, c).fill.fore_color.rgb = RGBColor(*pal["secondary"])
        # Thin inner borders on each body row cell
        try:
            from pptx.util import Pt
            g1 = RGBColor(209, 213, 219)
            g2 = RGBColor(229, 231, 235)
            for cidx in range(2):
                cobj = table.cell(r + 1, cidx)
                # horizontal separators
                cobj.border_top.color.rgb = g1; cobj.border_top.width = Pt(0.5)
                cobj.border_bottom.color.rgb = g1; cobj.border_bottom.width = Pt(0.5)
                # vertical separators
                if cidx == 0:
                    cobj.border_left.color.rgb = g2; cobj.border_left.width = Pt(0.5)
                cobj.border_right.color.rgb = g2; cobj.border_right.width = Pt(0.5)
        except Exception:
            pass

    # Slide 6 – Key Takeaways (Infographic, python-pptx only)
    s6 = prs.slides.add_slide(blank)
    apply_bg(s6)
    d6 = pos.get(6, {})
    labels = d6.get("takeaways") or []
    # If nothing provided, derive from prior slide signals
    if not _clean_takeaways(labels):
        d2s = pos.get(2, {}) or {}
        d3s = pos.get(3, {}) or {}
        d4s = pos.get(4, {}) or {}
        d5s = pos.get(5, {}) or {}
        signals: List[str] = []
        # Agenda bullets
        signals += (d2s.get("bullets") or [])
        # Background bullets/points/paragraph
        signals += (d3s.get("bullets") or []) + (d3s.get("left_points") or []) + (d3s.get("right_points") or [])
        if d3s.get("paragraph"):
            signals.append(str(d3s.get("paragraph")))
        # Key Concepts titles
        kc = d4s.get("concepts") or []
        for c in kc:
            if isinstance(c, dict):
                t = (c.get("title") or "").strip()
                if t:
                    signals.append(t)
            elif isinstance(c, str):
                signals.append(c)
        # Comparison headers and points
        signals += (d5s.get("headers") or [])
        signals += ((d5s.get("left", {}) or {}).get("points") or [])
        signals += ((d5s.get("right", {}) or {}).get("points") or [])
        labels = _clean_takeaways(signals, limit=5)
        # Ensure at least 4 unique, human-readable fillers
        topic6 = (d6.get("topic") or (pos.get(1, {}) or {}).get("title") or "Key Takeaways")
        default_fillers = [
            "Automate pre-production tasks",
            "Enhance VFX with AI assists",
            "Accelerate post-production",
            "Improve distribution targeting",
            "Plan AI adoption & policy",
        ]
        i = 1
        while len(labels) < 4:
            # try from curated defaults first, then numbered topic-based
            if default_fillers:
                filler = default_fillers.pop(0)
            else:
                filler = f"{topic6} — outcome {i}"
                i += 1
            if filler.lower() not in {s.lower() for s in labels}:
                labels.append(filler)

    # Render as chevron roadmap (python-pptx only)
    render_takeaways_chevrons(
        s6,
        labels,
        title="Key Takeaways",
        kicker="At-a-glance summary",
        title_color=(255, 255, 255),
        pal=pal,
    )
    # Final safeguard: remove any existing title shape and add a new white textbox (non-placeholder)
    try:
        # Remove any existing title text shapes that contain the phrase
        for shp in list(s6.shapes):
            if not hasattr(shp, "text_frame"):
                continue
            tf = shp.text_frame
            if any("Key Takeaways" in ((p.text or "") + "".join([getattr(r, "text", "") for r in p.runs])) for p in tf.paragraphs):
                try:
                    elm = shp.element
                    elm.getparent().remove(elm)
                except Exception:
                    pass
        # Overlay our own title in white, slightly lower to avoid any theme placeholder area
        safe_title = "Key\u00A0Takeaways"  # NBSP to break any theme keyword binding while looking identical
        box = _add_textbox(s6, 1.0, 1.05, 10.8, 0.95, safe_title, font_size_pt=42, bold=True, color=(255, 255, 255), word_wrap=True, auto_fit=True)
        # Force white at run level to avoid theme overrides
        try:
            tf2 = box.text_frame
            for p in tf2.paragraphs:
                for r in p.runs:
                    r.font.color.rgb = RGBColor(255, 255, 255)
        except Exception:
            pass
    except Exception:
        pass
    # Absolute override: set any text within top 2.0in to white
    try:
        for shp in s6.shapes:
            if not hasattr(shp, "text_frame"):
                continue
            if shp.top <= Inches(2.0):
                tf = shp.text_frame
                for p in tf.paragraphs:
                    try:
                        p.font.color.rgb = RGBColor(255, 255, 255)
                    except Exception:
                        pass
                    for r in p.runs:
                        try:
                            r.font.color.rgb = RGBColor(255, 255, 255)
                        except Exception:
                            pass
    except Exception:
        pass
    _mark_takeaways_rendered()

    # Capture Thank You content source early; render it at the very end
    d7 = pos.get(7, {})

    # --- Extra Slides (8+) Intelligent Rendering ---
    def _render_two_col_bullets(slide, left_pts: List[str], right_pts: List[str]):
        """Render a two-column bullets slide using the same deck-level bullet style
        and color logic as render_double (consistent per deck).
        """
        left_pts = [str(p).strip() for p in (left_pts or []) if str(p).strip()]
        right_pts = [str(p).strip() for p in (right_pts or []) if str(p).strip()]

        # Colors derived strictly from the current theme palette so that
        # bullets and number icons stay on-brand and consistent. We use a
        # small, repeating set per column instead of arbitrary bright
        # palettes.
        if theme_key == "PULSE":
            accent = _pulse_bullet_accent()
            bullet_cols = [accent, accent, accent]
            left_colors = bullet_cols
            right_colors = bullet_cols
        else:
            bullet_cols = _bullet_colors()
            left_colors = bullet_cols
            right_colors = [bullet_cols[1], bullet_cols[0], bullet_cols[2]]

        def _lighter(rgb: Tuple[int,int,int], pct: float = 0.35) -> Tuple[int,int,int]:
            r,g,b = rgb
            return (min(255, int(r + (255-r)*pct)), min(255, int(g + (255-g)*pct)), min(255, int(b + (255-b)*pct)))

        # Layout metrics consistent with render_double
        gutter = 0.9
        margin = 1.2
        col_w = (SLIDE_W_IN - 2 * margin - gutter) / 2.0
        left_x = margin
        right_x = margin + col_w + gutter
        top_y = 2.0
        row_h = 1.25
        gap_y = 0.25

        def render_col(items: List[str], base_x: float, colors: List[Tuple[int, int, int]], start_idx: int) -> int:
            y = top_y
            subset = items[:3]
            for i, lab in enumerate(subset):
                idx = start_idx + i
                global CURRENT_DOUBLE_BULLET_STYLE
                global DOUBLE_BULLET_STYLE_INDEX
                style_id = (CURRENT_DOUBLE_BULLET_STYLE if 'CURRENT_DOUBLE_BULLET_STYLE' in globals() else (DOUBLE_BULLET_STYLE_INDEX % 4))
                col = colors[i % len(colors)]
                if style_id == 0:
                    dot = slide.shapes.add_shape(MsoShape.OVAL, Inches(base_x), Inches(y + 0.20), Inches(0.36), Inches(0.36))
                    dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*col)
                    dot.line.fill.background()
                elif style_id == 1:
                    bar = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(base_x), Inches(y + 0.22), Inches(0.48), Inches(0.22))
                    bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*col)
                    bar.line.fill.background()
                    try:
                        bar.radius = 0.12
                    except Exception:
                        pass
                elif style_id == 2:
                    outer = slide.shapes.add_shape(MsoShape.OVAL, Inches(base_x), Inches(y + 0.20), Inches(0.36), Inches(0.36))
                    outer.fill.solid(); outer.fill.fore_color.rgb = RGBColor(*col)
                    outer.line.fill.background()
                    try:
                        outer.shadow.inherit = False; outer.shadow.visible = True; outer.shadow.blur = 8; outer.shadow.distance = 1
                    except Exception:
                        pass
                    inner = slide.shapes.add_shape(MsoShape.OVAL, Inches(base_x + 0.06), Inches(y + 0.26), Inches(0.24), Inches(0.24))
                    inner.fill.solid(); inner.fill.fore_color.rgb = RGBColor(*_lighter(col, 0.5))
                    inner.line.fill.background()
                else:
                    circ = slide.shapes.add_shape(MsoShape.OVAL, Inches(base_x), Inches(y + 0.16), Inches(0.40), Inches(0.40))
                    circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*col)
                    circ.line.fill.background()
                    tfc = circ.text_frame; tfc.text = str(idx + 1)
                    for pnum in tfc.paragraphs:
                        for rr in pnum.runs:
                            rr.font.bold = True; rr.font.size = Pt(14); rr.font.color.rgb = RGBColor(*_contrast_text(col))
                        pnum.alignment = PP_ALIGN.CENTER

                # text block
                tf = slide.shapes.add_textbox(Inches(base_x + 0.60), Inches(y), Inches(col_w - 0.70), Inches(row_h - 0.05)).text_frame
                tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                try:
                    from pptx.enum.text import MSO_ANCHOR
                    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
                except Exception:
                    pass
                p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
                head, tail = _split_head_tail(lab)
                r1 = p.add_run(); r1.text = (head or lab); r1.font.size = Pt(20); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal["text"])
                if tail:
                    p2 = tf.add_paragraph(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (100,116,139))))
                y += row_h + gap_y
            return start_idx + len(subset)

        next_idx = render_col(left_pts, left_x, left_colors, 0)
        render_col(right_pts, right_x, right_colors, next_idx)

    def _render_comparison_table(slide, headers: List[str], left_pts: List[str], right_pts: List[str]):
        body_rows = max(3, min(max(len(left_pts), len(right_pts)), 6))
        total_rows = body_rows + 1
        table = _add_table(slide, 1.6665, 2.2, 10.0, 3.0, rows=total_rows, cols=2)
        # headers
        if len(headers) < 2:
            headers = (headers + ["Option B"] )[:2]
        # Determine styling based on theme brightness
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        # Header: force vivid blue on dark theme to match reference; keep theme primary on light
        header_rgb = (59, 130, 246) if dark_bg else pal["primary"]
        for c in range(2):
            cell = table.cell(0, c)
            cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor(*header_rgb)
            _set_cell_text(cell, headers[c] or ("Option A" if c == 0 else "Option B"), size=20, bold=True, align_center=True, color=(255, 255, 255))

        # Body rows: white with soft gray alternation and dark text on dark theme
        light_alt = pal.get("secondary", (240, 245, 255))
        body_text_rgb = (24, 24, 27) if dark_bg else pal["text"]
        base_fill = (255, 255, 255) if dark_bg else None
        alt_fill = (248, 250, 252) if dark_bg else light_alt

        for r in range(body_rows):
            li = left_pts[r] if r < len(left_pts) else ""
            ri = right_pts[r] if r < len(right_pts) else ""
            _set_cell_text(table.cell(r + 1, 0), str(li), size=18, color=body_text_rgb)
            _set_cell_text(table.cell(r + 1, 1), str(ri), size=18, color=body_text_rgb)
            for c in range(2):
                if dark_bg:
                    fill_rgb = alt_fill if (r % 2 == 1) else base_fill
                    table.cell(r + 1, c).fill.solid(); table.cell(r + 1, c).fill.fore_color.rgb = RGBColor(*fill_rgb)
                    try:
                        table.cell(r + 1, c).border_bottom.color.rgb = RGBColor(209, 213, 219)
                        table.cell(r + 1, c).border_right.color.rgb = RGBColor(229, 231, 235)
                    except Exception:
                        pass
                else:
                    if r % 2 == 1:
                        table.cell(r + 1, c).fill.solid(); table.cell(r + 1, c).fill.fore_color.rgb = RGBColor(*alt_fill)
    # Add cell padding and vertical centering so text doesn't sit on borders
    try:
        from pptx.util import Pt
        from pptx.enum.text import MSO_ANCHOR
        for rr in range(total_rows):
            for cc in range(2):
                cell = table.cell(rr, cc)
                cell.margin_left = Pt(6)
                cell.margin_right = Pt(6)
                cell.margin_top = Pt(4)
                cell.margin_bottom = Pt(4)
                try:
                    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                except Exception:
                    pass
                try:
                    cell.text_frame.word_wrap = True
                except Exception:
                    pass
    except Exception:
        pass
    # Avoid overlay grid to prevent lines over text; rely on row fills and header styling

    extra_keys = sorted([k for k in pos.keys() if isinstance(k, int) and k >= 8])
    for i in extra_keys:
        data = pos.get(i, {}) or {}
        # Normalize content sources
        content = data.get("content") or data
        left = (content.get("left") or {}) if isinstance(content, dict) else {}
        right = (content.get("right") or {}) if isinstance(content, dict) else {}
        table_obj = content.get("table") if isinstance(content, dict) else None

        def _has_text(x) -> bool:
            if isinstance(x, str):
                return bool(x.strip())
            if isinstance(x, list):
                return any(str(v).strip() for v in x)
            if isinstance(x, dict):
                return any(_has_text(v) for v in x.values())
            return False

        meaningful = False
        if isinstance(content, dict):
            meaningful = any([
                _has_text(content.get("bullets")),
                _has_text(content.get("left_points")),
                _has_text(content.get("right_points")),
                _has_text(content.get("paragraph")),
                _has_text((content.get("table") or {}).get("left")),
                _has_text((content.get("table") or {}).get("right")),
            ])
        if not meaningful:
            # Skip creating a blank extra slide
            continue

        s = prs.slides.add_slide(blank)
        apply_bg(s)
        title = (data.get("title") or slide_heading(i) or f"Slide {i}")
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL)

        # 1) Table-style comparison if available
        if isinstance(table_obj, dict) and table_obj.get("left") and table_obj.get("right"):
            headers = [
                (table_obj.get("left") or {}).get("heading") or left.get("heading") or "Option A",
                (table_obj.get("right") or {}).get("heading") or right.get("heading") or "Option B",
            ]
            left_pts = (table_obj.get("left") or {}).get("points") or left.get("points") or []
            right_pts = (table_obj.get("right") or {}).get("points") or right.get("points") or []
            _render_comparison_table(s, headers, left_pts, right_pts)
            continue

        # 2) Double-column bullets
        lp = content.get("left_points") if isinstance(content, dict) else None
        rp = content.get("right_points") if isinstance(content, dict) else None
        if isinstance(lp, list) or isinstance(rp, list):
            _render_two_col_bullets(s, lp or [], rp or [])
            continue

        # 3) Single-column bullets
        bullets = content.get("bullets") if isinstance(content, dict) else None
        if isinstance(bullets, list) and bullets:
            _add_bullets(s, 1.0, 2.0, 11.0, 4.5, bullets, font_size_pt=20, color=pal["text"]) 
            continue

        # 4) Paragraph fallback
        para = str(content.get("paragraph") or "").strip() if isinstance(content, dict) else ""
        if para:
            _add_textbox(s, 1.0, 2.0, 11.0, 4.5, para, font_size_pt=20, color=pal["text"], word_wrap=True, auto_fit=True)

    # Finally, render Thank You / CTA as the last slide
    s_last = prs.slides.add_slide(blank)
    apply_bg(s_last)
    dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
    _add_textbox(s_last, 1.0, 2.8, 11.3, 1.2, "Thank You", font_size_pt=40, bold=True, align="center", color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
    if d7.get("cta"):
        _add_textbox(s_last, 3.0, 4.2, 7.3, 0.8, d7.get("cta", ""), font_size_pt=18, align="center", color=pal["text"])

    prs.save(out_path)
    return out_path


def build_pptx_from_slides(slides: List[Dict[str, Any]], out_path: str, *, theme_name: str | None = None, render_exact: bool = False, me_name: str | None = None) -> str:
    """
    Build a PPTX deck following the provided AI outline order.
    Ensures the deck includes these layouts at least once:
      - Title slide
      - Agenda slide
      - Double column slide
      - Table slide (comparison)
      - Infographic slide (key takeaways)
      - Thank You slide (last)
    Does NOT add Background/Evolution unless present in the outline.
    """
    SLIDE_DEBUG = os.getenv("SLIDE_DEBUG", "0") == "1"
    if SLIDE_DEBUG:
        try:
            slide_types = [s.get("type", "unknown") for s in slides]
            print(f"slide_debug:render_input slide_types={slide_types}")
        except Exception:
            pass
    
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W_IN)
    prs.slide_height = Inches(SLIDE_H_IN)

    # Reset per-deck variant rotation indices for deterministic output.
    # Seed is stable for a given deck input + theme so rotation is deterministic.
    seed_title = ""
    try:
        seed_title = str((slides or [{}])[0].get("title") or "").strip()
    except Exception:
        seed_title = ""
    seed_salt = ""
    try:
        seed_salt = str((slides or [{}])[0].get("_deck_id") or "").strip()
    except Exception:
        seed_salt = ""
    seed = f"{(theme_name or '').strip().upper()}|{seed_title}|{len(slides or [])}|{seed_salt}"
    reset_variant_rotation(seed=seed)
    global IMPACT_MAP_VARIANT_INDEX
    IMPACT_MAP_VARIANT_INDEX = 0
    global TAKEAWAYS_RENDERED
    TAKEAWAYS_RENDERED = False
    global TAKEAWAYS_STYLE_GEN_INDEX
    takeaways_style_idx = TAKEAWAYS_STYLE_GEN_INDEX % 4
    TAKEAWAYS_STYLE_GEN_INDEX += 1

    global COMPARISON_STYLE_GEN_INDEX
    comparison_style_idx = COMPARISON_STYLE_GEN_INDEX % 2
    COMPARISON_STYLE_GEN_INDEX += 1

    # Seed comparison variant family index so consecutive exports start with a
    # different comparison style, while still alternating within the deck.
    try:
        VARIANT_ROTATOR._family_idx["comparison"] = int(comparison_style_idx)
    except Exception:
        pass

    blank = prs.slide_layouts[6]
    pal = _theme(theme_name)

    # Normalize theme key so we can branch behavior per theme.
    theme_key = (theme_name or "SLEEK").strip().upper()
    is_sleek_theme = theme_key == "SLEEK"
    uses_single_bullet_color = theme_key in {"SLEEK", "MINIMAL FLUX", "BLUE HORIZON"}
    pulse_bullet_slide_index = 0

    current_render_idx = 0

    def _debug_variant(*, family: str, chosen: str, eligible: List[str], forced: str | None) -> None:
        if not SLIDE_DEBUG:
            return
        try:
            print(
                f"slide_debug:variant idx={current_render_idx} family={family} chosen={chosen} eligible={eligible} forced={forced}"
            )
        except Exception:
            pass

    def _pulse_bullet_accent() -> Tuple[int, int, int]:
        nonlocal pulse_bullet_slide_index
        primary_p = pal.get("primary", (220, 38, 38))
        secondary_p = pal.get("secondary", primary_p)
        pulse_bullet_slide_index += 1
        accent_a = secondary_p
        accent_b = primary_p
        return accent_a if (pulse_bullet_slide_index % 2 == 1) else accent_b

    # Default bullet icon color: theme primary. For non-SLEEK themes we keep
    # using a small theme-based palette in _bullet_colors; this value mainly
    # powers SLEEK's per-deck rotation.
    bullet_icon_rgb: Tuple[int, int, int] = pal.get("primary", (59, 130, 246))

    if uses_single_bullet_color:
        # Per-deck bullet icon color rotation (generations 1–5), keeping a
        # single bullet color within each SLEEK deck. Rotation persists across
        # runs via the module-level BULLET_COLOR_GEN_INDEX.
        global BULLET_COLOR_GEN_INDEX
        gen_idx = BULLET_COLOR_GEN_INDEX % 5
        BULLET_COLOR_GEN_INDEX += 1

        _bp = pal.get("primary", (59, 130, 246))
        _bs = pal.get("secondary", _bp)
        _bg = pal.get("bg", (255, 255, 255))

        def _lighten(rgb: Tuple[int,int,int], delta: int) -> Tuple[int,int,int]:
            return _shade(rgb, abs(delta))

        medium_light = _lighten(_bp, 20)
        lighter = _lighten(_bp, 40)
        accent = _lighten(_bp, 10)

        if gen_idx == 0:      # Generation 1 – primary theme color
            _bullet_base = _bp
        elif gen_idx == 1:    # Generation 2 – medium-light theme color
            _bullet_base = medium_light
        elif gen_idx == 2:    # Generation 3 – secondary theme color
            _bullet_base = _bs
        elif gen_idx == 3:    # Generation 4 – lighter theme color
            _bullet_base = lighter
        else:                 # Generation 5 – accent theme color
            _bullet_base = accent

        def _ensure_bright_bullet(rgb: Tuple[int,int,int]) -> Tuple[int,int,int]:
            r, g, b = rgb
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            br, bgc, bb = _bg
            lum_bg = 0.2126 * br + 0.7152 * bgc + 0.0722 * bb
            # Avoid dark / greyish bullets: nudge toward a brighter tint
            if lum < 150:
                rgb = _shade(rgb, 40)
                r, g, b = rgb
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            # Ensure the bullet stands out from the background luminance
            if abs(lum - lum_bg) < 25:
                adjust = -30 if lum > lum_bg else 30
                rgb = _shade(rgb, adjust)
            return rgb

        bullet_icon_rgb = _ensure_bright_bullet(_bullet_base)

    # Title color for this build scope
    TITLE_COL = (255, 255, 255) if _is_dark_rgb(pal.get("bg", (255,255,255))) else pal["primary"]

    def apply_bg(slide):
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor(*pal["bg"])

    # Normalize and check required types
    def _ctype(sl: Dict[str, Any]) -> str:
        return (sl.get("type") or "").strip().lower()

    def _content(sl: Dict[str, Any]) -> Dict[str, Any]:
        c = sl.get("content")
        return c if isinstance(c, dict) else {}

    def _synthesize_topic_bullets(title: str) -> List[str]:
        t = (title or "").strip()
        if not t:
            return []
        # Deterministic, topic-injected bullets to avoid generic placeholders
        base = [
            f"Definition and scope of {t}",
            f"Key drivers influencing {t}",
            f"Opportunities and challenges in {t}",
            f"Recent examples or case studies in {t}",
            f"KPIs/metrics to track for {t}",
        ]
        # Ensure uniqueness and trim
        seen, out = set(), []
        for b in base:
            b2 = re.sub(r"\s{2,}", " ", b).strip()
            if b2 and b2.lower() not in seen:
                out.append(b2)
                seen.add(b2.lower())
        return out[:5]

    def _looks_placeholder_node_label(s: str) -> bool:
        txt = re.sub(r"\s+", " ", str(s or "").strip())
        if not txt:
            return True
        low = txt.lower()
        # Common placeholder patterns from LLM outputs
        if re.match(r"^(company|org|organization|client|vendor|customer|supplier)\s*[a-z]\b", low):
            return True
        if re.match(r"^(company|org|organization|client|vendor|customer|supplier)\s*\d+\b", low):
            return True
        if re.match(r"^(product|solution|platform)\s*[xyz]\b", low):
            return True
        if low in {"company a", "company b", "company c", "company d"}:
            return True
        if low in {"example", "example company", "sample", "sample company"}:
            return True
        return False

    def _sanitize_infographic_items(title: str, items: List[str]) -> List[str]:
        clean = [re.sub(r"\s+", " ", str(x or "").strip()) for x in (items or [])]
        clean = [x for x in clean if x]
        if not clean:
            return _synthesize_topic_bullets(title)[:5]
        placeholder_n = sum(1 for x in clean if _looks_placeholder_node_label(x))
        # If the majority are placeholders, replace the whole set.
        if placeholder_n >= max(1, int(len(clean) * 0.6)):
            return _synthesize_topic_bullets(title)[:5]
        # Otherwise, only replace placeholder entries.
        if placeholder_n:
            synth = _synthesize_topic_bullets(title)
            it = iter(synth)
            out2: List[str] = []
            for x in clean:
                if _looks_placeholder_node_label(x):
                    out2.append(next(it, x))
                else:
                    out2.append(x)
            return out2
        return clean

    types = [(_ctype(s) or "") for s in (slides or [])]
    # If any slide carries `_render_exact`, we will not auto-pattern or synthesize
    render_exact = any(bool(getattr(s, "get", lambda *_: None)("_render_exact")) for s in (slides or []))
    has_title = any(t == "title" for t in types)
    has_agenda = any(t == "agenda" for t in types)
    has_double = any(t in {"double_column", "deep_dive"} for t in types)
    has_table = any(t in {"comparison", "table"} for t in types)
    has_info = any(t in {"infographic", "key_takeaways"} for t in types)
    has_thanks = any(t in {"thank_you", "cta"} for t in types)

    slides_norm = list(slides or [])

    # Titles from outline to inform synthesized content
    def outline_titles(max_n: int | None = None) -> List[str]:
        keep_types = {"title", "agenda", "thank_you", "cta"}
        titles = []
        for s in slides_norm:
            if _ctype(s) in keep_types:
                continue
            t = (s.get("title") or "").strip()
            if t:
                titles.append(t)
        return titles[:max_n] if max_n else titles

    # Ensure first slide is Title
    if not has_title:
        # Try infer from first slide title, else generic placeholder
        first_title = (slides_norm[0].get("title") if slides_norm else None) or "Presentation Title"
        slides_norm.insert(0, {"type": "title", "title": first_title, "subtitle": ""})
        has_title = True

    # Ensure/augment Agenda based on outline titles
    def _is_generic_agenda(items: List[str]) -> bool:
        vals = [re.sub(r"\W+", "", s).lower() for s in items]
        generic = {"overview", "discussion", "nextsteps"}
        return 0 < len(vals) <= 3 and set(vals).issubset(generic)

    if has_agenda:
        # If agenda exists but has empty or generic bullets, replace with outline titles
        ag_idx = next(i for i, s in enumerate(slides_norm) if _ctype(s) == "agenda")
        ag = slides_norm[ag_idx]
        c = _content(ag)
        bullets = [str(x) for x in (c.get("bullets") or []) if str(x).strip()]
        if not bullets or _is_generic_agenda(bullets):
            new_bullets = _clean_agenda_items(outline_titles(max_n=12), limit=12)
            ag["content"] = {"bullets": new_bullets}
    else:
        # Prefer AI outline slide titles as agenda items
        agenda_src: List[str] = outline_titles(max_n=12)
        if not agenda_src:
            # Fallback: collect any bullets already present
            for s in slides_norm:
                c = _content(s)
                if isinstance(c.get("bullets"), list):
                    agenda_src += [str(x) for x in c.get("bullets")]
        agenda_src = _clean_agenda_items(agenda_src, limit=12)
        # Insert after first title slide
        insert_idx = 1 if _ctype(slides_norm[0]) == "title" else 0
        slides_norm.insert(insert_idx, {"type": "agenda", "content": {"bullets": agenda_src}})
        has_agenda = True

    if not render_exact:
        basic_types = {"title", "agenda", "thank_you", "cta", "bullets"}
        types_now = [(_ctype(s) or "") for s in (slides_norm or [])]
        has_structured = any(t and t not in basic_types for t in types_now)

        if not has_structured:
            # Ensure at least one of each required content layout via pattern
            ag_idx = next((i for i, s in enumerate(slides_norm) if _ctype(s) == "agenda"), None)
            titles_seq = _content(slides_norm[ag_idx]).get("bullets", []) if ag_idx is not None else outline_titles(max_n=50)
            # Build a fresh list retaining pre-agenda slides
            prefix = slides_norm[:ag_idx + 1] if ag_idx is not None else slides_norm[:1]
            topic_slides: List[Dict[str, Any]] = []
            for i, t in enumerate(titles_seq, start=1):
                topic = str(t).strip()
                if not topic:
                    continue
                synth = _synthesize_topic_bullets(topic)
                pat = (i - 1) % 7  # 0..6
                if pat in {0, 1}:  # Topic 1 -> DC, Topic 2 -> DC
                    left = synth[::2][:6]
                    right = synth[1::2][:6]
                    topic_slides.append({"type": "double_column", "title": topic, "content": {"left_points": left, "right_points": right}})
                elif pat in {2, 3}:  # Topic 3 -> Table, Topic 4 -> Table
                    mid = max(1, len(synth)//2)
                    left_pts = synth[:mid][:6]
                    right_pts = synth[mid:][:6]
                    topic_slides.append({
                        "type": "comparison",
                        "title": topic,
                        "content": {"table": {"left": {"heading": "Aspects", "points": left_pts}, "right": {"heading": "Implications", "points": right_pts}}}
                    })
                elif pat in {4, 5}:  # Topic 5 -> Cards, Topic 6 -> Cards
                    topic_slides.append({"type": "cards", "title": topic, "content": {"cards": synth[:6]}})
                else:  # pat == 6 -> Infographic
                    topic_slides.append({"type": "infographic", "title": topic, "content": {"bullets": synth[:5]}})

            # Replace everything after agenda with pattern-generated topic slides
            if ag_idx is not None:
                slides_norm = prefix + topic_slides
            else:
                slides_norm = prefix + topic_slides

    # Ensure Thank You as last slide
    if not has_thanks:
        slides_norm.append({"type": "thank_you", "title": "Thank You", "cta": ""})
        has_thanks = True
    else:
        # Move any thank you to the end
        thanks = [s for s in slides_norm if _ctype(s) in {"thank_you", "cta"}]
        slides_norm = [s for s in slides_norm if _ctype(s) not in {"thank_you", "cta"}] + thanks[-1:]

    def _render_timeline(sl) -> None:
        content = _content(sl)
        title_txt = (sl.get("title") or content.get("title") or "Timeline").strip() or "Timeline"
        subtitle_override = (
            str(sl.get("subtitle") or "").strip()
            or str(content.get("subtitle") or "").strip()
            or None
        )
        raw = content.get("timeline")
        raw_items: List[Any] = raw if isinstance(raw, list) else []
        items: List[str] = []
        if isinstance(raw, list):
            for it in raw:
                if isinstance(it, dict):
                    lab = str(it.get("label") or it.get("title") or "").strip()
                    desc = str(it.get("description") or "").strip()
                    if lab and desc:
                        merged = f"{lab} — {desc}"
                        items.append(merged[:110])
                    elif lab:
                        items.append(lab[:110])
                    elif desc:
                        items.append(desc[:110])
                else:
                    s = str(it or "").strip()
                    if s:
                        items.append(s[:110])
                if len(items) >= 8:
                    break

        if not items:
            items = ["Step 1", "Step 2", "Step 3", "Step 4"]

        # Normalize to the supported range for the remaining timeline variants.
        if len(items) > 6:
            items = items[:6]
        if len(items) < 4:
            items = items + ([items[-1]] * (4 - len(items)))

        variant_raw = str(content.get("timeline_variant") or content.get("variant") or "").strip().lower()
        variant_map = {
            "ring": "ring_nodes",
            "rings": "ring_nodes",
            "ring_nodes": "ring_nodes",
            "ring-nodes": "ring_nodes",
            "circle": "ring_nodes",
            "circles": "ring_nodes",
            "circle_nodes": "ring_nodes",
            "circle-nodes": "ring_nodes",
            "tri": "tri_pennants",
            "tris": "tri_pennants",
            "triangle": "tri_pennants",
            "triangles": "tri_pennants",
            "pennant": "tri_pennants",
            "pennants": "tri_pennants",
            "tri_pennants": "tri_pennants",
            "tri-pennants": "tri_pennants",
            "triangle_pennants": "tri_pennants",
            "triangle-pennants": "tri_pennants",
            "callout": "callout_chevrons",
            "callouts": "callout_chevrons",
            "callout_chevrons": "callout_chevrons",
            "callout-chevrons": "callout_chevrons",
            "callout_timeline": "callout_chevrons",
            "callout-timeline": "callout_chevrons",
            "strip": "strip_columns",
            "columns": "strip_columns",
            "strip_columns": "strip_columns",
            "strip-columns": "strip_columns",
            "segmented": "strip_columns",
            "segmented_columns": "strip_columns",
            "segmented-columns": "strip_columns",
            "arc": "arc_progress",
            "curve": "arc_progress",
            "curved": "arc_progress",
            "arc_progress": "arc_progress",
            "arc-progress": "arc_progress",
            "curved_arc": "arc_progress",
            "curved-arc": "arc_progress",
        }
        forced = variant_map.get(variant_raw)

        # Eligibility gating: keep layouts that fit the content.
        pts = items[:6]
        n = len(pts)
        max_len = 0
        try:
            max_len = max((len(str(x)) for x in pts), default=0)
        except Exception:
            max_len = 0

        max_label_len = 0
        try:
            def _label_part(x: Any) -> str:
                s0 = str(x or "").strip()
                if "—" in s0:
                    return s0.split("—", 1)[0].strip()
                if " - " in s0:
                    return s0.split(" - ", 1)[0].strip()
                return s0
            max_label_len = max((len(_label_part(x)) for x in pts), default=0)
        except Exception:
            max_label_len = 0

        eligible: List[str] = []
        if n >= 4 and n <= 6:
            if max_label_len <= 26:
                eligible += ["ring_nodes", "tri_pennants"]
            if n >= 5 and max_label_len <= 22:
                eligible.append("callout_chevrons")
            if max_label_len <= 22:
                eligible.append("strip_columns")
            if n <= 5 and max_label_len <= 26:
                eligible.append("arc_progress")
        if not eligible:
            eligible = ["tri_pennants", "ring_nodes"]
        if forced and forced not in eligible:
            eligible.append(forced)

        chosen = choose_variant("timeline", eligible, forced=forced)
        _debug_variant(family="timeline", chosen=chosen, eligible=eligible, forced=forced)

        if chosen == "ring_nodes":
            render_timeline_ring_nodes(title_txt, (raw_items[:6] if raw_items else pts), title_txt, subtitle_override)
        elif chosen == "tri_pennants":
            render_timeline_tri_pennants(title_txt, (raw_items[:6] if raw_items else pts), title_txt, subtitle_override)
        elif chosen == "callout_chevrons":
            render_timeline_callout_chevrons(title_txt, (raw_items[:6] if raw_items else pts), title_txt, subtitle_override)
        elif chosen == "strip_columns":
            render_timeline_strip_columns(title_txt, (raw_items[:6] if raw_items else pts), title_txt, subtitle_override)
        elif chosen == "arc_progress":
            render_timeline_arc_progress(title_txt, (raw_items[:5] if raw_items else pts[:5]), title_txt, subtitle_override)
        else:
            render_timeline_tri_pennants(title_txt, (raw_items[:6] if raw_items else pts), title_txt, subtitle_override)

    def _render_roadmap(sl) -> None:
        """Render phase-based roadmap with title, date range, and description per phase."""
        content = _content(sl)
        title = sl.get("title", "Roadmap")
        # AI provides timeline structure, not phases
        phases = content.get("timeline", content.get("phases", []))
        if not isinstance(phases, list):
            phases = []
        phases = phases[:6]
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        nph = len(phases)
        if nph <= 0:
            return

        start_y = 2.2
        phase_h = 2.8
        margin_x = 0.9
        avail_w = SLIDE_W_IN - 2 * margin_x

        use_two_rows = nph >= 5
        if use_two_rows:
            top_n = (nph + 1) // 2
            rows = [phases[:top_n], phases[top_n:]]
            row_ys = [2.05, 4.85]
            phase_h = 2.15
        else:
            rows = [phases]
            row_ys = [start_y]

        for ridx, row in enumerate(rows):
            if not row:
                continue
            m = len(row)
            gap = 0.55 if m <= 4 else 0.35
            phase_w = (avail_w - gap * (m - 1)) / max(1, m)
            if phase_w > 2.8:
                phase_w = 2.8
            if phase_w < 1.8:
                gap = 0.25
                phase_w = (avail_w - gap * (m - 1)) / max(1, m)
                if phase_w < 1.6:
                    phase_w = 1.6
            total_used = m * phase_w + (m - 1) * gap
            x0 = margin_x + max(0.0, (avail_w - total_used) / 2)
            y0 = row_ys[ridx] if ridx < len(row_ys) else start_y

            for i, phase in enumerate(row):
                x = x0 + i * (phase_w + gap)

                phase_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y0), Inches(phase_w), Inches(phase_h))
                phase_box.fill.solid(); phase_box.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
                phase_box.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))

                phase_title = str((phase.get("title") if isinstance(phase, dict) else "") or "")[:50]
                _add_textbox(s, x + 0.15, y0 + 0.15, phase_w - 0.3, 0.5, phase_title,
                            font_size_pt=18, bold=True, color=pal["text"], word_wrap=True, auto_fit=True)

                date_range = str((phase.get("date_range") if isinstance(phase, dict) else "") or "")[:30]
                if date_range:
                    _add_textbox(s, x + 0.15, y0 + 0.65, phase_w - 0.3, 0.4, date_range,
                                font_size_pt=12, color=pal.get("muted", pal["text"]), word_wrap=True, auto_fit=True)

                desc = str((phase.get("description") if isinstance(phase, dict) else "") or "")[:150]
                if desc:
                    _add_textbox(s, x + 0.15, y0 + 1.05, phase_w - 0.3, max(0.3, phase_h - 1.20), desc,
                                font_size_pt=11, color=pal["text"], word_wrap=True, auto_fit=True)

                if i < m - 1:
                    arrow_w = max(0.22, min(0.46, gap * 0.85))
                    arrow_x = x + phase_w + (gap - arrow_w) / 2
                    arrow = s.shapes.add_shape(MsoShape.RIGHT_ARROW, Inches(arrow_x), Inches(y0 + phase_h/2 - 0.18), Inches(arrow_w), Inches(0.36))
                    arrow.fill.solid(); arrow.fill.fore_color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                    arrow.line.fill.background()

    def _render_swot(sl) -> None:
        """Render 2x2 SWOT grid with quadrants: Strengths, Weaknesses, Opportunities, Threats."""
        content = _content(sl)
        title = content.get("title", "SWOT Analysis")
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Grid layout: 2x2 (compute to always fit within slide bounds)
        margin_x = 1.0
        top_y = 2.05
        bottom_margin = 0.65
        gap_x, gap_y = 0.55, 0.45

        avail_w = max(6.0, SLIDE_W_IN - 2 * margin_x)
        avail_h = max(3.6, SLIDE_H_IN - bottom_margin - top_y)
        cell_w = max(3.0, (avail_w - gap_x) / 2.0)
        cell_h = max(1.6, (avail_h - gap_y) / 2.0)
        start_x, start_y = margin_x, top_y
        
        quadrants = [
            ("strengths", "Strengths", 0, 0),
            ("weaknesses", "Weaknesses", 1, 0),
            ("opportunities", "Opportunities", 0, 1),
            ("threats", "Threats", 1, 1)
        ]
        
        for key, label, col_idx, row_idx in quadrants:
            x = start_x + col_idx * (cell_w + gap_x)
            y = start_y + row_idx * (cell_h + gap_y)
            
            quadrant_data = content.get(key, {})
            points = quadrant_data.get("points", [])
            
            # Quadrant container
            quad_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(cell_h))
            quad_box.fill.solid(); quad_box.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            quad_box.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Quadrant title
            _add_textbox(
                s,
                x + 0.18,
                y + 0.12,
                cell_w - 0.36,
                0.46,
                label,
                font_size_pt=18,
                bold=True,
                color=pal["primary"],
                word_wrap=True,
                auto_fit=True,
            )
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:70]}" for p in points[:6])  # Max 6 points
                _add_textbox(
                    s,
                    x + 0.18,
                    y + 0.62,
                    cell_w - 0.36,
                    max(0.4, cell_h - 0.78),
                    bullet_text,
                    font_size_pt=13,
                    color=pal["text"],
                    word_wrap=True,
                    auto_fit=True,
                )

    def _render_kpi(sl) -> None:
        """Render 2-4 KPI metric cards with label, value, and optional caption."""
        content = _content(sl)
        title = content.get("title", "Key Metrics")
        metrics = content.get("metrics", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Layout: 2x2 grid for up to 4 metrics
        start_x, start_y = 1.0, 2.2
        card_w, card_h = 5.5, 2.0
        gap_x, gap_y = 0.8, 0.6
        
        for i, metric in enumerate(metrics[:4]):  # Max 4 metrics
            row, col = i // 2, i % 2
            x = start_x + col * (card_w + gap_x)
            y = start_y + row * (card_h + gap_y)
            
            # Metric card
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            card.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Label
            label = str(metric.get("label", ""))[:40]
            _add_textbox(s, x + 0.2, y + 0.15, card_w - 0.4, 0.4, label,
                        font_size_pt=14, bold=True, color=pal["text"])
            
            # Value (large)
            value = str(metric.get("value", ""))[:25]
            _add_textbox(s, x + 0.2, y + 0.55, card_w - 0.4, 0.6, value,
                        font_size_pt=28, bold=True, color=pal["primary"])
            
            # Caption
            caption = str(metric.get("caption", ""))[:80]
            if caption:
                _add_textbox(s, x + 0.2, y + 1.15, card_w - 0.4, card_h - 1.35, caption,
                            font_size_pt=10, color=pal.get("muted", pal["text"]))

    def _render_pricing(sl) -> None:
        """Render pricing comparison table with 2-4 plans and feature rows."""
        content = _content(sl)
        title = content.get("title", "Pricing Comparison")
        plans = content.get("plans", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        if not plans:
            return
            
        # Layout: table with plans as columns
        start_x, start_y = 1.0, 2.2
        margin_x = 1.0
        avail_w = max(7.0, SLIDE_W_IN - 2 * margin_x)
        n_plans = min(4, len(plans))
        gap_x = 0.35
        feature_w = min(4.1, max(3.2, avail_w * 0.34))
        plan_w = (avail_w - feature_w - gap_x * max(0, n_plans - 1)) / max(1, n_plans)
        if plan_w < 1.8:
            feature_w = 3.0
            plan_w = (avail_w - feature_w - gap_x * max(0, n_plans - 1)) / max(1, n_plans)

        row_h = 0.5
        header_h = 0.8
        
        # Plan headers
        for i, plan in enumerate(plans[:4]):  # Max 4 plans
            x = start_x + feature_w + i * (plan_w + gap_x)
            
            # Header background
            header = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(start_y), Inches(max(1.0, plan_w - 0.1)), Inches(header_h))
            header.fill.solid(); header.fill.fore_color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            header.line.fill.background()
            
            # Plan name
            name = str(plan.get("name", ""))[:30]
            _add_textbox(s, x + 0.05, start_y + 0.08, max(0.6, plan_w - 0.2), 0.34, name,
                        font_size_pt=15, bold=True, color=(255, 255, 255), word_wrap=True, auto_fit=True)
            
            # Price
            price = str(plan.get("price", ""))[:20]
            if price:
                _add_textbox(s, x + 0.05, start_y + 0.42, max(0.6, plan_w - 0.2), 0.3, price,
                            font_size_pt=13, color=(255, 255, 255), word_wrap=True, auto_fit=True)
        
        # Feature rows
        all_features: List[str] = []
        for plan in plans[:4]:
            feats = plan.get("features", []) if isinstance(plan, dict) else []
            if not isinstance(feats, list):
                continue
            for f in feats:
                t = str(f or "").strip()
                if t:
                    all_features.append(t)

        seen = set()
        feature_list: List[str] = []
        for f in sorted(all_features, key=lambda x: x.lower()):
            if f.lower() in seen:
                continue
            seen.add(f.lower())
            feature_list.append(f)
            if len(feature_list) >= 8:
                break

        for j, feature in enumerate(feature_list):
            y = start_y + header_h + j * row_h
            
            # Feature name (first column)
            feature_text = str(feature)[:40]
            _add_textbox(s, start_x + 0.05, y + 0.05, max(0.8, feature_w - 0.15), row_h - 0.1, feature_text,
                        font_size_pt=11, color=pal["text"], word_wrap=True, auto_fit=True)
            
            # Checkmarks for each plan
            for i, plan in enumerate(plans[:4]):
                x = start_x + feature_w + i * (plan_w + gap_x)
                has_feature = feature in plan.get("features", [])
                
                if has_feature:
                    # Checkmark
                    sz = 0.26
                    check = s.shapes.add_shape(
                        MsoShape.OVAL,
                        Inches(x + plan_w / 2 - sz / 2),
                        Inches(y + (row_h - sz) / 2),
                        Inches(sz),
                        Inches(sz),
                    )
                    check.fill.solid(); check.fill.fore_color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                    check.line.fill.background()

    def _render_risk_matrix(sl) -> None:
        """Render 2x2 risk matrix with Likelihood vs Impact axes."""
        content = _content(sl)
        title = content.get("title", "Risk Matrix")
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Grid layout: 2x2 (compute to always fit within slide bounds)
        margin_x = 1.0
        top_y = 2.05
        bottom_margin = 0.65
        gap_x, gap_y = 0.55, 0.45
        avail_w = max(6.0, SLIDE_W_IN - 2 * margin_x)
        avail_h = max(3.6, SLIDE_H_IN - bottom_margin - top_y)
        cell_w = max(3.0, (avail_w - gap_x) / 2.0)
        cell_h = max(1.6, (avail_h - gap_y) / 2.0)
        start_x, start_y = margin_x, top_y
        
        quadrants = [
            ("low_likelihood_low_impact", "Low Likelihood\nLow Impact", 0, 0, (200, 230, 201)),
            ("low_likelihood_high_impact", "Low Likelihood\nHigh Impact", 1, 0, (252, 196, 85)),
            ("high_likelihood_low_impact", "High Likelihood\nLow Impact", 0, 1, (252, 196, 85)),
            ("high_likelihood_high_impact", "High Likelihood\nHigh Impact", 1, 1, (248, 113, 113))
        ]
        
        for key, label, col_idx, row_idx, bg_color in quadrants:
            x = start_x + col_idx * (cell_w + gap_x)
            y = start_y + row_idx * (cell_h + gap_y)
            
            quadrant_data = content.get(key, {})
            points = quadrant_data.get("points", [])
            
            # Quadrant container with risk-based coloring
            quad_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(cell_h))
            quad_box.fill.solid(); quad_box.fill.fore_color.rgb = RGBColor(*bg_color)
            quad_box.line.color.rgb = RGBColor(*pal.get("text", (71, 71, 71)))
            
            # Quadrant label
            _add_textbox(
                s,
                x + 0.18,
                y + 0.12,
                cell_w - 0.36,
                0.65,
                label,
                font_size_pt=13,
                bold=True,
                color=pal["text"],
                word_wrap=True,
                auto_fit=True,
            )
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:65]}" for p in points[:4])  # Max 4 points
                _add_textbox(
                    s,
                    x + 0.18,
                    y + 0.78,
                    cell_w - 0.36,
                    max(0.4, cell_h - 0.96),
                    bullet_text,
                    font_size_pt=12,
                    color=pal["text"],
                    word_wrap=True,
                    auto_fit=True,
                )
        
        # Axis labels
        _add_textbox(s, start_x + (cell_w + gap_x) * 0.5, start_y - 0.5, cell_w, 0.4, "Impact →",
                    font_size_pt=14, bold=True, color=pal["text"], align="center")
        _add_textbox(s, start_x - 0.8, start_y + (cell_h + gap_y) * 0.5, 0.6, cell_h,
                    "Likelihood\n↓", font_size_pt=14, bold=True, color=pal["text"], align="center")

    def _render_impact_effort_matrix(sl) -> None:
        content = _content(sl)
        title = (sl.get("title") or content.get("title") or "Impact / Effort Matrix").strip()

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL, align="center")

        bg = pal.get("bg", (255, 255, 255))
        text_col = pal.get("text", (33, 37, 41))
        primary = pal.get("primary", text_col)
        muted = pal.get("muted", text_col)
        h, l, ss = _rgb_to_hls(primary)
        accent = _hls_to_rgb(h + 0.10, l, ss)
        if _rgb_dist(accent, bg) < 55:
            accent = _shade(accent, -30 if not _is_dark_rgb(bg) else 30)
        border = _blend_rgb(muted, bg, 0.60)

        def _contrast(fill_rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
            return (255, 255, 255) if _is_dark_rgb(fill_rgb) else text_col

        def _extract_list(obj) -> List[str]:
            if isinstance(obj, dict):
                obj = obj.get("bullets") or obj.get("points") or obj.get("items")
            if not isinstance(obj, list):
                return []
            out: List[str] = []
            for x in obj:
                t = str(x or "").strip()
                if t:
                    out.append(t)
            return out

        quads = content.get("quadrants") if isinstance(content.get("quadrants"), dict) else {}
        tl_items = _extract_list(content.get("quick_easy_wins") or content.get("quick_wins") or quads.get("quick_easy_wins") or quads.get("quick_wins") or quads.get("top_left") or content.get("top_left"))
        tr_items = _extract_list(content.get("major_projects") or quads.get("major_projects") or quads.get("top_right") or content.get("top_right"))
        bl_items = _extract_list(content.get("fill_in") or quads.get("fill_in") or quads.get("bottom_left") or content.get("bottom_left"))
        br_items = _extract_list(content.get("thankless_tasks") or content.get("money_pit") or quads.get("thankless_tasks") or quads.get("money_pit") or quads.get("bottom_right") or content.get("bottom_right"))

        try:
            quad_map = {
                "quick_easy_wins": list(tl_items or []),
                "major_projects": list(tr_items or []),
                "fill_in": list(bl_items or []),
                "thankless_tasks": list(br_items or []),
            }
            keys = ["quick_easy_wins", "major_projects", "fill_in", "thankless_tasks"]
            total_items = sum(len(quad_map[k]) for k in keys)
            nonempty = sum(1 for k in keys if quad_map[k])
            if nonempty <= 1 and total_items >= 3:
                all_items: List[str] = []
                for k in keys:
                    all_items.extend([str(x).strip() for x in (quad_map.get(k) or []) if str(x).strip()])
                start_idx = next((i for i, k in enumerate(keys) if quad_map.get(k)), 0)
                new_map = {k: [] for k in keys}
                for i, it in enumerate(all_items):
                    new_map[keys[(start_idx + i) % 4]].append(it)
                tl_items = new_map["quick_easy_wins"]
                tr_items = new_map["major_projects"]
                bl_items = new_map["fill_in"]
                br_items = new_map["thankless_tasks"]
        except Exception:
            pass

        labels_obj = content.get("labels") if isinstance(content.get("labels"), dict) else {}
        quad_labels = labels_obj.get("quadrants") if isinstance(labels_obj.get("quadrants"), dict) else {}
        axis_labels = labels_obj.get("axes") if isinstance(labels_obj.get("axes"), dict) else {}

        def _infer_topic_hint() -> str:
            try:
                src_parts: List[str] = []
                src_parts.append(title or "")
                src_parts += (tl_items or [])
                src_parts += (tr_items or [])
                src_parts += (bl_items or [])
                src_parts += (br_items or [])
                blob = " ".join([str(x) for x in src_parts if str(x).strip()])
                blob = re.sub(r"[^A-Za-z0-9\s\-]", " ", blob)
                blob = re.sub(r"\s{2,}", " ", blob).strip().lower()
                if not blob:
                    return ""

                stop = {
                    "the", "and", "for", "with", "from", "into", "your", "our", "their", "this", "that",
                    "these", "those", "are", "is", "was", "were", "be", "been", "being", "to", "of",
                    "in", "on", "at", "by", "as", "or", "an", "a", "it", "its", "vs", "v",
                }
                generic = {
                    "impact", "effort", "matrix", "analysis", "prioritization", "prioritize", "priority",
                    "quick", "wins", "win", "major", "projects", "project", "fill", "in", "thankless",
                    "tasks", "money", "pit", "low", "high", "easy", "hard", "value", "time", "cost",
                }

                words = [w for w in re.split(r"\s+", blob) if w]
                words = [w for w in words if len(w) >= 4 and w not in stop and w not in generic]
                if not words:
                    return ""
                freq: Dict[str, int] = {}
                for w in words:
                    freq[w] = freq.get(w, 0) + 1
                ranked = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
                top = [kv[0] for kv in ranked[:2]]
                hint = " ".join(top).strip()
                hint = hint[:28].strip()
                return hint.upper()
            except Exception:
                return ""

        topic_hint = ""
        try:
            if not quad_labels:
                topic_hint = _infer_topic_hint()
        except Exception:
            topic_hint = ""

        tl_default = "QUICK / EASY WINS"
        tr_default = "MAJOR PROJECTS"
        bl_default = "FILL-IN"
        br_default = "THANKLESS TASKS / MONEY PIT"

        def _lbl(d: Dict[str, Any], key: str, default: str) -> str:
            try:
                v = d.get(key)
                s = str(v or "").strip()
                return s or default
            except Exception:
                return default

        tl_label = _lbl(quad_labels, "quick_easy_wins", tl_default)
        tr_label = _lbl(quad_labels, "major_projects", tr_default)
        bl_label = _lbl(quad_labels, "fill_in", bl_default)
        br_label = _lbl(quad_labels, "thankless_tasks", br_default)

        y_high_label = _lbl(axis_labels, "y_high", "HIGH IMPACT")
        y_low_label = _lbl(axis_labels, "y_low", "LOW IMPACT")
        x_low_label = _lbl(axis_labels, "x_low", "LOW EFFORT")
        x_high_label = _lbl(axis_labels, "x_high", "HIGH EFFORT")

        grid_x = 1.0
        grid_y = 1.85
        grid_w = SLIDE_W_IN - 2.0
        grid_h = SLIDE_H_IN - 2.35
        center_x = grid_x + grid_w / 2
        center_y = grid_y + grid_h / 2

        cross_gutter_w = grid_w * 0.08
        cross_gutter_h = grid_h * 0.08
        half_gutter_w = cross_gutter_w / 2
        half_gutter_h = cross_gutter_h / 2

        q_w = (grid_w / 2) - half_gutter_w
        q_h = (grid_h / 2) - half_gutter_h

        tl = (grid_x, grid_y, q_w, q_h)
        tr = (center_x + half_gutter_w, grid_y, q_w, q_h)
        bl = (grid_x, center_y + half_gutter_h, q_w, q_h)
        br = (center_x + half_gutter_w, center_y + half_gutter_h, q_w, q_h)

        tl_bg = _blend_rgb(bg, primary, 0.06)
        tr_bg = _blend_rgb(bg, accent, 0.06)
        bl_bg = _blend_rgb(bg, muted, 0.04)
        br_bg = _blend_rgb(bg, muted, 0.08)

        def _quad_rect(x: float, y: float, w: float, h: float, fill_rgb: Tuple[int, int, int]):
            r = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
            r.fill.solid(); r.fill.fore_color.rgb = RGBColor(*fill_rgb)
            r.line.color.rgb = RGBColor(*border)
            r.line.width = Pt(1)
            return r

        _quad_rect(*tl, tl_bg)
        _quad_rect(*tr, tr_bg)
        _quad_rect(*bl, bl_bg)
        _quad_rect(*br, br_bg)

        header_h = q_h * 0.14
        pad_x = max(0.12, q_w * 0.05)
        pad_y = max(0.10, q_h * 0.05)
        gap_after_header = max(0.14, q_h * 0.05)

        tl_bar = primary
        tr_bar = accent
        bl_bar = _blend_rgb(muted, bg, 0.30)
        br_bar = _blend_rgb(muted, text_col, 0.18)

        def _label_bar(qx: float, qy: float, qw: float, fill_rgb: Tuple[int, int, int], label: str):
            x = qx + pad_x
            y = qy + pad_y
            w = max(0.5, qw - (2 * pad_x))
            r = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(header_h))
            r.fill.solid(); r.fill.fore_color.rgb = RGBColor(*fill_rgb)
            r.line.fill.background()
            tf = r.text_frame
            tf.clear()
            p = tf.paragraphs[0]
            p.text = label
            p.font.size = Pt(13)
            p.font.bold = True
            p.font.color.rgb = RGBColor(*_contrast(fill_rgb))
            p.alignment = PP_ALIGN.CENTER
            return r

        _label_bar(tl[0], tl[1], tl[2], tl_bar, tl_label)
        _label_bar(tr[0], tr[1], tr[2], tr_bar, tr_label)
        _label_bar(bl[0], bl[1], bl[2], bl_bar, bl_label)
        _label_bar(br[0], br[1], br[2], br_bar, br_label)

        def _bullets_text(items: List[str]) -> str:
            items = [str(x).strip() for x in (items or []) if str(x).strip()]
            if len(items) > 5:
                items = items[:4] + ["…"]
            return "\n".join([f"• {x}" for x in items])

        def _add_quad_bullets(qx: float, qy: float, qw: float, qh: float, items: List[str]):
            txt = _bullets_text(items)
            if not txt:
                return
            x = qx + pad_x
            y = qy + pad_y + header_h + gap_after_header
            w = max(0.5, qw - (2 * pad_x))
            h = max(0.5, (qy + qh - pad_y) - y)
            _add_textbox(s, x, y, w, h, txt, font_size_pt=13, color=text_col, word_wrap=True, auto_fit=True)

        _add_quad_bullets(tl[0], tl[1], tl[2], tl[3], tl_items)
        _add_quad_bullets(tr[0], tr[1], tr[2], tr[3], tr_items)
        _add_quad_bullets(bl[0], bl[1], bl[2], bl[3], bl_items)
        _add_quad_bullets(br[0], br[1], br[2], br[3], br_items)

        axis_fill = _blend_rgb(bg, muted, 0.35)
        axis_text = _contrast(axis_fill)

        ax_v_x = center_x - (cross_gutter_w * 0.30)
        ax_v_y = grid_y
        ax_v_w = cross_gutter_w * 0.60
        ax_v_h = grid_h

        ax_h_x = grid_x
        ax_h_y = center_y - (cross_gutter_h * 0.30)
        ax_h_w = grid_w
        ax_h_h = cross_gutter_h * 0.60

        v_head_h = max(0.18, cross_gutter_h * 0.80)
        v_head_w = min(cross_gutter_w * 0.90, ax_v_w * 1.60)
        h_head_w = max(0.18, cross_gutter_w * 0.80)
        h_head_h = min(cross_gutter_h * 0.90, ax_h_h * 1.40)

        v_shaft = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(ax_v_x), Inches(ax_v_y + (v_head_h * 0.50)), Inches(ax_v_w), Inches(max(0.2, ax_v_h - v_head_h)))
        v_shaft.fill.solid(); v_shaft.fill.fore_color.rgb = RGBColor(*axis_fill)
        v_shaft.line.fill.background()

        h_shaft = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(ax_h_x + (h_head_w * 0.50)), Inches(ax_h_y), Inches(max(0.2, ax_h_w - h_head_w)), Inches(ax_h_h))
        h_shaft.fill.solid(); h_shaft.fill.fore_color.rgb = RGBColor(*axis_fill)
        h_shaft.line.fill.background()

        up_head = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(center_x - (v_head_w / 2)), Inches(grid_y), Inches(v_head_w), Inches(v_head_h))
        up_head.fill.solid(); up_head.fill.fore_color.rgb = RGBColor(*axis_fill)
        up_head.line.fill.background()

        down_head = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(center_x - (v_head_w / 2)), Inches(grid_y + grid_h - v_head_h), Inches(v_head_w), Inches(v_head_h))
        down_head.fill.solid(); down_head.fill.fore_color.rgb = RGBColor(*axis_fill)
        down_head.line.fill.background()
        try:
            down_head.rotation = 180
        except Exception:
            pass

        left_head = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(grid_x), Inches(center_y - (h_head_h / 2)), Inches(h_head_w), Inches(h_head_h))
        left_head.fill.solid(); left_head.fill.fore_color.rgb = RGBColor(*axis_fill)
        left_head.line.fill.background()
        try:
            left_head.rotation = 270
        except Exception:
            pass

        right_head = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(grid_x + grid_w - h_head_w), Inches(center_y - (h_head_h / 2)), Inches(h_head_w), Inches(h_head_h))
        right_head.fill.solid(); right_head.fill.fore_color.rgb = RGBColor(*axis_fill)
        right_head.line.fill.background()
        try:
            right_head.rotation = 90
        except Exception:
            pass

        v_half_h = (grid_h / 2) - half_gutter_h
        v_box_w = max(0.35, v_half_h)
        # After rotation, this becomes the horizontal "thickness" of the label.
        # Match the shaft width so the label sits precisely on the axis.
        v_box_h = max(0.18, ax_v_w)

        # IMPORTANT: rotation changes the visual bounding box. Use pre-swapped dimensions
        # (long dimension in width) and center the box within the vertical gutter half.
        top_center_y = grid_y + (v_half_h / 2)
        bot_center_y = (center_y + half_gutter_h) + (v_half_h / 2)
        v_center_x = center_x

        high_tb = s.shapes.add_textbox(
            Inches(v_center_x - (v_box_w / 2)),
            Inches(top_center_y - (v_box_h / 2)),
            Inches(v_box_w),
            Inches(v_box_h),
        )
        high_tf = high_tb.text_frame
        high_tf.clear()
        try:
            from pptx.enum.text import MSO_ANCHOR
            high_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        except Exception:
            pass
        high_tf.word_wrap = False
        try:
            high_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        except Exception:
            pass
        try:
            high_tf.margin_left = Pt(0)
            high_tf.margin_right = Pt(0)
            high_tf.margin_top = Pt(0)
            high_tf.margin_bottom = Pt(0)
        except Exception:
            pass
        hp = high_tf.paragraphs[0]
        hp.text = y_high_label
        hp.font.size = Pt(10)
        hp.font.bold = True
        hp.font.color.rgb = RGBColor(*axis_text)
        hp.alignment = PP_ALIGN.CENTER
        try:
            high_tb.rotation = 90
        except Exception:
            pass

        low_tb = s.shapes.add_textbox(
            Inches(v_center_x - (v_box_w / 2)),
            Inches(bot_center_y - (v_box_h / 2)),
            Inches(v_box_w),
            Inches(v_box_h),
        )
        low_tf = low_tb.text_frame
        low_tf.clear()
        try:
            from pptx.enum.text import MSO_ANCHOR
            low_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        except Exception:
            pass
        low_tf.word_wrap = False
        try:
            low_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        except Exception:
            pass
        try:
            low_tf.margin_left = Pt(0)
            low_tf.margin_right = Pt(0)
            low_tf.margin_top = Pt(0)
            low_tf.margin_bottom = Pt(0)
        except Exception:
            pass
        lp = low_tf.paragraphs[0]
        lp.text = y_low_label
        lp.font.size = Pt(10)
        lp.font.bold = True
        lp.font.color.rgb = RGBColor(*axis_text)
        lp.alignment = PP_ALIGN.CENTER
        try:
            low_tb.rotation = 90
        except Exception:
            pass

        left_seg_w = (grid_w / 2) - half_gutter_w
        right_seg_x = center_x + half_gutter_w
        right_seg_w = (grid_w / 2) - half_gutter_w

        left_lbl = s.shapes.add_textbox(Inches(grid_x + (h_head_w * 0.80)), Inches(ax_h_y), Inches(max(0.5, left_seg_w - (h_head_w * 0.80))), Inches(ax_h_h))
        lf = left_lbl.text_frame
        lf.clear()
        lpp = lf.paragraphs[0]
        lpp.text = x_low_label
        lpp.font.size = Pt(11)
        lpp.font.bold = True
        lpp.font.color.rgb = RGBColor(*axis_text)
        lpp.alignment = PP_ALIGN.LEFT

        right_lbl = s.shapes.add_textbox(Inches(right_seg_x), Inches(ax_h_y), Inches(max(0.5, right_seg_w - (h_head_w * 0.80))), Inches(ax_h_h))
        rf = right_lbl.text_frame
        rf.clear()
        rpp = rf.paragraphs[0]
        rpp.text = x_high_label
        rpp.font.size = Pt(11)
        rpp.font.bold = True
        rpp.font.color.rgb = RGBColor(*axis_text)
        rpp.alignment = PP_ALIGN.RIGHT

    def _render_before_after(sl) -> None:
        """Render before/after comparison with two columns."""
        content = _content(sl)
        title = content.get("title", "Before vs After")
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.85, 11.3, 0.95, title, font_size_pt=34, bold=True, color=TITLE_COL, align="center", word_wrap=True, auto_fit=True)

        def _render_before_after_modern(slide, content2: Dict[str, Any]) -> None:
            before = content2.get("before") if isinstance(content2.get("before"), dict) else {}
            after = content2.get("after") if isinstance(content2.get("after"), dict) else {}

            left_heading = str((before or {}).get("heading") or "Before")[:50]
            right_heading = str((after or {}).get("heading") or "After")[:50]
            left_pts = [str(x).strip() for x in ((before or {}).get("points") or []) if str(x).strip()][:4]
            right_pts = [str(x).strip() for x in ((after or {}).get("points") or []) if str(x).strip()][:4]

            bg_rgb = pal.get("bg", (255, 255, 255))
            text_rgb = pal.get("text", (24, 24, 27))
            accent = pal.get("primary", (59, 130, 246))
            muted = pal.get("muted", text_rgb)

            def _set_transparency(shape, v: float) -> None:
                try:
                    shape.fill.fore_color.transparency = max(0.0, min(1.0, float(v)))
                except Exception:
                    pass

            center_x = SLIDE_W_IN / 2.0
            label_y = 1.85
            card_y = 2.55
            card_w = 4.65
            card_h = 3.10
            margin_x = 1.05
            left_x = margin_x
            right_x = SLIDE_W_IN - margin_x - card_w
            header_h = 0.62

            subtle = _blend_rgb(bg_rgb, pal.get("secondary", bg_rgb), 0.55)
            subtle = _blend_rgb(subtle, (255, 255, 255), 0.70)
            _add_textbox(slide, left_x, label_y, card_w, 0.70, "BEFORE", font_size_pt=44, bold=True, color=subtle, align="center")
            _add_textbox(slide, right_x, label_y, card_w, 0.70, "AFTER", font_size_pt=44, bold=True, color=subtle, align="center")

            glow_pad_x = 0.65
            glow_pad_y = 0.55
            glow_h = card_h + glow_pad_y * 2
            glow_y = card_y - glow_pad_y
            glow_w = card_w + glow_pad_x * 2

            glow_left = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(left_x - glow_pad_x), Inches(glow_y), Inches(glow_w), Inches(glow_h))
            glow_left.fill.solid(); glow_left.fill.fore_color.rgb = RGBColor(*_blend_rgb(bg_rgb, (160, 174, 192), 0.35))
            _set_transparency(glow_left, 0.78)
            glow_left.line.fill.background()

            glow_right = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(right_x - glow_pad_x), Inches(glow_y), Inches(glow_w), Inches(glow_h))
            glow_right.fill.solid(); glow_right.fill.fore_color.rgb = RGBColor(*_blend_rgb(bg_rgb, accent, 0.25))
            _set_transparency(glow_right, 0.78)
            glow_right.line.fill.background()

            divider = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(center_x - 0.01), Inches(card_y - 0.15), Inches(0.02), Inches(card_h + 0.30))
            divider.fill.solid(); divider.fill.fore_color.rgb = RGBColor(*_blend_rgb(bg_rgb, muted, 0.40))
            _set_transparency(divider, 0.70)
            divider.line.fill.background()

            def _card(x0: float, hdr: str, pts: List[str], *, header_fill: Tuple[int, int, int], header_text: Tuple[int, int, int]) -> None:
                card = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x0), Inches(card_y), Inches(card_w), Inches(card_h))
                card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
                card.line.fill.background()
                try:
                    card.shadow.inherit = False
                    card.shadow.visible = True
                    card.shadow.blur = 18
                    card.shadow.distance = 3
                except Exception:
                    pass

                band = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x0), Inches(card_y), Inches(card_w), Inches(header_h))
                band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*header_fill)
                band.line.fill.background()
                try:
                    band.shadow.inherit = False
                    band.shadow.visible = True
                    band.shadow.blur = 12
                    band.shadow.distance = 2
                except Exception:
                    pass

                _add_textbox(slide, x0 + 0.30, card_y + 0.14, card_w - 0.60, 0.40, hdr, font_size_pt=16, bold=True, color=header_text, word_wrap=True, auto_fit=True)

                y0 = card_y + header_h + 0.35
                row_h = 0.52
                dot_d = 0.085
                dot_x = x0 + 0.45
                txt_x = x0 + 0.62
                max_rows = 4
                pts2 = (pts or [])[:max_rows]
                for i, t in enumerate(pts2):
                    yy = y0 + i * row_h
                    dot = slide.shapes.add_shape(MsoShape.OVAL, Inches(dot_x), Inches(yy + 0.16), Inches(dot_d), Inches(dot_d))
                    dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*accent)
                    dot.line.fill.background()
                    _add_textbox(slide, txt_x, yy + 0.05, card_w - (txt_x - x0) - 0.35, row_h, str(t), font_size_pt=13, color=text_rgb, word_wrap=True, auto_fit=True)

            _card(left_x, left_heading, left_pts, header_fill=accent, header_text=(255, 255, 255))
            right_fill = _blend_rgb(bg_rgb, pal.get("secondary", bg_rgb), 0.70)
            _card(right_x, right_heading, right_pts, header_fill=right_fill, header_text=_shade(accent, -40))

        _render_before_after_modern(s, content)
        return

    def _render_architecture(sl) -> None:
        """Render architecture diagram with boxes and directional arrows."""
        content = _content(sl)
        title = (str(sl.get("title") or "").strip() or str(content.get("title") or "").strip() or "Architecture Diagram")
        boxes = content.get("boxes", [])
        connections = content.get("connections", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Create box positions in a centered grid layout (no overflow)
        box_positions = {}
        box_list = boxes if isinstance(boxes, list) else []
        n_boxes = min(8, len(box_list))
        if n_boxes <= 0:
            return

        cols = min(4, n_boxes)
        rows = int(math.ceil(n_boxes / max(1, cols)))

        margin_x = 0.8
        top_y = 2.05
        bottom_margin = 0.75
        gap_x = 0.60
        gap_y = 0.55

        avail_w = max(7.0, SLIDE_W_IN - 2 * margin_x)
        avail_h = max(2.0, SLIDE_H_IN - bottom_margin - top_y)

        box_w = (avail_w - (cols - 1) * gap_x) / max(1, cols)
        box_w = max(2.2, min(3.1, box_w))
        box_h = 1.25 if rows == 1 else 1.20

        total_w = cols * box_w + (cols - 1) * gap_x
        start_x = max(margin_x, (SLIDE_W_IN - total_w) / 2.0)

        total_h = rows * box_h + (rows - 1) * gap_y
        start_y = top_y + max(0.0, (avail_h - total_h) / 2.0)

        draw_boxes: List[tuple[float, float, float, float, str, str]] = []
        for i, box in enumerate(box_list[:8]):  # Max 8 boxes
            row, col = i // cols, i % cols
            x = start_x + col * (box_w + gap_x)
            y = start_y + row * (box_h + gap_y)

            box_label = str(box.get("label", ""))[:30]
            desc = str(box.get("description", ""))[:60]
            box_positions[box_label] = (x, y, box_w, box_h)
            draw_boxes.append((x, y, box_w, box_h, box_label, desc))
        
        # Draw connections first so they sit behind the boxes.
        for conn in connections:
            from_label = str(conn.get("from_box", ""))[:30]
            to_label = str(conn.get("to_box", ""))[:30]
            
            if from_label in box_positions and to_label in box_positions:
                from_x, from_y, from_w, from_h = box_positions[from_label]
                to_x, to_y, to_w, to_h = box_positions[to_label]
                
                # Connect between box edges (not centers) so lines don't cross the boxes/text.
                fx = from_x + from_w / 2
                fy = from_y + from_h / 2
                tx = to_x + to_w / 2
                ty = to_y + to_h / 2
                dx = tx - fx
                dy = ty - fy
                pad = 0.02

                if abs(dx) >= abs(dy):
                    # Horizontal connection
                    if dx >= 0:
                        start_x_conn = from_x + from_w + pad
                        end_x_conn = to_x - pad
                    else:
                        start_x_conn = from_x - pad
                        end_x_conn = to_x + to_w + pad
                    start_y_conn = fy
                    end_y_conn = ty
                else:
                    # Vertical connection
                    if dy >= 0:
                        start_y_conn = from_y + from_h + pad
                        end_y_conn = to_y - pad
                    else:
                        start_y_conn = from_y - pad
                        end_y_conn = to_y + to_h + pad
                    start_x_conn = fx
                    end_x_conn = tx
                
                try:
                    conn_type = MSO_CONNECTOR_TYPE.ELBOW if abs(dx) >= 0.35 and abs(dy) >= 0.35 else MSO_CONNECTOR_TYPE.STRAIGHT
                    arrow = s.shapes.add_connector(
                        conn_type,
                        Inches(start_x_conn),
                        Inches(start_y_conn),
                        Inches(end_x_conn),
                        Inches(end_y_conn),
                    )
                    arrow.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                    arrow.line.width = Pt(2)
                except Exception:
                    # Skip connection if it fails
                    continue

        for x, y, w, h, box_label, desc in draw_boxes:
            box_shape = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
            box_shape.fill.solid(); box_shape.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            box_shape.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))

            _add_textbox(
                s,
                x + 0.12,
                y + 0.10,
                w - 0.24,
                0.38,
                box_label,
                font_size_pt=12,
                bold=True,
                color=pal["text"],
                word_wrap=True,
                auto_fit=True,
            )

            if desc:
                _add_textbox(
                    s,
                    x + 0.12,
                    y + 0.48,
                    w - 0.24,
                    max(0.3, h - 0.58),
                    desc,
                    font_size_pt=9,
                    color=pal.get("muted", pal["text"]),
                    word_wrap=True,
                    auto_fit=True,
                )

        for x, y, w, h, box_label, desc in draw_boxes:
            box_shape = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
            box_shape.fill.solid(); box_shape.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            box_shape.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))

            _add_textbox(
                s,
                x + 0.12,
                y + 0.10,
                w - 0.24,
                0.38,
                box_label,
                font_size_pt=12,
                bold=True,
                color=pal["text"],
                word_wrap=True,
                auto_fit=True,
            )

            if desc:
                _add_textbox(
                    s,
                    x + 0.12,
                    y + 0.48,
                    w - 0.24,
                    max(0.3, h - 0.58),
                    desc,
                    font_size_pt=9,
                    color=pal.get("muted", pal["text"]),
                    word_wrap=True,
                    auto_fit=True,
                )

    # --- Render in order ---
    def render_title(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        title = (sl.get("title") or "Presentation Title").strip()
        subtitle = (sl.get("subtitle") or sl.get("presenter") or "").strip()
        _add_textbox(s, 1.0, 1.0, 11.3, 2.0, title, font_size_pt=46, bold=True, align="center", color=TITLE_COL, word_wrap=True, auto_fit=True)
        # Add ~0.5" (~48px) vertical padding below the title before the presenter/subtitle
        _add_textbox(s, 1.0, 3.5, 11.3, 0.8, subtitle, font_size_pt=20, color=pal["text"], align="center", word_wrap=True, auto_fit=True)

    def render_agenda(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        c = _content(sl)
        title_txt = str(sl.get("title") or "Agenda").strip() or "Agenda"
        subtitle_txt = str(c.get("subtitle") or "").strip() or None

        raw_forced = str(c.get("agenda_variant") or c.get("variant") or "").strip().lower()
        forced_map = {
            "zebra": "minimal_rule",
            "rows": "minimal_rule",
            "list": "minimal_rule",
            "step_squares": "step_squares",
            "step-squares": "step_squares",
            "squares": "step_squares",
            "square": "step_squares",
            "center_chevrons": "center_chevrons",
            "center-chevrons": "center_chevrons",
            "center": "center_chevrons",
            "chevrons": "center_chevrons",
            "arrows": "center_chevrons",
            "chevron_badges": "chevron_badges",
            "chevron-badges": "chevron_badges",
            "badges": "chevron_badges",
            "badge": "chevron_badges",
            "minimal": "minimal_rule",
            "minimal_rule": "minimal_rule",
            "minimal-rule": "minimal_rule",
            "rule": "minimal_rule",
            "line": "minimal_rule",
            "stacked": "stacked_circles",
            "stacked_circles": "stacked_circles",
            "stacked-circles": "stacked_circles",
            "circles": "stacked_circles",
            "circle": "stacked_circles",
            "split": "split_panel",
            "split_panel": "split_panel",
            "split-panel": "split_panel",
            "panel": "split_panel",
        }
        forced_norm = forced_map.get(raw_forced) if raw_forced else None

        pool = ["step_squares", "center_chevrons", "chevron_badges", "minimal_rule", "stacked_circles", "split_panel"]
        chosen = choose_variant("agenda", pool, forced=forced_norm)
        _debug_variant(family="agenda", chosen=chosen, eligible=pool, forced=forced_norm)

        bullets_raw = c.get("bullets", [])[:32]
        if chosen == "step_squares":
            _render_agenda_step_squares(s, bullets_raw, pal=pal, title=title_txt, subtitle=subtitle_txt)
        elif chosen == "center_chevrons":
            _render_agenda_center_chevrons(s, bullets_raw, pal=pal, title=title_txt, subtitle=subtitle_txt)
        elif chosen == "chevron_badges":
            _render_agenda_chevron_badges(s, bullets_raw, pal=pal, title=title_txt, subtitle=subtitle_txt)
        elif chosen == "minimal_rule":
            _render_agenda_minimal_rule(s, bullets_raw, pal=pal, title=title_txt, subtitle=subtitle_txt)
        elif chosen == "stacked_circles":
            _render_agenda_stacked_circles(s, bullets_raw, pal=pal, title=title_txt, subtitle=subtitle_txt)
        elif chosen == "split_panel":
            _render_agenda_split_panel(s, bullets_raw, pal=pal, title=title_txt, subtitle=subtitle_txt)
        else:
            # Fallback to a supported, single-slide agenda layout
            _render_agenda_minimal_rule(s, bullets_raw, pal=pal, title=title_txt, subtitle=subtitle_txt)

    def render_one_idea(sl):
        """Render a simple one-idea intro slide: bold headline + short paragraph.

        This slide is theme-aware via background and typography colors, and is
        intentionally minimal: no bullets, icons, or extra structure.
        """
        s = prs.slides.add_slide(blank); apply_bg(s)
        c = _content(sl)
        headline = (c.get("headline") or sl.get("title") or "Overview").strip()
        explainer = (c.get("explainer") or c.get("paragraph") or "").strip()
        if not explainer:
            topic_txt = (slides_norm[0].get("title") or "this topic").strip()
            explainer = f"An accessible summary of what {topic_txt} is, why it matters, and the core ideas to know."

        # Headline: large, bold, centered; color follows theme title rules
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        headline_color = (255, 255, 255) if dark_bg else pal.get("primary", (59, 130, 246))
        _add_textbox(
            s,
            1.0,
            1.4,
            11.3,
            1.4,
            headline,
            font_size_pt=40,
            bold=True,
            align="center",
            color=headline_color,
            word_wrap=True,
            auto_fit=True,
        )

        # Explainer paragraph: smaller, muted text below headline
        expl_color = pal.get("muted", pal.get("text", (55, 65, 81)))
        _add_textbox(
            s,
            2.0,
            3.0,
            9.3,
            1.6,
            explainer,
            font_size_pt=22,
            align="center",
            color=expl_color,
            word_wrap=True,
            auto_fit=True,
        )

    # --- Timeline helpers (conditional insertion after Agenda) ---
    def _is_timeline_irrelevant(topic: str, agenda_items: List[str]) -> bool:
        """Return True only when a timeline would add no value at all.
        Default stance: timeline is useful unless content is purely static or non-sequential.
        """
        low = (topic or "").lower() + " " + " ".join(str(x).lower() for x in (agenda_items or []))
        # Clearly non-sequential formats
        anti = [
            "glossary", "definitions", "reference guide", "faq", "checklist",
            "policy summary", "catalogue", "catalog", "directory", "recipe", "formula",
            "static overview", "contact list", "team bios", "pricing table", "spec sheet",
        ]
        if any(k in low for k in anti):
            return True
        # If topic is extremely narrow (single concept) and lacks any process terms
        process_hints = [
            "history", "evolution", "roadmap", "milestone", "phase", "stage", "rollout",
            "plan", "implementation", "deployment", "journey", "lifecycle", "timeline",
            "strategy", "steps", "process", "q1", "q2", "q3", "q4", "year", "year-by-year",
        ]
        if not any(h in low for h in process_hints) and len((topic or "").split()) <= 2:
            return True
        # Otherwise, treat timeline as relevant
        return False

    def _choose_timeline_layout(topic: str, agenda_items: List[str]) -> str:
        """Return one of: linear, roadmap, horizontal, vertical.
        Rotates across styles every generation (independent of theme).
        Strong cues (roadmap/phase/years) are honored on alternating turns to keep relevance but still rotate.
        """
        global TIMELINE_STYLE_INDEX
        styles = ["horizontal", "vertical", "linear", "roadmap"]
        rot = styles[TIMELINE_STYLE_INDEX % len(styles)]

        low = (topic or "").lower() + " " + " ".join([str(x).lower() for x in (agenda_items or [])])
        cue_style = None
        if any(w in low for w in ["roadmap", "journey", "rollout", "plan"]):
            cue_style = "roadmap"
        elif re.search(r"\b(step|phase)\s*\d+\b", low):
            cue_style = "linear"
        elif re.search(r"\b(19|20)\d{2}\b|\bq[1-4]\b", low):
            cue_style = "horizontal"

        # Alternate: on even turns prefer cue, on odd turns force rotation selection
        if cue_style is not None and (TIMELINE_STYLE_INDEX % 2 == 0):
            choice = cue_style
        else:
            choice = rot

        TIMELINE_STYLE_INDEX += 1
        return choice

    def _timeline_labels_for_topic(topic: str) -> List[str]:
        """Generate 4–6 topic-specific labels from the subject phrase, avoiding domain lists.
        Strategy: build phrase variants around the inferred subject so labels are always relevant without generic words alone.
        """
        subj = _extract_main_subject(topic)
        base = [
            f"Foundations of {subj}",
            f"{subj} Use Cases",
            f"Designing {subj}",
            f"Implementing {subj}",
            f"Measuring {subj}",
            f"Scaling {subj}",
        ]
        return base

    def _derive_timeline_points(topic: str, agenda_items: List[str]) -> List[str]:
        """Default to evolution stages; if not suitable, derive subject-bound process phases."""
        subj = _extract_main_subject(topic)
        if _evolution_applicable(topic):
            items = [
                f"Origins of {subj}",
                f"Early Adoption",
                f"Expansion",
                f"Modern {subj}",
                f"Current Practices",
                f"Future Outlook",
            ]
        else:
            items = [
                f"Plan {subj}",
                f"Design {subj}",
                f"Build {subj}",
                f"Validate {subj}",
                f"Launch {subj}",
                f"Optimize {subj}",
            ]
        items = [i for i in items if i][:6]
        if len(items) < 4:
            # ensure at least 4 items
            extras = [f"Principles of {subj}", f"Applications", f"Integration"]
            for e in extras:
                if len(items) >= 4:
                    break
                items.append(e)
        return items

    def _extract_main_subject(topic: str) -> str:
        """Infer a concise subject from topic text.
        - If pattern like 'Analytics in X' -> 'X Analytics'
        - If contains 'analytics' and another domain noun -> '<Domain> Analytics'
        - Otherwise fall back to last two non-stop words.
        """
        t = (topic or "").strip()
        lo = t.lower()
        # Pattern: <something> in <domain>
        m = re.search(r"\b(.+?)\s+in\s+(.+)$", lo)
        if m:
            left = m.group(1).strip()
            right = m.group(2).strip()
            if "analytics" in left:
                return f"{right.title()} Analytics".strip()
        # Heuristic: contains 'analytics' and a domain token
        if "analytics" in lo:
            # Prefer last non-analytics noun-like token
            tokens = [w for w in re.split(r"\W+", lo) if w and w not in {"the","of","and","to","for","a","an","on","with","in"}]
            domain = None
            for w in reversed(tokens):
                if w not in {"analytics"}:
                    domain = w
                    break
            if domain:
                return f"{domain.title()} Analytics"
        # Default: last two non-stop words
        words = [w for w in re.split(r"\W+", t) if w]
        stop = {"the","of","in","and","to","for","a","an","on","with"}
        core = [w for w in words if w.lower() not in stop]
        if not core:
            return (topic or "Topic").strip().title()
        subj = " ".join(core[-2:]).title()
        return subj

    def _evolution_applicable(topic: str) -> bool:
        low = (topic or "").lower()
        non_evo_cues = [
            "roadmap", "rollout", "plan", "project", "implementation", "deployment", "phases",
            "steps", "how to", "onboarding", "runbook", "sop", "migration", "launch", "go-live",
        ]
        return not any(c in low for c in non_evo_cues)

    def _smart_timeline_title(topic: str, layout: str) -> str:
        """Default to Evolution of {Subject}. If evolution not suitable, choose a process title."""
        subj = _extract_main_subject(topic)
        if _evolution_applicable(topic):
            return f"Evolution of {subj}"
        # Non-evolution context
        if any(k in (topic or "").lower() for k in ["roadmap", "plan", "journey", "rollout"]):
            return f"{subj} Roadmap"
        return f"{subj} Phases"

    def _timeline_subtitle(topic: str | None, labels: List[str] | None, layout: str | None = None) -> str:
        labs = [str(x).strip() for x in (labels or []) if str(x).strip()]
        lo_topic = (topic or "").lower()

        def _subject_from_title(t: str) -> str:
            words = [w for w in re.split(r"\W+", (t or "").strip()) if w]
            stop = {
                "the", "of", "in", "and", "to", "for", "a", "an", "on", "with", "vs", "vs.",
                "overview", "summary", "introduction", "analysis",
            }
            generic = {
                "timeline", "roadmap", "process", "workflow", "phases", "phase", "steps", "step",
                "plan", "planning", "project", "execution", "implementation", "rollout", "journey",
            }
            core = [w for w in words if w.lower() not in stop and w.lower() not in generic]
            if not core:
                core = [w for w in words if w.lower() not in stop]
            if not core:
                return ""
            return " ".join(core[:2]).title().strip()

        def _title_derived_one_liner() -> str:
            subj = _subject_from_title(topic or "")
            if any(k in lo_topic for k in ["roadmap", "rollout", "plan"]):
                return f"Key milestones to deliver {subj}".strip() if subj else "Key milestones to deliver the plan"
            if any(k in lo_topic for k in ["timeline", "phases", "phase"]):
                return f"Key phases to deliver {subj}".strip() if subj else "Key phases and milestones at a glance"
            if any(k in lo_topic for k in ["process", "workflow", "steps", "step"]):
                return f"A clear sequence to deliver {subj}".strip() if subj else "A clear sequence to guide execution"
            return f"Key steps to deliver {subj}".strip() if subj else "Key steps to guide execution"

        uses_process_layout = layout in {"horizontal", "v-chevrons", "process-alt", "vertical", "linear", "roadmap"}
        if uses_process_layout:
            if len(labs) >= 2:
                a, b = labs[0], labs[-1]
                def _ok2(s: str) -> bool:
                    return bool(s) and len(s) <= 26 and not any(ch in s for ch in ["—", ":", "/"])
                if _ok2(a) and _ok2(b) and a.lower() != b.lower():
                    return f"From {a} to {b}"
            if len(labs) == 1 and len(labs[0]) <= 28:
                return f"Focus: {labs[0]}"
            return _title_derived_one_liner()

        if len(labs) >= 2:
            a, b = labs[0], labs[-1]
            def _ok(s: str) -> bool:
                return bool(s) and len(s) <= 18 and not any(ch in s for ch in ["—", "-", ":", "/"])
            if _ok(a) and _ok(b):
                return f"From {a} to {b}"
            return _title_derived_one_liner()
        if any(k in lo_topic for k in ["history", "evolution", "growth"]):
            return "A chronological journey through key stages"
        if layout == "roadmap":
            return "Key milestones driving the journey"
        if layout == "linear":
            return "Step-by-step evolution of the process"
        return "Chronological overview"

    def _step_colors() -> List[Tuple[int, int, int]]:
        """Return a small set of step accent colors derived from the theme.

        Geometry/spacing for timelines is unchanged; only fills and strokes
        are mapped onto the theme's primary / secondary / muted colors so
        that process steps feel on-brand for the selected template.
        """
        base_primary = pal.get("primary", (59, 130, 246))
        bg_rgb = pal.get("bg", (255, 255, 255))

        is_dark_bg = _is_dark_rgb(bg_rgb)
        h, l, s = _rgb_to_hls(base_primary)

        # Keep accents professional: moderate saturation, controlled lightness.
        s = max(0.32, min(0.72, s if s > 0.20 else 0.45))
        l = max(0.35, min(0.62, l))
        if is_dark_bg:
            l = max(l, 0.55)
        else:
            l = min(l, 0.55)

        anchor = _hls_to_rgb(h, l, s)
        # Two nearby hues (analogous), blended back toward anchor to avoid clashes.
        h_shift = 0.06
        accent1 = _blend_rgb(anchor, _hls_to_rgb(h + h_shift, min(0.62, l + 0.03), max(0.28, s - 0.10)), 0.58)
        accent2 = _blend_rgb(anchor, _hls_to_rgb(h - h_shift, min(0.62, l + 0.03), max(0.28, s - 0.10)), 0.58)

        def _ensure_visible(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
            r, g, b = rgb
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            br, bgc, bb = bg_rgb
            lum_bg = 0.2126 * br + 0.7152 * bgc + 0.0722 * bb
            if abs(lum - lum_bg) < 28:
                delta = -28 if not is_dark_bg else 28
                return _shade(rgb, delta)
            return rgb

        anchor = _ensure_visible(anchor)
        accent1 = _ensure_visible(accent1)
        accent2 = _ensure_visible(accent2)

        # If hue shift produces near-identical colors (e.g., very low saturation),
        # fall back to anchor-only shading.
        if _rgb_dist(accent1, anchor) < 45:
            accent1 = _shade(anchor, 26 if is_dark_bg else -26)
        if _rgb_dist(accent2, anchor) < 45 or _rgb_dist(accent2, accent1) < 35:
            accent2 = _shade(anchor, 14 if is_dark_bg else -14)

        # Controlled reuse: anchor remains dominant; accents repeat as needed.
        return [anchor, accent1, accent2, anchor, accent1, accent2]

    def _bullet_colors() -> List[Tuple[int, int, int]]:
        """Return the per-deck bullet icon color.

        All bullet icon layouts call this helper and will therefore share a
        single, consistent color within the deck. The actual color is chosen
        once per deck (see bullet_icon_rgb in build_pptx_from_slides) using
        the 5-step rotation rule across generations.
        """
        if uses_single_bullet_color:
            return [bullet_icon_rgb, bullet_icon_rgb, bullet_icon_rgb]

        primary = pal.get("primary", (59, 130, 246))
        secondary = pal.get("secondary", primary)
        accent = pal.get("muted", pal.get("text", primary))
        bg_rgb = pal.get("bg", (255, 255, 255))

        def _ensure_icon(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
            r, g, b = rgb
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            br, bgc, bb = bg_rgb
            lum_bg = 0.2126 * br + 0.7152 * bgc + 0.0722 * bb
            if abs(lum - lum_bg) < 30:
                delta = -40 if not _is_dark_rgb(bg_rgb) else 40
                return _shade(rgb, delta)
            return rgb

        primary = _ensure_icon(primary)
        secondary = _ensure_icon(secondary)
        accent_light = _ensure_icon(accent)

        return [primary, secondary, accent_light]

    # Rotating color schemes for Card slides (6 schemes)
    CARD_COLOR_SCHEMES: List[Dict[str, object]] = [
        {  # Set A: Orange, Pink, Teal, Yellow (vivid)
            "primary": (15, 23, 42),
            "border": (255, 255, 255),
            "cards": [
                (247, 112, 65),   # vivid orange
                (236, 72, 153),   # vivid pink
                (20, 184, 166),   # vivid teal
                (234, 179, 8),    # warm yellow
            ],
        },
        {  # Set B: Blue, Cyan, Magenta, Amber
            "primary": (15, 23, 42),
            "border": (255, 255, 255),
            "cards": [
                (59, 130, 246),   # blue
                (14, 165, 233),   # sky
                (217, 70, 239),   # fuchsia
                (245, 158, 11),   # amber
            ],
        },
        {  # Set C: Indigo, Emerald, Rose, Blue
            "primary": (15, 23, 42),
            "border": (255, 255, 255),
            "cards": [
                (99, 102, 241),   # indigo
                (16, 185, 129),   # emerald
                (244, 63, 94),    # rose
                (2, 132, 199),    # sky deep
            ],
        },
        {  # Set D: Purple, Orange, Teal, Pink
            "primary": (15, 23, 42),
            "border": (255, 255, 255),
            "cards": [
                (168, 85, 247),   # purple
                (249, 115, 22),   # orange
                (45, 212, 191),   # teal light
                (236, 72, 153),   # pink
            ],
        },
        {  # Set E: Cyan, Yellow, Blue, Green
            "primary": (15, 23, 42),
            "border": (255, 255, 255),
            "cards": [
                (6, 182, 212),    # cyan
                (250, 204, 21),   # yellow
                (37, 99, 235),    # royal blue
                (34, 197, 94),    # green
            ],
        },
        {  # Set F: Coral, Aqua, Gold, Indigo
            "primary": (15, 23, 42),
            "border": (255, 255, 255),
            "cards": [
                (244, 114, 82),   # coral
                (20, 184, 166),   # teal
                (234, 179, 8),    # gold
                (99, 102, 241),   # indigo
            ],
        },
    ]
    # Index to alternate schemes per card slide across a presentation
    try:
        CARD_SCHEME_INDEX
    except NameError:
        CARD_SCHEME_INDEX = 0

    # Rotation index for process layout variants now comes from module scope

    def _contrast_text(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
        """Return white or near-black text for best contrast over given background color."""
        r, g, b = rgb
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        if lum < 150:
            return (255, 255, 255)
        txt = pal.get("text", (24, 24, 27))
        if _is_dark_rgb(txt):
            return txt
        return (24, 24, 27)

    def _shorten(txt: str, max_chars: int) -> str:
        t = (txt or "").strip()
        if len(t) <= max_chars:
            return t
        # try trimming at sentence/word boundary
        t = t[:max_chars].rsplit(" ", 1)[0].rstrip(",.;: ")
        return (t + "…") if t else ""

    def _fit_label_text(run, text: str) -> None:
        # heuristic shrink to avoid overflow in chevrons
        t = (text or "").strip()
        sz = 20
        if len(t) > 38:
            sz = 18
        if len(t) > 52:
            sz = 16
        if len(t) > 68:
            t = _shorten(t, 68)
        run.text = t
        run.font.size = Pt(sz)
        run.font.bold = True
        run.font.color.rgb = RGBColor(255, 255, 255)

    def _fit_label_text_width(run, text: str, chev_w_in: float) -> None:
        # Estimate max chars based on width; reserve padding
        usable = max(0.4, chev_w_in - 0.6)
        # Roughly ~0.11in per average char at 18–20pt
        est_chars = int(usable / 0.11)
        t = (text or "").strip()
        # initial size by length/width
        if est_chars < 16:
            size = 14
        elif est_chars < 22:
            size = 16
        else:
            size = 18 if len(t) <= est_chars else 16
        if len(t) > est_chars:
            t = _shorten(t, est_chars)
        run.text = t
        run.font.size = Pt(size)
        run.font.bold = True
        run.font.color.rgb = RGBColor(255, 255, 255)

    def render_timeline_linear(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        """Colorful linear timeline: chips on a soft baseline with captions."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        # Title
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=42, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        # Subtitle
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(s, 1.0, 1.8, 10.8, 0.6, sub, font_size_pt=19, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)

        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = ["Phase 1", "Phase 2", "Phase 3", "Phase 4"]

        n = min(6, max(4, len(items)))
        d = 1.35      # chip size (square) enlarged to better fill slide
        margin_x = 1.2
        top = 2.45

        # baseline
        usable = SLIDE_W_IN - 2*margin_x - n*d
        gap = max(0.38, usable / max(1, (n - 1)))
        left_center = margin_x + d/2 + 0.2
        right_center = left_center + (n - 1) * (d + gap)
        # container band behind timeline
        cont_x, cont_w = 0.7, SLIDE_W_IN - 1.4
        cont_y = top - 0.35
        cont_h = d + 1.10
        band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        band.line.color.rgb = RGBColor(229, 231, 235)
        try:
            band.shadow.inherit = False; band.shadow.visible = True; band.shadow.blur = 8; band.shadow.distance = 3
        except Exception:
            pass
        # soft gray baseline with subtle shadow
        line = s.shapes.add_connector(1, Inches(left_center), Inches(top + d/2), Inches(right_center), Inches(top + d/2))
        line.line.color.rgb = RGBColor(209, 213, 219)  # gray-300
        try:
            line.shadow.inherit = False; line.shadow.visible = True; line.shadow.blur = 6; line.shadow.distance = 2
        except Exception:
            pass

        STEP_COLS = _step_colors()
        light_bg = pal.get("secondary", (240, 245, 255))

        for i, lab in enumerate(items[:n], start=1):
            cx = (left_center + (i - 1) * (d + gap)) - d/2
            # chip
            chip = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cx), Inches(top), Inches(d), Inches(d))
            chip.fill.solid(); chip.fill.fore_color.rgb = RGBColor(*(light_bg))
            chip.line.color.rgb = RGBColor(*STEP_COLS[(i - 1) % len(STEP_COLS)])
            try:
                chip.shadow.inherit = False; chip.shadow.visible = True; chip.shadow.blur = 4; chip.shadow.distance = 1
            except Exception:
                pass
            # number inside
            tfn = chip.text_frame; tfn.clear(); pn = tfn.paragraphs[0]; pn.alignment = PP_ALIGN.CENTER
            rn = pn.add_run(); rn.text = str(i); rn.font.size = Pt(22); rn.font.bold = True
            rn.font.color.rgb = RGBColor(*STEP_COLS[(i - 1) % len(STEP_COLS)])
            # caption under

    def render_timeline_horizontal(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        """Colorful horizontal chevrons timeline (steps as colored arrows)."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=40, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub_raw = str(subtitle_override or "").strip()
        if sub_raw:
            _add_textbox(s, 1.0, 1.8, 10.8, 0.8, sub_raw, font_size_pt=18, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)
        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = ["Step 1", "Step 2", "Step 3", "Step 4"]
        n = min(8, max(4, len(items)))
        STEP_COLS = _step_colors()
        # layout metrics and multi-row handling
        band_h = 1.25  # taller chevrons to allow wrapped, multi-line labels
        band_x, band_w = 1.0, 11.3
        overlap = 0.35
        # Try single row first; make two rows when width gets cramped or many items
        chev_w_single = min(4.2, (band_w + overlap * (n - 1)) / n)
        use_two_rows = (chev_w_single < 3.8 and n >= 5) or n >= 7
        rows: List[List[str]]
        if use_two_rows:
            r1 = (n + 1) // 2
            rows = [items[:r1], items[r1:n]]
        else:
            rows = [items[:n]]

        # Vertical positioning with guards: keep gap from subtitle and bottom margin
        total_h = len(rows) * band_h + (len(rows) - 1) * 0.48
        SUB_Y, SUB_H = 1.55, 0.80
        sub_bottom = SUB_Y + SUB_H
        min_top_gap = 0.30
        bottom_margin = 0.45
        # initial center-based position
        center_y = 3.8
        start_y = center_y - total_h / 2
        y = start_y
        # container metrics
        cont_x, cont_w = 0.7, SLIDE_W_IN - 1.4
        cont_y = y - 0.34
        cont_h = total_h + 0.68
        # enforce safe top gap and bottom margin
        min_cont_y = sub_bottom + min_top_gap
        if cont_y < min_cont_y:
            cont_y = min_cont_y
        max_cont_y = SLIDE_H_IN - bottom_margin - cont_h
        if cont_y > max_cont_y:
            cont_y = max_cont_y
        # recompute row start after clamping
        y = cont_y + 0.34
        container = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        container.fill.solid(); container.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240,245,255))))
        container.line.color.rgb = RGBColor(229,231,235)
        try:
            container.shadow.inherit = False; container.shadow.visible = True; container.shadow.blur = 14; container.shadow.distance = 3
        except Exception:
            pass
        for ridx, row in enumerate(rows):
            m = len(row)
            chev_w = min(4.2, (band_w + overlap * (m - 1)) / m)
            total_used = m * chev_w - overlap * (m - 1)
            x = band_x + max(0.0, (band_w - total_used) / 2)
            centers: List[float] = []
            for i, lab in enumerate(row, start=1 + (0 if ridx == 0 else len(rows[0]))):
                base_col = STEP_COLS[(i - 1) % len(STEP_COLS)]
                # Top row slightly darker, bottom row slightly lighter to
                # create a clear hierarchy while staying within the theme
                # palette. Single-row layouts keep the base color.
                if use_two_rows:
                    if ridx == 0:
                        bg_rgb = _shade(base_col, -18)
                    else:
                        bg_rgb = _shade(base_col, 18)
                else:
                    bg_rgb = base_col
                chev = s.shapes.add_shape(MsoShape.CHEVRON, Inches(x), Inches(y), Inches(chev_w), Inches(band_h))
                chev.fill.solid(); chev.fill.fore_color.rgb = RGBColor(*bg_rgb)
                chev.line.fill.background()
                try:
                    chev.shadow.inherit = False; chev.shadow.visible = True; chev.shadow.blur = 5; chev.shadow.distance = 2
                except Exception:
                    pass
                # Place label inside a padded textbox over the chevron body so text does not spill into tips
                pad_l, pad_r, pad_t, pad_b = 0.55, 0.80, 0.16, 0.16
                tb = s.shapes.add_textbox(Inches(x + pad_l), Inches(y + pad_t), Inches(chev_w - pad_l - pad_r), Inches(band_h - pad_t - pad_b)).text_frame
                tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                try:
                    from pptx.enum.text import MSO_ANCHOR
                    tb.vertical_anchor = MSO_ANCHOR.MIDDLE
                except Exception:
                    pass
                try:
                    tb.margin_left = Inches(0.01); tb.margin_right = Inches(0.01); tb.margin_top = Inches(0.01); tb.margin_bottom = Inches(0.01)
                except Exception:
                    pass
                p = tb.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.CENTER; p.space_before = 0; p.space_after = 0
                fg_rgb = _contrast_text(bg_rgb)
                r = p.add_run(); r.text = str(lab); r.font.size = Pt(16); r.font.bold = True; r.font.color.rgb = RGBColor(*fg_rgb)
                centers.append(x + chev_w/2)
                x += chev_w - overlap
            # separator dots between chevrons
            dot_d = 0.1
            y_dot = y + band_h/2 - dot_d/2
            for j in range(len(centers) - 1):
                mid = (centers[j] + centers[j+1]) / 2
                dot = s.shapes.add_shape(MsoShape.OVAL, Inches(mid - dot_d/2), Inches(y_dot), Inches(dot_d), Inches(dot_d))
                dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(255,255,255); dot.line.color.rgb = RGBColor(209,213,219)
            y += band_h + 0.48

    # --- Alternate non-sequential friendly process layout (colored blocks with icons) ---
    def render_process_timeline_alt(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Process", font_size_pt=40, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(s, 1.0, 1.8, 10.8, 0.8, sub, font_size_pt=18, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)

        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = ["Focus Area", "Capability", "Strategy", "Outcome"]

        # Decide rows/cols and container
        # For better use of space: when 5+ items, render exactly 5 as a 3+2 grid
        if len(items) >= 5:
            max_cards = 5
        else:
            max_cards = min(4, len(items)) if items else 4
        rows = 2 if max_cards >= 5 else 1
        cols = 3 if rows == 2 else min(4, max_cards)
        card_h = 1.45
        gap_x, gap_y = 0.35, 0.40
        total_h = rows * card_h + (rows - 1) * gap_y
        # Compute container position from subtitle bottom to keep consistent top spacing
        SUB_Y, SUB_H = 1.55, 0.80
        sub_bottom = SUB_Y + SUB_H
        gap_top, gap_bottom = 0.30, 0.35  # reduced bottom margin
        pad = 0.12  # extra-tight wrap (~12px)
        cont_h = total_h + 2 * pad
        cont_y = sub_bottom + gap_top
        # Clamp to ensure bottom margin while minimizing empty space below
        max_cont_y = SLIDE_H_IN - gap_bottom - cont_h
        if cont_y > max_cont_y:
            cont_y = max_cont_y
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(0.7), Inches(cont_y), Inches(SLIDE_W_IN - 1.4), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240,245,255))))
        cont.line.color.rgb = RGBColor(229,231,235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        # Cards top aligned from container's inner padding so they remain inside
        base_y = cont_y + pad
        card_w = (SLIDE_W_IN - 1.4 - (cols + 1) * gap_x) / cols
        start_x = 0.7 + gap_x
        STEP_COLS = _step_colors()

        def _block(ix: int, lab: str, rr: int, cc: int, x_off: float = 0.0):
            x = start_x + x_off + cc * (card_w + gap_x)
            yy = base_y + rr * (card_h + gap_y)
            block_col = STEP_COLS[ix % len(STEP_COLS)]
            bg = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(yy), Inches(card_w), Inches(card_h))
            bg.fill.solid(); bg.fill.fore_color.rgb = RGBColor(*block_col)
            bg.line.fill.background()
            try:
                bg.shadow.inherit = False; bg.shadow.visible = True; bg.shadow.blur = 5; bg.shadow.distance = 2
            except Exception:
                pass
            # icon dot (top-left)
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(x + 0.18), Inches(yy + 0.18), Inches(0.22), Inches(0.22))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(255,255,255); ico.line.fill.background()

            tf = s.shapes.add_textbox(Inches(x + 0.50), Inches(yy + 0.18), Inches(card_w - 0.80), Inches(card_h - 0.36)).text_frame
            tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                tf.margin_left = Inches(0.01); tf.margin_right = Inches(0.01); tf.margin_top = Inches(0.01); tf.margin_bottom = Inches(0.01)
            except Exception:
                pass
            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            head, tail = _split_head_tail(lab)
            # Fit head/tail to stay within block without overflow
            h_len = len(head or lab)
            head_size = 18 if h_len <= 28 else (16 if h_len <= 42 else 15)
            head_rgb = _contrast_text(block_col)
            r1 = p.add_run(); r1.text = head or lab; r1.font.size = Pt(head_size); r1.font.bold = True; r1.font.color.rgb = RGBColor(*head_rgb)
            if tail:
                t_len = len(tail)
                tail_size = 14 if t_len <= 60 else 13
                p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
                base_tail = pal.get("muted", pal.get("text", head_rgb))
                if _is_dark_rgb(block_col):
                    tail_rgb = (235, 244, 255) if head_rgb == (255, 255, 255) else head_rgb
                else:
                    tail_rgb = base_tail if not _is_dark_rgb(base_tail) else pal.get("text", (24, 24, 27))
                r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(tail_size); r2.font.color.rgb = RGBColor(*tail_rgb)

        arr = items[:max_cards]
        if rows == 1:
            for i, lab in enumerate(arr):
                _block(i, lab, 0, i % cols, 0.0)
        else:
            split = (max_cards + 1) // 2  # for 5, split = 3 (top), then 2 (bottom)
            m_top = split
            m_bot = max_cards - split
            # Center rows that have fewer than 'cols' items
            off_top = ((cols - m_top) * (card_w + gap_x)) / 2.0 if m_top < cols else 0.0
            off_bot = ((cols - m_bot) * (card_w + gap_x)) / 2.0 if m_bot < cols else 0.0
            for i, lab in enumerate(arr[:split]):
                _block(i, lab, 0, i % cols, off_top)
            for j, lab in enumerate(arr[split:]):
                _block(split + j, lab, 1, j % cols, off_bot)


    def render_process_loop_cards(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.9,
            10.8,
            0.95,
            title_txt or "Process",
            font_size_pt=40,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(
                s,
                1.0,
                1.8,
                10.8,
                0.8,
                sub,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = ["Identify — Define the problem and success criteria", "Improve — Reduce friction and rework", "Innovate — Validate new approaches", "Management — Operationalize and measure"]

        n = min(5, max(4, len(items)))
        items = items[:n]

        primary = tuple(pal.get("primary", (41, 98, 255)))
        ref_cols: List[Tuple[int, int, int]] = [
            (160, 212, 104),
            (72, 201, 176),
            (76, 141, 219),
            (99, 102, 241),
            (239, 88, 102),
        ]
        cols = [_blend_rgb(c, primary, 0.10) for c in ref_cols][:n]

        # Layout region
        margin_x = 1.05
        y = 2.65
        h = 3.95
        gap = 0.50
        avail_w = SLIDE_W_IN - 2 * margin_x
        card_w = (avail_w - gap * (n - 1)) / n
        card_w = max(2.10, min(2.55, card_w))
        total_w = n * card_w + (n - 1) * gap
        start_x = margin_x + max(0.0, (avail_w - total_w) / 2)

        # Dashed loop around the cards
        loop_pad_x = 0.45
        loop_pad_y = 0.35
        loop_x1 = start_x - loop_pad_x
        loop_x2 = start_x + total_w + loop_pad_x
        loop_y1 = y - loop_pad_y
        loop_y2 = y + h + loop_pad_y
        loop_rgb = (209, 213, 219)

        def _dash(conn):
            try:
                conn.line.color.rgb = RGBColor(*loop_rgb)
                conn.line.width = Pt(2)
                conn.line.dash_style = MSO_LINE_DASH_STYLE.DASH
            except Exception:
                pass

        def _set_arrowhead(conn) -> None:
            try:
                from pptx.enum.dml import MSO_ARROWHEAD_STYLE  # type: ignore
                conn.line.end_arrowhead = MSO_ARROWHEAD_STYLE.TRIANGLE
            except Exception:
                return

        top_ln = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(loop_x1), Inches(loop_y1), Inches(loop_x2), Inches(loop_y1))
        _dash(top_ln)
        _set_arrowhead(top_ln)
        right_ln = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(loop_x2), Inches(loop_y1), Inches(loop_x2), Inches(loop_y2))
        _dash(right_ln)
        bot_ln = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(loop_x2), Inches(loop_y2), Inches(loop_x1), Inches(loop_y2))
        _dash(bot_ln)
        _set_arrowhead(bot_ln)
        left_ln = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(loop_x1), Inches(loop_y2), Inches(loop_x1), Inches(loop_y1))
        _dash(left_ln)

        for i, txt in enumerate(items):
            x = start_x + i * (card_w + gap)
            stroke = cols[i % len(cols)]

            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(255, 255, 255)
            card.line.color.rgb = RGBColor(*stroke)
            card.line.width = Pt(2.5)
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 10; card.shadow.distance = 2
            except Exception:
                pass

            # Step number (left of card)
            step_txt = f"{i+1:02d}"
            _add_textbox(
                s,
                x - 0.55,
                y + 1.55,
                0.60,
                0.60,
                step_txt,
                font_size_pt=28,
                bold=True,
                color=stroke,
                align="right",
                word_wrap=False,
                auto_fit=True,
            )

            # Icon placeholder (top-center)
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(x + card_w / 2 - 0.28), Inches(y + 0.38), Inches(0.56), Inches(0.56))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
            ico.line.color.rgb = RGBColor(*stroke)
            ico.line.width = Pt(1.5)

            head, tail = _split_head_tail(txt)
            head = (head or txt).strip()
            tail = (tail or "").strip()

            _add_textbox(
                s,
                x + 0.22,
                y + 1.15,
                card_w - 0.44,
                0.55,
                head,
                font_size_pt=16,
                bold=True,
                color=pal.get("text", (15, 23, 42)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )
            if tail:
                _add_textbox(
                    s,
                    x + 0.24,
                    y + 1.72,
                    card_w - 0.48,
                    1.55,
                    _shorten(tail, 110),
                    font_size_pt=10,
                    color=pal.get("muted", (71, 85, 105)),
                    align="center",
                    word_wrap=True,
                    auto_fit=True,
                )


    def render_process_alpha_badges(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.9,
            10.8,
            0.95,
            title_txt or "Process",
            font_size_pt=40,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(
                s,
                1.0,
                1.8,
                10.8,
                0.8,
                sub,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = [
                "Identify — Define the problem and success criteria",
                "Improve — Reduce friction and rework",
                "Innovation — Validate new approaches",
                "Management — Operationalize and measure",
                "Success — Sustain outcomes and scale",
            ]

        n = min(6, max(4, len(items)))
        items = items[:n]

        primary = tuple(pal.get("primary", (41, 98, 255)))
        ref_cols: List[Tuple[int, int, int]] = [
            (160, 212, 104),
            (72, 201, 176),
            (76, 141, 219),
            (99, 102, 241),
            (14, 165, 233),
            (239, 88, 102),
        ]
        cols = [_blend_rgb(c, primary, 0.08) for c in ref_cols][:n]

        # Layout region
        margin_x = 0.95
        y = 3.05
        h = 2.60
        header_h = 0.62
        gap = 0.42
        avail_w = SLIDE_W_IN - 2 * margin_x
        card_w = (avail_w - gap * (n - 1)) / n
        card_w = max(2.05, min(2.35, card_w))
        total_w = n * card_w + (n - 1) * gap
        start_x = margin_x + max(0.0, (avail_w - total_w) / 2)

        # Dashed weaving connectors (approximate)
        loop_rgb = (209, 213, 219)
        def _dash(conn):
            try:
                conn.line.color.rgb = RGBColor(*loop_rgb)
                conn.line.width = Pt(2)
                conn.line.dash_style = MSO_LINE_DASH_STYLE.DASH
            except Exception:
                pass

        centers: List[Tuple[float, float, float, float]] = []
        for i in range(n):
            x = start_x + i * (card_w + gap)
            centers.append((x, y, x + card_w, y + h))

        # Draw connector "U" shapes alternating above and below between cards
        for i in range(n - 1):
            x1l, y1t, x1r, y1b = centers[i]
            x2l, y2t, x2r, y2b = centers[i + 1]
            x1 = x1r
            x2 = x2l
            mid_x = (x1 + x2) / 2
            is_below = (i % 2 == 0)
            bend_y = (y + h + 0.55) if is_below else (y - 0.45)
            y_anchor = (y + h * 0.60) if is_below else (y + header_h * 0.55)

            seg1 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(x1), Inches(y_anchor), Inches(mid_x), Inches(y_anchor))
            seg2 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(mid_x), Inches(y_anchor), Inches(mid_x), Inches(bend_y))
            seg3 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(mid_x), Inches(bend_y), Inches(x2), Inches(bend_y))
            seg4 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(x2), Inches(bend_y), Inches(x2), Inches(y_anchor))
            for seg in (seg1, seg2, seg3, seg4):
                _dash(seg)

        # Start / Goals markers
        start_cx = start_x + card_w / 2
        start_y = y + h + 0.90
        start_dot = s.shapes.add_shape(MsoShape.OVAL, Inches(start_cx - 0.10), Inches(start_y - 0.10), Inches(0.20), Inches(0.20))
        start_dot.fill.solid(); start_dot.fill.fore_color.rgb = RGBColor(203, 213, 225)
        start_dot.line.fill.background()
        _add_textbox(s, start_cx - 0.70, start_y + 0.05, 1.40, 0.35, "Start", font_size_pt=12, bold=True, color=pal.get("text", (15, 23, 42)), align="center", word_wrap=False, auto_fit=True)

        end_cx = start_x + (n - 1) * (card_w + gap) + card_w / 2
        goals_y = y - 0.70
        goals_dot = s.shapes.add_shape(MsoShape.OVAL, Inches(end_cx - 0.10), Inches(goals_y - 0.10), Inches(0.20), Inches(0.20))
        goals_dot.fill.solid(); goals_dot.fill.fore_color.rgb = RGBColor(203, 213, 225)
        goals_dot.line.fill.background()
        goals_stem = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(end_cx), Inches(goals_y), Inches(end_cx), Inches(y - 0.08))
        try:
            goals_stem.line.color.rgb = RGBColor(203, 213, 225)
            goals_stem.line.width = Pt(2)
        except Exception:
            pass
        _add_textbox(s, end_cx - 0.70, goals_y - 0.45, 1.40, 0.35, "Goals", font_size_pt=12, bold=True, color=pal.get("text", (15, 23, 42)), align="center", word_wrap=False, auto_fit=True)

        # Cards
        for i, txt in enumerate(items):
            x = start_x + i * (card_w + gap)
            col = cols[i % len(cols)]

            outer = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(h))
            outer.fill.solid(); outer.fill.fore_color.rgb = RGBColor(255, 255, 255)
            outer.line.color.rgb = RGBColor(*col)
            outer.line.width = Pt(2)
            try:
                outer.shadow.inherit = False; outer.shadow.visible = True; outer.shadow.blur = 10; outer.shadow.distance = 2
            except Exception:
                pass

            header = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(header_h))
            header.fill.solid(); header.fill.fore_color.rgb = RGBColor(*col)
            header.line.fill.background()

            # Letter badge
            badge_d = 0.44
            badge_cx = x + card_w / 2
            badge_cy = y - 0.10
            badge = s.shapes.add_shape(MsoShape.OVAL, Inches(badge_cx - badge_d / 2), Inches(badge_cy - badge_d / 2), Inches(badge_d), Inches(badge_d))
            badge.fill.solid(); badge.fill.fore_color.rgb = RGBColor(*col)
            badge.line.color.rgb = RGBColor(255, 255, 255)
            badge.line.width = Pt(1.5)
            letter = chr(ord("A") + i)
            btf = badge.text_frame
            btf.clear()
            btf.word_wrap = False
            btf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            bp = btf.paragraphs[0]
            bp.alignment = PP_ALIGN.CENTER
            br = bp.add_run()
            br.text = letter
            br.font.size = Pt(14)
            br.font.bold = True
            br.font.color.rgb = RGBColor(255, 255, 255)

            head, tail = _split_head_tail(txt)
            head = (head or txt).strip()
            tail = (tail or "").strip()

            _add_textbox(
                s,
                x + 0.15,
                y + 0.16,
                card_w - 0.30,
                header_h - 0.18,
                head,
                font_size_pt=14,
                bold=True,
                color=(255, 255, 255),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )
            if tail:
                _add_textbox(
                    s,
                    x + 0.18,
                    y + header_h + 0.22,
                    card_w - 0.36,
                    h - header_h - 0.40,
                    _shorten(tail, 110),
                    font_size_pt=10,
                    color=pal.get("muted", (71, 85, 105)),
                    align="center",
                    word_wrap=True,
                    auto_fit=True,
                )


    def render_process_ribbon_nodes(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.9,
            10.8,
            0.95,
            title_txt or "Process",
            font_size_pt=40,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(
                s,
                1.0,
                1.8,
                10.8,
                0.8,
                sub,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = [
                "Identify — Define the problem and success criteria",
                "Improve — Remove friction and rework",
                "Innovation — Validate new approaches",
                "Management — Operationalize and measure",
                "Success — Sustain outcomes and scale",
            ]

        n = min(6, max(4, len(items)))
        items = items[:n]

        STEP_COLS = _step_colors()
        # Ribbon geometry (center band)
        band_x, band_w = 1.0, 11.3
        band_y, band_h = 4.55, 0.55
        overlap = 0.34
        chev_w = (band_w + (n - 1) * overlap) / n

        # Text and icon placement (alternating, like reference)
        top_text_y, top_text_h = 2.45, 0.95
        bot_text_y, bot_text_h = 5.20, 0.95
        top_ring_y = 3.25
        ring_d = 1.20
        bot_ring_y = 5.95
        letter_d = 0.42

        centers: List[Tuple[float, float]] = []
        x = band_x
        for i in range(n):
            col = STEP_COLS[i % len(STEP_COLS)]
            chev = s.shapes.add_shape(MsoShape.CHEVRON, Inches(x), Inches(band_y), Inches(chev_w), Inches(band_h))
            chev.fill.solid(); chev.fill.fore_color.rgb = RGBColor(*col)
            chev.line.fill.background()
            try:
                chev.shadow.inherit = False; chev.shadow.visible = True; chev.shadow.blur = 5; chev.shadow.distance = 2
            except Exception:
                pass

            cx = x + chev_w / 2
            centers.append((cx, band_y + band_h / 2))
            x += chev_w - overlap

        for i, (txt, (cx, cy)) in enumerate(zip(items, centers)):
            col = STEP_COLS[i % len(STEP_COLS)]
            letter = chr(ord("A") + i)

            # Letter node on ribbon
            l_x = cx - letter_d / 2
            l_y = cy - letter_d / 2
            node = s.shapes.add_shape(MsoShape.OVAL, Inches(l_x), Inches(l_y), Inches(letter_d), Inches(letter_d))
            node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*col)
            node.line.color.rgb = RGBColor(255, 255, 255)
            node.line.width = Pt(1.5)
            ntf = node.text_frame
            ntf.clear(); ntf.word_wrap = False; ntf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            np = ntf.paragraphs[0]; np.alignment = PP_ALIGN.CENTER
            nr = np.add_run(); nr.text = letter; nr.font.size = Pt(14); nr.font.bold = True; nr.font.color.rgb = RGBColor(255, 255, 255)

            # Alternate: text on one side, icon ring on the opposite side
            text_top = (i % 2 == 0)
            ring_y = bot_ring_y if text_top else top_ring_y
            text_y = top_text_y if text_top else bot_text_y
            text_h = top_text_h if text_top else bot_text_h

            ring = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ring_d / 2), Inches(ring_y), Inches(ring_d), Inches(ring_d))
            ring.fill.solid(); ring.fill.fore_color.rgb = RGBColor(255, 255, 255)
            ring.line.color.rgb = RGBColor(*col)
            ring.line.width = Pt(6)

            # Simple icon placeholder inside ring
            ico_d = 0.48
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ico_d / 2), Inches(ring_y + ring_d / 2 - ico_d / 2), Inches(ico_d), Inches(ico_d))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
            ico.line.color.rgb = RGBColor(*col)
            ico.line.width = Pt(2)

            # Stem from letter node to ring
            stem = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(cx), Inches(cy), Inches(cx), Inches(ring_y + ring_d / 2))
            try:
                stem.line.color.rgb = RGBColor(*col)
                stem.line.width = Pt(3)
            except Exception:
                pass

            head, tail = _split_head_tail(txt)
            head = (head or txt).strip()
            tail = (tail or "").strip()

            # Text block (centered above/below)
            _add_textbox(
                s,
                cx - 1.15,
                text_y,
                2.30,
                0.40,
                head,
                font_size_pt=14,
                bold=True,
                color=pal.get("text", (15, 23, 42)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )
            if tail:
                _add_textbox(
                    s,
                    cx - 1.20,
                    text_y + 0.38,
                    2.40,
                    max(0.40, text_h - 0.38),
                    _shorten(tail, 95),
                    font_size_pt=10,
                    color=pal.get("muted", (71, 85, 105)),
                    align="center",
                    word_wrap=True,
                    auto_fit=True,
                )

    def render_timeline_vchevrons(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        """Vertical chevron stack: left icon bar + right chevron rows (like provided sample)."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=40, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(s, 1.0, 1.8, 10.8, 0.7, sub, font_size_pt=18, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)

        items = [str(x).strip() for x in (points or []) if str(x).strip()] or ["Step 1", "Step 2", "Step 3", "Step 4"]
        n = min(6, max(4, len(items)))
        STEP_COLS = _step_colors()

        # Layout metrics & container
        margin_x = 1.2
        row_h = 1.05
        gap = 0.28
        chevron_h = 0.92
        icon_w = 0.92
        spine_gap = 0.12

        # Compute available vertical space and scale rows to fit
        SUB_Y, SUB_H = 1.55, 0.80
        top_y = max(2.05, SUB_Y + SUB_H + 0.30)
        bottom_margin = 0.65
        avail_h = max(1.0, SLIDE_H_IN - bottom_margin - top_y)
        total_h = n * row_h + (n - 1) * gap
        extra = 0.66  # container padding
        if total_h + extra > avail_h:
            scale = max(0.72, (avail_h - 0.02) / (total_h + extra))
            row_h *= scale
            gap *= scale
            chevron_h = min(chevron_h * scale, row_h - 0.10)
            total_h = n * row_h + (n - 1) * gap

        cont_y = top_y
        cont_h = min(avail_h, total_h + extra)
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(0.7), Inches(cont_y), Inches(SLIDE_W_IN - 1.4), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240,245,255))))
        cont.line.color.rgb = RGBColor(229,231,235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        y_top = cont_y + 0.33
        # Slightly widen available width for label text inside the chevron
        chev_w = SLIDE_W_IN - 2 * margin_x - icon_w - spine_gap - 0.2
        for i, lab in enumerate(items):
            y = y_top + i * (row_h)
            base_x = margin_x
            step_col = STEP_COLS[i % len(STEP_COLS)]
            # left icon/block
            icon = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(base_x), Inches(y + (row_h - chevron_h)/2), Inches(icon_w), Inches(chevron_h))
            icon.fill.solid(); icon.fill.fore_color.rgb = RGBColor(*step_col)
            icon.line.fill.background()
            dot = s.shapes.add_shape(MsoShape.OVAL, Inches(base_x + 0.26), Inches(y + row_h/2 - 0.14), Inches(0.28), Inches(0.28))
            dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(255,255,255); dot.line.fill.background()
            # chevron bar to the right
            cx = base_x + icon_w + spine_gap
            chev = s.shapes.add_shape(MsoShape.CHEVRON, Inches(cx), Inches(y + (row_h - chevron_h)/2), Inches(chev_w), Inches(chevron_h))
            chev.fill.solid(); chev.fill.fore_color.rgb = RGBColor(*step_col)
            chev.line.fill.background()
            try:
                chev.adjustments[0] = 0.25
            except Exception:
                pass
            tf = chev.text_frame; tf.clear(); tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                from pptx.enum.text import MSO_ANCHOR
                tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            except Exception:
                pass
            try:
                # Add safe insets so text stays away from the chevron tip and edges (slightly smaller to avoid truncation)
                from pptx.util import Inches as _In
                tf.margin_left = int(_In(0.12))
                tf.margin_right = int(_In(0.12))
                tf.margin_top = int(_In(0.10))
                tf.margin_bottom = int(_In(0.10))
            except Exception:
                pass
            p = tf.paragraphs[0]; p.alignment = PP_ALIGN.LEFT
            head, tail = _split_head_tail(lab)
            # Allow full label content; rely on word wrap and auto-fit to avoid truncation
            head_rgb = _contrast_text(step_col)
            r1 = p.add_run(); r1.text = head or lab; r1.font.size = Pt(19); r1.font.bold = True; r1.font.color.rgb = RGBColor(*head_rgb)
            if tail:
                p2 = tf.add_paragraph(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(12)
                base_tail = pal.get("muted", pal.get("text", head_rgb))
                if _is_dark_rgb(step_col):
                    tail_rgb = (235, 244, 255) if head_rgb == (255, 255, 255) else head_rgb
                else:
                    tail_rgb = base_tail if not _is_dark_rgb(base_tail) else pal.get("text", (24, 24, 27))
                r2.font.color.rgb = RGBColor(*tail_rgb)



    def render_timeline_hex_chain(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        """Hexagon chain timeline: alternating hex nodes on a baseline (matches reference design)."""
        s = prs.slides.add_slide(blank); apply_bg(s)

        title_up = (title_txt or "Timeline").strip() or "Timeline"
        _add_textbox(s, 1.0, 0.55, 11.3, 0.75, title_up.upper(), font_size_pt=24, bold=True, color=TITLE_COL, align="center", word_wrap=True, auto_fit=True)

        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(s, 1.0, 1.20, 11.3, 0.40, sub.upper(), font_size_pt=11, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)

        # Slide index bubble (top-right)
        try:
            idx_txt = str(int(current_render_idx))
        except Exception:
            idx_txt = ""
        if idx_txt:
            d = 0.36
            cx = SLIDE_W_IN - 0.75 - d
            cy = 0.60
            b = s.shapes.add_shape(MsoShape.OVAL, Inches(cx), Inches(cy), Inches(d), Inches(d))
            b.fill.solid(); b.fill.fore_color.rgb = RGBColor(255, 255, 255)
            b.line.color.rgb = RGBColor(209, 213, 219)
            tf = b.text_frame; tf.clear()
            p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
            r = p.add_run(); r.text = idx_txt
            r.font.size = Pt(10); r.font.bold = True
            r.font.color.rgb = RGBColor(*pal.get("muted", (71,85,105)))

        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = ["Identification", "Initiation", "Planning", "Execution & Control", "Closeout"]

        n = min(6, max(4, len(items)))
        items = items[:n]

        # Alternating hexagon chain geometry
        top_y = 2.75
        bot_y = 3.55
        y_line = 3.62
        margin_x = 1.00
        gap = 0.55
        avail_w = SLIDE_W_IN - 2 * margin_x
        hex_w = (avail_w - (n - 1) * gap) / max(1, n)
        hex_w = max(1.65, min(2.20, hex_w))
        total_w = n * hex_w + (n - 1) * gap
        start_x = margin_x + max(0.0, (avail_w - total_w) / 2)
        hex_h = 1.55

        # Baseline
        line = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(start_x + 0.10), Inches(y_line), Inches(total_w - 0.20), Inches(0.03))
        line.fill.solid(); line.fill.fore_color.rgb = RGBColor(209, 213, 219)
        line.line.fill.background()

        accent_a = tuple(pal.get("primary", (14, 165, 233)))
        accent_b = tuple(pal.get("text", (15, 23, 42)))
        light_fill = (243, 244, 246)

        positions: List[tuple[float, float]] = []
        for i in range(n):
            x = start_x + i * (hex_w + gap)
            y = top_y if (i % 2 == 0) else bot_y
            positions.append((x, y))

        for i, (x, y) in enumerate(positions):
            head, tail = _split_head_tail(items[i])
            label = (head or items[i]).strip()
            desc = (tail or "").strip()

            outline = accent_a if i == 0 else accent_b

            hex_shape = s.shapes.add_shape(MsoShape.HEXAGON, Inches(x), Inches(y), Inches(hex_w), Inches(hex_h))
            hex_shape.fill.solid(); hex_shape.fill.fore_color.rgb = RGBColor(255, 255, 255)
            hex_shape.line.color.rgb = RGBColor(*outline)
            hex_shape.line.width = Pt(2)

            # Number circle near top of hex
            circ_d = 0.52
            circ_x = x + (hex_w / 2) - (circ_d / 2)
            circ_y = y + 0.22
            circ = s.shapes.add_shape(MsoShape.OVAL, Inches(circ_x), Inches(circ_y), Inches(circ_d), Inches(circ_d))

            if i % 2 == 0:
                fill = accent_a if i == 0 else accent_b
                circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*fill)
                circ.line.fill.background()
                num_col = (255, 255, 255)
            else:
                circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*light_fill)
                circ.line.color.rgb = RGBColor(209, 213, 219)
                num_col = accent_b

            tf = circ.text_frame; tf.clear()
            p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
            r = p.add_run(); r.text = str(i + 1)
            r.font.size = Pt(14); r.font.bold = True
            r.font.color.rgb = RGBColor(*num_col)

            # Main label inside hex
            lbl_tf = s.shapes.add_textbox(Inches(x + 0.16), Inches(y + 0.78), Inches(hex_w - 0.32), Inches(0.70)).text_frame
            lbl_tf.word_wrap = True
            lbl_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p2 = lbl_tf.paragraphs[0]; p2.clear(); p2.alignment = PP_ALIGN.CENTER
            r2 = p2.add_run(); r2.text = label.upper()[:34]
            r2.font.size = Pt(11); r2.font.bold = True
            r2.font.color.rgb = RGBColor(*accent_b)

            # Optional short description between nodes (if provided via "Label  Description")
            if desc and i < n - 1:
                gap_x0 = x + hex_w
                gap_x1 = positions[i + 1][0]
                w = max(0.6, (gap_x1 - gap_x0) - 0.10)
                tx = gap_x0 + 0.05
                ty = y_line - 0.32 if (i % 2 == 0) else y_line + 0.07
                _add_textbox(s, tx, ty, w, 0.55, desc[:110], font_size_pt=8, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)


    def _timeline_explicit_year_from_item(it: Any) -> str | None:
        try:
            if isinstance(it, dict):
                dr = str(it.get("date_range") or it.get("year") or it.get("date") or "").strip()
                m = re.search(r"\b(19|20)\d{2}\b", dr)
                if m:
                    return m.group(0)
                lab = str(it.get("label") or it.get("title") or "").strip()
                m2 = re.match(r"\s*((19|20)\d{2})\b", lab)
                if m2:
                    return m2.group(1)
                return None
            s0 = str(it or "").strip()
            m3 = re.match(r"\s*((19|20)\d{2})\b", s0)
            if m3:
                return m3.group(1)
        except Exception:
            return None
        return None


    def _timeline_label_for_item(item: Any, index: int, raw_items: List[Any], variant_name: str):
        has_any_explicit = any(_timeline_explicit_year_from_item(x) for x in (raw_items or []))
        v = str(variant_name or "").strip().lower()

        if has_any_explicit:
            explicit = _timeline_explicit_year_from_item(item)
            if explicit:
                return explicit, True
            return "", False

        if v == "ring_nodes":
            return "", False
        return f"{int(index) + 1:02d}", True


    def render_timeline_pill_band(title_txt: str, points: List[Any], topic_src: str, subtitle_override: str | None = None):
        """Timeline infographic: pill band split into colored segments with year markers and captions."""
        s = prs.slides.add_slide(blank); apply_bg(s)

        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.75,
            10.8,
            0.95,
            title_txt or "Timeline",
            font_size_pt=44,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub_raw = str(subtitle_override or "").strip()
        if sub_raw:
            _add_textbox(
                s,
                1.0,
                1.65,
                10.8,
                0.6,
                sub_raw,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        raw_items: List[Any] = [x for x in (points or []) if x is not None]
        if not raw_items:
            raw_items = [
                {"label": "Planning", "description": "Define scope, goals, and constraints.", "date_range": "2016"},
                {"label": "Innovation", "description": "Prototype and validate new approaches.", "date_range": "2017"},
                {"label": "Competition", "description": "Differentiate with clear advantages.", "date_range": "2018"},
                {"label": "Execution", "description": "Deliver capabilities and measure outcomes.", "date_range": "2019"},
                {"label": "Success", "description": "Scale results and optimize continuously.", "date_range": "2020"},
            ]

        def _extract_title_desc(it: Any) -> tuple[str, str]:
            if isinstance(it, dict):
                lab = str(it.get("label") or it.get("title") or "").strip()
                desc = str(it.get("description") or "").strip()
                return lab, desc
            head, tail = _split_head_tail(str(it or "").strip())
            return (head or str(it or "").strip()), tail

        items: List[tuple[str, bool, str, str]] = []
        n = min(6, max(4, len(raw_items)))
        items_src = raw_items[:n]
        for i, it in enumerate(items_src):
            yr, show_yr = _timeline_label_for_item(it, i, items_src, "pill_band")
            lab, desc = _extract_title_desc(it)
            lab = re.sub(r"\s+", " ", lab).strip()
            desc = re.sub(r"\s+", " ", desc).strip()
            items.append((yr, show_yr, lab, desc))

        # Segment colors inspired by the reference screenshot; blended slightly toward theme primary.
        primary = tuple(pal.get("primary", (41, 98, 255)))
        ref_cols: List[Tuple[int, int, int]] = [
            (92, 155, 213),
            (72, 201, 176),
            (160, 212, 104),
            (255, 201, 84),
            (239, 88, 102),
            (99, 102, 241),
        ]
        seg_cols = [_blend_rgb(c, primary, 0.14) for c in ref_cols][:n]

        band_x = 0.90
        band_w = SLIDE_W_IN - 2 * band_x
        band_h = 0.75
        band_y = 3.05
        seg_w = band_w / max(1, n)

        # Base subtle shadow/background band
        bg_band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(band_y - 0.15), Inches(band_w), Inches(band_h + 0.30))
        bg_band.fill.solid(); bg_band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        bg_band.line.color.rgb = RGBColor(229, 231, 235)
        try:
            bg_band.shadow.inherit = False; bg_band.shadow.visible = True; bg_band.shadow.blur = 12; bg_band.shadow.distance = 3
        except Exception:
            pass

        # Colored pill segments
        for i in range(n):
            x = band_x + i * seg_w
            col = seg_cols[i % len(seg_cols)]
            if i == 0 or i == n - 1:
                shape = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(band_y), Inches(seg_w), Inches(band_h))
            else:
                shape = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(x), Inches(band_y), Inches(seg_w), Inches(band_h))
            shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor(*col)
            shape.line.fill.background()

        # Year markers and captions
        year_y = band_y - 0.80
        mark_top_y = band_y - 0.55
        for i, (year, show_year, lab, desc) in enumerate(items):
            col = seg_cols[i % len(seg_cols)]
            dark_col = _shade(col, -55)
            cx = band_x + (i + 0.5) * seg_w

            if show_year and str(year).strip():
                _add_textbox(
                    s,
                    cx - 0.60,
                    year_y,
                    1.20,
                    0.35,
                    str(year),
                    font_size_pt=22,
                    bold=True,
                    color=col,
                    align="center",
                    word_wrap=False,
                    auto_fit=True,
                )

                stem = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(cx - 0.02), Inches(mark_top_y), Inches(0.04), Inches(0.70))
                stem.fill.solid(); stem.fill.fore_color.rgb = RGBColor(*dark_col)
                stem.line.fill.background()

                dia = s.shapes.add_shape(MsoShape.DIAMOND, Inches(cx - 0.09), Inches(band_y + band_h * 0.40), Inches(0.18), Inches(0.18))
                dia.fill.solid(); dia.fill.fore_color.rgb = RGBColor(*dark_col)
                dia.line.fill.background()

            lab_y = band_y + band_h + 0.35
            _add_textbox(
                s,
                band_x + i * seg_w + 0.08,
                lab_y,
                seg_w - 0.16,
                0.38,
                lab,
                font_size_pt=16,
                bold=True,
                color=pal.get("text", (15, 23, 42)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )
            if desc:
                _add_textbox(
                    s,
                    band_x + i * seg_w + 0.08,
                    lab_y + 0.45,
                    seg_w - 0.16,
                    1.05,
                    desc,
                    font_size_pt=10,
                    color=pal.get("muted", (71, 85, 105)),
                    align="center",
                    word_wrap=True,
                    auto_fit=True,
                )


    def render_timeline_ring_nodes(title_txt: str, points: List[Any], topic_src: str, subtitle_override: str | None = None):
        """Timeline infographic: ring nodes connected to a baseline with year labels (matches reference)."""
        s = prs.slides.add_slide(blank); apply_bg(s)

        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.75,
            10.8,
            0.95,
            title_txt or "Timeline",
            font_size_pt=44,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub_raw = str(subtitle_override or "").strip()
        if sub_raw:
            _add_textbox(
                s,
                1.0,
                1.65,
                10.8,
                0.6,
                sub_raw,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        raw_items: List[Any] = [x for x in (points or []) if x is not None]
        if not raw_items:
            raw_items = [
                {"label": "Planning", "description": "Define scope, goals, and constraints.", "date_range": "2016"},
                {"label": "Execution", "description": "Deliver capabilities and remove blockers.", "date_range": "2017"},
                {"label": "Competition", "description": "Differentiate with clear advantages.", "date_range": "2018"},
                {"label": "Improve", "description": "Optimize with feedback loops and KPIs.", "date_range": "2019"},
            ]


        def _extract_title_desc(it: Any) -> tuple[str, str]:
            if isinstance(it, dict):
                lab = str(it.get("label") or it.get("title") or "").strip()
                desc = str(it.get("description") or "").strip()
                return lab, desc
            head, tail = _split_head_tail(str(it or "").strip())
            return (head or str(it or "").strip()), tail

        items: List[tuple[str, bool, str, str]] = []
        n = min(6, max(4, len(raw_items)))
        items_src = raw_items[:n]
        for i, it in enumerate(items_src):
            yr, show_yr = _timeline_label_for_item(it, i, items_src, "ring_nodes")
            lab, desc = _extract_title_desc(it)
            lab = re.sub(r"\s+", " ", lab).strip()
            desc = re.sub(r"\s+", " ", desc).strip()
            items.append((yr, show_yr, lab, desc))

        primary = tuple(pal.get("primary", (41, 98, 255)))
        ref_cols: List[Tuple[int, int, int]] = [
            (76, 141, 219),
            (72, 201, 176),
            (160, 212, 104),
            (255, 201, 84),
            (239, 88, 102),
            (99, 102, 241),
        ]
        node_cols = [_blend_rgb(c, primary, 0.12) for c in ref_cols][:n]

        margin_x = 1.0
        avail_w = SLIDE_W_IN - 2 * margin_x
        baseline_y = 5.05

        # Circle size scales slightly with number of nodes.
        if n <= 4:
            d = 2.05
        elif n == 5:
            d = 1.85
        else:
            d = 1.65
        top_y = 2.35
        stem_w = 0.10
        dot_d = 0.48

        # Place centers so full circles stay inside slide bounds.
        start_cx = margin_x + d / 2
        end_cx = (SLIDE_W_IN - margin_x) - d / 2
        span = max(0.01, end_cx - start_cx)
        gap = span / max(1, (n - 1))

        # Baseline spans between first/last nodes (with a small visual pad)
        base_pad = 0.10
        base_x = max(margin_x, start_cx - d / 2 + base_pad)
        base_w = min(avail_w, (end_cx - start_cx) + d - 2 * base_pad)
        base = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(base_x), Inches(baseline_y), Inches(base_w), Inches(0.05))
        base.fill.solid(); base.fill.fore_color.rgb = RGBColor(196, 204, 212)
        base.line.fill.background()
        for i, (year, show_year, lab, desc) in enumerate(items):
            col = node_cols[i % len(node_cols)]
            cx = start_cx + i * gap
            x = cx - d / 2

            ring = s.shapes.add_shape(MsoShape.OVAL, Inches(x), Inches(top_y), Inches(d), Inches(d))
            ring.fill.solid(); ring.fill.fore_color.rgb = RGBColor(255, 255, 255)
            ring.line.color.rgb = RGBColor(*col)
            ring.line.width = Pt(10)

            # Small top "icon" placeholder (simple mini circle)
            ico_d = max(0.34, d * 0.22)
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ico_d / 2), Inches(top_y + 0.32), Inches(ico_d), Inches(ico_d))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
            ico.line.color.rgb = RGBColor(*col)
            ico.line.width = Pt(2)

            # Label and description inside ring
            lab_y = top_y + 0.72
            _add_textbox(
                s,
                x + 0.16,
                lab_y,
                d - 0.32,
                0.42,
                lab,
                font_size_pt=16,
                bold=True,
                color=pal.get("text", (15, 23, 42)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )
            if desc:
                _add_textbox(
                    s,
                    x + 0.18,
                    lab_y + 0.46,
                    d - 0.36,
                    d - (lab_y + 0.46 - top_y) - 0.25,
                    _shorten(desc, 72),
                    font_size_pt=10,
                    color=pal.get("muted", (71, 85, 105)),
                    align="center",
                    word_wrap=True,
                    auto_fit=True,
                )

            # Stem down to baseline
            stem_top = top_y + d
            dot_y = baseline_y - dot_d / 2
            stem_h = max(0.20, dot_y - stem_top)
            stem = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(cx - stem_w / 2), Inches(stem_top), Inches(stem_w), Inches(stem_h))
            stem.fill.solid(); stem.fill.fore_color.rgb = RGBColor(*col)
            stem.line.fill.background()

            dot = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - dot_d / 2), Inches(dot_y), Inches(dot_d), Inches(dot_d))
            dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*col)
            dot.line.fill.background()

            if show_year and str(year).strip():
                _add_textbox(
                    s,
                    cx - 0.70,
                    baseline_y + 0.18,
                    1.40,
                    0.35,
                    str(year),
                    font_size_pt=14,
                    bold=True,
                    color=pal.get("text", (15, 23, 42)),
                    align="center",
                    word_wrap=False,
                    auto_fit=True,
                )


    def render_timeline_tri_pennants(title_txt: str, points: List[Any], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)

        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.75,
            10.8,
            0.95,
            title_txt or "Timeline",
            font_size_pt=44,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub_raw = str(subtitle_override or "").strip()
        if sub_raw:
            _add_textbox(
                s,
                1.0,
                1.65,
                10.8,
                0.6,
                sub_raw,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        # Constraint-based layout:
        # - Triangle is the anchor.
        # - Caption (label/description) is positioned relative to the triangle with fixed gaps.
        # - Baseline is clamped so top captions never collide with title/subtitle and bottom captions
        #   stay within slide margins.
        scale = min(SLIDE_W_IN / 13.333, SLIDE_H_IN / 7.5)
        top_safe_y = 2.05 if sub_raw else 1.85
        bottom_safe_y = SLIDE_H_IN - 0.55
        label_gap_above = 0.22 * scale
        label_gap_below = 0.22 * scale

        raw_items: List[Any] = [x for x in (points or []) if x is not None]
        if not raw_items:
            raw_items = [
                {"label": "Project", "description": "Kick off scope and stakeholders.", "date_range": "2015"},
                {"label": "Planning", "description": "Define milestones, owners, and risks.", "date_range": "2016"},
                {"label": "Execution", "description": "Ship deliverables and validate outcomes.", "date_range": "2017"},
                {"label": "Control", "description": "Monitor KPIs and adjust course.", "date_range": "2018"},
                {"label": "Competition", "description": "Differentiate with clear advantages.", "date_range": "2019"},
                {"label": "Target", "description": "Hit goals and standardize best practices.", "date_range": "2020"},
            ]

        def _explicit_year(it: Any) -> str | None:
            try:
                if isinstance(it, dict):
                    dr = str(it.get("date_range") or it.get("year") or it.get("date") or "").strip()
                    m = re.search(r"\b(19|20)\d{2}\b", dr)
                    if m:
                        return m.group(0)
                    lab = str(it.get("label") or it.get("title") or "").strip()
                    m2 = re.match(r"\s*((19|20)\d{2})\b", lab)
                    if m2:
                        return m2.group(1)
                    return None
                s0 = str(it or "").strip()
                m3 = re.match(r"\s*((19|20)\d{2})\b", s0)
                if m3:
                    return m3.group(1)
            except Exception:
                return None
            return None

        def _extract_year(it: Any, fallback: int) -> str:
            try:
                if isinstance(it, dict):
                    dr = str(it.get("date_range") or it.get("year") or it.get("date") or "").strip()
                    m = re.search(r"\b(19|20)\d{2}\b", dr)
                    if m:
                        return m.group(0)
                    lab = str(it.get("label") or it.get("title") or "").strip()
                    m2 = re.match(r"\s*((19|20)\d{2})\b", lab)
                    if m2:
                        return m2.group(1)
                else:
                    s0 = str(it or "").strip()
                    m3 = re.match(r"\s*((19|20)\d{2})\b", s0)
                    if m3:
                        return m3.group(1)
            except Exception:
                pass
            return str(fallback)

        def _extract_title_desc(it: Any) -> tuple[str, str]:
            if isinstance(it, dict):
                lab = str(it.get("label") or it.get("title") or "").strip()
                desc = str(it.get("description") or "").strip()
                return lab, desc
            head, tail = _split_head_tail(str(it or "").strip())
            return (head or str(it or "").strip()), tail

        items: List[tuple[str, bool, str, str]] = []
        n = min(6, max(4, len(raw_items)))
        items_src = raw_items[:n]
        for i, it in enumerate(items_src):
            yr, show_yr = _timeline_label_for_item(it, i, items_src, "tri_pennants")
            lab, desc = _extract_title_desc(it)
            lab = re.sub(r"\s+", " ", lab).strip()
            desc = re.sub(r"\s+", " ", desc).strip()
            items.append((yr, show_yr, lab, desc))

        primary = tuple(pal.get("primary", (41, 98, 255)))
        ref_cols: List[Tuple[int, int, int]] = [
            (92, 155, 213),
            (72, 201, 176),
            (160, 212, 104),
            (255, 201, 84),
            (239, 88, 102),
            (71, 85, 105),
        ]
        tri_cols = [_blend_rgb(c, primary, 0.10) for c in ref_cols][:n]

        margin_x = 0.85 * scale
        avail_w = SLIDE_W_IN - 2 * margin_x
        tri_h = 1.65 * scale

        # Captions need a stable (snap) size so spacing is consistent. Use a size that can
        # accommodate wrapping while keeping labels visually close to their anchors.
        cap_h = 1.18 * scale
        cap_h_max = 1.55 * scale
        cap_h = min(cap_h, cap_h_max)

        # Choose a baseline that guarantees both:
        # - enough room above for "down" triangle captions
        # - enough room below for "up" triangle captions
        baseline_min = top_safe_y + cap_h + label_gap_above + 0.10 * scale
        baseline_max = bottom_safe_y - (tri_h + label_gap_below + cap_h + 0.10 * scale)
        baseline_y = 4.15 * scale
        if baseline_min <= baseline_max:
            baseline_y = max(baseline_min, min(baseline_max, baseline_y))
        else:
            # If content area is tight (rare), reduce caption height to preserve the system.
            cap_h = max(0.95 * scale, min(cap_h, (bottom_safe_y - top_safe_y - tri_h - label_gap_above - label_gap_below - 0.30 * scale) / 2))
            baseline_min = top_safe_y + cap_h + label_gap_above + 0.10 * scale
            baseline_max = bottom_safe_y - (tri_h + label_gap_below + cap_h + 0.10 * scale)
            baseline_y = max(baseline_min, min(baseline_max, baseline_y))

        gap = 0.50 * scale
        tri_w = (avail_w - gap * (n - 1)) / max(1, n)
        tri_w = max(1.35 * scale, min(2.05 * scale, tri_w))
        total_w = n * tri_w + (n - 1) * gap
        start_x = margin_x + max(0.0, (avail_w - total_w) / 2)

        base_x = start_x - 0.10
        base_w = min(avail_w + 0.20, total_w + 0.20)
        base = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(base_x), Inches(baseline_y), Inches(base_w), Inches(0.05 * scale))
        base.fill.solid(); base.fill.fore_color.rgb = RGBColor(210, 216, 223)
        base.line.fill.background()

        for i, (year, show_year, lab, desc) in enumerate(items):
            col = tri_cols[i % len(tri_cols)]
            x = start_x + i * (tri_w + gap)
            is_up = (i % 2 == 0)
            y = baseline_y - tri_h if is_up else baseline_y

            tri = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(x), Inches(y), Inches(tri_w), Inches(tri_h))
            if not is_up:
                try:
                    tri.rotation = 180
                except Exception:
                    pass
            tri.fill.solid(); tri.fill.fore_color.rgb = RGBColor(*col)
            tri.line.fill.background()

            year_tf = s.shapes.add_textbox(Inches(x), Inches(y), Inches(tri_w), Inches(tri_h)).text_frame
            year_tf.clear()
            year_tf.word_wrap = False
            year_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                from pptx.enum.text import MSO_ANCHOR
                year_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            except Exception:
                pass
            p = year_tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            r = p.add_run()
            r.text = str(year) if show_year else ""
            r.font.size = Pt(max(18, int(round(22 * scale))))
            r.font.bold = True
            r.font.color.rgb = RGBColor(255, 255, 255)

            # Caption box is centered on the triangle midpoint (X-midpoint match).
            cap_w = min(tri_w + 0.45 * scale, tri_w + gap)
            cap_x = (x + tri_w / 2) - (cap_w / 2)
            cap_x = max(margin_x, min(SLIDE_W_IN - margin_x - cap_w, cap_x))

            # Fixed vertical spacing rules (snap):
            # - For up triangles: caption below triangle
            # - For down triangles: caption above triangle
            if is_up:
                cap_y = (y + tri_h) + label_gap_below
                cap_y = min(cap_y, bottom_safe_y - cap_h)
            else:
                cap_y = y - label_gap_above - cap_h
                cap_y = max(cap_y, top_safe_y)

            cap = s.shapes.add_textbox(Inches(cap_x), Inches(cap_y), Inches(cap_w), Inches(cap_h)).text_frame
            cap.word_wrap = True
            cap.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                from pptx.enum.text import MSO_ANCHOR
                cap.vertical_anchor = MSO_ANCHOR.TOP
            except Exception:
                pass
            p1 = cap.paragraphs[0]
            p1.clear()
            p1.alignment = PP_ALIGN.CENTER
            rr1 = p1.add_run()
            rr1.text = lab
            rr1.font.size = Pt(max(12, int(round(14 * scale))))
            rr1.font.bold = True
            rr1.font.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))

            if desc:
                p2 = cap.add_paragraph()
                p2.alignment = PP_ALIGN.CENTER
                rr2 = p2.add_run()
                rr2.text = _shorten(desc, 78)
                rr2.font.size = Pt(max(9, int(round(10 * scale))))
                rr2.font.color.rgb = RGBColor(*pal.get("muted", (71, 85, 105)))


    def render_timeline_callout_chevrons(title_txt: str, points: List[Any], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)

        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.75,
            10.8,
            0.95,
            title_txt or "Timeline",
            font_size_pt=44,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub_raw = str(subtitle_override or "").strip()
        if sub_raw:
            _add_textbox(
                s,
                1.0,
                1.65,
                10.8,
                0.6,
                sub_raw,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        raw_items: List[Any] = [x for x in (points or []) if x is not None]
        items_src = raw_items[:6]
        items: List[tuple[str, bool, str, str]] = []
        for idx, it in enumerate(items_src):
            lab: str
            desc: str
            if isinstance(it, dict):
                lab = str(it.get("label") or it.get("title") or "").strip()
                desc = str(it.get("description") or "").strip()
            else:
                head, tail = _split_head_tail(str(it or "").strip())
                lab, desc = (head or str(it or "").strip()), tail
            yr, show_yr = _timeline_label_for_item(it, idx, items_src, "callout_chevrons")
            items.append((yr, show_yr, lab, desc))
        if len(items) < 4:
            while len(items) < 4:
                items.append(items[-1])

        n = min(6, max(4, len(items)))
        items = items[:n]

        STEP_COLS = _step_colors()
        band_x, band_w = 1.0, 11.3
        band_y, band_h = 4.0, 0.72
        overlap = 0.34
        chev_w = (band_w + (n - 1) * overlap) / n

        centers: List[float] = []
        x = band_x
        for i in range(n):
            col = STEP_COLS[i % len(STEP_COLS)]
            chev = s.shapes.add_shape(MsoShape.CHEVRON, Inches(x), Inches(band_y), Inches(chev_w), Inches(band_h))
            chev.fill.solid(); chev.fill.fore_color.rgb = RGBColor(*col)
            chev.line.fill.background()
            cx = x + chev_w / 2
            centers.append(cx)
            if items[i][1] and str(items[i][0]).strip():
                _add_textbox(
                    s,
                    cx - 0.65,
                    band_y + 0.18,
                    1.30,
                    0.36,
                    str(items[i][0]),
                    font_size_pt=18,
                    bold=True,
                    color=(255, 255, 255),
                    align="center",
                    word_wrap=False,
                    auto_fit=True,
                )
            x += chev_w - overlap

        for i, (cx, (_lbl, _show_lbl, lab, desc)) in enumerate(zip(centers, items)):
            col = STEP_COLS[i % len(STEP_COLS)]
            is_top = (i % 2 == 1)
            card_w, card_h = 2.05, 1.55
            card_x = cx - card_w / 2
            card_y = (2.35 if is_top else 4.95)
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(card_x), Inches(card_y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*col)
            card.line.fill.background()
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 10; card.shadow.distance = 2
            except Exception:
                pass

            tri_w, tri_h = 0.28, 0.22
            tri_x = cx - tri_w / 2
            tri_y = (card_y + card_h - 0.02) if is_top else (card_y - tri_h + 0.02)
            tri = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(tri_x), Inches(tri_y), Inches(tri_w), Inches(tri_h))
            if is_top:
                try:
                    tri.rotation = 180
                except Exception:
                    pass
            tri.fill.solid(); tri.fill.fore_color.rgb = RGBColor(*col)
            tri.line.fill.background()

            _add_textbox(
                s,
                card_x + 0.12,
                card_y + 0.16,
                card_w - 0.24,
                0.40,
                _shorten(lab or "", 26),
                font_size_pt=14,
                bold=True,
                color=(255, 255, 255),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )
            if desc:
                _add_textbox(
                    s,
                    card_x + 0.14,
                    card_y + 0.58,
                    card_w - 0.28,
                    0.85,
                    _shorten(desc, 78),
                    font_size_pt=10,
                    color=(255, 255, 255),
                    align="center",
                    word_wrap=True,
                    auto_fit=True,
                )


    def render_timeline_strip_columns(title_txt: str, points: List[Any], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)

        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.75,
            10.8,
            0.95,
            title_txt or "Timeline",
            font_size_pt=44,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub_raw = str(subtitle_override or "").strip()
        if sub_raw:
            _add_textbox(
                s,
                1.0,
                1.65,
                10.8,
                0.6,
                sub_raw,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        raw_items: List[Any] = [x for x in (points or []) if x is not None]
        items_src = raw_items[:6]
        items: List[tuple[str, bool, str, str]] = []
        for idx, it in enumerate(items_src):
            lab: str
            desc: str
            if isinstance(it, dict):
                lab = str(it.get("label") or it.get("title") or "").strip()
                desc = str(it.get("description") or "").strip()
            else:
                head, tail = _split_head_tail(str(it or "").strip())
                lab, desc = (head or str(it or "").strip()), tail
            yr, show_yr = _timeline_label_for_item(it, idx, items_src, "strip_columns")
            items.append((yr, show_yr, lab, desc))
        if len(items) < 4:
            while len(items) < 4:
                items.append(items[-1])

        n = min(6, max(4, len(items)))
        items = items[:n]

        STEP_COLS = _step_colors()
        band_x, band_w = 1.0, 11.3
        band_y, band_h = 2.85, 0.55
        overlap = 0.28
        seg_w = (band_w + (n - 1) * overlap) / n

        x = band_x
        centers: List[float] = []
        for i in range(n):
            col = STEP_COLS[i % len(STEP_COLS)]
            seg = s.shapes.add_shape(MsoShape.CHEVRON, Inches(x), Inches(band_y), Inches(seg_w), Inches(band_h))
            seg.fill.solid(); seg.fill.fore_color.rgb = RGBColor(*col)
            seg.line.fill.background()
            cx = x + seg_w / 2
            centers.append(cx)
            if items[i][1] and str(items[i][0]).strip():
                _add_textbox(
                    s,
                    cx - 0.65,
                    band_y - 0.50,
                    1.30,
                    0.35,
                    str(items[i][0]),
                    font_size_pt=16,
                    bold=True,
                    color=pal.get("text", (15, 23, 42)),
                    align="center",
                    word_wrap=False,
                    auto_fit=True,
                )
            x += seg_w - overlap

        # Column separators + content
        col_top = band_y + band_h + 0.25
        col_h = 3.65
        for i in range(n + 1):
            xv = band_x + (band_w * i / n)
            ln = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(xv), Inches(col_top), Inches(xv), Inches(col_top + col_h))
            try:
                ln.line.color.rgb = RGBColor(226, 232, 240)
                ln.line.width = Pt(1)
            except Exception:
                pass

        for i, (cx, (_lbl, _show_lbl, lab, desc)) in enumerate(zip(centers, items)):
            col = STEP_COLS[i % len(STEP_COLS)]
            icon_d = 0.62
            icon = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(cx - icon_d / 2), Inches(col_top + 0.12), Inches(icon_d), Inches(icon_d))
            icon.fill.solid(); icon.fill.fore_color.rgb = RGBColor(255, 255, 255)
            icon.line.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))
            icon.line.width = Pt(1.5)
            _add_textbox(
                s,
                cx - 1.10,
                col_top + 0.90,
                2.20,
                0.42,
                _shorten(lab or "", 22),
                font_size_pt=14,
                bold=True,
                color=col,
                align="center",
                word_wrap=True,
                auto_fit=True,
            )
            if desc:
                _add_textbox(
                    s,
                    cx - 1.20,
                    col_top + 1.30,
                    2.40,
                    0.78,
                    _shorten(desc, 78),
                    font_size_pt=10,
                    color=pal.get("muted", (71, 85, 105)),
                    align="center",
                    word_wrap=True,
                    auto_fit=True,
                )


    def render_timeline_arc_progress(title_txt: str, points: List[Any], topic_src: str, subtitle_override: str | None = None):
        s = prs.slides.add_slide(blank); apply_bg(s)

        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(
            s,
            1.0,
            0.75,
            10.8,
            0.95,
            title_txt or "Timeline",
            font_size_pt=44,
            bold=True,
            color=(255, 255, 255) if dark_bg else TITLE_COL,
            word_wrap=True,
            auto_fit=True,
        )
        sub_raw = str(subtitle_override or "").strip()
        if sub_raw:
            _add_textbox(
                s,
                1.0,
                1.65,
                10.8,
                0.6,
                sub_raw,
                font_size_pt=18,
                color=pal.get("muted", (71, 85, 105)),
                align="center",
                word_wrap=True,
                auto_fit=True,
            )

        raw_items: List[Any] = [x for x in (points or []) if x is not None]
        items_src = raw_items[:5]
        items: List[tuple[str, bool, str, str]] = []
        for idx, it in enumerate(items_src):
            lab: str
            desc: str
            if isinstance(it, dict):
                lab = str(it.get("label") or it.get("title") or "").strip()
                desc = str(it.get("description") or "").strip()
            else:
                head, tail = _split_head_tail(str(it or "").strip())
                lab, desc = (head or str(it or "").strip()), tail
            yr, show_yr = _timeline_label_for_item(it, idx, items_src, "arc_progress")
            items.append((yr, show_yr, lab, desc))
        if len(items) < 4:
            while len(items) < 4:
                items.append(items[-1])

        n = min(5, max(4, len(items)))
        items = items[:n]
        STEP_COLS = _step_colors()

        # Left arc (polyline approximation)
        year_x = 0.10
        year_w = 0.78

        arc_outer_x = 1.25
        arc_inner_x = 0.98

        # Nodes + right content layout (auto-fit vertical space)
        col_x = 2.35
        txt_x = 3.10
        circ_d = 0.62

        top_content_y = 2.75
        bottom_margin = 0.60
        # Reserve room for bar + label block so nothing overflows.
        bottom_content_y = SLIDE_H_IN - bottom_margin
        if n > 1:
            row_h = (bottom_content_y - top_content_y) / float(n - 1)
        else:
            row_h = 1.0
        row_h = max(0.92, min(1.18, row_h))
        start_y = top_content_y

        top_y = start_y - 0.55
        bot_y = start_y + (n - 1) * row_h + 0.15
        mid_x = arc_inner_x
        arc_x = arc_outer_x
        segs = 12
        prev = (arc_x, top_y)
        for j in range(1, segs + 1):
            t = j / segs
            y = top_y + (bot_y - top_y) * t
            # simple bow to the left
            x = arc_x - (arc_x - mid_x) * math.sin(math.pi * t)
            ln = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(prev[0]), Inches(prev[1]), Inches(x), Inches(y))
            try:
                ln.line.color.rgb = RGBColor(15, 23, 42)
                ln.line.width = Pt(2)
            except Exception:
                pass
            prev = (x, y)
        for i, (year, show_year, lab, desc) in enumerate(items):
            col = STEP_COLS[i % len(STEP_COLS)]
            y = start_y + i * row_h

            if show_year and str(year).strip():
                _add_textbox(
                    s,
                    year_x,
                    y - 0.12,
                    year_w,
                    0.35,
                    str(year),
                    font_size_pt=18,
                    bold=True,
                    color=col,
                    align="center",
                    word_wrap=False,
                    auto_fit=True,
                )

            circ = s.shapes.add_shape(MsoShape.OVAL, Inches(col_x), Inches(y - circ_d / 2), Inches(circ_d), Inches(circ_d))
            circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*col)
            circ.line.fill.background()
            ico_d = 0.28
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(col_x + circ_d / 2 - ico_d / 2), Inches(y - ico_d / 2), Inches(ico_d), Inches(ico_d))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
            ico.line.fill.background()

            _add_textbox(
                s,
                txt_x,
                y - 0.28,
                8.8,
                0.32,
                _shorten(lab or "", 36),
                font_size_pt=16,
                bold=True,
                color=pal.get("text", (15, 23, 42)),
                align="left",
                word_wrap=True,
                auto_fit=True,
            )
            if desc:
                _add_textbox(
                    s,
                    txt_x,
                    y + 0.05,
                    8.8,
                    0.42,
                    _shorten(desc, 120),
                    font_size_pt=10,
                    color=pal.get("muted", (71, 85, 105)),
                    align="left",
                    word_wrap=True,
                    auto_fit=True,
                )

            # progress bar
            bar_x = txt_x
            bar_y = y + 0.46
            bar_w = 5.6
            base = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(bar_x), Inches(bar_y), Inches(bar_w), Inches(0.08))
            base.fill.solid(); base.fill.fore_color.rgb = RGBColor(226, 232, 240)
            base.line.fill.background()
            frac = (i + 1) / max(1, n)
            fill = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(bar_x), Inches(bar_y), Inches(bar_w * frac), Inches(0.08))
            fill.fill.solid(); fill.fill.fore_color.rgb = RGBColor(*col)
            fill.line.fill.background()
    def render_process_variant(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None, *, forced: str | None = None):
        forced_norm = str(forced or "").strip().lower() or None
        forced_map = {
            "loop": "loop_cards",
            "loop_cards": "loop_cards",
            "loop-cards": "loop_cards",
            "loopcards": "loop_cards",
            "alpha": "alpha_badges",
            "alpha_badges": "alpha_badges",
            "alpha-badges": "alpha_badges",
            "badges": "alpha_badges",
            "letters": "alpha_badges",
            "ribbon": "ribbon_nodes",
            "ribbon_nodes": "ribbon_nodes",
            "ribbon-nodes": "ribbon_nodes",
            "ribbonnodes": "ribbon_nodes",
            "ribbon_chevrons": "ribbon_nodes",
            "ribbon-chevrons": "ribbon_nodes",
        }
        forced_v = forced_map.get(forced_norm or "")
        eligible = ["loop_cards", "alpha_badges", "ribbon_nodes"]
        chosen = choose_variant("process_cards", eligible, forced=forced_v)
        _debug_variant(family="process_cards", chosen=chosen, eligible=eligible, forced=forced_v)
        if chosen == "loop_cards":
            render_process_loop_cards(title_txt, points, topic_src, subtitle_override)
        elif chosen == "alpha_badges":
            render_process_alpha_badges(title_txt, points, topic_src, subtitle_override)
        elif chosen == "ribbon_nodes":
            render_process_ribbon_nodes(title_txt, points, topic_src, subtitle_override)
        else:
            render_process_loop_cards(title_txt, points, topic_src, subtitle_override)

    def render_timeline_vertical(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        """Colorful vertical timeline with soft spine and numbered bubbles."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        # Title
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=42, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(s, 1.0, 1.8, 10.8, 0.5, sub, font_size_pt=18, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)
        # Items and layout metrics with fit-to-available-height
        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = ["Step 1", "Step 2", "Step 3", "Step 4"]
        n = max(4, len(items))
        x = 1.8
        top = 2.15
        bottom = SLIDE_H_IN - 0.8
        avail_h = max(1.0, bottom - top)
        row_h_max, row_h_min = 1.20, 0.95
        row_h = min(row_h_max, max(row_h_min, (avail_h - 0.4) / n))
        y_start = top
        height = row_h * n + 0.4
        # soft spine with shadow
        # background panel behind timeline
        panel = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(0.7), Inches(y_start - 0.35), Inches(SLIDE_W_IN - 1.4), Inches(height + 0.55))
        panel.fill.solid(); panel.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        panel.line.color.rgb = RGBColor(229, 231, 235)
        try:
            panel.shadow.inherit = False; panel.shadow.visible = True; panel.shadow.blur = 14; panel.shadow.distance = 3
        except Exception:
            pass
        spine = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(x + 0.35), Inches(y_start - 0.10), Inches(0.10), Inches(height - 0.10))
        spine.fill.solid(); spine.fill.fore_color.rgb = RGBColor(255, 228, 230)  # rose-100
        spine.line.fill.background()
        try:
            spine.shadow.inherit = False; spine.shadow.visible = True; spine.shadow.blur = 6; spine.shadow.distance = 2
        except Exception:
            pass

        STEP_COLS = [
            (244, 114, 82),   # coral
            (99, 102, 241),   # indigo
            (45, 212, 191),   # teal
            (251, 191, 36),   # amber
            (2, 132, 199),    # sky
            (16, 185, 129),   # emerald
        ]

        items = [str(x).strip() for x in (points or []) if str(x).strip()][:6]
        if not items:
            items = ["Step 1", "Step 2", "Step 3", "Step 4"]

        for i, lab in enumerate(items, start=1):
            cy = y_start + (i - 1) * row_h
            # numbered bubble
            bub = s.shapes.add_shape(MsoShape.OVAL, Inches(x + 0.15), Inches(cy - 0.22), Inches(0.44), Inches(0.44))
            bub.fill.solid(); bub.fill.fore_color.rgb = RGBColor(255, 255, 255)
            bub.line.color.rgb = RGBColor(*STEP_COLS[(i - 1) % len(STEP_COLS)])
            try:
                bub.shadow.inherit = False; bub.shadow.visible = True; bub.shadow.blur = 4; bub.shadow.distance = 1
            except Exception:
                pass
            tf = bub.text_frame; tf.clear(); p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
            r = p.add_run(); r.text = str(i); r.font.size = Pt(16); r.font.bold = True; r.font.color.rgb = RGBColor(*STEP_COLS[(i - 1) % len(STEP_COLS)])
            # label
            _add_textbox(s, x + 0.90, cy - 0.10, 10.0 - x, 0.62, str(lab), font_size_pt=20, bold=True, color=pal["text"], word_wrap=True, auto_fit=True)
        # subtle separators along spine between bubbles
        sep_d = 0.10
        for i in range(1, len(items)):
            midy = y_start + (i - 0.5) * row_h
            dot = s.shapes.add_shape(MsoShape.OVAL, Inches(x + 0.40 - sep_d/2), Inches(midy - sep_d/2), Inches(sep_d), Inches(sep_d))
            dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(255,255,255); dot.line.color.rgb = RGBColor(209,213,219)

    def render_timeline_roadmap(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        """Roadmap path with colored location pins along a meandering soft path."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Roadmap", font_size_pt=42, bold=True, color=pal["primary"], word_wrap=True, auto_fit=True)
        sub = str(subtitle_override or "").strip()
        if sub:
            _add_textbox(s, 1.0, 1.8, 10.8, 0.5, sub, font_size_pt=18, color=pal.get("muted", (71,85,105)), align="center", word_wrap=True, auto_fit=True)
        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = ["Step 1", "Step 2", "Step 3", "Step 4"]
        n = min(6, max(4, len(items)))
        STEP_COLS = _step_colors()

        # Positions along meandering path (alternate up/down)
        margin_x = 1.2
        total_w = SLIDE_W_IN - 2*margin_x
        step_w = total_w / (n + 1)
        y_mid = 3.5
        y_amp = 0.8
        pts_xy = []
        for i in range(n):
            x = margin_x + (i + 1) * step_w
            y = y_mid + (y_amp if i % 2 == 1 else -y_amp)
            pts_xy.append((x, y))

        # Background container behind path area
        min_x = min(px for px, _ in pts_xy) - 0.8
        max_x = max(px for px, _ in pts_xy) + 0.8
        min_y = min(py for _, py in pts_xy) - 0.9
        max_y = max(py for _, py in pts_xy) + 0.9
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(min_x), Inches(min_y), Inches(max_x - min_x), Inches(max_y - min_y))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240,245,255))))
        cont.line.color.rgb = RGBColor(229,231,235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        # Draw soft path with elbow connectors between consecutive points
        for i in range(n - 1):
            x1, y1 = pts_xy[i]
            x2, y2 = pts_xy[i + 1]
            # first horizontal then vertical to create a gentle bend
            mid_x = (x1 + x2) / 2
            seg1 = s.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(mid_x), Inches(y1))
            seg2 = s.shapes.add_connector(1, Inches(mid_x), Inches(y1), Inches(mid_x), Inches(y2))
            seg3 = s.shapes.add_connector(1, Inches(mid_x), Inches(y2), Inches(x2), Inches(y2))
            for seg in (seg1, seg2, seg3):
                seg.line.color.rgb = RGBColor(209, 213, 219)  # gray-300
                seg.line.width = Pt(3)
                try:
                    seg.shadow.inherit = False; seg.shadow.visible = True; seg.shadow.blur = 4; seg.shadow.distance = 1
                except Exception:
                    pass
            # inline separator dot at elbow midpoint
            dot_d = 0.10
            dot = s.shapes.add_shape(MsoShape.OVAL, Inches(mid_x - dot_d/2), Inches((y1 + y2)/2 - dot_d/2), Inches(dot_d), Inches(dot_d))
            dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(255,255,255); dot.line.color.rgb = RGBColor(209,213,219)

        # Location pins and labels
        for i, lab in enumerate(items[:n], start=1):
            px, py = pts_xy[i - 1]
            col = STEP_COLS[(i - 1) % len(STEP_COLS)]
            # pin head
            head = s.shapes.add_shape(MsoShape.OVAL, Inches(px - 0.22), Inches(py - 0.30), Inches(0.44), Inches(0.44))
            head.fill.solid(); head.fill.fore_color.rgb = RGBColor(255, 255, 255); head.line.color.rgb = RGBColor(*col)
            # pin tail (triangle)
            tri = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(px - 0.12), Inches(py + 0.05), Inches(0.24), Inches(0.28))
            tri.rotation = 180
            tri.fill.solid(); tri.fill.fore_color.rgb = RGBColor(*col); tri.line.fill.background()
            try:
                head.shadow.inherit = False; head.shadow.visible = True; head.shadow.blur = 3; head.shadow.distance = 1
                tri.shadow.inherit = False; tri.shadow.visible = True; tri.shadow.blur = 3; tri.shadow.distance = 1
            except Exception:
                pass
            # step label near pin
            _add_textbox(s, px + 0.35, py - 0.08, 4.5, 0.7, lab, font_size_pt=20, bold=True, color=pal["text"], word_wrap=True, auto_fit=True)

    def render_double(sl):
        c = _content(sl)
        lp_all = [str(x) for x in (c.get("left_points") or [])]
        rp_all = [str(x) for x in (c.get("right_points") or [])]

        # Prepare pages of up to 3 bullets per column, but enforce a
        # single rendered slide per topic by only using the first page.
        def chunks(lst: List[str], n: int) -> List[List[str]]:
            return [lst[i:i+n] for i in range(0, len(lst), n)]

        left_pages = chunks(lp_all, 3) or [[]]
        right_pages = chunks(rp_all, 3) or [[]]
        pages = 1  # force a single slide; drop any additional pages

        # Theme-aligned color schemes for square bullets. We keep the
        # layout and icon geometry identical, but derive colors from the
        # current theme palette so the slide matches the deck.
        if theme_key == "PULSE":
            accent = _pulse_bullet_accent()
            bullet_cols = [accent, accent, accent]
            schemes = [bullet_cols]
            right_schemes = [bullet_cols]
        else:
            bullet_cols = _bullet_colors()
            schemes = [bullet_cols]
            right_schemes = [[bullet_cols[1], bullet_cols[0], bullet_cols[2]]]

        global DOUBLE_SCHEME_INDEX
        # New: rotate bullet layout styles across double-column slides
        global DOUBLE_BULLET_STYLE_INDEX
        if 'DOUBLE_BULLET_STYLE_INDEX' not in globals():
            DOUBLE_BULLET_STYLE_INDEX = 0  # 0..3

        scheme_idx = DOUBLE_SCHEME_INDEX % len(schemes)
        left_colors = schemes[scheme_idx]
        right_colors = right_schemes[scheme_idx]

        for page in range(pages):
            s = prs.slides.add_slide(blank); apply_bg(s)
            title = (sl.get("title") or "Discussion").strip() or "Discussion"
            _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

            # Layout metrics matching reference
            gutter = 0.9
            margin = 1.2
            col_w = (SLIDE_W_IN - 2 * margin - gutter) / 2.0
            left_x = margin
            right_x = margin + col_w + gutter
            top_y = 2.0
            row_h = 1.25
            gap_y = 0.25

            # Center divider with soft shadow
            divider_w = 0.10
            divider_x = left_x + col_w + (gutter - divider_w) / 2.0
            div = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(divider_x), Inches(top_y - 0.10), Inches(divider_w), Inches(3 * row_h + 2 * gap_y + 0.20))
            div.fill.solid(); div.fill.fore_color.rgb = RGBColor(229, 231, 235); div.line.fill.background()

            lp = left_pages[page] if page < len(left_pages) else []
            rp = right_pages[page] if page < len(right_pages) else []

            style_id = DOUBLE_BULLET_STYLE_INDEX % 4

            def _lighter(rgb: Tuple[int,int,int], pct: float = 0.35) -> Tuple[int,int,int]:
                r,g,b = rgb
                return (min(255, int(r + (255-r)*pct)), min(255, int(g + (255-g)*pct)), min(255, int(b + (255-b)*pct)))

            def render_col(items: List[str], base_x: float, colors: List[Tuple[int, int, int]], start_idx: int) -> int:
                y = top_y
                subset = items[:3]
                for i, lab in enumerate(subset):
                    idx = start_idx + i
                    col = colors[i % len(colors)]
                    # Draw bullet shape based on style
                    if style_id == 0:
                        # 1) Colored round bullets (alternating colors)
                        dot = s.shapes.add_shape(MsoShape.OVAL, Inches(base_x), Inches(y + 0.20), Inches(0.36), Inches(0.36))
                        dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*col)
                        dot.line.fill.background()
                    elif style_id == 1:
                        # 2) Rounded color bar bullets
                        bar = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(base_x), Inches(y + 0.22), Inches(0.48), Inches(0.22))
                        bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*col)
                        bar.line.fill.background();
                        try:
                            bar.radius = 0.12
                        except Exception:
                            pass
                    elif style_id == 2:
                        # 3) "Gradient" bullets: two concentric ovals (lighter inner) with soft shadow
                        outer = s.shapes.add_shape(MsoShape.OVAL, Inches(base_x), Inches(y + 0.20), Inches(0.36), Inches(0.36))
                        outer.fill.solid(); outer.fill.fore_color.rgb = RGBColor(*col)
                        outer.line.fill.background()
                        try:
                            outer.shadow.inherit = False; outer.shadow.visible = True; outer.shadow.blur = 8; outer.shadow.distance = 1
                        except Exception:
                            pass
                        inner = s.shapes.add_shape(MsoShape.OVAL, Inches(base_x + 0.06), Inches(y + 0.26), Inches(0.24), Inches(0.24))
                        inner.fill.solid(); inner.fill.fore_color.rgb = RGBColor(*_lighter(col, 0.5))
                        inner.line.fill.background()
                    else:
                        # 4) Numbered colored circles
                        circ = s.shapes.add_shape(MsoShape.OVAL, Inches(base_x), Inches(y + 0.16), Inches(0.40), Inches(0.40))
                        circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*col)
                        circ.line.fill.background()
                        tfc = circ.text_frame; tfc.text = str(idx + 1)
                        for pnum in tfc.paragraphs:
                            for rr in pnum.runs:
                                rr.font.bold = True; rr.font.size = Pt(14); rr.font.color.rgb = RGBColor(*_contrast_text(col))
                            pnum.alignment = PP_ALIGN.CENTER

                    # text with bold head + normal tail (consistent spacing)
                    tf = s.shapes.add_textbox(Inches(base_x + 0.60), Inches(y), Inches(col_w - 0.70), Inches(row_h - 0.05)).text_frame
                    tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                    try:
                        from pptx.enum.text import MSO_ANCHOR
                        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
                    except Exception:
                        pass
                    p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
                    head, tail = _split_head_tail(lab)
                    r1 = p.add_run(); r1.text = (head or lab); r1.font.size = Pt(20); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal["text"])
                    if tail:
                        p2 = tf.add_paragraph(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (100,116,139))))
                    y += row_h + gap_y
                return start_idx + len(subset)

            next_idx = render_col(lp, left_x, left_colors, 0)
            render_col(rp, right_x, right_colors, next_idx)

        DOUBLE_SCHEME_INDEX += 1
        DOUBLE_BULLET_STYLE_INDEX += 1

    def render_table(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        c = _content(sl)
        table_obj = c.get("table") or {}
        left = table_obj.get("left") or {}
        right = table_obj.get("right") or {}
        headers = [left.get("heading") or "Option A", right.get("heading") or "Option B"]
        left_pts = [str(x) for x in (left.get("points") or [])]
        right_pts = [str(x) for x in (right.get("points") or [])]
        variant_raw = str(c.get("comparison_variant") or c.get("table_variant") or c.get("variant") or "").strip().lower()
        variant_map = {
            "table": "table",
            "side_by_side_table": "table",
            "side-by-side-table": "table",
            "cards": "cards",
            "pros_cons_cards": "cards",
            "pros-cons": "cards",
            "pros_cons": "cards",
        }
        forced = variant_map.get(variant_raw)

        eligible = ["table", "cards"]
        chosen = choose_variant("comparison", eligible, forced=forced)
        _debug_variant(family="comparison", chosen=chosen, eligible=eligible, forced=forced)

        def _render_comparison_cards(slide, headers_in: List[str], lp: List[str], rp: List[str]) -> None:
            dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
            head_rgb = (255, 255, 255) if dark_bg else pal.get("text", (24, 24, 27))
            muted_rgb = pal.get("muted", pal.get("text", (71, 85, 105)))

            margin_x = 0.9
            gap_x = 0.55
            cont_y = 2.15
            cont_h = 5.05
            col_w = (SLIDE_W_IN - 2 * margin_x - gap_x) / 2.0
            left_x = margin_x
            right_x = margin_x + col_w + gap_x

            for x0, hdr, pts, accent in [
                (left_x, headers_in[0] if headers_in else "Option A", lp, pal.get("primary", (59, 130, 246))),
                (right_x, headers_in[1] if len(headers_in) > 1 else "Option B", rp, pal.get("secondary", pal.get("primary", (59, 130, 246)))),
            ]:
                card = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x0), Inches(cont_y), Inches(col_w), Inches(cont_h))
                card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
                card.line.color.rgb = RGBColor(*accent)
                try:
                    card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 10; card.shadow.distance = 2
                except Exception:
                    pass

                band = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x0), Inches(cont_y), Inches(col_w), Inches(0.62))
                band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*accent)
                band.line.fill.background()

                _add_textbox(slide, x0 + 0.25, cont_y + 0.12, col_w - 0.5, 0.45, str(hdr), font_size_pt=18, bold=True, color=_contrast_text(accent), word_wrap=True, auto_fit=True)

                pts2 = [str(x).strip() for x in (pts or []) if str(x).strip()]
                if not pts2:
                    pts2 = ["", "", ""]
                pts2 = pts2[:6]
                y_txt = cont_y + 0.82
                h_txt = cont_h - 0.96
                tb = slide.shapes.add_textbox(Inches(x0 + 0.28), Inches(y_txt), Inches(col_w - 0.56), Inches(h_txt)).text_frame
                tb.word_wrap = True
                tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                tb.clear()
                for i, ptxt in enumerate(pts2):
                    p = tb.paragraphs[0] if i == 0 else tb.add_paragraph()
                    p.text = f"• {ptxt}" if ptxt else ""
                    try:
                        p.space_after = Pt(10)
                    except Exception:
                        pass
                    p.font.size = Pt(16)
                    p.font.color.rgb = RGBColor(*head_rgb)
                    if not ptxt:
                        p.font.color.rgb = RGBColor(*muted_rgb)

        # Title uses the provided slide title (topic-aligned)
        comp_title = sl.get("title") or "Comparison"
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, comp_title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        if chosen == "cards":
            _render_comparison_cards(s, headers, left_pts, right_pts)
            return
        body_rows = max(3, min(max(len(left_pts), len(right_pts)), 6))
        table = _add_table(s, 1.6665, 2.2, 10.0, 3.0, rows=body_rows + 1, cols=2)
        # header row
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        header_rgb = (59, 130, 246) if dark_bg else pal["primary"]
        for cidx in range(2):
            cell = table.cell(0, cidx)
            cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor(*header_rgb)
            _set_cell_text(cell, headers[cidx] or ("Option A" if cidx == 0 else "Option B"), size=20, bold=True, align_center=True, color=(255, 255, 255))
        # Populate body rows with text and zebra fills
        # Body text should be dark against the light table fills across themes
        body_rgb = (31, 41, 55)
        for r in range(body_rows):
            li = left_pts[r] if r < len(left_pts) else ""
            ri = right_pts[r] if r < len(right_pts) else ""
            _set_cell_text(table.cell(r + 1, 0), str(li), size=18, color=body_rgb)
            _set_cell_text(table.cell(r + 1, 1), str(ri), size=18, color=body_rgb)
            # subtle zebra striping
            try:
                base_fill = (248,250,252)
                alt_fill = (226,232,240)
                for cidx in range(2):
                    if r % 2 == 1:
                        cell = table.cell(r + 1, cidx)
                        cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor(*alt_fill)
            except Exception:
                pass
        # rows: padding, centering, wrapping, and thin inner borders (header+body)
        try:
            from pptx.util import Pt as _Pt
            from pptx.enum.text import MSO_ANCHOR
            g1 = RGBColor(209, 213, 219)
            g2 = RGBColor(229, 231, 235)
            for rr in range(body_rows + 1):
                for cc in range(2):
                    cell = table.cell(rr, cc)
                    # padding
                    cell.margin_left = _Pt(6)
                    cell.margin_right = _Pt(6)
                    cell.margin_top = _Pt(4)
                    cell.margin_bottom = _Pt(4)
                    # vertical center + wrap
                    try:
                        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                    except Exception:
                        pass
                    try:
                        cell.text_frame.word_wrap = True
                    except Exception:
                        pass
                    # borders
                    try:
                        cell.border_top.color.rgb = g1; cell.border_top.width = _Pt(0.5)
                        cell.border_bottom.color.rgb = g1; cell.border_bottom.width = _Pt(0.5)
                        if cc == 0:
                            cell.border_left.color.rgb = g2; cell.border_left.width = _Pt(0.5)
                        cell.border_right.color.rgb = g2; cell.border_right.width = _Pt(0.5)
                    except Exception:
                        pass
        except Exception:
            pass

        # Simple centered layout metrics for the comparison table; no pagination here
        # (the above body_rows limits rows to a reasonable amount per slide)

    # --- Infographic layouts (topic-level, deterministic) ---

    def _infographic_items(sl) -> tuple[str, List[str], Dict[str, Any]]:
        c = _content(sl)
        title = (sl.get("title") or "Overview").strip() or "Overview"
        bullets = [str(x).strip() for x in (c.get("bullets") or []) if str(x).strip()]
        return title, bullets, c

    def _infer_infographic_intent(title: str, content: Dict[str, Any]) -> str:
        # Explicit hint wins if present; normalise known tags into coarse intents
        raw = str(content.get("infographic_intent") or content.get("intent") or "").strip().upper()
        if raw:
            if raw in {"GANTT_CHART", "GANTT"}:
                return "GANTT"
            if raw in {"IMPACT_MAP_COMPLEX", "IMPACT_MAP_SIMPLE", "IMPACT_MAP", "IMPACT"}:
                return "IMPACT"
            if raw in {"PROCESS_FLOW_CHEVRON", "PROCESS_CHEVRON", "PROCESS_FLOW", "PROCESS"}:
                return "PROCESS"
            if raw in {"RISK_MATRIX_ADVANCED", "RISK_MATRIX", "RISK"}:
                return "RISK"
            if raw in {"CYCLIC_OPTIMIZATION_LOOP", "OPTIMIZATION_LOOP", "CYCLE", "LOOP", "OPTIMIZATION"}:
                return "OPTIMIZATION"
            if raw in {"LAYERED_BENEFITS_STACK", "BENEFITS_STACK", "BENEFITS"}:
                return "BENEFITS"
            # Unknown explicit tag: treat as GENERAL but remain deterministic
            return "GENERAL"
        t = (title or "").lower()
        txt = t + " " + " ".join(str(x).lower() for x in (content.get("bullets") or []))
        risk_like = (
            "risk matrix" in txt
            or "risk assessment matrix" in txt
            or ("likelihood" in txt and "impact" in txt)
            or ("probability" in txt and "impact" in txt)
        )
        if risk_like:
            return "RISK"
        if any(k in txt for k in ["impact", "business impact", "effect on"]):
            return "IMPACT"
        if any(k in txt for k in ["roadmap", "implementation", "phases", "steps", "process"]):
            return "PROCESS"
        if any(k in txt for k in ["optimiz", "continuous", "iteration", "improvement"]):
            return "OPTIMIZATION"
        if any(k in txt for k in ["benefit", "value", "advantages", "gains"]):
            return "BENEFITS"
        return "GENERAL"

    def _fit_gantt_task_label(txt: str, *, box_w_in: float, base_font_pt: int = 14, min_font_pt: int = 10, max_lines: int = 3) -> tuple[str, int]:
        t = (txt or "").strip()
        if not t:
            return "", base_font_pt

        def _wrap(s: str, font_pt: int) -> List[str]:
            chars_per_in = 9.5 * (14.0 / max(8.0, float(font_pt)))
            width = max(10, int(box_w_in * chars_per_in))
            import textwrap

            return textwrap.wrap(s, width=width, break_long_words=False, break_on_hyphens=False) or [s]

        size = base_font_pt
        lines = _wrap(t, size)
        if len(lines) <= 2:
            return "\n".join(lines[:2]), size

        while len(lines) > max_lines and size > min_font_pt:
            size -= 1
            lines = _wrap(t, size)

        if len(lines) <= max_lines:
            return "\n".join(lines[:max_lines]), size

        lines = lines[:max_lines]
        last = lines[-1]
        hard_cap = max(8, int(len(last) * 0.85))
        cut = last[:hard_cap].rstrip()
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        lines[-1] = (cut.rstrip(".") + "...") if cut else "..."
        return "\n".join(lines), min_font_pt

    def _render_gantt_chart(sl) -> None:
        title, bullets, content = _infographic_items(sl)
        tasks = [str(x).strip() for x in (bullets or []) if str(x).strip()]
        if len(tasks) < 4:
            tasks = (_synthesize_topic_bullets(title) or [])[:6]
        tasks = tasks[:6]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title or "Project Timeline", font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        axis = content.get("axis") or content.get("x_labels") or []
        axis = [str(x).strip() for x in (axis or []) if str(x).strip()]
        if len(axis) < 3:
            axis = timeline_date_range_labels(today=date.today(), total_months=6, phases=3)
        axis = axis[:6]

        chart_x, chart_y = 1.0, 2.1
        chart_w, chart_h = 11.3, 4.6
        label_w = 2.6
        grid_x = chart_x + label_w
        grid_w = chart_w - label_w
        cols = max(3, len(axis))
        axis = (axis + [""] * cols)[:cols]
        rows = max(4, min(6, len(tasks)))
        header_h = 0.6
        row_h = (chart_h - header_h) / rows
        col_w = grid_w / cols

        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(chart_x), Inches(chart_y), Inches(chart_w), Inches(chart_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 12; cont.shadow.distance = 3
        except Exception:
            pass

        head = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(chart_x), Inches(chart_y), Inches(chart_w), Inches(header_h))
        head.fill.solid(); head.fill.fore_color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
        head.line.fill.background()

        for c_idx, lab in enumerate(axis):
            _add_textbox(
                s,
                grid_x + c_idx * col_w,
                chart_y + 0.10,
                col_w,
                header_h - 0.15,
                str(lab),
                font_size_pt=12 if len(str(lab)) > 18 else 14,
                bold=True,
                align="center",
                color=(255, 255, 255),
                word_wrap=True,
                auto_fit=True,
            )

        _draw_table_grid_rect(s, grid_x, chart_y + header_h, grid_w, chart_h - header_h, rows, cols, rgb=(209, 213, 219), weight_pt=0.75)
        div = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(grid_x), Inches(chart_y), Inches(grid_x), Inches(chart_y + chart_h))
        div.line.color.rgb = RGBColor(255, 255, 255)
        div.line.width = Pt(1)

        step_cols = _step_colors()
        task_box_w = label_w - 0.35
        for r_idx, task in enumerate(tasks[:rows]):
            y0 = chart_y + header_h + r_idx * row_h
            label_txt, fs = _fit_gantt_task_label(str(task), box_w_in=task_box_w, base_font_pt=14, min_font_pt=10, max_lines=3)
            _add_textbox(
                s,
                chart_x + 0.20,
                y0 + 0.08,
                task_box_w,
                row_h - 0.12,
                label_txt,
                font_size_pt=fs,
                bold=True,
                align="left",
                color=pal.get("text", (24, 24, 27)),
                word_wrap=True,
                auto_fit=False,
            )

            start = r_idx % max(1, cols - 1)
            span = 2 if (r_idx % 3 != 0) else 1
            end = min(cols - 1, start + span)
            bar_x = grid_x + start * col_w + 0.08
            bar_w = max(0.25, (end - start + 1) * col_w - 0.16)
            bar = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(bar_x), Inches(y0 + 0.18), Inches(bar_w), Inches(row_h - 0.36))
            col = step_cols[r_idx % len(step_cols)]
            bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*col)
            bar.line.fill.background()
            try:
                bar.shadow.inherit = False; bar.shadow.visible = True; bar.shadow.blur = 6; bar.shadow.distance = 2
            except Exception:
                pass

    def _wrap_text_no_midword(text: str, *, max_chars_per_line: int, max_lines: int) -> List[str]:
        t = str(text or "").strip()
        if not t:
            return []
        words = [w for w in t.split() if w]
        if not words:
            return []

        lines: List[str] = []
        cur: List[str] = []
        for w in words:
            candidate = (" ".join(cur + [w])).strip()
            if cur and len(candidate) > max_chars_per_line:
                lines.append(" ".join(cur).strip())
                cur = [w]
                continue
            cur.append(w)
        if cur:
            lines.append(" ".join(cur).strip())

        if max_lines > 0 and len(lines) > max_lines:
            # Keep full words; truncate extra lines into the last allowed line.
            keep = lines[: max_lines - 1]
            rest_words = " ".join(lines[max_lines - 1 :]).split()
            last: List[str] = []
            for w in rest_words:
                cand = (" ".join(last + [w])).strip()
                if last and len(cand) > max_chars_per_line:
                    break
                last.append(w)
            keep.append(" ".join(last).strip())
            lines = keep
        return [ln for ln in lines if ln]

    def _fit_text_no_midword(
        text: str,
        *,
        box_w_in: float,
        box_h_in: float,
        base_font_pt: int,
        min_font_pt: int,
        max_lines: int,
        width_factor: float = 1.0,
        height_factor: float = 1.0,
    ) -> tuple[str, int]:
        t = str(text or "").strip()
        if not t:
            return "", base_font_pt

        # Heuristic: average character width ~0.55em.
        def _max_chars(font_pt: int) -> int:
            eff_w = max(0.6, float(box_w_in) * float(width_factor))
            return max(6, int((eff_w * 144.0) / max(8.0, float(font_pt))))

        def _max_lines(font_pt: int) -> int:
            eff_h = max(0.4, float(box_h_in) * float(height_factor))
            line_h_in = (float(font_pt) / 72.0) * 1.15
            return max(1, int(eff_h / max(0.18, line_h_in)))

        fs = int(base_font_pt)
        while fs >= int(min_font_pt):
            maxc = _max_chars(fs)
            lines = _wrap_text_no_midword(t, max_chars_per_line=maxc, max_lines=max_lines)
            if not lines:
                return "", fs
            # Single long word: keep it intact and rely on shrinking.
            if any(len(w) > maxc for w in t.split()):
                pass

            if len(lines) <= max(1, max_lines) and len(lines) <= _max_lines(fs):
                return "\n".join(lines), fs
            fs -= 1

        lines = _wrap_text_no_midword(t, max_chars_per_line=_max_chars(min_font_pt), max_lines=max_lines)
        return "\n".join(lines[:max_lines] if max_lines else lines), int(min_font_pt)

    def _render_impact_map(simple: bool, sl) -> None:
        title, bullets, content = _infographic_items(sl)
        if not bullets:
            bullets = _synthesize_topic_bullets(title)[:5]
        n = max(3, min(5, len(bullets)))
        items = _sanitize_infographic_items(title, bullets[:n])
        # The classic impact map layout has 5 positions. When the model produces
        # exactly 4 items, the missing bottom node makes the diagram look
        # incomplete. Add a topic-derived 5th item deterministically.
        try:
            if isinstance(items, list) and len(items) == 4:
                extra = (_synthesize_topic_bullets(title) or [])[:2]
                for ex in extra:
                    ex2 = str(ex or "").strip()
                    if ex2 and ex2 not in items:
                        items.append(ex2)
                        break
        except Exception:
            pass

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        # Soft background band behind the impact map area for visual structure
        node_w, node_h = 3.1, 1.30
        bot_y = 5.15
        band_x, band_y, band_w = 0.9, 2.0, SLIDE_W_IN - 1.8
        band_h = 3.8
        if n >= 5:
            band_h = max(band_h, (bot_y + node_h + 0.35) - band_y)
        band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(band_y), Inches(band_w), Inches(band_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
        try:
            band.line.color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
            band.line.width = Pt(0.75)
        except Exception:
            band.line.fill.background()

        # Central node (hexagon-style impact core)
        hex_w, hex_h = 3.4, 2.0
        hex_x = (SLIDE_W_IN - hex_w) / 2.0
        hex_y = 2.6
        core = s.shapes.add_shape(MsoShape.HEXAGON, Inches(hex_x), Inches(hex_y), Inches(hex_w), Inches(hex_h))
        core_rgb = pal.get("primary", (59, 130, 246))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*core_rgb)
        core.line.color.rgb = RGBColor(*_shade(core_rgb, -20))
        try:
            core.shadow.inherit = False; core.shadow.visible = True; core.shadow.blur = 14; core.shadow.distance = 3
        except Exception:
            pass
        center_label = (content.get("center_label") or sl.get("center_label") or sl.get("title") or "Impact").strip()
        tfc = core.text_frame; tfc.clear()
        tfc.word_wrap = False
        tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        wrapped, fs = _fit_text_no_midword(center_label or "Impact", box_w_in=hex_w * 0.70, box_h_in=hex_h * 0.70, base_font_pt=20, min_font_pt=14, max_lines=3, width_factor=1.0)
        rc = pc.add_run(); rc.text = wrapped
        rc.font.size = Pt(fs); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        # Positions for surrounding nodes: tuned for up to 5, fully inside the band
        left_x = 1.1
        right_x = SLIDE_W_IN - 1.1 - node_w
        top_y = 2.4
        mid_y = 3.8
        positions = [
            (left_x, mid_y),
            (right_x, mid_y),
            (left_x, top_y),
            (right_x, top_y),
            ((SLIDE_W_IN - node_w) / 2.0, bot_y),
        ]

        # Draw each surrounding node with icon + title + short text
        nodes = []
        for idx, raw in enumerate(items):
            if idx >= len(positions):
                break
            nx, ny = positions[idx]
            head, tail = _split_head_tail(raw)

            # Fit the headline into the card without chopping mid-thought.
            # Prefer wrapping + gentle font shrinking over truncation.
            head_txt = (head or raw or "").strip()
            body_txt = (tail or "").strip()
            if body_txt and len(body_txt.split()) > 14:
                body_txt = " ".join(body_txt.split()[:14]).rstrip(" ,.;:-")
            # If the headline is already dense, drop the body to keep the layout airy.
            if head_txt and len(head_txt.split()) >= 6:
                body_txt = ""

            node = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(nx), Inches(ny), Inches(node_w), Inches(node_h))
            # Keep cards light against the band using the deck background color
            bg_rgb = pal.get("bg", (255, 255, 255))
            node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*bg_rgb)
            node.line.color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
            try:
                node.shadow.inherit = False; node.shadow.visible = True; node.shadow.blur = 6; node.shadow.distance = 2
            except Exception:
                pass
            # icon circle
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(nx + 0.18), Inches(ny + 0.26), Inches(0.30), Inches(0.30))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
            ico.line.fill.background()
            nodes.append(node)
            # text: keep comfortably inside the rounded rectangle with left/right padding
            inner_w = max(0.6, node_w - 0.95)
            inner_h = max(0.4, node_h - 0.40)
            tb = s.shapes.add_textbox(Inches(nx + 0.60), Inches(ny + 0.18), Inches(inner_w), Inches(inner_h)).text_frame
            tb.word_wrap = False; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            tb.margin_top = Pt(0); tb.margin_bottom = Pt(0)
            p = tb.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            p.line_spacing = 1.0
            wrapped_h, fs_h = _fit_text_no_midword(head_txt, box_w_in=inner_w, box_h_in=inner_h * (0.62 if (not simple and body_txt) else 1.0), base_font_pt=15, min_font_pt=11, max_lines=2)
            r1 = p.add_run(); r1.text = wrapped_h; r1.font.size = Pt(fs_h); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if not simple and body_txt:
                p2 = tb.add_paragraph(); p2.clear(); p2.alignment = PP_ALIGN.LEFT
                p2.line_spacing = 1.05
                wrapped_b, fs_b = _fit_text_no_midword(body_txt, box_w_in=inner_w, box_h_in=inner_h * 0.38, base_font_pt=10, min_font_pt=9, max_lines=2)
                r2 = p2.add_run(); r2.text = wrapped_b; r2.font.size = Pt(fs_b); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

        # Optional connectors from center to nodes in complex mode.
        # Draw straight straight-line connectors from the hexagon edges to the
        # card edges so that lines attach cleanly without cutting through shapes.
        if nodes:
            for idx, node in enumerate(nodes):
                try:
                    # Default: connect midpoints of opposing edges.
                    end_y = node.top + node.height // 2
                    start_y = core.top + core.height // 2
                    end_x = node.left + node.width // 2
                    start_x = core.left + core.width // 2

                    # Left column cards (indices 0 and 2 in positions list):
                    # from middle of left hex edge to middle of right card edge.
                    if idx in (0, 2):
                        end_x = node.left + node.width
                        start_x = core.left
                    # Right column cards (indices 1 and 3):
                    # from middle of right hex edge to middle of left card edge.
                    elif idx in (1, 3):
                        end_x = node.left
                        start_x = core.left + core.width
                    # Bottom card (index 4): vertical line from bottom center of hex
                    # to top center of card.
                    elif idx == 4:
                        start_x = core.left + core.width // 2
                        end_x = start_x
                        start_y = core.top + core.height
                        end_y = node.top

                    conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, start_x, start_y, end_x, end_y)
                    base_rgb = pal.get("primary", (59, 130, 246))
                    line_rgb = _shade(base_rgb, -25)
                    conn.line.color.rgb = RGBColor(*line_rgb)
                    conn.line.width = Pt(1.25 if simple else 1.75)
                except Exception:
                    # If connectors cannot be drawn, skip gracefully without
                    # impacting the rest of the layout.
                    continue

    def _impact_map_norm_items(sl) -> tuple[str, List[str], Dict[str, Any]]:
        title, bullets, content = _infographic_items(sl)
        if not bullets:
            bullets = _synthesize_topic_bullets(title)[:6]
        items = [str(x).strip() for x in (bullets or []) if str(x).strip()]
        items = _sanitize_infographic_items(title, items[:6])
        if len(items) < 3:
            items = (items + _synthesize_topic_bullets(title))[:3]
        return (title or "Impact Map"), items, (content or {})

    def _hub_spoke_items(sl, *, target_n: int = 6) -> tuple[str, List[str], Dict[str, Any]]:
        title, items, content = _impact_map_norm_items(sl)
        out = list(items or [])
        if len(out) < target_n:
            synth = [str(x).strip() for x in (_synthesize_topic_bullets(title) or []) if str(x).strip()]
            for s2 in synth:
                if len(out) >= target_n:
                    break
                if s2 and s2 not in out:
                    out.append(s2)
        if not out:
            out = ["Spoke 1", "Spoke 2", "Spoke 3", "Spoke 4", "Spoke 5", "Spoke 6"]
        if len(out) >= target_n:
            out = out[:target_n]
        else:
            out = (out + [out[-1]] * (target_n - len(out)))[:target_n]
        return title, out, content

    def _hub_label(sl, content: Dict[str, Any]) -> str:
        t = str(content.get("center_label") or sl.get("center_label") or sl.get("title") or "Hub")
        t = t.replace("\r", " ").replace("\n", " ").strip()
        t = re.sub(r"\s+", " ", t)
        return t or "Hub"

    def _render_hub_spoke_side_by_side_6(sl) -> None:
        title, items, content = _hub_spoke_items(sl, target_n=6)
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        hub = _hub_label(sl, content)
        step_cols = _step_colors()
        core_rgb = pal.get("primary", (59, 130, 246))

        cx, cy = SLIDE_W_IN / 2.0, SLIDE_H_IN / 2.0

        outer_d = 2.85
        outer_line_pt = 10
        outer = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - outer_d / 2), Inches(cy - outer_d / 2), Inches(outer_d), Inches(outer_d))
        outer.fill.solid(); outer.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
        outer.line.color.rgb = RGBColor(*_shade(core_rgb, 10))
        outer.line.width = Pt(outer_line_pt)

        inner_d = 2.10
        inner = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - inner_d / 2), Inches(cy - inner_d / 2), Inches(inner_d), Inches(inner_d))
        inner.fill.solid(); inner.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
        try:
            inner.line.color.rgb = RGBColor(220, 226, 235)
            inner.line.width = Pt(2)
        except Exception:
            pass

        core_d = 1.55
        core = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - core_d / 2), Inches(cy - core_d / 2), Inches(core_d), Inches(core_d))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(255, 255, 255)
        try:
            core.line.color.rgb = RGBColor(220, 226, 235)
            core.line.width = Pt(1.5)
        except Exception:
            pass
        tfc = core.text_frame
        tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        try:
            tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
            tfc.margin_left = Pt(6)
            tfc.margin_right = Pt(6)
            tfc.margin_top = Pt(2)
            tfc.margin_bottom = Pt(2)
        except Exception:
            pass
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        wrapped_c, fs_c = _fit_text_no_midword(hub, box_w_in=core_d * 0.90, box_h_in=core_d * 0.75, base_font_pt=16, min_font_pt=11, max_lines=3)
        rc = pc.add_run(); rc.text = wrapped_c
        rc.font.size = Pt(fs_c); rc.font.bold = True; rc.font.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))

        node_w, node_h = 4.20, 0.92
        side_margin = 0.65
        left_x = side_margin
        right_x = SLIDE_W_IN - side_margin - node_w
        inner_gap_from_hub = 0.55
        row_gap = 0.58
        start_y = cy - (node_h * 1.5 + row_gap)

        dot_d = 0.10
        dot_rgb = (190, 198, 210)

        def _node_row_y(i: int) -> float:
            return start_y + i * (node_h + row_gap)

        def _draw_node(*, x: float, y: float, idx: int, text: str, align_left: bool) -> None:
            col = step_cols[idx % len(step_cols)]

            pill = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(node_w), Inches(node_h))
            pill.fill.solid(); pill.fill.fore_color.rgb = RGBColor(*bg_rgb)
            try:
                pill.line.color.rgb = RGBColor(231, 236, 244)
                pill.line.width = Pt(1)
            except Exception:
                pill.line.fill.background()

            stripe_w = 1.30
            stripe_x = x if align_left else (x + node_w - stripe_w)
            stripe = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(stripe_x), Inches(y), Inches(stripe_w), Inches(node_h))
            stripe.fill.solid(); stripe.fill.fore_color.rgb = RGBColor(*col)
            stripe.line.fill.background()

            badge_d = 0.54
            badge_x = (stripe_x + stripe_w - badge_d - 0.16) if align_left else (stripe_x + 0.16)
            badge = s.shapes.add_shape(MsoShape.OVAL, Inches(badge_x), Inches(y + node_h / 2.0 - badge_d / 2.0), Inches(badge_d), Inches(badge_d))
            badge.fill.solid(); badge.fill.fore_color.rgb = RGBColor(255, 255, 255)
            try:
                badge.line.color.rgb = RGBColor(*_shade(col, -20))
                badge.line.width = Pt(1.25)
            except Exception:
                pass
            btf = badge.text_frame
            btf.clear(); btf.word_wrap = False; btf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                btf.vertical_anchor = MSO_ANCHOR.MIDDLE
                btf.margin_left = Pt(0)
                btf.margin_right = Pt(0)
                btf.margin_top = Pt(0)
                btf.margin_bottom = Pt(0)
            except Exception:
                pass
            bp = btf.paragraphs[0]; bp.alignment = PP_ALIGN.CENTER
            br = bp.add_run(); br.text = f"{idx + 1:02d}"
            br.font.size = Pt(11)
            br.font.bold = True
            br.font.color.rgb = RGBColor(*_shade(col, -25))

            text_pad = 0.28
            if align_left:
                tx = x + stripe_w + text_pad
                tw = node_w - stripe_w - 2 * text_pad
            else:
                tx = x + text_pad
                tw = node_w - stripe_w - 2 * text_pad
            _add_textbox(
                s,
                tx,
                y + 0.16,
                tw,
                node_h - 0.25,
                _shorten(str(text or "").strip(), 64),
                font_size_pt=14,
                bold=True,
                color=pal.get("text", (15, 23, 42)),
                align="left",
                word_wrap=True,
                auto_fit=True,
            )

            ymid = y + node_h / 2.0
            if align_left:
                hub_x = cx - outer_d / 2.0
                dot_x = hub_x - inner_gap_from_hub
                end_x = x + node_w
            else:
                hub_x = cx + outer_d / 2.0
                dot_x = hub_x + inner_gap_from_hub
                end_x = x

            dot = s.shapes.add_shape(MsoShape.OVAL, Inches(dot_x - dot_d / 2), Inches(ymid - dot_d / 2), Inches(dot_d), Inches(dot_d))
            dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*dot_rgb)
            dot.line.fill.background()

            c1 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(hub_x), Inches(ymid), Inches(dot_x), Inches(ymid))
            c2 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(dot_x), Inches(ymid), Inches(end_x), Inches(ymid))
            for c in (c1, c2):
                c.line.color.rgb = RGBColor(222, 228, 238)
                c.line.width = Pt(1)

        for i in range(3):
            y = _node_row_y(i)
            _draw_node(x=left_x, y=y, idx=i, text=items[i], align_left=True)

        for i in range(3):
            y = _node_row_y(i)
            _draw_node(x=right_x, y=y, idx=i + 3, text=items[i + 3], align_left=False)

    def _render_hub_spoke_side_by_side_infographic_6(sl) -> None:
        title, items, content = _hub_spoke_items(sl, target_n=6)
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        hub = _hub_label(sl, content)
        step_cols = _step_colors()
        core_rgb = pal.get("primary", (59, 130, 246))
        bg_rgb = pal.get("bg", (255, 255, 255))

        cx, cy = SLIDE_W_IN / 2.0, SLIDE_H_IN / 2.0

        # Center ring: keep it clean and light. PowerPoint gradients aren't
        # reliable via python-pptx, so approximate a soft gradient with two
        # concentric strokes.
        outer_d = 3.00
        ring = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - outer_d / 2), Inches(cy - outer_d / 2), Inches(outer_d), Inches(outer_d))
        ring.fill.solid(); ring.fill.fore_color.rgb = RGBColor(*bg_rgb)
        ring.line.color.rgb = RGBColor(*_shade(core_rgb, 30))
        ring.line.width = Pt(6)

        mid_d = 2.72
        ring2 = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - mid_d / 2), Inches(cy - mid_d / 2), Inches(mid_d), Inches(mid_d))
        ring2.fill.solid(); ring2.fill.fore_color.rgb = RGBColor(*bg_rgb)
        ring2.line.color.rgb = RGBColor(*_shade(core_rgb, 10))
        ring2.line.width = Pt(4)

        inner_d = 2.30
        inner = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - inner_d / 2), Inches(cy - inner_d / 2), Inches(inner_d), Inches(inner_d))
        inner.fill.solid(); inner.fill.fore_color.rgb = RGBColor(*bg_rgb)
        try:
            inner.line.color.rgb = RGBColor(228, 233, 241)
            inner.line.width = Pt(1.25)
        except Exception:
            pass

        core_d = 1.65
        core = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - core_d / 2), Inches(cy - core_d / 2), Inches(core_d), Inches(core_d))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(255, 255, 255)
        try:
            core.line.color.rgb = RGBColor(220, 226, 235)
            core.line.width = Pt(1.5)
        except Exception:
            pass
        tfc = core.text_frame
        tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        try:
            tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
            tfc.margin_left = Pt(6)
            tfc.margin_right = Pt(6)
            tfc.margin_top = Pt(2)
            tfc.margin_bottom = Pt(2)
        except Exception:
            pass
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        wrapped_c, fs_c = _fit_text_no_midword(hub, box_w_in=core_d * 0.90, box_h_in=core_d * 0.75, base_font_pt=16, min_font_pt=11, max_lines=3)
        rc = pc.add_run(); rc.text = wrapped_c
        rc.font.size = Pt(fs_c); rc.font.bold = True; rc.font.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))

        # Side pills: smaller + lighter to keep the slide airy.
        node_w, node_h = 4.05, 0.86
        side_margin = 0.78
        left_x = side_margin
        right_x = SLIDE_W_IN - side_margin - node_w
        inner_gap_from_hub = 0.46
        row_gap = 0.62
        start_y = cy - (node_h * 1.5 + row_gap)

        dot_d = 0.10
        dot_rgb = (190, 198, 210)

        def _node_row_y(i: int) -> float:
            return start_y + i * (node_h + row_gap)

        def _draw_node(*, x: float, y: float, idx: int, text: str, align_left: bool) -> None:
            col = step_cols[idx % len(step_cols)]
            head, tail = _split_head_tail(str(text or "").strip())
            head_txt = (head or str(text or "")).strip() or f"Option {idx + 1}"
            body_txt = (tail or "").strip()
            if body_txt and len(body_txt.split()) > 12:
                body_txt = " ".join(body_txt.split()[:12]).rstrip(" ,.;:-")

            pill = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(node_w), Inches(node_h))
            pill.fill.solid(); pill.fill.fore_color.rgb = RGBColor(*bg_rgb)
            try:
                pill.line.color.rgb = RGBColor(231, 236, 244)
                pill.line.width = Pt(1)
            except Exception:
                pill.line.fill.background()

            stripe_w = 1.12
            stripe_x = x if align_left else (x + node_w - stripe_w)
            stripe = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(stripe_x), Inches(y), Inches(stripe_w), Inches(node_h))
            stripe.fill.solid(); stripe.fill.fore_color.rgb = RGBColor(*col)
            stripe.line.fill.background()

            badge_d = 0.50
            badge_x = (stripe_x + stripe_w - badge_d - 0.16) if align_left else (stripe_x + 0.16)
            badge = s.shapes.add_shape(MsoShape.OVAL, Inches(badge_x), Inches(y + node_h / 2.0 - badge_d / 2.0), Inches(badge_d), Inches(badge_d))
            badge.fill.solid(); badge.fill.fore_color.rgb = RGBColor(255, 255, 255)
            try:
                badge.line.color.rgb = RGBColor(*_shade(col, -20))
                badge.line.width = Pt(1)
            except Exception:
                pass
            btf = badge.text_frame
            btf.clear(); btf.word_wrap = False; btf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                btf.vertical_anchor = MSO_ANCHOR.MIDDLE
                btf.margin_left = Pt(0)
                btf.margin_right = Pt(0)
                btf.margin_top = Pt(0)
                btf.margin_bottom = Pt(0)
            except Exception:
                pass
            bp = btf.paragraphs[0]; bp.alignment = PP_ALIGN.CENTER
            br = bp.add_run(); br.text = f"{idx + 1:02d}"
            br.font.size = Pt(11)
            br.font.bold = True
            br.font.color.rgb = RGBColor(*_shade(col, -25))

            text_pad = 0.24
            if align_left:
                tx = x + stripe_w + text_pad
                tw = node_w - stripe_w - 2 * text_pad
            else:
                tx = x + text_pad
                tw = node_w - stripe_w - 2 * text_pad
            th = node_h - 0.22
            tframe = s.shapes.add_textbox(Inches(tx), Inches(y + 0.12), Inches(tw), Inches(th)).text_frame
            tframe.clear(); tframe.word_wrap = True; tframe.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p1 = tframe.paragraphs[0]; p1.clear(); p1.alignment = PP_ALIGN.LEFT
            wrapped_h, fs_h = _fit_text_no_midword(head_txt, box_w_in=tw, box_h_in=th * (0.58 if body_txt else 1.0), base_font_pt=13, min_font_pt=11, max_lines=2)
            r1 = p1.add_run(); r1.text = wrapped_h
            r1.font.size = Pt(fs_h); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))
            if body_txt:
                p2 = tframe.add_paragraph(); p2.clear(); p2.alignment = PP_ALIGN.LEFT
                wrapped_b, fs_b = _fit_text_no_midword(body_txt, box_w_in=tw, box_h_in=th * 0.42, base_font_pt=9, min_font_pt=8, max_lines=2)
                r2 = p2.add_run(); r2.text = wrapped_b
                r2.font.size = Pt(fs_b); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

            ymid = y + node_h / 2.0
            if align_left:
                hub_x = cx - outer_d / 2.0
                dot_x = hub_x - inner_gap_from_hub
                end_x = x + node_w
            else:
                hub_x = cx + outer_d / 2.0
                dot_x = hub_x + inner_gap_from_hub
                end_x = x

            dot = s.shapes.add_shape(MsoShape.OVAL, Inches(dot_x - dot_d / 2), Inches(ymid - dot_d / 2), Inches(dot_d), Inches(dot_d))
            dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*dot_rgb)
            dot.line.fill.background()

            c1 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(hub_x), Inches(ymid), Inches(dot_x), Inches(ymid))
            c2 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(dot_x), Inches(ymid), Inches(end_x), Inches(ymid))
            for c in (c1, c2):
                c.line.color.rgb = RGBColor(222, 228, 238)
                c.line.width = Pt(1)

        for i in range(3):
            y = _node_row_y(i)
            _draw_node(x=left_x, y=y, idx=i, text=items[i], align_left=True)

        for i in range(3):
            y = _node_row_y(i)
            _draw_node(x=right_x, y=y, idx=i + 3, text=items[i + 3], align_left=False)

    def _render_hub_spoke_classic_circles(sl) -> None:
        title, items, content = _hub_spoke_items(sl, target_n=6)
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.45, 11.3, 0.85, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        hub = _hub_label(sl, content)
        step_cols = _step_colors()
        cx, cy = SLIDE_W_IN / 2.0, 4.25
        core_d = 1.95
        core_rgb = pal.get("primary", (59, 130, 246))

        core = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - core_d / 2), Inches(cy - core_d / 2), Inches(core_d), Inches(core_d))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*core_rgb)
        core.line.color.rgb = RGBColor(*_shade(core_rgb, -20))
        tfc = core.text_frame
        tfc.clear()
        tfc.word_wrap = False
        tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        try:
            tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
            tfc.margin_left = Pt(0)
            tfc.margin_right = Pt(0)
            tfc.margin_top = Pt(0)
            tfc.margin_bottom = Pt(0)
        except Exception:
            pass
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        wrapped_c, fs_c = _fit_text_no_midword(hub, box_w_in=core_d * 0.90, box_h_in=core_d * 0.80, base_font_pt=18, min_font_pt=12, max_lines=3)
        rc = pc.add_run(); rc.text = wrapped_c
        rc.font.size = Pt(fs_c); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        node_d = 1.55
        r_x = 4.15
        r_y = 2.35
        angles = [-math.pi / 2.0, -math.pi / 6.0, math.pi / 6.0, math.pi / 2.0, 5.0 * math.pi / 6.0, -5.0 * math.pi / 6.0]
        for i, raw in enumerate(items):
            ang = angles[i % len(angles)]
            nx = cx + r_x * math.cos(ang) - node_d / 2.0
            ny = cy + r_y * math.sin(ang) - node_d / 2.0
            nx = max(0.65, min(SLIDE_W_IN - 0.65 - node_d, nx))
            ny = max(1.05, min(SLIDE_H_IN - 0.30 - node_d, ny))

            col = step_cols[i % len(step_cols)]
            node = s.shapes.add_shape(MsoShape.OVAL, Inches(nx), Inches(ny), Inches(node_d), Inches(node_d))
            node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*col)
            node.line.color.rgb = RGBColor(*_shade(col, -20))
            tf = node.text_frame
            tf.clear()
            tf.word_wrap = False
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                tf.vertical_anchor = MSO_ANCHOR.MIDDLE
                tf.margin_left = Pt(0)
                tf.margin_right = Pt(0)
                tf.margin_top = Pt(0)
                tf.margin_bottom = Pt(0)
            except Exception:
                pass
            p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
            head, tail = _split_head_tail(str(raw or ""))
            lab = (head or str(raw or "")).strip()
            wrapped, fs = _fit_text_no_midword(lab, box_w_in=node_d * 0.92, box_h_in=node_d * 0.78, base_font_pt=13, min_font_pt=9, max_lines=3)
            rr = p.add_run(); rr.text = wrapped
            rr.font.size = Pt(fs); rr.font.bold = True; rr.font.color.rgb = RGBColor(255, 255, 255)

            # connector from hub to node
            tx, ty = nx + node_d / 2.0, ny + node_d / 2.0
            vx, vy = tx - cx, ty - cy
            d = (vx * vx + vy * vy) ** 0.5 or 1.0
            ux, uy = vx / d, vy / d
            sx, sy = cx + ux * (core_d / 2.0), cy + uy * (core_d / 2.0)
            ex, ey = tx - ux * (node_d / 2.0), ty - uy * (node_d / 2.0)
            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(sx), Inches(sy), Inches(ex), Inches(ey))
            conn.line.color.rgb = RGBColor(*_shade(core_rgb, -25))
            conn.line.width = Pt(1.75)

    def _render_hub_spoke_circular_ring(sl) -> None:
        title, items, content = _hub_spoke_items(sl, target_n=6)
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        hub = _hub_label(sl, content)
        step_cols = _step_colors()
        cx, cy = SLIDE_W_IN / 2.0, SLIDE_H_IN * 0.62

        ring_d = 2.20
        ring_line_pt = 8
        ring = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ring_d / 2), Inches(cy - ring_d / 2), Inches(ring_d), Inches(ring_d))
        ring.fill.solid(); ring.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
        ring.line.color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
        ring.line.width = Pt(ring_line_pt)

        core_d = 1.50
        core_rgb = pal.get("primary", (59, 130, 246))
        core = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - core_d / 2), Inches(cy - core_d / 2), Inches(core_d), Inches(core_d))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*core_rgb)
        core.line.fill.background()
        tfc = core.text_frame
        tfc.clear()
        tfc.word_wrap = False
        tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        try:
            tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
            tfc.margin_left = Pt(0)
            tfc.margin_right = Pt(0)
            tfc.margin_top = Pt(0)
            tfc.margin_bottom = Pt(0)
        except Exception:
            pass
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        wrapped_c, fs_c = _fit_text_no_midword(hub, box_w_in=core_d * 0.92, box_h_in=core_d * 0.78, base_font_pt=16, min_font_pt=11, max_lines=3)
        rc = pc.add_run(); rc.text = wrapped_c
        rc.font.size = Pt(fs_c); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        node_d = 1.22
        badge_d = 0.46
        n_nodes = 6
        node_r = node_d / 2.0
        node_line_pt = 3
        angles = [(-math.pi / 2.0) + (2.0 * math.pi / n_nodes) * i for i in range(n_nodes)]

        ring_r = ring_d / 2.0
        top_limit = 1.95
        bottom_margin = 0.15
        ring_stroke_in = float(ring_line_pt) / 72.0
        node_stroke_in = float(node_line_pt) / 72.0
        start_r = ring_r + ring_stroke_in / 2.0
        node_edge_r = node_r + node_stroke_in / 2.0
        min_conn_len = 0.32
        r_min = start_r + node_edge_r + min_conn_len
        r_max = min((cy - top_limit) - node_r, (SLIDE_H_IN - bottom_margin - cy) - node_r)
        r_max = max(0.0, r_max)
        r = min(r_max, max(r_min, 2.05)) if r_max > 0.0 else max(r_min, 2.05)

        for i, raw in enumerate(items[:n_nodes]):
            ang = angles[i]
            tx = cx + r * math.cos(ang)
            ty = cy + r * math.sin(ang)
            nx, ny = tx - node_r, ty - node_r

            col = step_cols[i % len(step_cols)]
            node = s.shapes.add_shape(MsoShape.OVAL, Inches(nx), Inches(ny), Inches(node_d), Inches(node_d))
            node.fill.solid(); node.fill.fore_color.rgb = RGBColor(255, 255, 255)
            node.line.color.rgb = RGBColor(*col)
            node.line.width = Pt(node_line_pt)

            head, _ = _split_head_tail(str(raw or ""))
            lab = (head or str(raw or "")).strip()
            tf = node.text_frame
            tf.clear()
            tf.word_wrap = False
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                tf.vertical_anchor = MSO_ANCHOR.TOP
                tf.margin_left = Pt(2)
                tf.margin_right = Pt(2)
                # leave room for the numeric badge at the top of the circle
                tf.margin_top = Pt(26)
                tf.margin_bottom = Pt(2)
            except Exception:
                pass
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            try:
                p.space_before = Pt(0)
                p.space_after = Pt(0)
                p.line_spacing = 1.0
            except Exception:
                pass
            wrapped, fs = _fit_text_no_midword(lab, box_w_in=node_d * 0.92, box_h_in=node_d * 0.60, base_font_pt=10, min_font_pt=8, max_lines=2)
            tr = p.add_run(); tr.text = wrapped
            tr.font.size = Pt(fs)
            tr.font.bold = True
            tr.font.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))

            badge = s.shapes.add_shape(MsoShape.OVAL, Inches(nx + node_d / 2.0 - badge_d / 2.0), Inches(ny + 0.07), Inches(badge_d), Inches(badge_d))
            badge.fill.solid(); badge.fill.fore_color.rgb = RGBColor(*col)
            badge.line.fill.background()
            btf = badge.text_frame
            btf.clear()
            btf.word_wrap = False
            btf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                btf.vertical_anchor = MSO_ANCHOR.MIDDLE
                btf.margin_left = Pt(0)
                btf.margin_right = Pt(0)
                btf.margin_top = Pt(0)
                btf.margin_bottom = Pt(0)
            except Exception:
                pass
            bp = btf.paragraphs[0]; bp.alignment = PP_ALIGN.CENTER
            rr = bp.add_run(); rr.text = f"{i+1:02d}"; rr.font.size = Pt(10); rr.font.bold = True; rr.font.color.rgb = RGBColor(255, 255, 255)

            # connector
            ux, uy = math.cos(ang), math.sin(ang)
            sx, sy = cx + ux * start_r, cy + uy * start_r
            ex, ey = cx + ux * (r - node_edge_r), cy + uy * (r - node_edge_r)
            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(sx), Inches(sy), Inches(ex), Inches(ey))
            conn.line.color.rgb = RGBColor(*_shade(col, -10))
            conn.line.width = Pt(1.75)

    def _render_hub_spoke_lr_boxes(sl) -> None:
        title, items, content = _hub_spoke_items(sl, target_n=6)
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        hub = _hub_label(sl, content)
        step_cols = _step_colors()
        core_rgb = pal.get("primary", (59, 130, 246))

        cx, cy = SLIDE_W_IN / 2.0, 4.15
        core_d = 1.95
        core = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - core_d / 2), Inches(cy - core_d / 2), Inches(core_d), Inches(core_d))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*core_rgb)
        core.line.color.rgb = RGBColor(*_shade(core_rgb, -20))
        tfc = core.text_frame
        tfc.clear()
        tfc.word_wrap = False
        tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        try:
            tfc.vertical_anchor = MSO_ANCHOR.MIDDLE
            tfc.margin_left = Pt(0)
            tfc.margin_right = Pt(0)
            tfc.margin_top = Pt(0)
            tfc.margin_bottom = Pt(0)
        except Exception:
            pass
        pc = tfc.paragraphs[0]
        pc.alignment = PP_ALIGN.CENTER
        try:
            pc.space_before = Pt(0)
            pc.space_after = Pt(0)
            pc.line_spacing = 1.0
        except Exception:
            pass
        wrapped_c, fs_c = _fit_text_no_midword(hub, box_w_in=core_d * 0.92, box_h_in=core_d * 0.78, base_font_pt=16, min_font_pt=11, max_lines=3)
        rc = pc.add_run(); rc.text = wrapped_c
        rc.font.size = Pt(fs_c); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        box_w, box_h = 2.55, 0.62
        gap_y = 0.52
        left_x = 0.90
        right_x = SLIDE_W_IN - 0.90 - box_w
        start_y = 2.65

        elbow_x_left = cx - core_d / 2.0 - 0.35
        elbow_x_right = cx + core_d / 2.0 + 0.35

        for i in range(3):
            y = start_y + i * (box_h + gap_y)
            col = step_cols[i % len(step_cols)]
            lab = (items[i] if i < len(items) else "").strip()
            box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(left_x), Inches(y), Inches(box_w), Inches(box_h))
            box.fill.solid(); box.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
            box.line.color.rgb = RGBColor(*col)
            box.line.width = Pt(1.5)
            _add_textbox(s, left_x + 0.18, y + 0.12, box_w - 0.36, box_h - 0.20, _shorten(lab, 54), font_size_pt=13, bold=True, color=pal.get("text", (15, 23, 42)), align="center", word_wrap=True, auto_fit=True)

            sy = y + box_h / 2.0
            c1 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(cx - core_d / 2.0), Inches(cy), Inches(elbow_x_left), Inches(sy))
            c2 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(elbow_x_left), Inches(sy), Inches(left_x + box_w), Inches(sy))
            for c in (c1, c2):
                c.line.color.rgb = RGBColor(*_shade(core_rgb, -25))
                c.line.width = Pt(1.75)

        for i in range(3):
            y = start_y + i * (box_h + gap_y)
            col = step_cols[(i + 3) % len(step_cols)]
            lab = (items[i + 3] if (i + 3) < len(items) else "").strip()
            box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(right_x), Inches(y), Inches(box_w), Inches(box_h))
            box.fill.solid(); box.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
            box.line.color.rgb = RGBColor(*col)
            box.line.width = Pt(1.5)
            _add_textbox(s, right_x + 0.18, y + 0.12, box_w - 0.36, box_h - 0.20, _shorten(lab, 54), font_size_pt=13, bold=True, color=pal.get("text", (15, 23, 42)), align="center", word_wrap=True, auto_fit=True)

            sy = y + box_h / 2.0
            c1 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(cx + core_d / 2.0), Inches(cy), Inches(elbow_x_right), Inches(sy))
            c2 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(elbow_x_right), Inches(sy), Inches(right_x), Inches(sy))
            for c in (c1, c2):
                c.line.color.rgb = RGBColor(*_shade(core_rgb, -25))
                c.line.width = Pt(1.75)

    def _render_hub_spoke_orbit(sl) -> None:
        title, items, content = _hub_spoke_items(sl, target_n=6)
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        hub = _hub_label(sl, content)
        step_cols = _step_colors()
        cx, cy = SLIDE_W_IN / 2.0, 4.20
        core_d = 1.80
        core_rgb = pal.get("primary", (59, 130, 246))

        core = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - core_d / 2), Inches(cy - core_d / 2), Inches(core_d), Inches(core_d))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*core_rgb)
        core.line.color.rgb = RGBColor(*_shade(core_rgb, -20))
        tfc = core.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        wrapped_c, fs_c = _fit_text_no_midword(hub, box_w_in=core_d * 0.92, box_h_in=core_d * 0.78, base_font_pt=16, min_font_pt=11, max_lines=3)
        rc = pc.add_run(); rc.text = wrapped_c
        rc.font.size = Pt(fs_c); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        orb_d = 1.45
        r_x = 3.95
        r_y = 2.05
        angles = [-math.pi / 2.0, -math.pi / 6.0, math.pi / 6.0, math.pi / 2.0, 5.0 * math.pi / 6.0, -5.0 * math.pi / 6.0]
        for i, raw in enumerate(items):
            ang = angles[i % len(angles)]
            tx = cx + r_x * math.cos(ang)
            ty = cy + r_y * math.sin(ang)
            nx, ny = tx - orb_d / 2.0, ty - orb_d / 2.0
            nx = max(0.65, min(SLIDE_W_IN - 0.65 - orb_d, nx))
            ny = max(1.95, min(SLIDE_H_IN - 0.70 - orb_d, ny))

            col = step_cols[i % len(step_cols)]
            spoke = s.shapes.add_shape(MsoShape.OVAL, Inches(nx), Inches(ny), Inches(orb_d), Inches(orb_d))
            spoke.fill.solid(); spoke.fill.fore_color.rgb = RGBColor(*col)
            spoke.line.fill.background()
            tf = spoke.text_frame
            tf.clear()
            tf.word_wrap = False
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                tf.vertical_anchor = MSO_ANCHOR.MIDDLE
                tf.margin_left = Pt(0)
                tf.margin_right = Pt(0)
                tf.margin_top = Pt(0)
                tf.margin_bottom = Pt(0)
            except Exception:
                pass
            p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
            head, _ = _split_head_tail(str(raw or ""))
            lab = (head or str(raw or "")).strip()
            wrapped, fs = _fit_text_no_midword(lab, box_w_in=orb_d * 0.92, box_h_in=orb_d * 0.78, base_font_pt=12, min_font_pt=9, max_lines=3)
            rr = p.add_run(); rr.text = wrapped
            rr.font.size = Pt(fs); rr.font.bold = True; rr.font.color.rgb = RGBColor(255, 255, 255)

            # arrow-ish connector (line + tiny triangle)
            txc, tyc = nx + orb_d / 2.0, ny + orb_d / 2.0
            vx, vy = txc - cx, tyc - cy
            d = (vx * vx + vy * vy) ** 0.5 or 1.0
            ux, uy = vx / d, vy / d
            sx, sy = cx + ux * (core_d / 2.0), cy + uy * (core_d / 2.0)
            ex, ey = txc - ux * (orb_d / 2.0), tyc - uy * (orb_d / 2.0)
            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(sx), Inches(sy), Inches(ex), Inches(ey))
            conn.line.color.rgb = RGBColor(*_shade(col, -15))
            conn.line.width = Pt(2.0)

            tri = s.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(ex - 0.08), Inches(ey - 0.06), Inches(0.16), Inches(0.12))
            tri.fill.solid(); tri.fill.fore_color.rgb = RGBColor(*_shade(col, -15))
            tri.line.fill.background()
            try:
                tri.rotation = int(round((math.degrees(math.atan2(vy, vx))) + 90.0))
            except Exception:
                pass

    def _render_impact_map_radial(sl) -> None:
        title, items, content = _impact_map_norm_items(sl)
        target_n = 6
        items = list(items or [])
        if len(items) < target_n:
            synth = [str(x).strip() for x in (_synthesize_topic_bullets(title) or []) if str(x).strip()]
            for s2 in synth:
                if len(items) >= target_n:
                    break
                items.append(s2)
        items = items[:target_n]
        n = target_n

        s = prs.slides.add_slide(blank); apply_bg(s)

        band_x, band_y, band_w, band_h = 1.0, 1.35, SLIDE_W_IN - 2.0, 6.00
        band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(band_y), Inches(band_w), Inches(band_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
        try:
            band.line.color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
            band.line.width = Pt(0.75)
        except Exception:
            band.line.fill.background()

        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        center_label = (content.get("center_label") or sl.get("center_label") or sl.get("title") or "Goal").strip() or "Goal"
        cx, cy = band_x + band_w / 2.0, band_y + band_h / 2.0
        core_d = 2.25
        core = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - core_d / 2), Inches(cy - core_d / 2), Inches(core_d), Inches(core_d))
        core_rgb = pal.get("primary", (59, 130, 246))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*core_rgb)
        core.line.color.rgb = RGBColor(*_shade(core_rgb, -20))
        try:
            core.shadow.inherit = False; core.shadow.visible = True; core.shadow.blur = 14; core.shadow.distance = 3
        except Exception:
            pass
        tfc = core.text_frame; tfc.clear(); tfc.word_wrap = False; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        wrapped_c, fs_c = _fit_text_no_midword(center_label, box_w_in=core_d, box_h_in=core_d, base_font_pt=20, min_font_pt=14, max_lines=3, width_factor=0.68, height_factor=0.72)
        rc = pc.add_run(); rc.text = wrapped_c
        rc.font.size = Pt(fs_c); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        step_cols = _step_colors()
        node_w, node_h = 2.85, 0.95
        edge_pad = 0.12
        pts: List[tuple[float, float]] = []
        # Use an elliptical layout (wider than tall) so nodes can sit farther away
        # horizontally without exceeding the slide's limited vertical real estate.
        # This matches the "premium" look in the reference screenshot.
        limit_x = max(0.4, (band_w / 2.0) - (node_w / 2.0) - edge_pad)
        limit_y = max(0.4, (band_h / 2.0) - (node_h / 2.0) - edge_pad)
        r_x = max(2.2, min(limit_x, limit_x * 0.98))
        r_y = max(1.6, min(limit_y, limit_y * 0.95))

        # 6 evenly spaced nodes with top/bottom + 4 diagonals.
        angles: List[float] = [
            -math.pi / 2.0,
            -math.pi / 6.0,
            math.pi / 6.0,
            math.pi / 2.0,
            5.0 * math.pi / 6.0,
            -5.0 * math.pi / 6.0,
        ]

        for ang in angles:
            pts.append((cx + r_x * math.cos(ang), cy + r_y * math.sin(ang)))

        targets: List[tuple[float, float]] = []
        node_rects: List[tuple[float, float, float, float]] = []
        for i, raw in enumerate(items):
            x, y = pts[i]
            nx = x - node_w / 2.0
            ny = y - node_h / 2.0
            # Clamp into the band to avoid accidental overflow.
            nx = max(band_x + edge_pad, min(band_x + band_w - node_w - edge_pad, nx))
            ny = max(band_y + edge_pad, min(band_y + band_h - node_h - edge_pad, ny))
            node_rects.append((nx, ny, node_w, node_h))

            head, tail = _split_head_tail(str(raw or ""))
            head_txt = (head or str(raw or "")).strip()
            body_txt = (tail or "").strip()
            # Keep a premium, airy look: prioritise the headline, de-emphasise long tails.
            if len(body_txt.split()) > 8:
                body_txt = ""

            node = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(nx), Inches(ny), Inches(node_w), Inches(node_h))
            bg_rgb = pal.get("bg", (255, 255, 255))
            node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*bg_rgb)
            node.line.color.rgb = RGBColor(*step_cols[i % len(step_cols)])
            try:
                node.shadow.inherit = False; node.shadow.visible = True; node.shadow.blur = 6; node.shadow.distance = 2
            except Exception:
                pass
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(nx + 0.16), Inches(ny + 0.24), Inches(0.28), Inches(0.28))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(*step_cols[i % len(step_cols)])
            ico.line.fill.background()

            inner_w = max(0.6, node_w - 0.72)
            inner_h = max(0.4, node_h - 0.26)
            tb = s.shapes.add_textbox(Inches(nx + 0.52), Inches(ny + 0.12), Inches(inner_w), Inches(inner_h)).text_frame
            tb.clear(); tb.word_wrap = False; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            tb.margin_top = Pt(0); tb.margin_bottom = Pt(0)
            p = tb.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            p.line_spacing = 1.05

            wrapped_h, fs_h = _fit_text_no_midword(head_txt, box_w_in=inner_w, box_h_in=inner_h * (0.60 if body_txt else 1.0), base_font_pt=14, min_font_pt=11, max_lines=2)
            r1 = p.add_run(); r1.text = wrapped_h; r1.font.size = Pt(fs_h); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if body_txt:
                p2 = tb.add_paragraph(); p2.clear(); p2.alignment = PP_ALIGN.LEFT
                p2.line_spacing = 1.05
                wrapped_b, fs_b = _fit_text_no_midword(body_txt, box_w_in=inner_w, box_h_in=inner_h * 0.40, base_font_pt=10, min_font_pt=9, max_lines=2)
                r2 = p2.add_run(); r2.text = wrapped_b; r2.font.size = Pt(fs_b); r2.font.bold = False; r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

            targets.append((nx + node_w / 2.0, ny + node_h / 2.0))

        base_rgb = pal.get("primary", (59, 130, 246))
        line_rgb = _shade(base_rgb, -25)
        for tx, ty in targets:
            vx = tx - cx
            vy = ty - cy
            d = (vx * vx + vy * vy) ** 0.5 or 1.0
            ux, uy = vx / d, vy / d
            # Center node is an oval: shrink start distance a bit to avoid touching text region.
            sx = cx + ux * (core_d / 2.0)
            sy = cy + uy * (core_d / 2.0)

            # Outer nodes are rectangles: compute intersection with their bounding box.
            denom_x = abs(ux) if abs(ux) > 1e-6 else 1e-6
            denom_y = abs(uy) if abs(uy) > 1e-6 else 1e-6
            t_edge = min((node_w / 2.0) / denom_x, (node_h / 2.0) / denom_y)
            ex = tx - ux * t_edge
            ey = ty - uy * t_edge

            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(sx), Inches(sy), Inches(ex), Inches(ey))
            conn.line.color.rgb = RGBColor(*line_rgb)
            conn.line.width = Pt(1.75)

    def _render_impact_map_pyramid(sl) -> None:
        title, items, content = _impact_map_norm_items(sl)
        n = max(3, min(6, len(items)))
        items = items[:n]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        band_x, band_y, band_w, band_h = 0.9, 2.0, SLIDE_W_IN - 1.8, 4.95
        band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(band_y), Inches(band_w), Inches(band_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
        band.line.fill.background()
        try:
            band.shadow.inherit = False; band.shadow.visible = True; band.shadow.blur = 18; band.shadow.distance = 3
        except Exception:
            pass

        center_label = (content.get("center_label") or sl.get("center_label") or sl.get("title") or "Goal").strip() or "Goal"
        core_w, core_h = 3.6, 1.45
        core_x = (SLIDE_W_IN - core_w) / 2.0
        core_y = 2.35
        mid_w, mid_h = 4.75, 1.05
        bot_w, bot_h = 3.65, 0.95
        mid_y = core_y + core_h + 0.45
        bot_y = mid_y + mid_h + 0.45
        gap_x = 0.55

        mid_positions: List[tuple[float, float]] = [
            ((SLIDE_W_IN - (2 * mid_w + gap_x)) / 2.0, mid_y),
            ((SLIDE_W_IN - (2 * mid_w + gap_x)) / 2.0 + mid_w + gap_x, mid_y),
        ]
        bot_positions: List[tuple[float, float]] = [
            ((SLIDE_W_IN - (2 * bot_w + gap_x)) / 2.0, bot_y),
            ((SLIDE_W_IN - (2 * bot_w + gap_x)) / 2.0 + bot_w + gap_x, bot_y),
        ]

        mid_items = items[:2]
        bot_items = items[2:4]
        if len(mid_items) < 2:
            mid_items = (mid_items + [""] * 2)[:2]
        if len(bot_items) < 2:
            bot_items = (bot_items + [""] * 2)[:2]

        base_rgb = pal.get("primary", (59, 130, 246))
        line_rgb = _shade(base_rgb, -25)
        conn1 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(core_x + core_w / 2.0), Inches(core_y + core_h), Inches(mid_positions[0][0] + mid_w / 2.0), Inches(mid_y))
        conn2 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(core_x + core_w / 2.0), Inches(core_y + core_h), Inches(mid_positions[1][0] + mid_w / 2.0), Inches(mid_y))
        conn3 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(mid_positions[0][0] + mid_w / 2.0), Inches(mid_y + mid_h), Inches(bot_positions[0][0] + bot_w / 2.0), Inches(bot_y))
        conn4 = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(mid_positions[1][0] + mid_w / 2.0), Inches(mid_y + mid_h), Inches(bot_positions[1][0] + bot_w / 2.0), Inches(bot_y))
        for c in (conn1, conn2, conn3, conn4):
            c.line.color.rgb = RGBColor(*line_rgb)
            c.line.width = Pt(1.75)

        core = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(core_x), Inches(core_y), Inches(core_w), Inches(core_h))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*base_rgb)
        core.line.color.rgb = RGBColor(*_shade(base_rgb, -20))
        try:
            core.shadow.inherit = False; core.shadow.visible = True; core.shadow.blur = 14; core.shadow.distance = 3
        except Exception:
            pass
        tfc = core.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        rc = pc.add_run(); rc.text = center_label
        rc.font.size = Pt(18); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        step_cols = _step_colors()
        for i, (x, y) in enumerate(mid_positions):
            raw = mid_items[i]
            head, _ = _split_head_tail(raw)
            txt = (head or raw or "").strip() or ""
            words = txt.split()
            if len(words) > 7:
                txt = " ".join(words[:7])
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(mid_w), Inches(mid_h))
            bg_rgb = pal.get("bg", (255, 255, 255))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*bg_rgb)
            card.line.color.rgb = RGBColor(*step_cols[i % len(step_cols)])
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 6; card.shadow.distance = 2
            except Exception:
                pass
            tb = card.text_frame; tb.clear(); tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
            r1 = p.add_run(); r1.text = txt; r1.font.size = Pt(15); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))

        for i, (x, y) in enumerate(bot_positions):
            raw = bot_items[i]
            head, _ = _split_head_tail(raw)
            txt = (head or raw or "").strip() or ""
            words = txt.split()
            if len(words) > 7:
                txt = " ".join(words[:7])
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(bot_w), Inches(bot_h))
            bg_rgb = pal.get("bg", (255, 255, 255))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*bg_rgb)
            card.line.color.rgb = RGBColor(*step_cols[(i + 2) % len(step_cols)])
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 6; card.shadow.distance = 2
            except Exception:
                pass
            tb = card.text_frame; tb.clear(); tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
            r1 = p.add_run(); r1.text = txt; r1.font.size = Pt(14); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))

    def _render_impact_map_two_sided(sl) -> None:
        title, items, content = _impact_map_norm_items(sl)
        n = max(3, min(6, len(items)))
        items = items[:n]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        band_x, band_y, band_w, band_h = 0.9, 2.0, SLIDE_W_IN - 1.8, 4.95
        band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(band_y), Inches(band_w), Inches(band_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
        band.line.fill.background()
        try:
            band.shadow.inherit = False; band.shadow.visible = True; band.shadow.blur = 18; band.shadow.distance = 3
        except Exception:
            pass

        left_title = str(content.get("baseline_label") or "Current").strip() or "Current"
        right_title = str(content.get("future_label") or "Future").strip() or "Future"
        _add_textbox(s, 1.15, 2.15, 3.6, 0.45, left_title, font_size_pt=14, bold=True, color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)
        _add_textbox(s, SLIDE_W_IN - 1.15 - 3.6, 2.15, 3.6, 0.45, right_title, font_size_pt=14, bold=True, align="right", color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)

        center_label = (content.get("center_label") or sl.get("center_label") or sl.get("title") or "Goal").strip() or "Goal"
        hex_w, hex_h = 3.3, 1.85
        hex_x = (SLIDE_W_IN - hex_w) / 2.0
        hex_y = 3.15

        left = items[: max(1, n // 2)]
        right = items[max(1, n // 2):]
        if len(right) < len(left):
            right = right + [""]

        node_w, node_h = 3.05, 1.15
        gap_y = 0.35
        left_x = 1.1
        right_x = SLIDE_W_IN - 1.1 - node_w
        start_y = 2.75

        left_positions: List[tuple[float, float]] = []
        right_positions: List[tuple[float, float]] = []
        for i in range(min(3, len(left))):
            left_positions.append((left_x, start_y + i * (node_h + gap_y)))
        for i in range(min(3, len(right))):
            right_positions.append((right_x, start_y + i * (node_h + gap_y)))

        base_rgb = pal.get("primary", (59, 130, 246))
        line_rgb = _shade(base_rgb, -25)
        for x, y in left_positions:
            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(x + node_w), Inches(y + node_h / 2.0), Inches(hex_x), Inches(hex_y + hex_h / 2.0))
            conn.line.color.rgb = RGBColor(*line_rgb)
            conn.line.width = Pt(1.75)
        for x, y in right_positions:
            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(hex_x + hex_w), Inches(hex_y + hex_h / 2.0), Inches(x), Inches(y + node_h / 2.0))
            conn.line.color.rgb = RGBColor(*line_rgb)
            conn.line.width = Pt(1.75)

        core = s.shapes.add_shape(MsoShape.HEXAGON, Inches(hex_x), Inches(hex_y), Inches(hex_w), Inches(hex_h))
        core.fill.solid(); core.fill.fore_color.rgb = RGBColor(*base_rgb)
        core.line.color.rgb = RGBColor(*_shade(base_rgb, -20))
        try:
            core.shadow.inherit = False; core.shadow.visible = True; core.shadow.blur = 14; core.shadow.distance = 3
        except Exception:
            pass
        tfc = core.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        rc = pc.add_run(); rc.text = center_label
        rc.font.size = Pt(18); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        step_cols = _step_colors()
        def _node(i: int, x: float, y: float, raw: str) -> None:
            head, _ = _split_head_tail(raw)
            head_txt = (head or raw or "").strip() or ""
            hw = head_txt.split()
            if len(hw) > 6:
                head_txt = " ".join(hw[:6])
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(node_w), Inches(node_h))
            bg_rgb = pal.get("bg", (255, 255, 255))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*bg_rgb)
            card.line.color.rgb = RGBColor(*step_cols[i % len(step_cols)])
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 6; card.shadow.distance = 2
            except Exception:
                pass
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(x + 0.18), Inches(y + 0.23), Inches(0.28), Inches(0.28))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(*step_cols[i % len(step_cols)])
            ico.line.fill.background()
            tb = s.shapes.add_textbox(Inches(x + 0.56), Inches(y + 0.12), Inches(node_w - 0.70), Inches(node_h - 0.24)).text_frame
            tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            p.line_spacing = 1.0
            r1 = p.add_run(); r1.text = head_txt; r1.font.size = Pt(14); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))

        for i, (x, y) in enumerate(left_positions):
            _node(i, x, y, left[i] if i < len(left) else "")
        for i, (x, y) in enumerate(right_positions):
            _node(i + 3, x, y, right[i] if i < len(right) else "")

    def _render_impact_map_swimlane(sl) -> None:
        title, items, content = _impact_map_norm_items(sl)
        roles = _extract_swimlane_roles(items)
        lanes = roles[:3]
        if len(lanes) < 2:
            lanes = ["Stakeholders", "Operations"]

        role_to_items: Dict[str, List[str]] = {r: [] for r in lanes}
        unassigned: List[str] = []
        for raw in items:
            t = str(raw or "").strip()
            if ":" in t:
                left = t.split(":", 1)[0].strip()
                rest = t.split(":", 1)[1].strip()
                if left in role_to_items and rest:
                    role_to_items[left].append(rest)
                    continue
            unassigned.append(t)

        if unassigned:
            for i, t in enumerate(unassigned):
                role_to_items[lanes[i % len(lanes)]].append(t)

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        band_x, band_y, band_w, band_h = 0.9, 2.0, SLIDE_W_IN - 1.8, 4.95
        band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(band_y), Inches(band_w), Inches(band_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
        band.line.fill.background()
        try:
            band.shadow.inherit = False; band.shadow.visible = True; band.shadow.blur = 18; band.shadow.distance = 3
        except Exception:
            pass

        center_label = (content.get("center_label") or sl.get("center_label") or sl.get("title") or "Goal").strip() or "Goal"
        goal_x, goal_y, goal_w, goal_h = 1.15, 2.60, 2.30, 3.90
        core_rgb = pal.get("primary", (59, 130, 246))
        goal = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(goal_x), Inches(goal_y), Inches(goal_w), Inches(goal_h))
        goal.fill.solid(); goal.fill.fore_color.rgb = RGBColor(*core_rgb)
        goal.line.color.rgb = RGBColor(*_shade(core_rgb, -20))
        try:
            goal.shadow.inherit = False; goal.shadow.visible = True; goal.shadow.blur = 14; goal.shadow.distance = 3
        except Exception:
            pass
        tfc = goal.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        rc = pc.add_run(); rc.text = center_label
        rc.font.size = Pt(18); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        lane_n = max(2, min(3, len(lanes)))
        inner_y = band_y + 0.45
        inner_h = band_h - 0.75
        lane_h = inner_h / lane_n
        content_x = 3.70
        content_w = SLIDE_W_IN - 1.10 - content_x
        gap_x = 0.35
        card_w = (content_w - gap_x) / 2.0
        step_cols = _step_colors()
        line_rgb = _shade(core_rgb, -25)

        for i in range(lane_n):
            lane = lanes[i]
            y0 = inner_y + i * lane_h
            if i > 0:
                div = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(band_x + 0.25), Inches(y0), Inches(band_x + band_w - 0.25), Inches(y0))
                div.line.color.rgb = RGBColor(255, 255, 255)
                div.line.width = Pt(1.0)

            col = step_cols[i % len(step_cols)]
            _add_textbox(s, content_x, y0 - 0.02, content_w, 0.28, lane, font_size_pt=12, bold=True, color=col, word_wrap=True, auto_fit=True)

            lane_items = role_to_items.get(lane, [])[:2]
            card_h = max(0.90, min(1.05, lane_h - 0.40))
            card_y = y0 + 0.30

            card_left_x = content_x
            if lane_items:
                conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(goal_x + goal_w), Inches(card_y + card_h / 2.0), Inches(card_left_x), Inches(card_y + card_h / 2.0))
                conn.line.color.rgb = RGBColor(*line_rgb)
                conn.line.width = Pt(1.75)

            for j, raw in enumerate(lane_items):
                txt = (str(raw or "").strip() or "")
                words = txt.split()
                if len(words) > 10:
                    txt = " ".join(words[:10])
                x = content_x + j * (card_w + gap_x)
                card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(card_y), Inches(card_w), Inches(card_h))
                bg_rgb = pal.get("bg", (255, 255, 255))
                card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*bg_rgb)
                card.line.color.rgb = RGBColor(*col)
                try:
                    card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 6; card.shadow.distance = 2
                except Exception:
                    pass
                tb = s.shapes.add_textbox(Inches(x + 0.22), Inches(card_y + 0.12), Inches(card_w - 0.40), Inches(card_h - 0.24)).text_frame
                tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                p = tb.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
                p.line_spacing = 1.0
                r1 = p.add_run(); r1.text = txt; r1.font.size = Pt(13); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))

    def _process_blob(title_txt: str, items: List[str]) -> str:
        return (title_txt or "").lower() + " " + " ".join(str(x).lower() for x in (items or []))

    def _extract_swimlane_roles(items: List[str]) -> List[str]:
        roles: List[str] = []
        for raw in (items or []):
            t = (raw or "").strip()
            if ":" in t:
                left = t.split(":", 1)[0].strip()
                if 2 <= len(left) <= 24 and all(ch.isalnum() or ch in " &/" for ch in left):
                    if left not in roles:
                        roles.append(left)
            if len(roles) >= 3:
                break
        return roles

    def _choose_process_variant(title_txt: str, items: List[str]) -> str:
        blob = _process_blob(title_txt, items)
        n = len(items)
        parts = [_split_head_tail(x) for x in (items or [])]
        lens = [len(((tail or head) or "").strip()) for head, tail in parts]
        max_len = max(lens) if lens else 0
        avg_len = (sum(lens) / len(lens)) if lens else 0.0

        governance = any(k in blob for k in [
            "approve", "approval", "sign-off", "sign off", "compliance", "audit", "control",
            "gate", "gating", "checkpoint", "validation", "readiness", "go/no-go", "go no-go", "review",
        ])
        cyclical = any(k in blob for k in [
            "cycle", "cyclical", "continuous", "ongoing", "recurring", "repeat", "iteration", "iterate", "loop", "feedback",
        ])
        instructional = any(k in blob for k in [
            "how to", "how it works", "step-by-step", "step by step", "instructions", "guide", "tutorial",
            "setup", "configure", "install", "onboarding",
        ])
        exec_tone = any(k in blob for k in [
            "overview", "executive", "high-level", "high level", "operating model", "summary",
        ])
        saas_tone = any(k in blob for k in [
            "saas", "product", "platform", "app", "workflow", "automation",
        ])
        roles = _extract_swimlane_roles(items)
        long_desc = (max_len >= 80) or (avg_len >= 55) or any(len(str(x)) >= 110 for x in (items or []))

        if governance and 4 <= n <= 6:
            return "GATE"
        if cyclical and 4 <= n <= 6:
            return "CYCLE"
        if len(roles) >= 2 and n <= 6:
            return "SWIMLANE"
        if long_desc or n >= 7:
            return "VERTICAL"
        if instructional:
            return "CARDS"
        if saas_tone and max_len <= 36:
            return "ICONS"
        if exec_tone and max_len <= 60:
            return "BANDS"
        return "CHEVRON"

    def _render_process_numbered_cards(title_txt: str, items: List[str]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Process", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        n = min(6, max(4, len(items)))
        arr = items[:n]
        cols = 3 if n >= 5 else n
        rows = 2 if n >= 5 else 1
        card_h = 1.55
        gap_x = 0.35
        gap_y = 0.40
        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        total_h = rows * card_h + (rows - 1) * gap_y
        cont_y = 2.15
        cont_h = total_h + 0.5
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        inner_x = cont_x + 0.35
        inner_y = cont_y + 0.25
        card_w = (cont_w - (cols - 1) * gap_x - 0.70) / cols
        STEP_COLS = _step_colors()

        for idx, raw in enumerate(arr):
            rr = idx // cols
            cc = idx % cols
            x = inner_x + cc * (card_w + gap_x)
            y = inner_y + rr * (card_h + gap_y)
            base_col = STEP_COLS[idx % len(STEP_COLS)]

            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            card.line.color.rgb = RGBColor(*base_col)
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 6; card.shadow.distance = 2
            except Exception:
                pass

            circ = s.shapes.add_shape(MsoShape.OVAL, Inches(x + 0.18), Inches(y + 0.18), Inches(0.42), Inches(0.42))
            circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*base_col)
            circ.line.fill.background()
            tfc = circ.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
            rc = pc.add_run(); rc.text = str(idx + 1); rc.font.size = Pt(16); rc.font.bold = True; rc.font.color.rgb = RGBColor(*_contrast_text(base_col))

            head, tail = _split_head_tail(raw)
            head_txt = (head or raw).strip()
            tail_txt = (tail or "").strip()
            tf = s.shapes.add_textbox(Inches(x + 0.70), Inches(y + 0.18), Inches(card_w - 0.90), Inches(card_h - 0.32)).text_frame
            tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            r1 = p.add_run(); r1.text = head_txt; r1.font.size = Pt(14); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if tail_txt:
                p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
                r2 = p2.add_run(); r2.text = tail_txt; r2.font.size = Pt(11); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def _render_cards_grid(title_txt: str, items: List[Any]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Highlights", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        try:
            arr = [_card_item_to_text(x) for x in (items or []) if _card_item_to_text(x)]
        except Exception:
            arr = [str(x).strip() for x in (items or []) if str(x).strip()]
        if not arr:
            arr = ["Example 1", "Example 2", "Example 3", "Example 4"]

        n = min(6, max(3, len(arr)))
        arr = arr[:n]
        cols = 3 if n >= 5 else (2 if n >= 4 else 3)
        rows = 2 if n >= 4 else 1

        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        gap_x = 0.40
        gap_y = 0.40
        card_h = 1.70
        total_h = rows * card_h + (rows - 1) * gap_y
        cont_y = 2.10
        cont_h = total_h + 0.65
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        inner_x = cont_x + 0.35
        inner_y = cont_y + 0.30
        card_w = (cont_w - (cols - 1) * gap_x - 0.70) / cols
        STEP_COLS = _step_colors()

        for idx, raw in enumerate(arr):
            rr = idx // cols
            cc = idx % cols
            x = inner_x + cc * (card_w + gap_x)
            y = inner_y + rr * (card_h + gap_y)
            accent = STEP_COLS[idx % len(STEP_COLS)]

            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            card.line.color.rgb = RGBColor(*accent)
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 6; card.shadow.distance = 2
            except Exception:
                pass

            head, tail = _split_head_tail(str(raw))
            head_txt = (head or str(raw)).strip()
            tail_txt = (tail or "").strip()

            tf = s.shapes.add_textbox(Inches(x + 0.28), Inches(y + 0.22), Inches(card_w - 0.56), Inches(card_h - 0.44)).text_frame
            tf.word_wrap = True
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            try:
                tf.margin_left = Inches(0.01); tf.margin_right = Inches(0.01); tf.margin_top = Inches(0.01); tf.margin_bottom = Inches(0.01)
            except Exception:
                pass

            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            r1 = p.add_run(); r1.text = head_txt; r1.font.size = Pt(15); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if tail_txt:
                p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
                r2 = p2.add_run(); r2.text = tail_txt; r2.font.size = Pt(11); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def _render_process_vertical_timeline(title_txt: str, items: List[str]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Process", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        n = min(7, max(4, len(items)))
        arr = items[:n]
        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        cont_y = 2.05
        cont_h = 5.05
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        spine_x = cont_x + 1.05
        top_y = cont_y + 0.55
        bot_y = cont_y + cont_h - 0.55
        spine = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(spine_x), Inches(top_y), Inches(spine_x), Inches(bot_y))
        spine.line.color.rgb = RGBColor(209, 213, 219)
        spine.line.width = Pt(2)

        step = (bot_y - top_y) / (n - 1)
        STEP_COLS = _step_colors()
        for idx, raw in enumerate(arr):
            y = top_y + idx * step
            col = STEP_COLS[idx % len(STEP_COLS)]
            node = s.shapes.add_shape(MsoShape.OVAL, Inches(spine_x - 0.18), Inches(y - 0.18), Inches(0.36), Inches(0.36))
            node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*col)
            node.line.fill.background()

            head, tail = _split_head_tail(raw)
            head_txt = (head or raw).strip()
            tail_txt = (tail or "").strip()
            card_x = spine_x + 0.55
            card_w = cont_x + cont_w - 0.55 - card_x
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(card_x), Inches(y - 0.42), Inches(card_w), Inches(0.84))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            card.line.color.rgb = RGBColor(*col)
            card.line.width = Pt(1)

            tf = s.shapes.add_textbox(Inches(card_x + 0.22), Inches(y - 0.38), Inches(card_w - 0.40), Inches(0.76)).text_frame
            tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            r1 = p.add_run(); r1.text = f"{idx + 1}. {head_txt}"; r1.font.size = Pt(13); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if tail_txt:
                p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
                r2 = p2.add_run(); r2.text = tail_txt; r2.font.size = Pt(11); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def _render_process_phase_bands(title_txt: str, items: List[str]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Phases", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        n = min(6, max(4, len(items)))
        arr = items[:n]
        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        cont_y = 2.1
        cont_h = 5.0
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        band_h = (cont_h - 0.70) / n
        STEP_COLS = _step_colors()
        for idx, raw in enumerate(arr):
            y = cont_y + 0.35 + idx * band_h
            col = STEP_COLS[idx % len(STEP_COLS)]
            band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x + 0.55), Inches(y), Inches(cont_w - 1.10), Inches(band_h - 0.12))
            band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*_shade(col, 26))
            band.line.color.rgb = RGBColor(*col)
            band.line.width = Pt(1)
            circ = s.shapes.add_shape(MsoShape.OVAL, Inches(cont_x + 0.25), Inches(y + (band_h - 0.12)/2 - 0.20), Inches(0.40), Inches(0.40))
            circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(*col)
            circ.line.fill.background()
            tfc = circ.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
            rc = pc.add_run(); rc.text = str(idx + 1); rc.font.size = Pt(14); rc.font.bold = True; rc.font.color.rgb = RGBColor(*_contrast_text(col))

            head, tail = _split_head_tail(raw)
            head_txt = (head or raw).strip()
            tail_txt = (tail or "").strip()
            tf = s.shapes.add_textbox(Inches(cont_x + 1.10), Inches(y + 0.10), Inches(cont_w - 1.70), Inches(band_h - 0.30)).text_frame
            tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            r1 = p.add_run(); r1.text = head_txt; r1.font.size = Pt(14); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if tail_txt:
                p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
                r2 = p2.add_run(); r2.text = tail_txt; r2.font.size = Pt(11); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def _render_process_cycle(title_txt: str, items: List[str]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Cycle", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        n = min(6, max(4, len(items)))
        arr = items[:n]
        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        cont_y = 2.05
        cont_h = 5.05
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        cx = cont_x + cont_w / 2
        cy = cont_y + cont_h / 2 + 0.05
        rx = min(3.8, (cont_w - 1.9) / 2)
        ry = min(2.0, (cont_h - 2.0) / 2)
        node_w = 2.0
        node_h = 0.80
        STEP_COLS = _step_colors()

        pts: List[tuple[float, float]] = []
        for i in range(n):
            ang = (-math.pi / 2) + (2 * math.pi * i / n)
            x = cx + rx * math.cos(ang)
            y = cy + ry * math.sin(ang)
            pts.append((x, y))

        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(x0), Inches(y0), Inches(x1), Inches(y1))
            conn.line.color.rgb = RGBColor(209, 213, 219)
            conn.line.width = Pt(1.25)

        for idx, raw in enumerate(arr):
            x, y = pts[idx]
            col = STEP_COLS[idx % len(STEP_COLS)]
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x - node_w / 2), Inches(y - node_h / 2), Inches(node_w), Inches(node_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            card.line.color.rgb = RGBColor(*col)
            card.line.width = Pt(1)

            head, tail = _split_head_tail(raw)
            head_txt = (head or raw).strip()
            tail_txt = (tail or "").strip()
            tf = s.shapes.add_textbox(Inches(x - node_w / 2 + 0.18), Inches(y - node_h / 2 + 0.12), Inches(node_w - 0.36), Inches(node_h - 0.20)).text_frame
            tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.CENTER
            r1 = p.add_run(); r1.text = f"{idx + 1}. {head_txt}" if len(head_txt) <= 26 else head_txt
            r1.font.size = Pt(12); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if tail_txt and len(tail_txt) <= 48:
                p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.CENTER
                r2 = p2.add_run(); r2.text = tail_txt; r2.font.size = Pt(10); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def _parse_swimlane_items(items: List[str]) -> List[tuple[str, str]]:
        out: List[tuple[str, str]] = []
        for raw in (items or []):
            t = (raw or "").strip()
            if ":" in t:
                left, right = t.split(":", 1)
                role = left.strip()
                desc = right.strip()
                if role and desc:
                    out.append((role, desc))
                    continue
            out.append(("", t))
        return out

    def _render_process_swimlane(title_txt: str, items: List[str]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Swimlane Process", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        n = min(6, max(4, len(items)))
        pairs = _parse_swimlane_items(items[:n])
        roles = _extract_swimlane_roles([f"{r}: {d}" if r else d for r, d in pairs])
        if not roles:
            roles = ["Team A", "Team B"]
        lanes = min(3, max(2, len(roles)))
        roles = roles[:lanes]

        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        cont_y = 2.05
        cont_h = 5.05
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        header_w = 1.55
        grid_x = cont_x + header_w
        grid_w = cont_w - header_w - 0.35
        grid_y = cont_y + 0.35
        grid_h = cont_h - 0.70
        lane_h = grid_h / lanes
        col_w = grid_w / n
        STEP_COLS = _step_colors()

        for li, role in enumerate(roles):
            y0 = grid_y + li * lane_h
            lane_bg = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(cont_x + 0.35), Inches(y0), Inches(cont_w - 0.70), Inches(lane_h))
            lane_bg.fill.solid(); lane_bg.fill.fore_color.rgb = RGBColor(*_shade(pal.get("bg", (255, 255, 255)), 6 if li % 2 == 0 else 10))
            lane_bg.line.fill.background()
            _add_textbox(s, cont_x + 0.45, y0 + 0.12, header_w - 0.15, lane_h - 0.24, role, font_size_pt=12, bold=True, align="left", color=pal.get("text", (24, 24, 27)), word_wrap=True, auto_fit=True)
            if li > 0:
                div = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(cont_x + 0.35), Inches(y0), Inches(cont_x + cont_w - 0.35), Inches(y0))
                div.line.color.rgb = RGBColor(209, 213, 219)
                div.line.width = Pt(1)

        for si, (role, desc) in enumerate(pairs):
            lane_idx = 0
            if role:
                try:
                    lane_idx = roles.index(role)
                except ValueError:
                    lane_idx = 0
            x = grid_x + si * col_w + 0.10
            y = grid_y + lane_idx * lane_h + 0.18
            w = max(0.8, col_w - 0.20)
            h = lane_h - 0.36
            col = STEP_COLS[si % len(STEP_COLS)]
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            card.line.color.rgb = RGBColor(*col)
            card.line.width = Pt(1)
            tf = s.shapes.add_textbox(Inches(x + 0.14), Inches(y + 0.10), Inches(w - 0.28), Inches(h - 0.20)).text_frame
            tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            r = p.add_run(); r.text = f"{si + 1}. {desc}"; r.font.size = Pt(11); r.font.bold = True; r.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))

    def _render_process_icon_led_horizontal(title_txt: str, items: List[str]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Workflow", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        n = min(6, max(4, len(items)))
        arr = items[:n]
        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        cont_y = 2.15
        cont_h = 4.85
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        pad_x = 0.55
        center_y = cont_y + cont_h / 2 - 0.15
        gap = (cont_w - 2 * pad_x) / (n - 1)
        STEP_COLS = _step_colors()
        base_line = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(cont_x + pad_x), Inches(center_y), Inches(cont_x + cont_w - pad_x), Inches(center_y))
        base_line.line.color.rgb = RGBColor(209, 213, 219)
        base_line.line.width = Pt(2)

        for idx, raw in enumerate(arr):
            x = cont_x + pad_x + idx * gap
            col = STEP_COLS[idx % len(STEP_COLS)]
            ico = s.shapes.add_shape(MsoShape.OVAL, Inches(x - 0.30), Inches(center_y - 0.30), Inches(0.60), Inches(0.60))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(*col)
            ico.line.fill.background()
            tfc = ico.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
            rc = pc.add_run(); rc.text = str(idx + 1); rc.font.size = Pt(16); rc.font.bold = True; rc.font.color.rgb = RGBColor(*_contrast_text(col))

            head, tail = _split_head_tail(raw)
            head_txt = (head or raw).strip()
            tail_txt = (tail or "").strip()
            _add_textbox(s, x - 0.95, center_y + 0.40, 1.90, 0.65, head_txt, font_size_pt=13 if len(head_txt) <= 18 else 11, bold=True, align="center", color=pal.get("text", (24, 24, 27)), word_wrap=True, auto_fit=True)
            if tail_txt and len(tail_txt) <= 52:
                _add_textbox(s, x - 0.95, center_y + 0.92, 1.90, 0.65, tail_txt, font_size_pt=10, bold=False, align="center", color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)

    def _render_process_milestone_gate(title_txt: str, items: List[str]) -> None:
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255, 255, 255)))
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title_txt or "Milestone Gates", font_size_pt=32, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)

        n = min(6, max(4, len(items)))
        arr = items[:n]
        cont_x = 0.7
        cont_w = SLIDE_W_IN - 1.4
        cont_y = 2.15
        cont_h = 4.85
        cont = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(cont_x), Inches(cont_y), Inches(cont_w), Inches(cont_h))
        cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", (240, 245, 255))))
        cont.line.color.rgb = RGBColor(229, 231, 235)
        try:
            cont.shadow.inherit = False; cont.shadow.visible = True; cont.shadow.blur = 14; cont.shadow.distance = 3
        except Exception:
            pass

        pad_x = 0.55
        mid_y = cont_y + cont_h / 2
        step_w = 1.55
        gate_w = 0.55
        avail = cont_w - 2 * pad_x
        total = n * step_w + (n - 1) * gate_w
        scale = min(1.0, avail / total) if total > 0 else 1.0
        step_w *= scale
        gate_w *= scale
        total = n * step_w + (n - 1) * gate_w

        STEP_COLS = _step_colors()
        x = cont_x + pad_x + max(0.0, (avail - total) / 2)
        for idx, raw in enumerate(arr):
            col = STEP_COLS[idx % len(STEP_COLS)]
            box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(mid_y - 0.55), Inches(step_w), Inches(1.10))
            box.fill.solid(); box.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            box.line.color.rgb = RGBColor(*col)
            box.line.width = Pt(1.25)

            head, tail = _split_head_tail(raw)
            head_txt = (head or raw).strip()
            tail_txt = (tail or "").strip()
            tf = s.shapes.add_textbox(Inches(x + 0.12), Inches(mid_y - 0.48), Inches(step_w - 0.24), Inches(0.96)).text_frame
            tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tf.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.CENTER
            r1 = p.add_run(); r1.text = f"{idx + 1}. {head_txt}" if len(head_txt) <= 22 else head_txt
            r1.font.size = Pt(11); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if tail_txt and len(tail_txt) <= 44:
                p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.CENTER
                r2 = p2.add_run(); r2.text = tail_txt; r2.font.size = Pt(9); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

            x_next = x + step_w
            if idx < n - 1:
                conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(x_next - 0.05), Inches(mid_y), Inches(x_next + gate_w + 0.05), Inches(mid_y))
                conn.line.color.rgb = RGBColor(209, 213, 219)
                conn.line.width = Pt(2)

                gate = s.shapes.add_shape(MsoShape.DIAMOND, Inches(x_next), Inches(mid_y - 0.28), Inches(gate_w), Inches(0.56))
                gate.fill.solid(); gate.fill.fore_color.rgb = RGBColor(*_shade(col, 18))
                gate.line.color.rgb = RGBColor(*col)
                gate.line.width = Pt(1)
                tfc = gate.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
                rc = pc.add_run(); rc.text = "✓"; rc.font.size = Pt(14); rc.font.bold = True; rc.font.color.rgb = RGBColor(*_contrast_text(_shade(col, 18)))

                x = x_next + gate_w
            else:
                x = x_next

    def _render_process_workflow(title_txt: str, items: List[str], *, forced: str | None = None) -> None:
        blob = _process_blob(title_txt, items)
        n = len(items)
        parts = [_split_head_tail(x) for x in (items or [])]
        lens = [len(((tail or head) or "").strip()) for head, tail in parts]
        max_len = max(lens) if lens else 0
        avg_len = (sum(lens) / len(lens)) if lens else 0.0

        governance = any(k in blob for k in [
            "approve", "approval", "sign-off", "sign off", "compliance", "audit", "control",
            "gate", "gating", "checkpoint", "validation", "readiness", "go/no-go", "go no-go", "review",
        ])
        cyclical = any(k in blob for k in [
            "cycle", "cyclical", "continuous", "ongoing", "recurring", "repeat", "iteration", "iterate", "loop", "feedback",
        ])
        instructional = any(k in blob for k in [
            "how to", "how it works", "step-by-step", "step by step", "instructions", "guide", "tutorial",
            "setup", "configure", "install", "onboarding",
        ])
        exec_tone = any(k in blob for k in [
            "overview", "executive", "high-level", "high level", "operating model", "summary",
        ])
        saas_tone = any(k in blob for k in [
            "saas", "product", "platform", "app", "workflow", "automation",
        ])
        roles = _extract_swimlane_roles(items)
        long_desc = (max_len >= 80) or (avg_len >= 55) or any(len(str(x)) >= 110 for x in (items or []))

        eligible: List[str] = ["CHEVRON"]
        if governance and 4 <= n <= 6:
            eligible.append("GATE")
        if cyclical and 4 <= n <= 6:
            eligible.append("CYCLE")
        if len(roles) >= 2 and n <= 6:
            eligible.append("SWIMLANE")
        if long_desc or n >= 7:
            eligible.append("VERTICAL")
        if instructional:
            eligible.append("CARDS")
        if saas_tone and max_len <= 36:
            eligible.append("ICONS")
        if exec_tone and max_len <= 60:
            eligible.append("BANDS")

        forced_norm = str(forced or "").strip().upper() or None
        chosen = choose_variant("process_workflow", eligible, forced=forced_norm)
        _debug_variant(family="process_workflow", chosen=chosen, eligible=eligible, forced=forced_norm)

        v = chosen
        if v == "GATE":
            _render_process_milestone_gate(title_txt, items)
            return
        if v == "CYCLE":
            _render_process_cycle(title_txt, items)
            return
        if v == "SWIMLANE":
            _render_process_swimlane(title_txt, items)
            return
        if v == "ICONS":
            _render_process_icon_led_horizontal(title_txt, items)
            return
        if v == "CARDS":
            _render_process_numbered_cards(title_txt, items)
            return
        if v == "VERTICAL":
            _render_process_vertical_timeline(title_txt, items)
            return
        if v == "BANDS":
            _render_process_phase_bands(title_txt, items)
            return
        render_timeline_horizontal(title_txt or "Process", items[:6], topic_src=title_txt)

    def _render_process_flow_chevron_infographic(sl) -> None:
        title, bullets, content = _infographic_items(sl)
        steps = [str(x).strip() for x in (bullets or []) if str(x).strip()]
        if len(steps) < 4:
            steps = (steps + _synthesize_topic_bullets(title))[:4]
        variant_raw = str(content.get("process_variant") or content.get("variant") or "").strip().upper()
        variant_map = {
            "CHEVRON": "CHEVRON",
            "BANDS": "BANDS",
            "ICONS": "ICONS",
            "CARDS": "CARDS",
            "VERTICAL": "VERTICAL",
            "CYCLE": "CYCLE",
            "GATE": "GATE",
            "SWIMLANE": "SWIMLANE",
        }
        forced = variant_map.get(variant_raw)
        _render_process_workflow(title or "Process", steps, forced=forced)

    def _render_risk_matrix_advanced(sl) -> None:
        title, bullets, content = _infographic_items(sl)
        risks = [str(x).strip() for x in (bullets or []) if str(x).strip()]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title or "Risk Matrix", font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        rows, cols = 3, 4
        x_labels = content.get("x_labels") or ["Low", "Moderate", "High", "Critical"]
        y_labels = content.get("y_labels") or ["Rare", "Possible", "Likely"]

        grid_x, grid_y, grid_w, grid_h = 2.4, 2.3, 6.4, 3.3
        cell_w = grid_w / cols
        cell_h = grid_h / rows

        col_scale = [
            (34, 197, 94),
            (234, 179, 8),
            (249, 115, 22),
            (239, 68, 68),
        ]

        for r in range(rows):
            for c_idx in range(cols):
                x = grid_x + c_idx * cell_w
                y = grid_y + (rows - 1 - r) * cell_h
                col_rgb = col_scale[c_idx]
                rect = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(cell_h))
                rect.fill.solid(); rect.fill.fore_color.rgb = RGBColor(*col_rgb)
                rect.line.color.rgb = RGBColor(255, 255, 255)
        _draw_table_grid_rect(s, grid_x, grid_y, grid_w, grid_h, rows, cols, rgb=(209, 213, 219), weight_pt=0.75)

        for c_idx in range(cols):
            lab = str(x_labels[c_idx] if c_idx < len(x_labels) else "")
            _add_textbox(s, grid_x + c_idx * cell_w, grid_y + grid_h + 0.10, cell_w, 0.40, lab, font_size_pt=14, bold=True, align="center", color=pal.get("text", (24, 24, 27)))
        _add_textbox(s, grid_x, grid_y + grid_h + 0.60, grid_w, 0.40, "Impact", font_size_pt=14, bold=True, align="center", color=pal.get("muted", pal.get("text", (71, 85, 105))))

        for r in range(rows):
            lab = str(y_labels[rows - 1 - r] if r < len(y_labels) else "")
            _add_textbox(s, grid_x - 1.05, grid_y + r * cell_h, 1.0, 0.40, lab, font_size_pt=14, bold=True, align="right", color=pal.get("text", (24, 24, 27)))
        _add_textbox(s, grid_x - 1.45, grid_y - 0.10, 1.40, 0.60, "Likelihood", font_size_pt=14, bold=True, align="right", color=pal.get("muted", pal.get("text", (71, 85, 105))))

        if risks:
            panel_x = grid_x + grid_w + 0.45
            panel_w = max(2.0, SLIDE_W_IN - 1.0 - panel_x)
            panel_y = grid_y
            panel_h = grid_h
            _add_bullets(
                s,
                panel_x,
                panel_y,
                panel_w,
                panel_h,
                risks[:6],
                font_size_pt=14,
                min_items=0,
                color=pal.get("text", (24, 24, 27)),
                word_wrap=True,
                auto_fit=True,
                line_spacing=1.20,
                bold_first_phrase=False,
                space_before_pt=0,
                space_after_pt=6,
            )

    def _render_cyclic_optimization_loop(sl) -> None:
        title, bullets, content = _infographic_items(sl)
        stages = bullets or _synthesize_topic_bullets(title)[:6]
        n = max(4, min(6, len(stages)))
        stages = stages[:n]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title or "Continuous Optimization", font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        center_lbl = (content.get("center_label") or "Continuous Improvement").strip() or "Continuous Improvement"
        # Center the loop below the title and keep comfortable top/bottom margins
        cx, cy = SLIDE_W_IN / 2.0, 4.65
        center_d = 1.8
        center = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - center_d / 2), Inches(cy - center_d / 2), Inches(center_d), Inches(center_d))
        center.fill.solid(); center.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal.get("bg", (240, 245, 255))))
        center.line.color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
        tfc = center.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        rc = pc.add_run(); rc.text = center_lbl; rc.font.size = Pt(14); rc.font.bold = True; rc.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))

        # Subtle orbit ring to anchor the loop visually
        ring_outer_d = 5.0
        ring_inner_d = 4.1
        outer = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ring_outer_d / 2), Inches(cy - ring_outer_d / 2), Inches(ring_outer_d), Inches(ring_outer_d))
        outer.fill.solid(); outer.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
        outer.line.fill.background()
        inner = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ring_inner_d / 2), Inches(cy - ring_inner_d / 2), Inches(ring_inner_d), Inches(ring_inner_d))
        inner.fill.solid(); inner.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
        inner.line.fill.background()
        # Place stage nodes near the orbit edge
        radius = 2.05
        node_d = 1.35
        step_cols = _step_colors()

        pts: List[Tuple[float, float]] = []
        for i in range(n):
            ang = 2.0 * math.pi * i / n
            pts.append((cx + radius * float(math.cos(ang)), cy + radius * float(math.sin(ang))))

        # Draw connectors first (behind nodes) and attach to circle edges (not center)
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            vx = x1 - x0
            vy = y1 - y0
            mag = max(0.001, float((vx * vx + vy * vy) ** 0.5))
            ux = vx / mag
            uy = vy / mag
            pad = node_d / 2 + 0.06
            sx = x0 + ux * pad
            sy = y0 + uy * pad
            ex = x1 - ux * pad
            ey = y1 - uy * pad
            conn = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(sx), Inches(sy), Inches(ex), Inches(ey))
            conn.line.color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
            conn.line.width = Pt(2)

        # Nodes on top
        for i, raw in enumerate(stages, start=0):
            nx, ny = pts[i]
            node = s.shapes.add_shape(MsoShape.OVAL, Inches(nx - node_d / 2), Inches(ny - node_d / 2), Inches(node_d), Inches(node_d))
            col_rgb = step_cols[i % len(step_cols)]
            node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*col_rgb)
            node.line.fill.background()
            try:
                node.shadow.inherit = False; node.shadow.visible = True; node.shadow.blur = 6; node.shadow.distance = 2
            except Exception:
                pass
            head, tail = _split_head_tail(raw)
            label = (head or raw or "").strip()
            # Keep bubble labels concise to avoid overflow
            words = label.split()
            if len(words) > 8:
                label = " ".join(words[:8])
            tfn = node.text_frame; tfn.clear(); tfn.word_wrap = True; tfn.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pn = tfn.paragraphs[0]; pn.alignment = PP_ALIGN.CENTER
            rn = pn.add_run(); rn.text = label; rn.font.size = Pt(11); rn.font.bold = True; rn.font.color.rgb = RGBColor(*_contrast_text(col_rgb))

    def _render_layered_benefits_stack(sl) -> None:
        title, bullets, _ = _infographic_items(sl)
        benefits = bullets or _synthesize_topic_bullets(title)[:6]
        n = max(4, min(6, len(benefits)))
        if len(benefits) < n:
            benefits = (list(benefits) + _synthesize_topic_bullets(title))[:n]
        else:
            benefits = benefits[:n]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title or "Benefits", font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        base_col = pal.get("primary", (59, 130, 246))
        depth_delta = -10

        # Fit stack within slide bounds: between title and bottom margin
        top_y = 2.20
        bottom_margin = 0.55
        gap_y = 0.16
        avail_h = max(2.6, (SLIDE_H_IN - bottom_margin) - top_y)
        layer_h = (avail_h - (n - 1) * gap_y) / n
        if layer_h < 0.72:
            gap_y = 0.12
            layer_h = (avail_h - (n - 1) * gap_y) / n
        layer_h = max(0.70, min(0.92, layer_h))

        shift_x = 0.22
        layer_w = min(9.4, SLIDE_W_IN - 2.0 - (n - 1) * shift_x)
        stack_w = layer_w + (n - 1) * shift_x
        start_x = max(0.7, (SLIDE_W_IN - stack_w) / 2.0)
        start_y = top_y

        title_fs = 18 if layer_h >= 0.86 else 16
        tail_fs = 12 if layer_h >= 0.86 else 11

        for idx, raw in enumerate(reversed(benefits), start=0):
            # Bottom layer rendered first; each higher layer shifted up and right
            x = start_x + idx * shift_x
            y = start_y + (n - 1 - idx) * (layer_h + gap_y)
            col_rgb = _shade(base_col, depth_delta * idx)
            layer = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(layer_w), Inches(layer_h))
            layer.fill.solid(); layer.fill.fore_color.rgb = RGBColor(*col_rgb)
            layer.line.fill.background()
            try:
                layer.shadow.inherit = False; layer.shadow.visible = True; layer.shadow.blur = 10; layer.shadow.distance = 3
            except Exception:
                pass
            head, tail = _split_head_tail(raw)
            tb = s.shapes.add_textbox(Inches(x + 0.45), Inches(y + 0.12), Inches(layer_w - 0.75), Inches(layer_h - 0.24)).text_frame
            tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]; p.clear()
            fg = _contrast_text(col_rgb)
            r1 = p.add_run(); r1.text = head or raw; r1.font.size = Pt(title_fs); r1.font.bold = True; r1.font.color.rgb = RGBColor(*fg)
            if tail:
                p2 = tb.add_paragraph(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(tail_fs); r2.font.color.rgb = RGBColor(235, 244, 255) if fg == (255, 255, 255) else RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def _render_takeaways_steps(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.2, 0.9, 10.8, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        start_y = 1.75
        row_h = 1.08
        gap = 0.16
        num_rgb = pal.get("primary", pal.get("text", (59, 130, 246)))
        for i, raw in enumerate((items or [])[:5], start=1):
            nb = slide.shapes.add_textbox(Inches(1.2), Inches(start_y - 0.10), Inches(0.9), Inches(row_h)).text_frame
            nb.clear(); pnum = nb.paragraphs[0]; pnum.alignment = PP_ALIGN.LEFT
            rnum = pnum.add_run(); rnum.text = f"{i}."; rnum.font.size = Pt(44); rnum.font.bold = True; rnum.font.color.rgb = RGBColor(*num_rgb)

            head, tail = _split_head_tail(str(raw))
            tb = slide.shapes.add_textbox(Inches(2.3), Inches(start_y), Inches(9.7), Inches(row_h - 0.20)).text_frame
            tb.word_wrap = True
            p1 = tb.paragraphs[0]; p1.clear()
            if head:
                r1 = p1.add_run(); r1.text = head; r1.font.size = Pt(22); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal["text"])
            if tail:
                p2 = tb.add_paragraph(); p2.clear()
                r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

            if i < min(5, len(items or [])):
                line_y = start_y + row_h - 0.10
                line = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(1.2), Inches(line_y), Inches(10.8), Inches(0.02))
                line.fill.solid(); line.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal.get("text", (229, 231, 235))))
                line.line.fill.background()

            start_y += row_h + gap

    def _render_takeaways_banner(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=44, bold=True, color=pal["primary"])
        y = 2.35
        h = 0.86
        gap = 0.32
        base_primary = pal.get("primary", (59, 130, 246))
        light_bg = pal.get("secondary", (238, 244, 255))
        dark_text = pal.get("text", (15, 23, 42))
        for i, raw in enumerate((items or [])[:4]):
            row_idx = i + 1
            odd_row = (row_idx % 2 == 1)
            bg_rgb = base_primary if odd_row else light_bg

            pill_x = 1.55
            pill_w = 10.6
            pill = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(pill_x), Inches(y), Inches(pill_w), Inches(h))
            pill.fill.solid(); pill.fill.fore_color.rgb = RGBColor(*bg_rgb)
            pill.line.fill.background()
            try:
                pill.shadow.inherit = False; pill.shadow.visible = True; pill.shadow.blur = 6; pill.shadow.distance = 2
            except Exception:
                pass

            ico_d = 0.86
            ico_x = pill_x - 0.45
            ico_y = y + (h - ico_d) / 2
            ico = slide.shapes.add_shape(MsoShape.OVAL, Inches(ico_x), Inches(ico_y), Inches(ico_d), Inches(ico_d))
            ico.fill.solid()
            if odd_row:
                ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
                ico.line.color.rgb = RGBColor(*base_primary)
                glyph_rgb = base_primary
                head_rgb = (255, 255, 255)
                tail_rgb = (235, 244, 255)
            else:
                ico.fill.fore_color.rgb = RGBColor(*base_primary)
                ico.line.color.rgb = RGBColor(*base_primary)
                glyph_rgb = (255, 255, 255)
                head_rgb = dark_text
                tail_rgb = pal.get("muted", dark_text)
            itf = ico.text_frame; itf.clear(); ip = itf.paragraphs[0]; ip.alignment = PP_ALIGN.CENTER
            ir = ip.add_run(); ir.text = str(row_idx); ir.font.size = Pt(20); ir.font.bold = True; ir.font.color.rgb = RGBColor(*glyph_rgb)

            head, tail = _split_head_tail(str(raw))
            tb = slide.shapes.add_textbox(Inches(pill_x + 0.65), Inches(y + 0.14), Inches(pill_w - 0.90), Inches(h - 0.28)).text_frame
            tb.word_wrap = True
            tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]
            p.clear()
            if head:
                r1 = p.add_run(); r1.text = head + (" " if tail else ""); r1.font.size = Pt(22); r1.font.bold = True; r1.font.color.rgb = RGBColor(*head_rgb)
            if tail:
                r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(15); r2.font.color.rgb = RGBColor(*tail_rgb)
            y += h + gap

    def _render_takeaways_pill_rows(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"], word_wrap=True, auto_fit=True)
        rows = (items or [])[:4]
        if len(rows) < 4:
            rows = (list(rows) + (_synthesize_topic_bullets("Key Takeaways") or []))[:4]

        y = 2.05
        h = 0.95
        gap = 0.26
        x = 1.45
        w = 10.45
        step_cols = _step_colors()
        border_rgb = (60, 60, 60) if not _is_dark_rgb(pal.get("bg", (255, 255, 255))) else (235, 244, 255)

        for i, raw in enumerate(rows, start=1):
            col_rgb = step_cols[(i - 1) % len(step_cols)]
            pill = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
            pill.fill.solid(); pill.fill.fore_color.rgb = RGBColor(*col_rgb)
            pill.line.color.rgb = RGBColor(*border_rgb)
            pill.line.width = Pt(2.5)
            try:
                pill.shadow.inherit = False; pill.shadow.visible = True; pill.shadow.blur = 8; pill.shadow.distance = 2
            except Exception:
                pass

            d = 0.86
            left_side = ((i % 2) == 1)
            if left_side:
                cx = x - 0.55
                tx = x + 0.78
                tw = w - 1.65
                align = PP_ALIGN.LEFT
                icon_x = x + w - 0.68
            else:
                cx = x + w - 0.31
                tx = x + 0.40
                tw = w - 1.65
                align = PP_ALIGN.RIGHT
                icon_x = x + 0.10
            cy = y + (h - d) / 2

            circ = slide.shapes.add_shape(MsoShape.OVAL, Inches(cx), Inches(cy), Inches(d), Inches(d))
            circ.fill.solid(); circ.fill.fore_color.rgb = RGBColor(255, 255, 255)
            circ.line.color.rgb = RGBColor(*_shade(col_rgb, -25))
            circ.line.width = Pt(2)
            tfc = circ.text_frame; tfc.clear(); tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
            rc = pc.add_run(); rc.text = f"{i:02d}"; rc.font.size = Pt(18); rc.font.bold = True; rc.font.color.rgb = RGBColor(*_shade(col_rgb, -35))

            icon = slide.shapes.add_shape(MsoShape.OVAL, Inches(icon_x), Inches(cy + 0.14), Inches(0.56), Inches(0.56))
            icon.fill.solid(); icon.fill.fore_color.rgb = RGBColor(255, 255, 255)
            icon.line.color.rgb = RGBColor(*_shade(col_rgb, -30))
            icon.line.width = Pt(1.5)

            head, tail = _split_head_tail(str(raw))
            tb = slide.shapes.add_textbox(Inches(tx), Inches(y + 0.12), Inches(tw), Inches(h - 0.24)).text_frame
            tb.word_wrap = True
            tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]
            p.clear(); p.alignment = align
            fg = _contrast_text(col_rgb)
            if head:
                r1 = p.add_run(); r1.text = head + (" " if tail else ""); r1.font.size = Pt(20); r1.font.bold = True; r1.font.color.rgb = RGBColor(*fg)
            if tail:
                r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(235, 244, 255) if fg == (255, 255, 255) else RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))
            y += h + gap

        arrow_y = min(7.05, y + 0.02)
        arr = slide.shapes.add_shape(MsoShape.RIGHT_ARROW, Inches(1.2), Inches(arrow_y), Inches(11.0), Inches(0.22))
        arr.fill.solid(); arr.fill.fore_color.rgb = RGBColor(*_shade(pal.get("text", (60, 60, 60)), -10))
        arr.line.fill.background()

    def _render_takeaways_minimal_keys(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "KEY\nTAKEAWAYS", font_size_pt=46, bold=True, color=pal.get("text", pal.get("primary", (59, 130, 246))), word_wrap=True, auto_fit=False)
        rows = (items or [])[:5]
        if len(rows) < 3:
            rows = (list(rows) + (_synthesize_topic_bullets("Key Takeaways") or []))[:3]

        y = 2.70
        h = 0.70
        gap = 0.36
        for raw in rows[:5]:
            ico_d = 0.36
            ico = slide.shapes.add_shape(MsoShape.OVAL, Inches(1.10), Inches(y + 0.05), Inches(ico_d), Inches(ico_d))
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(*pal.get("primary", (251, 188, 5)))
            ico.line.fill.background()
            itf = ico.text_frame; itf.clear(); ip = itf.paragraphs[0]; ip.alignment = PP_ALIGN.CENTER
            ir = ip.add_run(); ir.text = "🔑"; ir.font.size = Pt(12)

            head, tail = _split_head_tail(str(raw))
            tb = slide.shapes.add_textbox(Inches(1.60), Inches(y), Inches(10.3), Inches(h)).text_frame
            tb.word_wrap = True
            tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]; p.clear()
            if head:
                r1 = p.add_run(); r1.text = head + (" " if tail else ""); r1.font.size = Pt(18); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))
            if tail:
                r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))
            y += h + gap

    def _render_takeaways_grid_blocks(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"], word_wrap=True, auto_fit=True)
        rows = (items or [])[:4]
        if len(rows) < 4:
            rows = (list(rows) + (_synthesize_topic_bullets("Key Takeaways") or []))[:4]

        y0 = 2.15
        card_w = 5.85
        card_h = 2.05
        gap_x = 0.55
        gap_y = 0.55
        step_cols = _step_colors()
        card_bg = pal.get("secondary", (245, 248, 255))

        grid_w = card_w * 2 + gap_x
        x0 = max(0.45, (SLIDE_W_IN - grid_w) / 2.0)

        for i, raw in enumerate(rows, start=1):
            r = (i - 1) // 2
            c = (i - 1) % 2
            x = x0 + c * (card_w + gap_x)
            y = y0 + r * (card_h + gap_y)
            card = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*card_bg)
            card.line.color.rgb = RGBColor(*_shade(pal.get("primary", (59, 130, 246)), -10))
            card.line.width = Pt(1.25)
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 8; card.shadow.distance = 2
            except Exception:
                pass

            tile = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x + 0.35), Inches(y + 0.45), Inches(0.95), Inches(0.95))
            col_rgb = step_cols[(i - 1) % len(step_cols)]
            tile.fill.solid(); tile.fill.fore_color.rgb = RGBColor(*col_rgb)
            tile.line.fill.background()
            tft = tile.text_frame; tft.clear(); tft.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pt = tft.paragraphs[0]; pt.alignment = PP_ALIGN.CENTER
            rt = pt.add_run(); rt.text = f"{i:02d}"; rt.font.size = Pt(18); rt.font.bold = True; rt.font.color.rgb = RGBColor(*_contrast_text(col_rgb))

            head, tail = _split_head_tail(str(raw))
            tb = slide.shapes.add_textbox(Inches(x + 1.55), Inches(y + 0.40), Inches(card_w - 1.85), Inches(card_h - 0.80)).text_frame
            tb.word_wrap = True
            tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p1 = tb.paragraphs[0]; p1.clear()
            if head:
                r1 = p1.add_run(); r1.text = head; r1.font.size = Pt(18); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (15, 23, 42)))
            if tail:
                p2 = tb.add_paragraph(); p2.clear()
                r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(13); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def _render_takeaways_focus_rows(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"], word_wrap=True, auto_fit=True)
        rows = (items or [])[:4]
        if len(rows) < 4:
            rows = (list(rows) + (_synthesize_topic_bullets("Key Takeaways") or []))[:4]

        y = 2.05
        h = 1.05
        gap = 0.28
        x = 1.10
        left_w = 3.25
        mid_w = 7.15
        step_cols = _step_colors()

        for i, raw in enumerate(rows, start=1):
            col_rgb = step_cols[(i - 1) % len(step_cols)]
            head, tail = _split_head_tail(str(raw))
            label = (head or f"Focus Area {i}").strip() or f"Focus Area {i}"
            desc = (tail or (head or str(raw))).strip() or str(raw)

            pill = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(left_w), Inches(h))
            pill.fill.solid(); pill.fill.fore_color.rgb = RGBColor(*col_rgb)
            pill.line.fill.background()
            try:
                pill.shadow.inherit = False; pill.shadow.visible = True; pill.shadow.blur = 6; pill.shadow.distance = 2
            except Exception:
                pass
            tfl = pill.text_frame; tfl.clear(); tfl.word_wrap = True; tfl.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pl = tfl.paragraphs[0]; pl.alignment = PP_ALIGN.CENTER
            rl = pl.add_run(); rl.text = label; rl.font.size = Pt(16); rl.font.bold = True; rl.font.color.rgb = RGBColor(*_contrast_text(col_rgb))

            box = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x + left_w + 0.25), Inches(y), Inches(mid_w), Inches(h))
            box.fill.solid(); box.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            box.line.color.rgb = RGBColor(*_shade(pal.get("secondary", pal.get("text", (229, 231, 235))), -15))
            box.line.width = Pt(1.0)

            tfd = slide.shapes.add_textbox(Inches(x + left_w + 0.55), Inches(y + 0.18), Inches(mid_w - 0.70), Inches(h - 0.36)).text_frame
            tfd.word_wrap = True; tfd.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pd = tfd.paragraphs[0]; pd.clear(); pd.alignment = PP_ALIGN.LEFT
            rd = pd.add_run(); rd.text = desc; rd.font.size = Pt(15); rd.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

            num = slide.shapes.add_textbox(Inches(x + left_w + mid_w + 0.55), Inches(y - 0.05), Inches(1.0), Inches(h + 0.10)).text_frame
            num.clear(); pn = num.paragraphs[0]; pn.alignment = PP_ALIGN.CENTER
            rn = pn.add_run(); rn.text = str(i); rn.font.size = Pt(56); rn.font.bold = True; rn.font.color.rgb = RGBColor(*col_rgb)

            y += h + gap

    def render_infographic(sl):
        t = _ctype(sl)
        title, bullets, content = _infographic_items(sl)
        intent = _infer_infographic_intent(title, content)
        count = len(bullets)

        try:
            if t == "key_takeaways" or ("key takeaways" in str(title or "").strip().lower()):
                _mark_takeaways_rendered()
        except Exception:
            pass

        def _render_takeaways_rotated() -> None:
            items = [str(x).strip() for x in (bullets or []) if str(x).strip()]
            if len(items) < 3:
                items = (_synthesize_topic_bullets(title) or [])[:6]
            items = items[:8]

            raw = str((content or {}).get("takeaways_variant") or (content or {}).get("variant") or "").strip().lower()
            forced_map = {
                "stack": "pill_rows",
                "layered": "pill_rows",
                "layered_stack": "pill_rows",
                "steps": "minimal_keys",
                "numbered": "pill_rows",
                "list": "minimal_keys",
                "banner": "pill_rows",
                "pill": "pill_rows",
                "pills": "pill_rows",
                "bar": "pill_rows",
                "bars": "pill_rows",
                "pill_rows": "pill_rows",
                "pill-rows": "pill_rows",
                "rows": "pill_rows",
                "minimal": "minimal_keys",
                "minimal_keys": "minimal_keys",
                "minimal-keys": "minimal_keys",
                "keys": "minimal_keys",
                "grid": "grid_blocks",
                "grid_blocks": "grid_blocks",
                "grid-blocks": "grid_blocks",
                "blocks": "grid_blocks",
                "focus": "focus_rows",
                "focus_rows": "focus_rows",
                "focus-rows": "focus_rows",
                "focus_area": "focus_rows",
                "focus-area": "focus_rows",
            }
            forced = forced_map.get(raw)

            eligible = ["pill_rows", "minimal_keys", "grid_blocks", "focus_rows"]
            chosen = forced if forced in eligible else eligible[int(takeaways_style_idx) % len(eligible)]
            _debug_variant(family="takeaways", chosen=chosen, eligible=eligible, forced=forced)

            s = prs.slides.add_slide(blank)
            apply_bg(s)
            if chosen == "minimal_keys":
                _render_takeaways_minimal_keys(s, items, pal=pal)
            elif chosen == "grid_blocks":
                _render_takeaways_grid_blocks(s, items, pal=pal)
            elif chosen == "focus_rows":
                _render_takeaways_focus_rows(s, items, pal=pal)
            else:
                _render_takeaways_pill_rows(s, items, pal=pal)

        def _render_impact_map_rotated(*, allow_simple: bool) -> None:
            roles = _extract_swimlane_roles([str(x) for x in (bullets or [])])
            has_roles = len(roles) >= 2

            raw_variant = str((content or {}).get("impact_map_variant") or (content or {}).get("variant") or "").strip().lower()
            variant_map = {
                "ring": "circular_ring",
                "circular": "circular_ring",
                "circular_ring": "circular_ring",
                "circular-ring": "circular_ring",
                "classic": "lr_boxes",
                "radial": "classic_circles",
                "pyramid": "orbit",
                "two_sided": "lr_boxes",
                "two-sided": "lr_boxes",
                "before_after": "lr_boxes",
                "before-after": "lr_boxes",
                "swimlane": "lr_boxes",
                "hub_spoke": "classic_circles",
                "hub-spoke": "classic_circles",
                "hub_spoke_classic": "classic_circles",
                "hub-spoke-classic": "classic_circles",
                "hub_spoke_classic_circles": "classic_circles",
                "hub-spoke-classic-circles": "classic_circles",
                "hub_spoke_lr": "lr_boxes",
                "hub-spoke-lr": "lr_boxes",
                "hub_spoke_lr_boxes": "lr_boxes",
                "hub-spoke-lr-boxes": "lr_boxes",
                "hub_spoke_circular_ring": "circular_ring",
                "hub-spoke-circular-ring": "circular_ring",
                "hub_spoke_orbit": "orbit",
                "hub-spoke-orbit": "orbit",
                "side_by_side": "side_by_side_6",
                "side-by-side": "side_by_side_6",
                "side_by_side_6": "side_by_side_6",
                "side-by-side-6": "side_by_side_6",
                "side_by_side_infographic": "side_by_side_infographic_6",
                "side-by-side-infographic": "side_by_side_infographic_6",
                "side_by_side_infographic_6": "side_by_side_infographic_6",
                "side-by-side-infographic-6": "side_by_side_infographic_6",
                "infographic_side_by_side": "side_by_side_infographic_6",
                "infographic-side-by-side": "side_by_side_infographic_6",
                "side": "side_by_side_6",
                "side6": "side_by_side_6",
                "spokes": "classic_circles",
            }
            forced = variant_map.get(raw_variant)

            eligible: List[str] = ["classic_circles", "circular_ring", "lr_boxes", "orbit", "side_by_side_6", "side_by_side_infographic_6"]
            if forced and forced not in eligible:
                eligible.append(forced)

            chosen = choose_variant("impact_map", eligible, forced=forced)
            _debug_variant(family="impact_map", chosen=chosen, eligible=eligible, forced=forced)

            # Maintain legacy index advancement for compatibility/tests.
            global IMPACT_MAP_VARIANT_INDEX
            if 'IMPACT_MAP_VARIANT_INDEX' not in globals():
                IMPACT_MAP_VARIANT_INDEX = 0
            IMPACT_MAP_VARIANT_INDEX += 1

            if chosen == "circular_ring":
                _render_hub_spoke_circular_ring(sl)
            elif chosen == "lr_boxes":
                _render_hub_spoke_lr_boxes(sl)
            elif chosen == "orbit":
                _render_hub_spoke_orbit(sl)
            elif chosen == "side_by_side_6":
                _render_hub_spoke_side_by_side_6(sl)
            elif chosen == "side_by_side_infographic_6":
                _render_hub_spoke_side_by_side_infographic_6(sl)
            else:
                _render_hub_spoke_classic_circles(sl)

        # Deterministic selection rules based on intent and density
        if t == "key_takeaways" or ("key takeaways" in str(title or "").strip().lower()):
            _render_takeaways_rotated()
            return

        if intent == "GANTT" and count >= 4:
            _render_gantt_chart(sl)
            return
        if intent == "RISK":
            _render_risk_matrix_advanced(sl)
            return
        if intent == "PROCESS" and count >= 4:
            _render_process_flow_chevron_infographic(sl)
            return
        if intent in {"OPTIMIZATION", "CONTINUOUS"} and count >= 4:
            _render_cyclic_optimization_loop(sl)
            return
        if intent == "BENEFITS" and count >= 4:
            _render_layered_benefits_stack(sl)
            return
        if intent == "IMPACT":
            _render_impact_map_rotated(allow_simple=(count <= 4))
            return

        # Fallbacks: use simple impact map for general infographics or treat
        # key_takeaways as a compact layered stack.
        if t == "key_takeaways" and count >= 3:
            _render_layered_benefits_stack(sl)
        else:
            _render_impact_map_rotated(allow_simple=True)

    def render_thanks(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 2.8, 11.3, 1.2, "Thank You", font_size_pt=40, bold=True, align="center", color=TITLE_COL)
        cta = (sl.get("cta") or _content(sl).get("cta") or "").strip()
        if cta:
            _add_textbox(s, 3.0, 4.2, 7.3, 0.8, cta, font_size_pt=18, align="center", color=pal["text"])

    def render_bullets(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        title = (sl.get("title") or "Slide").strip() or "Slide"
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)
        c = _content(sl)
        bullets = [str(x).strip() for x in (c.get("bullets") or []) if str(x).strip()]
        if not bullets:
            bullets = _synthesize_topic_bullets(title)[:4]
        _add_bullets(s, 1.0, 2.0, 11.0, 4.5, bullets, font_size_pt=20, color=pal["text"], word_wrap=True, auto_fit=True)

    # Pre-pass: avoid empty bullets only if we are not in render_exact mode
    if not render_exact:
        for sl in slides_norm:
            if _ctype(sl) == "bullets":
                c = _content(sl)
                bs = [str(x).strip() for x in (c.get("bullets") or []) if str(x).strip()]
                if not bs:
                    c["bullets"] = _synthesize_topic_bullets(sl.get("title") or "")
                    sl["content"] = c

    # Deck-level bullet style selection for double-column slides
    global DOUBLE_BULLET_STYLE_INDEX
    if 'DOUBLE_BULLET_STYLE_INDEX' not in globals():
        DOUBLE_BULLET_STYLE_INDEX = 0
    global CURRENT_DOUBLE_BULLET_STYLE
    CURRENT_DOUBLE_BULLET_STYLE = DOUBLE_BULLET_STYLE_INDEX % 4

    # Wrapper functions to transform AI content structures
    def _render_roadmap_wrapper(sl):
        # Transform AI timeline structure to expected phases structure
        content = _content(sl)
        if "timeline" in content:
            sl["content"]["phases"] = content["timeline"]
        _render_roadmap(sl)
    
    def _render_swot_wrapper(sl):
        content = _content(sl)
        bullets: List[str] = []
        try:
            has_structured = False
            for k in ["strengths", "weaknesses", "opportunities", "threats"]:
                q = content.get(k)
                if isinstance(q, dict) and isinstance(q.get("points"), list) and any(str(x).strip() for x in q.get("points") or []):
                    has_structured = True
                    break
            if has_structured:
                bullets = []
        except Exception:
            pass

        if "bullets" in content:
            bullets = content.get("bullets", [])
        if not isinstance(bullets, list):
            bullets = []

        if bullets and any(str(x).strip() for x in bullets):
            quadrant_data = {
                "strengths": {"points": []},
                "weaknesses": {"points": []},
                "opportunities": {"points": []},
                "threats": {"points": []},
            }

            for bullet in bullets:
                bullet_txt = str(bullet or "").strip()
                if not bullet_txt:
                    continue
                bullet_lower = bullet_txt.lower()
                if "strengths:" in bullet_lower:
                    quadrant_data["strengths"]["points"].append(bullet_txt.split(":", 1)[1].strip())
                elif "weaknesses:" in bullet_lower:
                    quadrant_data["weaknesses"]["points"].append(bullet_txt.split(":", 1)[1].strip())
                elif "opportunities:" in bullet_lower:
                    quadrant_data["opportunities"]["points"].append(bullet_txt.split(":", 1)[1].strip())
                elif "threats:" in bullet_lower:
                    quadrant_data["threats"]["points"].append(bullet_txt.split(":", 1)[1].strip())

            for key, data in quadrant_data.items():
                sl["content"][key] = data

        def _render_swot_banded(sl2) -> None:
            content2 = _content(sl2)
            title2 = content2.get("title", "SWOT Analysis")
            s2 = prs.slides.add_slide(blank); apply_bg(s2)
            _add_textbox(s2, 1.0, 0.9, 11.3, 0.9, title2, font_size_pt=32, bold=True, color=TITLE_COL)

            margin_x = 1.0
            top_y = 2.05
            bottom_margin = 0.65
            gap_x, gap_y = 0.55, 0.45
            avail_w = max(6.0, SLIDE_W_IN - 2 * margin_x)
            avail_h = max(3.6, SLIDE_H_IN - bottom_margin - top_y)
            cell_w = max(3.0, (avail_w - gap_x) / 2.0)
            cell_h = max(1.6, (avail_h - gap_y) / 2.0)
            start_x, start_y = margin_x, top_y

            quadrants2 = [
                ("strengths", "Strengths", 0, 0),
                ("weaknesses", "Weaknesses", 1, 0),
                ("opportunities", "Opportunities", 0, 1),
                ("threats", "Threats", 1, 1),
            ]

            for key2, label2, col_idx, row_idx in quadrants2:
                x = start_x + col_idx * (cell_w + gap_x)
                y = start_y + row_idx * (cell_h + gap_y)
                quadrant_data2 = content2.get(key2, {})
                points2 = quadrant_data2.get("points", [])

                quad_box = s2.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(cell_h))
                quad_box.fill.solid(); quad_box.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
                quad_box.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))

                band_h = 0.48
                band = s2.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(band_h))
                band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                band.line.fill.background()
                _add_textbox(
                    s2,
                    x + 0.18,
                    y + 0.08,
                    cell_w - 0.36,
                    band_h - 0.12,
                    label2,
                    font_size_pt=16,
                    bold=True,
                    color=(255, 255, 255),
                    word_wrap=True,
                    auto_fit=True,
                )

                if points2:
                    bullet_text = "\n".join(f"• {str(p)[:70]}" for p in points2[:6])
                    _add_textbox(
                        s2,
                        x + 0.18,
                        y + band_h + 0.12,
                        cell_w - 0.36,
                        max(0.4, cell_h - band_h - 0.26),
                        bullet_text,
                        font_size_pt=13,
                        color=pal["text"],
                        word_wrap=True,
                        auto_fit=True,
                    )

        variant_raw = str(content.get("swot_variant") or content.get("variant") or "").strip().lower()
        variant_map = {
            "classic": "classic",
            "banded": "banded",
            "header_bands": "banded",
            "headers": "banded",
        }
        forced = variant_map.get(variant_raw)
        eligible = ["classic", "banded"]
        chosen = choose_variant("swot", eligible, forced=forced)
        _debug_variant(family="swot", chosen=chosen, eligible=eligible, forced=forced)

        if chosen == "banded":
            _render_swot_banded(sl)
        else:
            _render_swot(sl)
    
    def _render_kpi_wrapper(sl):
        content = _content(sl)
        variant_raw = str(content.get("kpi_variant") or content.get("variant") or "").strip().lower()
        variant_map = {
            "grid": "grid",
            "2x2": "grid",
            "grid_2x2": "grid",
            "strip": "strip",
            "horizontal": "strip",
            "list": "list",
            "stacked": "list",
        }
        forced = variant_map.get(variant_raw)

        metrics = content.get("metrics") or []
        n = len(metrics) if isinstance(metrics, list) else 0
        eligible = ["grid", "strip", "list"]
        if n < 4:
            eligible = ["grid"]

        chosen = choose_variant("kpi", eligible, forced=forced)
        _debug_variant(family="kpi", chosen=chosen, eligible=eligible, forced=forced)

        if chosen == "strip":
            _render_kpi_strip(sl)
        elif chosen == "list":
            _render_kpi_list(sl)
        else:
            _render_kpi(sl)

    def _render_kpi_strip(sl) -> None:
        content = _content(sl)
        title = content.get("title", "Key Metrics")
        metrics = content.get("metrics") or []
        if not isinstance(metrics, list):
            metrics = []
        metrics = metrics[:4]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)

        margin_x = 0.8
        gap_x = 0.35
        y = 2.3
        h = 4.65
        card_w = (SLIDE_W_IN - 2 * margin_x - 3 * gap_x) / 4.0
        card_h = min(2.25, h)
        val_fs = 30
        for i, m in enumerate(metrics):
            x = margin_x + i * (card_w + gap_x)
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            card.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 8; card.shadow.distance = 2
            except Exception:
                pass

            label = str((m or {}).get("label") or "")[:40]
            value = str((m or {}).get("value") or "")[:25]
            caption = str((m or {}).get("caption") or "")[:80]

            _add_textbox(s, x + 0.18, y + 0.18, card_w - 0.36, 0.45, label, font_size_pt=12, bold=True, color=pal.get("text", (24, 24, 27)), word_wrap=True, auto_fit=True)
            _add_textbox(s, x + 0.18, y + 0.68, card_w - 0.36, 0.70, value, font_size_pt=val_fs, bold=True, color=pal.get("primary", pal.get("text", (59, 130, 246))), word_wrap=True, auto_fit=True)
            if caption:
                _add_textbox(s, x + 0.18, y + 1.45, card_w - 0.36, card_h - 1.60, caption, font_size_pt=10, bold=False, color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)

    def _render_kpi_list(sl) -> None:
        content = _content(sl)
        title = content.get("title", "Key Metrics")
        metrics = content.get("metrics") or []
        if not isinstance(metrics, list):
            metrics = []
        metrics = metrics[:6]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)

        x = 1.0
        y = 2.2
        w = SLIDE_W_IN - 2.0
        row_h = 0.95
        gap = 0.22
        accent = pal.get("primary", (59, 130, 246))
        for i, m in enumerate(metrics[:5]):
            yy = y + i * (row_h + gap)
            row = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(yy), Inches(w), Inches(row_h))
            row.fill.solid(); row.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            row.line.color.rgb = RGBColor(*accent)
            try:
                row.shadow.inherit = False; row.shadow.visible = True; row.shadow.blur = 6; row.shadow.distance = 2
            except Exception:
                pass

            label = str((m or {}).get("label") or "")[:60]
            value = str((m or {}).get("value") or "")[:30]
            caption = str((m or {}).get("caption") or "")[:120]

            _add_textbox(s, x + 0.30, yy + 0.18, w * 0.55, 0.60, label, font_size_pt=16, bold=True, color=pal.get("text", (24, 24, 27)), word_wrap=True, auto_fit=True)
            _add_textbox(s, x + w * 0.70, yy + 0.16, w * 0.28, 0.65, value, font_size_pt=24, bold=True, align="right", color=accent, word_wrap=True, auto_fit=True)
            if caption:
                _add_textbox(s, x + 0.30, yy + 0.58, w * 0.62, 0.35, caption, font_size_pt=10, bold=False, color=pal.get("muted", pal.get("text", (71, 85, 105))), word_wrap=True, auto_fit=True)
    
    def _render_pricing_wrapper(sl):
        # Pricing table structure should work as-is
        _render_pricing(sl)
    
    def _render_risk_matrix_wrapper(sl):
        # Risk matrix table structure should work as-is  
        _render_risk_matrix(sl)
    
    def _render_before_after_wrapper(sl):
        _render_before_after(sl)
    
    def _render_architecture_wrapper(sl):
        _render_architecture(sl)

    def _render_impact_effort_matrix_wrapper(sl):
        _render_impact_effort_matrix(sl)

    def _looks_like_impact_effort_cards(sl: Dict[str, Any]) -> bool:
        try:
            if _ctype(sl) not in {"cards", "card", "card_style", "process_timeline_alt"}:
                return False
            title = str(sl.get("title") or "").lower()
            cards_arr = _content(sl).get("cards")
            if not (isinstance(cards_arr, list) and any(str(x).strip() for x in cards_arr)):
                return False
            stages = [str(x).strip() for x in cards_arr if str(x).strip()]
            blob = (title + " " + " ".join(stages)).lower()
            if ("comparison" in title or "compare" in title or "table" in title) and not any(
                k in blob for k in ["impact effort matrix", "impact/effort matrix", "prioritization matrix", "2x2", "quadrant"]
            ):
                return False

            matrix_phrases = [
                "impact effort matrix",
                "impact/effort matrix",
                "effort-impact matrix",
                "impact-effort matrix",
                "impact vs effort",
                "effort vs impact",
                "impact vs. effort",
                "effort vs. impact",
                "prioritization matrix",
                "priority matrix",
                "2x2",
                "quadrant",
            ]
            has_matrix_phrase = any(k in blob for k in matrix_phrases)
            has_axis_cue = ("impact" in blob and ("effort" in blob or "ease" in blob))
            has_prioritization_cue = any(k in blob for k in ["priorit", "rank", "ranking", "trade-off", "tradeoff"])
            strong_title_cue = ("impact" in title and ("effort" in title or "ease" in title) and has_prioritization_cue)

            # Quadrant label hits (explicit naming of buckets).
            quadrant_terms = {
                "quick_easy_wins": ["quick", "easy win", "easy wins"],
                "major_projects": ["major project", "major projects"],
                "fill_in": ["fill-in", "fill in"],
                "thankless_tasks": ["thankless", "money pit"],
            }
            quad_label_kinds = 0
            for keys in quadrant_terms.values():
                if any(k in blob for k in keys):
                    quad_label_kinds += 1

            def _stage_quadrant_hit(st: str) -> bool:
                lo = (st or "").lower()
                if any(k in lo for k in ["quick", "easy win", "easy wins", "major project", "major projects", "fill-in", "fill in", "thankless", "money pit"]):
                    return True
                if ("high" in lo and "impact" in lo and ("low" in lo or "easy" in lo) and ("effort" in lo or "ease" in lo)):
                    return True
                if ("high" in lo and "impact" in lo and "high" in lo and ("effort" in lo or "ease" in lo)):
                    return True
                if ("low" in lo and "impact" in lo and "low" in lo and ("effort" in lo or "ease" in lo)):
                    return True
                if ("low" in lo and "impact" in lo and "high" in lo and ("effort" in lo or "ease" in lo)):
                    return True
                return False

            q_hits = sum(1 for st in stages if _stage_quadrant_hit(st))
            if not (has_matrix_phrase or strong_title_cue or quad_label_kinds >= 2):
                return False
            if not (has_axis_cue or q_hits >= 3):
                return False
            return True
        except Exception:
            return False

    def _cards_to_impact_effort_matrix(sl: Dict[str, Any]) -> Dict[str, Any] | None:
        if not _looks_like_impact_effort_cards(sl):
            return None

        cards_arr = _content(sl).get("cards") or []
        stages = [str(x).strip() for x in cards_arr if str(x).strip()]
        if not stages:
            return None

        def _extract_desc(raw: str) -> str:
            s = (raw or "").strip()
            if not s:
                return s
            parts = s.splitlines()
            if len(parts) >= 2:
                rest = " ".join(p.strip() for p in parts[1:] if p.strip())
                return rest or s
            m = re.split(r"\s*[:\u2014\-]\s*", s, maxsplit=1)
            if len(m) == 2 and m[1].strip():
                return m[1].strip()
            return s

        def _quad_key(raw: str) -> str | None:
            lo = (raw or "").lower()
            if any(k in lo for k in ["quick", "easy win", "easy wins"]):
                return "quick_easy_wins"
            if any(k in lo for k in ["major project", "major projects"]):
                return "major_projects"
            if any(k in lo for k in ["fill-in", "fill in"]):
                return "fill_in"
            if any(k in lo for k in ["thankless", "money pit"]):
                return "thankless_tasks"
            if ("high" in lo and "impact" in lo and "low" in lo and "effort" in lo) or ("high" in lo and "impact" in lo and "easy" in lo):
                return "quick_easy_wins"
            if "high" in lo and "impact" in lo and "high" in lo and "effort" in lo:
                return "major_projects"
            if "low" in lo and "impact" in lo and "low" in lo and "effort" in lo:
                return "fill_in"
            if "low" in lo and "impact" in lo and "high" in lo and "effort" in lo:
                return "thankless_tasks"
            return None

        quadrant_lists: Dict[str, List[str]] = {
            "quick_easy_wins": [],
            "major_projects": [],
            "fill_in": [],
            "thankless_tasks": [],
        }

        assigned = 0
        for st in stages:
            k = _quad_key(st)
            if not k:
                continue
            d = _extract_desc(st)
            if d:
                quadrant_lists[k].append(d)
                assigned += 1

        if assigned == 0:
            title = str(sl.get("title") or "")
            blob = (title + " " + " ".join(stages)).lower()
            explicit_matrix = any(
                k in blob
                for k in [
                    "impact effort matrix",
                    "impact/effort matrix",
                    "impact-effort matrix",
                    "effort-impact matrix",
                    "impact vs effort",
                    "effort vs impact",
                    "prioritization matrix",
                    "priority matrix",
                    "2x2",
                    "quadrant",
                ]
            ) or ("priorit" in blob and "impact" in blob and ("effort" in blob or "ease" in blob))
            has_axis_cue = ("impact" in blob and ("effort" in blob or "ease" in blob))
            if not (explicit_matrix and has_axis_cue and len(stages) >= 4):
                return None

            keys = ["quick_easy_wins", "major_projects", "fill_in", "thankless_tasks"]
            for i, st in enumerate(stages):
                d = _extract_desc(st)
                if d:
                    quadrant_lists[keys[i % 4]].append(d)

        out = dict(sl)
        out["type"] = "impact_effort_matrix"
        out["content"] = quadrant_lists
        return out

    try:
        ai_router_cache: Dict[str, Dict[str, Any]] = {}

        def _ai_route_cards(sl: Dict[str, Any]) -> Dict[str, Any] | None:
            if not ENABLE_SEMANTIC_CARDS_ROUTER:
                return None
            try:
                from app.utils.chatgpt_client import ChatGPTError, chat_completion
            except Exception:
                return None

            title = str(sl.get("title") or "").strip()
            c = _content(sl)
            items = c.get("cards") or c.get("bullets") or []
            if not isinstance(items, list):
                items = []
            points = [str(x).strip() for x in items if str(x).strip()]
            if not points:
                return None

            cache_key = json.dumps({"t": title, "p": points}, ensure_ascii=False)
            if cache_key in ai_router_cache:
                return ai_router_cache[cache_key]

            system = (
                "You are a presentation layout router. Output ONLY valid JSON. "
                "Choose the best rendering kind for a single slide based on the meaning of its title and items."
            )
            user_payload = {
                "task": "Classify the best layout kind for this slide.",
                "slide": {"title": title, "items": points},
                "kinds": {
                    "impact_effort_matrix": "A 2x2 prioritization matrix with axes impact and effort/ease.",
                    "benefits_stack": "A layered/stacked set of benefits or maturity tiers.",
                    "process_cards": "A sequence of steps/phases/process or a list of items not meant for a matrix.",
                },
                "rules": [
                    "Decide by semantics, not by keyword matching.",
                    "If the slide is clearly a prioritization tradeoff between impact and effort, choose impact_effort_matrix.",
                    "Do NOT choose impact_effort_matrix for a generic summary/recap/overview/visual summary slide unless impact-vs-effort prioritization is the central purpose.",
                    "Only choose impact_effort_matrix if it makes sense as a 2x2.",
                    "If you choose impact_effort_matrix, you MUST output a 'quadrants' object with keys: quick_easy_wins, major_projects, fill_in, thankless_tasks. Each value must be an array of strings (0-6 items).",
                    "Only choose impact_effort_matrix if you can place at least 4 total items across at least 2 non-empty quadrants.",
                    "If you choose impact_effort_matrix, you MUST output 'labels' with {quadrants: {...}, axes: {...}}. axes keys: x_low, x_high, y_low, y_high.",
                    "If you cannot confidently build quadrants, choose process_cards instead.",
                    "Output schema: {kind: <one of the keys above>, confidence: 0..1, quadrants?: {...}, labels?: {...}}",
                ],
            }
            try:
                raw = chat_completion(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                    ],
                    max_tokens=450,
                    temperature=0.0,
                    timeout=45,
                )
            except ChatGPTError:
                return None
            except Exception:
                return None
            try:
                obj = json.loads(raw)
            except Exception:
                return None
            if not isinstance(obj, dict):
                return None
            kind = str(obj.get("kind") or "").strip()
            confidence = obj.get("confidence")
            try:
                conf_v = float(confidence)
            except Exception:
                conf_v = 0.0
            out = {"kind": kind, "confidence": conf_v}
            quads = obj.get("quadrants")
            if isinstance(quads, dict):
                out["quadrants"] = quads
            labels = obj.get("labels")
            if isinstance(labels, dict):
                out["labels"] = labels
            ai_router_cache[cache_key] = out
            return out

        if ENABLE_SEMANTIC_CARDS_ROUTER:
            new_norm: List[Dict[str, Any]] = []
            for s in (slides_norm or []):
                try:
                    if not (isinstance(s, dict) and _ctype(s) in {"cards", "card", "card_style", "process_timeline_alt"}):
                        new_norm.append(s)
                        continue
                    routed = _ai_route_cards(s)
                    if not routed:
                        new_norm.append(s)
                        continue
                    kind = str(routed.get("kind") or "").strip()
                    if kind == "impact_effort_matrix":
                        conf_v = 0.0
                        try:
                            conf_v = float(routed.get("confidence") or 0.0)
                        except Exception:
                            conf_v = 0.0
                        if conf_v < 0.75:
                            out = dict(s)
                            out["_semantic_cards_kind"] = "process_cards"
                            new_norm.append(out)
                            continue
                        quads = routed.get("quadrants")
                        if isinstance(quads, dict):
                            def _arr(k: str) -> List[str]:
                                v = quads.get(k) or []
                                if not isinstance(v, list):
                                    return []
                                return [str(x).strip() for x in v if str(x).strip()][:6]
                            q_obj = {
                                "quick_easy_wins": _arr("quick_easy_wins"),
                                "major_projects": _arr("major_projects"),
                                "fill_in": _arr("fill_in"),
                                "thankless_tasks": _arr("thankless_tasks"),
                            }
                            total_items = sum(len(v) for v in q_obj.values())
                            nonempty = sum(1 for v in q_obj.values() if v)
                            if total_items >= 4 and nonempty >= 2:
                                out = dict(s)
                                out["type"] = "impact_effort_matrix"
                                out_content = dict(q_obj)
                                labels = routed.get("labels")
                                if isinstance(labels, dict) and labels:
                                    out_content["labels"] = labels
                                out["content"] = out_content
                                new_norm.append(out)
                                continue
                    out = dict(s)
                    out["_semantic_cards_kind"] = kind
                    new_norm.append(out)
                except Exception:
                    new_norm.append(s)
            slides_norm = new_norm
        else:
            slides_norm = [(_cards_to_impact_effort_matrix(s) or s) for s in (slides_norm or [])]
    except Exception:
        pass

    try:
        def _matrix_counts(c: Dict[str, Any]) -> Tuple[int, int]:
            keys = ["quick_easy_wins", "major_projects", "fill_in", "thankless_tasks"]
            total = 0
            nonempty = 0
            for k in keys:
                v = c.get(k)
                if isinstance(v, dict):
                    v = v.get("bullets") or v.get("points") or v.get("items")
                if not isinstance(v, list):
                    v = []
                vv = [str(x).strip() for x in v if str(x).strip()]
                total += len(vv)
                if vv:
                    nonempty += 1
            return total, nonempty

        def _matrix_to_infographic(sl: Dict[str, Any]) -> Dict[str, Any]:
            c = _content(sl)
            keys = ["quick_easy_wins", "major_projects", "fill_in", "thankless_tasks"]
            bullets: List[str] = []
            for k in keys:
                v = c.get(k)
                if isinstance(v, dict):
                    v = v.get("bullets") or v.get("points") or v.get("items")
                if isinstance(v, list):
                    bullets += [str(x).strip() for x in v if str(x).strip()]
            bullets = bullets[:8]
            out = dict(sl)
            out["type"] = "infographic"
            out["content"] = {"bullets": bullets, "visual_hint": "summary"}
            return out

        def _should_downgrade_matrix(sl: Dict[str, Any]) -> bool:
            t0 = _ctype(sl)
            if t0 not in {"impact_effort_matrix", "impact_matrix", "prioritization_matrix"}:
                return False
            c = _content(sl)
            title = str(sl.get("title") or "").strip()
            title_lc = title.lower()
            summaryish = any(k in title_lc for k in ["visual summary", "summary", "recap", "overview", "highlights"])
            title_matrix_intent = any(
                k in title_lc
                for k in [
                    "matrix",
                    "impact vs effort",
                    "effort vs impact",
                    "impact vs. effort",
                    "effort vs. impact",
                    "prioritization",
                    "prioritisation",
                    "priority",
                    "2x2",
                    "quadrant",
                ]
            )
            total, nonempty = _matrix_counts(c)
            blob = (title + " " + " ".join([str(x) for x in (
                (c.get("quick_easy_wins") or [])
                + (c.get("major_projects") or [])
                + (c.get("fill_in") or [])
                + (c.get("thankless_tasks") or [])
            ) if str(x).strip()])).lower()
            has_axis_cue = ("impact" in blob and ("effort" in blob or "ease" in blob))
            if summaryish and not title_matrix_intent:
                return True
            if summaryish and not has_axis_cue:
                return True
            if summaryish and (total < 4 or nonempty < 2):
                return True
            if (not summaryish) and total == 0:
                return True
            return False

        new_norm2: List[Dict[str, Any]] = []
        for s in (slides_norm or []):
            if isinstance(s, dict) and _should_downgrade_matrix(s):
                new_norm2.append(_matrix_to_infographic(s))
            else:
                new_norm2.append(s)
        slides_norm = new_norm2
    except Exception:
        pass

    def _card_item_to_text(x: Any) -> str:
        if isinstance(x, dict):
            h = x.get("heading") or x.get("title") or x.get("label") or x.get("name") or x.get("step")
            d = x.get("description") or x.get("desc") or x.get("details") or x.get("detail") or x.get("body") or x.get("text")
            hs = str(h or "").strip()
            ds = str(d or "").strip()
            if hs and ds:
                return f"{hs} — {ds}"
            if hs:
                return hs
            if ds:
                return ds
            return ""
        if isinstance(x, str):
            s = x.strip()
            if s.startswith("{") and s.endswith("}") and ("title" in s or "heading" in s or "description" in s):
                parsed: Any = None
                try:
                    parsed = json.loads(s)
                except Exception:
                    try:
                        import ast as _ast
                        parsed = _ast.literal_eval(s)
                    except Exception:
                        parsed = None
                if isinstance(parsed, dict):
                    return _card_item_to_text(parsed)
            return s
        return str(x or "").strip()

    try:
        ag_idx = next((i for i, s in enumerate(slides_norm) if isinstance(s, dict) and _ctype(s) == "agenda"), None)
        if ag_idx is not None:
            body_titles: List[str] = []
            for s in (slides_norm or []):
                if not isinstance(s, dict):
                    continue
                t = _ctype(s)
                if t in {"title", "agenda", "thank_you", "cta"}:
                    continue
                ttl = str(s.get("title") or "").strip()
                if ttl:
                    body_titles.append(ttl)
            if body_titles:
                slides_norm[ag_idx] = dict(slides_norm[ag_idx])
                slides_norm[ag_idx]["content"] = {"bullets": body_titles[:32]}
    except Exception:
        pass

    for idx, sl in enumerate(slides_norm, start=1):
        t = _ctype(sl)
        current_render_idx = idx
        title_dbg = ""
        try:
            title_dbg = str(sl.get("title") or "").strip()
        except Exception:
            title_dbg = ""
        if SLIDE_DEBUG:
            try:
                fb_from = sl.get("_fallback_from")
                fb_to = sl.get("_fallback_to")
                fb_reason = sl.get("_fallback_reason")
                print(
                    f"slide_debug:dispatch idx={idx} title={title_dbg!r} type={t} renderer=? fallback_from={fb_from} fallback_to={fb_to} reason={fb_reason}"
                )
            except Exception:
                pass
        def _looks_like_case_studies_cards(title_txt: str, stages: List[str]) -> bool:
            blob = ((title_txt or "") + " " + " ".join(stages or [])).lower()
            if any(k in blob for k in ["case stud", "case-study", "case study", "success stor", "success story", "use case", "use-case", "examples", "customer story"]):
                return True
            colonish = 0
            numericish = 0
            for s0 in (stages or []):
                s = str(s0 or "")
                if ":" in s and 2 <= len(s.split(":", 1)[0].strip()) <= 20:
                    colonish += 1
                if re.search(r"\b\d{1,3}%\b|\$\s*\d|\broi\b|\bmetrics?\b", s.lower()):
                    numericish += 1
            if colonish >= max(2, (len(stages or []) + 1) // 2):
                return True
            if numericish >= 2:
                return True
            return False

        # Force replacement: slides with a 'cards' array should render as process only when they represent steps.
        try:
            cards_arr = _content(sl).get("cards")
            if t in {"cards", "card", "card_style", "process_timeline_alt"} and isinstance(cards_arr, list) and any(_card_item_to_text(x) for x in cards_arr):
                stages = [_card_item_to_text(x) for x in cards_arr if _card_item_to_text(x)]
                forced_process_variant = None
                try:
                    forced_process_variant = str((_content(sl).get("process_variant") or "")).strip() or None
                except Exception:
                    forced_process_variant = None
                if ENABLE_SEMANTIC_CARDS_ROUTER:
                    kind = str(sl.get("_semantic_cards_kind") or "").strip()
                    if kind == "benefits_stack":
                        _render_layered_benefits_stack({"title": (sl.get("title") or "Benefits"), "content": {"bullets": stages}})
                    elif _looks_like_case_studies_cards((sl.get("title") or ""), stages):
                        _render_cards_grid((sl.get("title") or "Highlights"), stages)
                    else:
                        render_process_variant((sl.get("title") or "Process"), stages, topic_src=(sl.get("title") or ""), forced=forced_process_variant)
                else:
                    blob = ((sl.get("title") or "") + " " + " ".join(stages)).lower()
                    if any(k in blob for k in ["benefit", "benefits", "value", "advantages", "advantage", "gains", "gain"]):
                        _render_layered_benefits_stack({"title": (sl.get("title") or "Benefits"), "content": {"bullets": stages}})
                    elif _looks_like_case_studies_cards((sl.get("title") or ""), stages):
                        _render_cards_grid((sl.get("title") or "Highlights"), stages)
                    else:
                        render_process_variant((sl.get("title") or "Process"), stages, topic_src=(sl.get("title") or ""), forced=forced_process_variant)
                continue
        except Exception:
            pass
        if t == "title":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_title")
            render_title(sl)
        elif t == "agenda":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_agenda")
            # Render Agenda
            render_agenda(sl)
            # Conditionally insert Timeline slide immediately after Agenda
            # Gather signals
            topic_title = (slides_norm[0].get("title") or "").strip()
            ag_bullets = [_content(sl).get("bullets", [])]
            # ag_bullets is a list containing a list; flatten properly
            if ag_bullets and isinstance(ag_bullets[0], list):
                ag_items = [str(x) for x in ag_bullets[0] if str(x).strip()]
            else:
                ag_items = []
            # Timeline generation disabled per request
            if False:
                layout = _choose_timeline_layout(topic_title, ag_items)
                pts = _derive_timeline_points(topic_title, ag_items)
                title_txt = _smart_timeline_title(topic_title, layout)
                if layout == "linear":
                    render_timeline_linear(title_txt, pts, topic_title)
                elif layout == "horizontal":
                    render_timeline_horizontal(title_txt, pts, topic_title)
                elif layout == "roadmap":
                    render_timeline_roadmap(title_txt, pts, topic_title)
                else:
                    render_timeline_vertical(title_txt, pts, topic_title)
        elif t == "one_idea":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_one_idea")
            render_one_idea(sl)
        elif t in {"double_column", "deep_dive"}:
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_double")
            render_double(sl)
        elif t in {"comparison", "table"}:
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_table")
            render_table(sl)
        elif t in {"infographic", "key_takeaways"}:
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_infographic")
            render_infographic(sl)
        elif t in {"cards", "card", "card_style", "process_timeline_alt"}:
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_process_variant")
            # Use the rotation for all process-like slide requests so all styles, including bubbles, appear
            c = _content(sl)
            subtitle_override = (
                str(sl.get("subtitle") or "").strip()
                or str(c.get("subtitle") or "").strip()
                or None
            )
            forced_process_variant = None
            try:
                forced_process_variant = str(c.get("process_variant") or "").strip() or None
            except Exception:
                forced_process_variant = None
            raw_items = (c.get("cards") or c.get("bullets") or [])
            if not isinstance(raw_items, list):
                raw_items = []
            stages = [_card_item_to_text(x) for x in raw_items if _card_item_to_text(x)]
            if ENABLE_SEMANTIC_CARDS_ROUTER:
                kind = str(sl.get("_semantic_cards_kind") or "").strip()
                if kind == "benefits_stack":
                    _render_layered_benefits_stack({"title": (sl.get("title") or "Benefits"), "content": {"bullets": stages}})
                elif _looks_like_case_studies_cards((sl.get("title") or ""), stages):
                    _render_cards_grid((sl.get("title") or "Highlights"), stages)
                else:
                    render_process_variant((sl.get("title") or "Process"), stages, topic_src=(sl.get("title") or ""), subtitle_override=subtitle_override, forced=forced_process_variant)
            else:
                blob = ((sl.get("title") or "") + " " + " ".join(stages)).lower()
                if any(k in blob for k in ["benefit", "benefits", "value", "advantages", "advantage", "gains", "gain"]):
                    _render_layered_benefits_stack({"title": (sl.get("title") or "Benefits"), "content": {"bullets": stages}})
                elif _looks_like_case_studies_cards((sl.get("title") or ""), stages):
                    _render_cards_grid((sl.get("title") or "Highlights"), stages)
                else:
                    render_process_variant((sl.get("title") or "Process"), stages, topic_src=(sl.get("title") or ""), subtitle_override=subtitle_override, forced=forced_process_variant)
        elif t == "timeline":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_timeline")
            _render_timeline(sl)
        elif t == "roadmap":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_roadmap_wrapper")
            _render_roadmap_wrapper(sl)
        elif t == "swot":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_swot_wrapper")
            _render_swot_wrapper(sl)
        elif t == "kpi":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_kpi_wrapper")
            _render_kpi_wrapper(sl)
        elif t == "pricing":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_pricing_wrapper")
            _render_pricing_wrapper(sl)
        elif t == "risk_matrix":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_risk_matrix_wrapper")
            _render_risk_matrix_wrapper(sl)
        elif t == "before_after":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_before_after_wrapper")
            _render_before_after_wrapper(sl)
        elif t == "architecture":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_architecture_wrapper")
            _render_architecture_wrapper(sl)
        elif t in {"impact_effort_matrix", "impact_matrix", "prioritization_matrix"}:
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=_render_impact_effort_matrix_wrapper")
            _render_impact_effort_matrix_wrapper(sl)
        elif t in {"thank_you", "cta"}:
            # skip for now; rendered at end to guarantee last
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=skip_thanks")
            continue
        elif t == "bullets":
            if SLIDE_DEBUG:
                print(f"slide_debug:renderer idx={idx} type={t} renderer=render_bullets")
            render_bullets(sl)
        else:
            fb_to = None
            try:
                fb_to = str(sl.get("_fallback_to") or "").strip().lower() or None
            except Exception:
                fb_to = None

            if SLIDE_DEBUG:
                raise ValueError(f"Unknown/unsupported slide type '{t}' for slide idx={idx} title={title_dbg!r}")

            # Non-debug: only fall back when finalizer explicitly recorded a fallback.
            if fb_to in {"bullets", "cards", "double_column", "comparison", "table", "infographic", "key_takeaways"}:
                sl2 = dict(sl)
                sl2["type"] = fb_to
                if fb_to in {"cards"}:
                    # allow downstream existing cards routing
                    c = _content(sl2)
                    if "cards" not in c and c.get("bullets"):
                        c["cards"] = c.get("bullets")
                        sl2["content"] = c
                if fb_to in {"bullets"}:
                    render_bullets(sl2)
                elif fb_to in {"double_column", "deep_dive"}:
                    render_double(sl2)
                elif fb_to in {"comparison", "table"}:
                    render_table(sl2)
                elif fb_to in {"infographic", "key_takeaways"}:
                    render_infographic(sl2)
                else:
                    # cards/process types
                    c = _content(sl2)
                    stages = [str(x).strip() for x in (c.get("cards") or c.get("bullets") or []) if str(x).strip()]
                    if len(stages) < 4:
                        continue
                    forced_process_variant = None
                    try:
                        forced_process_variant = str((_content(sl2).get("process_variant") or "")).strip() or None
                    except Exception:
                        forced_process_variant = None
                    render_process_variant((sl2.get("title") or "Process"), stages, topic_src=(sl2.get("title") or ""), forced=forced_process_variant)
            # Otherwise: skip silently (prevents surprise plain bullet slides)

    # --- Smart Infographic Summary (rotate among 4 templates) ---
    def _collect_takeaways(sl_list: List[Dict[str, Any]]) -> List[str]:
        pts: List[str] = []
        # Prefer Key Takeaways / Infographic bullets
        for s in sl_list:
            t = _ctype(s)
            c = _content(s)
            if t in {"key_takeaways", "infographic"}:
                pts += [str(x).strip() for x in (c.get("bullets") or c.get("takeaways") or []) if str(x).strip()]
        # Agenda bullets
        for s in sl_list:
            if _ctype(s) == "agenda":
                pts += [str(x).strip() for x in (_content(s).get("bullets") or []) if str(x).strip()]
                break
        # Fallback: other slide titles
        if len(pts) < 3:
            for s in sl_list:
                t = _ctype(s)
                if t in {"title", "agenda", "thank_you", "cta"}:
                    continue
                lab = (s.get("title") or "").strip()
                if lab:
                    pts.append(lab)
        # Normalize 3–5
        uniq = []
        seen = set()
        for p in pts:
            lo = p.lower()
            if lo and lo not in seen:
                seen.add(lo); uniq.append(p)
        if len(uniq) < 4:
            # pad with topic-driven generics
            topic = (slides_norm[0].get("title") or "Key Insights").strip()
            base = [f"{topic}: summary", "Outcomes", "Next steps", "Owners", "Risks/Effort"]
            for b in base:
                if len(uniq) >= 4:
                    break
                if b.lower() not in seen:
                    seen.add(b.lower()); uniq.append(b)
        return uniq[:5]

    def _render_summary_banner(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        """Horizontal stacked banners inspired by the sample screenshot.
        - Big title + subtitle at top
        - 4 rounded pills with left icon bubble
        - High contrast text per pill color
        """
        # Use theme colors for consistency with all slide titles
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=44, bold=True, color=pal["primary"])
        _add_textbox(slide, 1.0, 1.6, 11.3, 0.6, "", font_size_pt=22, color=pal.get("muted", (100,116,139)), align="center")
        # SLEEK: alternate row styles for visual rhythm
        # Row 1/3/...: primary blue container, white text, white bubble with blue number
        # Row 2/4/...: very light blue container, dark text, blue bubble with white number
        if is_sleek_theme:
            base_primary = pal.get("primary", (59, 130, 246))
            # Very light, non-grey blue for even rows; use explicit light-blue fallback
            light_bg = (238, 244, 255)
            dark_text = pal.get("text", (15, 23, 42))

            # Layout tuned to 13.33in x 7.5in. This fits 4 rows comfortably.
            y = 2.35           # top of first pill
            h = 0.86           # pill height
            gap = 0.32         # vertical gap between pills
            for i, raw in enumerate(items[:4]):
                row_idx = i + 1
                odd_row = (row_idx % 2 == 1)
                bg_rgb = base_primary if odd_row else light_bg

                # Pill background
                pill_x = 1.55   # shift a bit right for visual balance
                pill_w = 10.6
                pill = slide.shapes.add_shape(
                    MsoShape.ROUNDED_RECTANGLE,
                    Inches(pill_x), Inches(y), Inches(pill_w), Inches(h),
                )
                pill.fill.solid(); pill.fill.fore_color.rgb = RGBColor(*bg_rgb)
                pill.line.fill.background()
                try:
                    pill.shadow.inherit = False; pill.shadow.visible = True; pill.shadow.blur = 6; pill.shadow.distance = 2
                except Exception:
                    pass

                # Number bubble on the left (overlapping look)
                ico_d = 0.86
                ico_x = pill_x - 0.45
                ico_y = y + (h - ico_d) / 2
                ico = slide.shapes.add_shape(MsoShape.OVAL, Inches(ico_x), Inches(ico_y), Inches(ico_d), Inches(ico_d))
                ico.fill.solid()
                if odd_row:
                    # White bubble, blue text
                    ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
                    ico_border_rgb = base_primary
                    glyph_rgb = base_primary
                else:
                    # Blue bubble, white text
                    ico.fill.fore_color.rgb = RGBColor(*base_primary)
                    ico_border_rgb = base_primary
                    glyph_rgb = (255, 255, 255)
                ico.line.color.rgb = RGBColor(*ico_border_rgb)
                glyph = str(row_idx)
                itf = ico.text_frame; itf.clear(); ip = itf.paragraphs[0]; ip.alignment = PP_ALIGN.CENTER
                ir = ip.add_run(); ir.text = glyph; ir.font.size = Pt(20); ir.font.bold = True; ir.font.color.rgb = RGBColor(*glyph_rgb)
                try:
                    ico.shadow.inherit = False; ico.shadow.visible = True; ico.shadow.blur = 3; ico.shadow.distance = 1
                except Exception:
                    pass

                # Text: white on blue rows, dark on light rows; no auto-darkening on blue
                head, tail = _split_head_tail(str(raw))
                tb = slide.shapes.add_textbox(Inches(pill_x + 0.65), Inches(y + 0.14), Inches(pill_w - 0.90), Inches(h - 0.28)).text_frame
                tb.word_wrap = True
                tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                p = tb.paragraphs[0]
                p.clear()
                if odd_row:
                    head_rgb = (255, 255, 255)
                    tail_rgb = (235, 244, 255)
                else:
                    head_rgb = dark_text
                    tail_rgb = pal.get("muted", dark_text)
                if head:
                    r1 = p.add_run(); r1.text = head + (" " if tail else ""); r1.font.size = Pt(22); r1.font.bold = True; r1.font.color.rgb = RGBColor(*head_rgb)
                if tail:
                    r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(15); r2.font.color.rgb = RGBColor(*tail_rgb)
                y += h + gap
            return

        # Non-SLEEK themes: pill background + text colors derived from the active theme
        base_bgs = [
            pal.get("primary"),
            pal.get("secondary", pal.get("primary")),
            pal.get("muted", pal.get("secondary", pal.get("primary"))),
            pal.get("bg", (255, 255, 255)),
        ]
        PILL_COLORS: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = []
        for bg_rgb in base_bgs:
            if not isinstance(bg_rgb, tuple):
                continue
            txt_rgb = (255, 255, 255) if _is_dark_rgb(bg_rgb) else pal.get("text", (35, 35, 35))
            PILL_COLORS.append((bg_rgb, txt_rgb))
        if not PILL_COLORS:
            bg_fallback = pal.get("primary", (59, 130, 246))
            txt_fallback = (255, 255, 255) if _is_dark_rgb(bg_fallback) else pal.get("text", (35, 35, 35))
            PILL_COLORS = [(bg_fallback, txt_fallback)]

        def _luma(rgb: Tuple[int, int, int]) -> float:
            r, g, b = rgb
            return 0.2126*r + 0.7152*g + 0.0722*b

        # Layout tuned to 13.33in x 7.5in. This fits 4 rows comfortably.
        y = 2.35           # top of first pill
        h = 0.86           # pill height
        gap = 0.32         # vertical gap between pills
        for i, raw in enumerate(items[:4]):
            bg_rgb, preferred_txt = PILL_COLORS[i % len(PILL_COLORS)]
            # Pill background
            pill_x = 1.55   # shift a bit right for visual balance
            pill_w = 10.6
            pill = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(pill_x), Inches(y), Inches(pill_w), Inches(h))
            pill.fill.solid(); pill.fill.fore_color.rgb = RGBColor(*bg_rgb)
            pill.line.fill.background()
            try:
                pill.shadow.inherit = False; pill.shadow.visible = True; pill.shadow.blur = 6; pill.shadow.distance = 2
            except Exception:
                pass

            # Icon circle on the left (overlapping look)
            ico_d = 0.86
            ico_x = pill_x - 0.45
            ico_y = y + (h - ico_d) / 2
            ico = slide.shapes.add_shape(MsoShape.OVAL, Inches(ico_x), Inches(ico_y), Inches(ico_d), Inches(ico_d))
            ico.fill.solid()
            # For the PULSE theme, use numbered markers with a soft accent fill
            # instead of a generic dot, while keeping geometry unchanged.
            if theme_key == "PULSE":
                circle_fill = pal.get("secondary", pal.get("bg", (255, 255, 255)))
                border_rgb = pal.get("primary", preferred_txt)
                glyph = str(i + 1)
                # Darker text on light circle; fall back to theme text color
                glyph_rgb = border_rgb if not _is_dark_rgb(circle_fill) else (255, 255, 255)
                ico.fill.fore_color.rgb = RGBColor(*circle_fill)
                ico.line.color.rgb = RGBColor(*border_rgb)
            elif theme_key in ("MINIMAL FLUX", "BLUE HORIZON"):
                circle_fill = pal.get("secondary", pal.get("bg", (255, 255, 255)))
                border_rgb = pal.get("primary", preferred_txt)
                glyph = str(i + 1)
                if _is_dark_rgb(circle_fill):
                    glyph_rgb = pal.get("text", (255, 255, 255))
                else:
                    glyph_rgb = border_rgb
                ico.fill.fore_color.rgb = RGBColor(*circle_fill)
                ico.line.color.rgb = RGBColor(*border_rgb)
            else:
                ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
                ico_border_rgb = preferred_txt
                glyph = "\u25cf"
                glyph_rgb = ico_border_rgb
                ico.line.color.rgb = RGBColor(*ico_border_rgb)

            # simple glyph/number placeholder
            itf = ico.text_frame; itf.clear(); ip = itf.paragraphs[0]; ip.alignment = PP_ALIGN.CENTER
            ir = ip.add_run(); ir.text = glyph; ir.font.size = Pt(20); ir.font.bold = True; ir.font.color.rgb = RGBColor(*glyph_rgb)
            try:
                ico.shadow.inherit = False; ico.shadow.visible = True; ico.shadow.blur = 3; ico.shadow.distance = 1
            except Exception:
                pass

            # Text: bold head + lighter tail for visibility
            head, tail = _split_head_tail(str(raw))
            tb = slide.shapes.add_textbox(Inches(pill_x + 0.65), Inches(y + 0.14), Inches(pill_w - 0.90), Inches(h - 0.28)).text_frame
            tb.word_wrap = True
            tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]
            p.clear()
            # Choose contrast color automatically if needed
            txt_rgb = preferred_txt if _luma(bg_rgb) < 170 else pal.get("text", (35, 35, 35))
            if head:
                r1 = p.add_run(); r1.text = head + (" " if tail else ""); r1.font.size = Pt(22); r1.font.bold = True; r1.font.color.rgb = RGBColor(*txt_rgb)
            if tail:
                if txt_rgb == (255, 255, 255):
                    tail_rgb = (255, 255, 255)
                else:
                    tail_rgb = pal.get("text", (35, 35, 35))
                r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(15); r2.font.color.rgb = RGBColor(*tail_rgb)
            y += h + gap

    def _render_summary_cluster(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        # arrange in grid 2x2 or 3x2
        cols = 2 if len(items) <= 4 else 3
        rows = 2
        start_x, start_y = 1.0, 2.0
        gap_x, gap_y = 0.6, 0.6
        w, h = (SLIDE_W_IN - 2*start_x - (cols-1)*gap_x)/cols, 1.6
        idx = 0
        for r in range(rows):
            for c in range(cols):
                if idx >= len(items) or idx >= 6:
                    break
                x = start_x + c*(w+gap_x); y = start_y + r*(h+gap_y)
                card = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
                card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
                card.line.color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
                tb = slide.shapes.add_textbox(Inches(x+0.35), Inches(y+0.25), Inches(w-0.6), Inches(h-0.5)).text_frame
                tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                p = tb.paragraphs[0]; p.text = items[idx]; p.font.size = Pt(18); p.font.bold = True; p.font.color.rgb = RGBColor(*pal["text"])
                idx += 1

    def _render_summary_flow(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        x, y = 4.2, 2.0
        w, h, gap = 5.6, 0.9, 0.25
        for i, lab in enumerate(items[:5], start=1):
            chev = slide.shapes.add_shape(MsoShape.CHEVRON, Inches(x), Inches(y), Inches(w), Inches(h))
            chev.fill.solid(); chev.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            chev.line.color.rgb = RGBColor(*pal["primary"])
            tf = chev.text_frame; tf.clear(); p = tf.paragraphs[0]; p.text = f"{i}. {lab}"; p.font.size = Pt(18); p.font.bold = True
            y += h + gap

    def _render_summary_steps(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        # Align title to inner grid for visual balance
        _add_textbox(slide, 1.2, 0.9, 10.8, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        start_y = 1.75
        row_h = 1.08
        gap = 0.16
        # Accent colors for big numbers derived from theme palette
        # For SLEEK, use a single strong blue so every number remains clearly
        # visible against the light background (no washed-out grey "2").
        if is_sleek_theme or theme_key in ("MINIMAL FLUX", "BLUE HORIZON"):
            NUM_COLORS: List[Tuple[int, int, int]] = [pal.get("primary", pal.get("text", (59, 130, 246)))]
        else:
            bg_rgb = pal.get("bg", (255, 255, 255))

            def _ensure_visible_num(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
                r, g, b = rgb
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                br, bgc, bb = bg_rgb
                lum_bg = 0.2126 * br + 0.7152 * bgc + 0.0722 * bb
                if abs(lum - lum_bg) < 35:
                    delta = -40 if not _is_dark_rgb(bg_rgb) else 40
                    return _shade(rgb, delta)
                return rgb

            base_colors: List[Tuple[int, int, int]] = [
                pal.get("primary"),
                pal.get("secondary", pal.get("primary")),
                pal.get("muted", pal.get("secondary", pal.get("primary"))),
                pal.get("text", pal.get("primary")),
            ]
            NUM_COLORS = [_ensure_visible_num(c) for c in base_colors if isinstance(c, tuple)]
            if not NUM_COLORS:
                NUM_COLORS = [_ensure_visible_num(pal.get("primary", (59, 130, 246)))]
        for i, raw in enumerate(items[:5], start=1):
            # Big number on the left
            num_col = NUM_COLORS[(i - 1) % len(NUM_COLORS)]
            nb = slide.shapes.add_textbox(Inches(1.2), Inches(start_y - 0.10), Inches(0.9), Inches(row_h)).text_frame
            nb.clear(); pnum = nb.paragraphs[0]; pnum.alignment = PP_ALIGN.LEFT
            rnum = pnum.add_run(); rnum.text = f"{i}."; rnum.font.size = Pt(44); rnum.font.bold = True; rnum.font.color.rgb = RGBColor(*num_col)

            # Head (bold) + tail (lighter) stacked to the right
            head, tail = _split_head_tail(str(raw))
            # Keep right edge consistent at ~12.0in: x=2.3, w=9.7
            tb = slide.shapes.add_textbox(Inches(2.3), Inches(start_y), Inches(9.7), Inches(row_h - 0.20)).text_frame
            tb.word_wrap = True
            p1 = tb.paragraphs[0]; p1.clear()
            if head:
                r1 = p1.add_run(); r1.text = head; r1.font.size = Pt(22); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal["text"]) 
            if tail:
                p2 = tb.add_paragraph(); p2.clear()
                r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

            # Subtle divider line under each row except the last
            if i < min(5, len(items)):
                line_y = start_y + row_h - 0.10
                # Divider aligned to grid (x=1.2) with width 10.8 to end around 12.0in
                line = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(1.2), Inches(line_y), Inches(10.8), Inches(0.02))
                line.fill.solid(); line.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal.get("text", (229, 231, 235))))
                line.line.fill.background()

            start_y += row_h + gap

    def _render_summary_vertical_arrows(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        # Title block aligned to grid
        _add_textbox(slide, 1.2, 0.9, 10.8, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        # Colors aligned with the current theme palette
        if is_sleek_theme:
            base_primary = pal.get("primary", (59, 130, 246))
            # For SLEEK, keep all rows in the same strong blue for a solid stack
            base_bgs: List[Tuple[int, int, int]] = [base_primary]
        else:
            base_bgs = [
                pal.get("primary"),
                pal.get("secondary", pal.get("primary")),
                pal.get("muted", pal.get("secondary", pal.get("primary"))),
                pal.get("bg", (255, 255, 255)),
            ]
        PILL_COLORS: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = []
        for bg_rgb in base_bgs:
            if not isinstance(bg_rgb, tuple):
                continue
            txt_rgb = (255, 255, 255) if _is_dark_rgb(bg_rgb) else pal.get("text", (35, 35, 35))
            PILL_COLORS.append((bg_rgb, txt_rgb))
        if not PILL_COLORS:
            bg_fallback = pal.get("primary", (59, 130, 246))
            txt_fallback = (255, 255, 255) if _is_dark_rgb(bg_fallback) else pal.get("text", (35, 35, 35))
            PILL_COLORS = [(bg_fallback, txt_fallback)]
        # Balanced grid: left=1.2, right ~11.6 (0.4in gutter both sides)
        x = 1.2
        card_w, card_h = 10.4, 1.10
        gap = 0.30
        y = 1.9
        for i, raw in enumerate(items[:4]):
            bg_rgb, txt_pref = PILL_COLORS[i % len(PILL_COLORS)]
            # Rounded rectangle card
            card = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*bg_rgb)
            card.line.fill.background()
            try:
                card.shadow.inherit = False; card.shadow.visible = True; card.shadow.blur = 6; card.shadow.distance = 2
            except Exception:
                pass
            # Down arrow tip (triangle) centered under card
            tri_w = 1.00; tri_h = 0.34
            tri_x = x + (card_w - tri_w) / 2
            tri = slide.shapes.add_shape(MsoShape.ISOSCELES_TRIANGLE, Inches(tri_x), Inches(y + card_h - 0.02), Inches(tri_w), Inches(tri_h))
            tri.rotation = 180  # point downward
            tri.fill.solid(); tri.fill.fore_color.rgb = RGBColor(*bg_rgb)
            tri.line.fill.background()
            # Text
            head, tail = _split_head_tail(str(raw))
            tb = slide.shapes.add_textbox(Inches(x + 0.55), Inches(y + 0.20), Inches(card_w - 1.10), Inches(card_h - 0.40)).text_frame
            tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p = tb.paragraphs[0]; p.clear()
            # Choose contrast
            txt_rgb = txt_pref
            if head:
                r1 = p.add_run(); r1.text = head; r1.font.size = Pt(21); r1.font.bold = True; r1.font.color.rgb = RGBColor(*txt_rgb)
            if tail:
                p2 = tb.add_paragraph(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(13)
                tail_rgb = (235, 244, 255) if txt_rgb == (255, 255, 255) else pal.get("muted", pal.get("text", (71, 85, 105)))
                r2.font.color.rgb = RGBColor(*tail_rgb)
            y += card_h + gap

    def _render_summary_horizontal_arrows(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        # Title block
        _add_textbox(slide, 1.2, 0.9, 10.8, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        # Base band and overlay chevron
        y = 2.0
        band_h, gap = 0.95, 0.28
        band_x, band_w = 1.2, 10.8
        CHEV_W = 6.4
        base_bgs: List[Tuple[int, int, int]] = [
            pal.get("primary"),
            pal.get("secondary", pal.get("primary")),
            pal.get("muted", pal.get("secondary", pal.get("primary"))),
            pal.get("bg", (255, 255, 255)),
        ]
        PILL_COLORS: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = []
        for bg_rgb in base_bgs:
            if not isinstance(bg_rgb, tuple):
                continue
            txt_rgb = (255, 255, 255) if _is_dark_rgb(bg_rgb) else pal.get("text", (35, 35, 35))
            PILL_COLORS.append((bg_rgb, txt_rgb))
        if not PILL_COLORS:
            bg_fallback = pal.get("primary", (59, 130, 246))
            txt_fallback = (255, 255, 255) if _is_dark_rgb(bg_fallback) else pal.get("text", (35, 35, 35))
            PILL_COLORS = [(bg_fallback, txt_fallback)]

        for i, raw in enumerate(items[:4]):
            bg_rgb, txt_pref = PILL_COLORS[i % len(PILL_COLORS)]
            # White base rounded band
            band = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(y), Inches(band_w), Inches(band_h))
            band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*pal.get("bg", (255, 255, 255)))
            band.line.color.rgb = RGBColor(*pal.get("secondary", pal.get("text", (229, 231, 235))))
            # Colored chevron overlay from left
            chev = slide.shapes.add_shape(MsoShape.CHEVRON, Inches(band_x), Inches(y), Inches(CHEV_W), Inches(band_h))
            chev.fill.solid(); chev.fill.fore_color.rgb = RGBColor(*bg_rgb)
            chev.line.fill.background()
            # Head text on chevron
            head, tail = _split_head_tail(str(raw))
            tb1 = slide.shapes.add_textbox(Inches(band_x + 0.40), Inches(y + 0.14), Inches(CHEV_W - 0.80), Inches(band_h - 0.28)).text_frame
            tb1.word_wrap = True; tb1.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p1 = tb1.paragraphs[0]; p1.clear(); r1 = p1.add_run(); r1.text = head or str(raw); r1.font.size = Pt(20); r1.font.bold = True; r1.font.color.rgb = RGBColor(*txt_pref)
            # Tail text on right white area
            if tail:
                tb2 = slide.shapes.add_textbox(Inches(band_x + CHEV_W + 0.40), Inches(y + 0.14), Inches(band_w - CHEV_W - 0.80), Inches(band_h - 0.28)).text_frame
                tb2.word_wrap = True; tb2.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                p2 = tb2.paragraphs[0]; p2.clear(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(16); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))
            y += band_h + gap

    def _render_roadmap(sl) -> None:
        """Render phase-based roadmap with title, date range, and description per phase."""
        content = _content(sl)
        title = content.get("title", "Roadmap")
        phases = content.get("phases", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Layout: horizontal phases with rounded rectangles
        start_x, start_y = 1.0, 2.2
        phase_w, phase_h = 2.8, 2.8
        gap = 0.6
        
        for i, phase in enumerate(phases[:6]):  # Max 6 phases
            x = start_x + i * (phase_w + gap)
            
            # Phase container
            phase_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(start_y), Inches(phase_w), Inches(phase_h))
            phase_box.fill.solid(); phase_box.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            phase_box.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Phase title
            phase_title = str(phase.get("title", ""))[:50]  # Truncate long titles
            _add_textbox(s, x + 0.15, start_y + 0.15, phase_w - 0.3, 0.5, phase_title, 
                        font_size_pt=18, bold=True, color=pal["text"], word_wrap=True)
            
            # Date range
            date_range = str(phase.get("date_range", ""))[:30]
            if date_range:
                _add_textbox(s, x + 0.15, start_y + 0.65, phase_w - 0.3, 0.4, date_range,
                            font_size_pt=12, color=pal.get("muted", pal["text"]), word_wrap=True)
            
            # Description
            desc = str(phase.get("description", ""))[:150]  # Limit description length
            if desc:
                _add_textbox(s, x + 0.15, start_y + 1.1, phase_w - 0.3, phase_h - 1.25, desc,
                            font_size_pt=11, color=pal["text"], word_wrap=True, auto_fit=True)
            
            # Connector arrow to next phase
            if i < len(phases) - 1:
                arrow_x = x + phase_w + 0.05
                arrow = s.shapes.add_shape(MsoShape.RIGHT_ARROW, Inches(arrow_x), Inches(start_y + phase_h/2 - 0.2), Inches(gap - 0.1), Inches(0.4))
                arrow.fill.solid(); arrow.fill.fore_color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                arrow.line.fill.background()

    def _render_swot(sl) -> None:
        """Render 2x2 SWOT grid with quadrants: Strengths, Weaknesses, Opportunities, Threats."""
        content = _content(sl)
        title = content.get("title", "SWOT Analysis")
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Grid layout: 2x2 (compute to always fit within slide bounds)
        margin_x = 1.0
        top_y = 2.05
        bottom_margin = 0.65
        gap_x, gap_y = 0.55, 0.45

        avail_w = max(6.0, SLIDE_W_IN - 2 * margin_x)
        avail_h = max(3.6, SLIDE_H_IN - bottom_margin - top_y)
        cell_w = max(3.0, (avail_w - gap_x) / 2.0)
        cell_h = max(1.6, (avail_h - gap_y) / 2.0)
        start_x, start_y = margin_x, top_y
        
        quadrants = [
            ("strengths", "Strengths", 0, 0),
            ("weaknesses", "Weaknesses", 1, 0),
            ("opportunities", "Opportunities", 0, 1),
            ("threats", "Threats", 1, 1)
        ]
        
        for key, label, col_idx, row_idx in quadrants:
            x = start_x + col_idx * (cell_w + gap_x)
            y = start_y + row_idx * (cell_h + gap_y)
            
            quadrant_data = content.get(key, {})
            points = quadrant_data.get("points", [])
            
            # Quadrant container
            quad_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(cell_h))
            quad_box.fill.solid(); quad_box.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            quad_box.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Quadrant title
            _add_textbox(
                s,
                x + 0.18,
                y + 0.12,
                cell_w - 0.36,
                0.46,
                label,
                font_size_pt=18,
                bold=True,
                color=pal["primary"],
                word_wrap=True,
                auto_fit=True,
            )
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:70]}" for p in points[:6])  # Max 6 points
                _add_textbox(
                    s,
                    x + 0.18,
                    y + 0.62,
                    cell_w - 0.36,
                    max(0.4, cell_h - 0.78),
                    bullet_text,
                    font_size_pt=13,
                    color=pal["text"],
                    word_wrap=True,
                    auto_fit=True,
                )

    def _render_kpi(sl) -> None:
        """Render 2-4 KPI metric cards with label, value, and optional caption."""
        content = _content(sl)
        title = content.get("title", "Key Metrics")
        metrics = content.get("metrics", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Layout: 2x2 grid for up to 4 metrics
        start_x, start_y = 1.0, 2.2
        card_w, card_h = 5.5, 2.0
        gap_x, gap_y = 0.8, 0.6
        
        for i, metric in enumerate(metrics[:4]):  # Max 4 metrics
            row, col = i // 2, i % 2
            x = start_x + col * (card_w + gap_x)
            y = start_y + row * (card_h + gap_y)
            
            # Metric card
            card = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            card.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Label
            label = str(metric.get("label", ""))[:40]
            _add_textbox(s, x + 0.2, y + 0.15, card_w - 0.4, 0.4, label,
                        font_size_pt=14, bold=True, color=pal["text"], word_wrap=True)
            
            # Value (large)
            value = str(metric.get("value", ""))[:25]
            _add_textbox(s, x + 0.2, y + 0.55, card_w - 0.4, 0.6, value,
                        font_size_pt=28, bold=True, color=pal["primary"], word_wrap=True)
            
            # Caption
            caption = str(metric.get("caption", ""))[:80]
            if caption:
                _add_textbox(s, x + 0.2, y + 1.15, card_w - 0.4, card_h - 1.35, caption,
                            font_size_pt=10, color=pal.get("muted", pal["text"]), word_wrap=True, auto_fit=True)

    def _render_pricing(sl) -> None:
        """Render pricing comparison table with 2-4 plans and feature rows."""
        content = _content(sl)
        title = content.get("title", "Pricing Comparison")
        plans = content.get("plans", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        if not plans:
            return
            
        # Layout: table with plans as columns
        start_x, start_y = 1.0, 2.2
        col_w = 2.5
        row_h = 0.5
        header_h = 0.8
        
        # Plan headers
        for i, plan in enumerate(plans[:4]):  # Max 4 plans
            x = start_x + i * col_w
            
            # Header background
            header = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(start_y), Inches(col_w - 0.1), Inches(header_h))
            header.fill.solid(); header.fill.fore_color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            header.line.fill.background()
            
            # Plan name
            name = str(plan.get("name", ""))[:30]
            _add_textbox(s, x + 0.05, start_y + 0.1, col_w - 0.2, 0.3, name,
                        font_size_pt=16, bold=True, color=(255, 255, 255), word_wrap=True)
            
            # Price
            price = str(plan.get("price", ""))[:20]
            if price:
                _add_textbox(s, x + 0.05, start_y + 0.4, col_w - 0.2, 0.3, price,
                            font_size_pt=14, color=(255, 255, 255), word_wrap=True)
        
        # Feature rows
        all_features = set()
        for plan in plans:
            all_features.update(plan.get("features", []))
        
        feature_list = list(all_features)[:8]  # Max 8 features
        for j, feature in enumerate(feature_list):
            y = start_y + header_h + j * row_h
            
            # Feature name (first column)
            feature_text = str(feature)[:40]
            _add_textbox(s, start_x + 0.05, y + 0.05, col_w - 0.15, row_h - 0.1, feature_text,
                        font_size_pt=11, color=pal["text"], word_wrap=True)
            
            # Checkmarks for each plan
            for i, plan in enumerate(plans[:4]):
                x = start_x + i * col_w
                has_feature = feature in plan.get("features", [])
                
                if has_feature:
                    # Checkmark
                    check = s.shapes.add_shape(MsoShape.OVAL, Inches(x + col_w/2 - 0.15), Inches(y + 0.15), Inches(0.3), Inches(0.3))
                    check.fill.solid(); check.fill.fore_color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                    check.line.fill.background()

    def _render_risk_matrix(sl) -> None:
        """Render 2x2 risk matrix with Likelihood vs Impact axes."""
        content = _content(sl)
        title = content.get("title", "Risk Matrix")
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Grid layout: 2x2 (compute to always fit within slide bounds)
        margin_x = 1.0
        top_y = 2.05
        bottom_margin = 0.65
        gap_x, gap_y = 0.55, 0.45
        avail_w = max(6.0, SLIDE_W_IN - 2 * margin_x)
        avail_h = max(3.6, SLIDE_H_IN - bottom_margin - top_y)
        cell_w = max(3.0, (avail_w - gap_x) / 2.0)
        cell_h = max(1.6, (avail_h - gap_y) / 2.0)
        start_x, start_y = margin_x, top_y
        
        quadrants = [
            ("low_likelihood_low_impact", "Low Likelihood\nLow Impact", 0, 0, (200, 230, 201)),
            ("low_likelihood_high_impact", "Low Likelihood\nHigh Impact", 1, 0, (252, 196, 85)),
            ("high_likelihood_low_impact", "High Likelihood\nLow Impact", 0, 1, (252, 196, 85)),
            ("high_likelihood_high_impact", "High Likelihood\nHigh Impact", 1, 1, (248, 113, 113))
        ]
        
        for key, label, col_idx, row_idx, bg_color in quadrants:
            x = start_x + col_idx * (cell_w + gap_x)
            y = start_y + row_idx * (cell_h + gap_y)
            
            quadrant_data = content.get(key, {})
            points = quadrant_data.get("points", [])
            
            # Quadrant container with risk-based coloring
            quad_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(cell_h))
            quad_box.fill.solid(); quad_box.fill.fore_color.rgb = RGBColor(*bg_color)
            quad_box.line.color.rgb = RGBColor(*pal.get("text", (71, 71, 71)))
            
            # Quadrant label
            _add_textbox(
                s,
                x + 0.18,
                y + 0.12,
                cell_w - 0.36,
                0.65,
                label,
                font_size_pt=13,
                bold=True,
                color=pal["text"],
                word_wrap=True,
                auto_fit=True,
            )
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:65]}" for p in points[:4])  # Max 4 points
                _add_textbox(
                    s,
                    x + 0.18,
                    y + 0.78,
                    cell_w - 0.36,
                    max(0.4, cell_h - 0.96),
                    bullet_text,
                    font_size_pt=12,
                    color=pal["text"],
                    word_wrap=True,
                    auto_fit=True,
                )
        
        # Axis labels
        _add_textbox(s, start_x + (cell_w + gap_x) * 0.5, start_y - 0.5, cell_w, 0.4, "Impact →",
                    font_size_pt=14, bold=True, color=pal["text"], align="center")
        _add_textbox(s, start_x - 0.8, start_y + (cell_h + gap_y) * 0.5, 0.6, cell_h,
                    "Likelihood\n↓", font_size_pt=14, bold=True, color=pal["text"], align="center")

    def _render_before_after(sl) -> None:
        """Render before/after comparison with two columns."""
        content = _content(sl)
        title = content.get("title", "Before vs After")
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.85, 11.3, 0.95, title, font_size_pt=34, bold=True, color=TITLE_COL, align="center", word_wrap=True, auto_fit=True)

        def _render_before_after_modern(slide, content2: Dict[str, Any]) -> None:
            before = content2.get("before") if isinstance(content2.get("before"), dict) else {}
            after = content2.get("after") if isinstance(content2.get("after"), dict) else {}

            left_heading = str((before or {}).get("heading") or "Before")[:50]
            right_heading = str((after or {}).get("heading") or "After")[:50]
            left_pts = [str(x).strip() for x in ((before or {}).get("points") or []) if str(x).strip()][:4]
            right_pts = [str(x).strip() for x in ((after or {}).get("points") or []) if str(x).strip()][:4]

            bg_rgb = pal.get("bg", (255, 255, 255))
            text_rgb = pal.get("text", (24, 24, 27))
            accent = pal.get("primary", (59, 130, 246))
            muted = pal.get("muted", text_rgb)

            def _set_transparency(shape, v: float) -> None:
                try:
                    shape.fill.fore_color.transparency = max(0.0, min(1.0, float(v)))
                except Exception:
                    pass

            center_x = SLIDE_W_IN / 2.0
            label_y = 1.85
            card_y = 2.55
            card_w = 4.65
            card_h = 3.10
            margin_x = 1.05
            left_x = margin_x
            right_x = SLIDE_W_IN - margin_x - card_w
            header_h = 0.62

            subtle = _blend_rgb(bg_rgb, pal.get("secondary", bg_rgb), 0.55)
            subtle = _blend_rgb(subtle, (255, 255, 255), 0.70)
            _add_textbox(slide, left_x, label_y, card_w, 0.70, "BEFORE", font_size_pt=44, bold=True, color=subtle, align="center")
            _add_textbox(slide, right_x, label_y, card_w, 0.70, "AFTER", font_size_pt=44, bold=True, color=subtle, align="center")

            glow_pad_x = 0.65
            glow_pad_y = 0.55
            glow_h = card_h + glow_pad_y * 2
            glow_y = card_y - glow_pad_y
            glow_w = card_w + glow_pad_x * 2

            glow_left = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(left_x - glow_pad_x), Inches(glow_y), Inches(glow_w), Inches(glow_h))
            glow_left.fill.solid(); glow_left.fill.fore_color.rgb = RGBColor(*_blend_rgb(bg_rgb, (160, 174, 192), 0.35))
            _set_transparency(glow_left, 0.78)
            glow_left.line.fill.background()

            glow_right = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(right_x - glow_pad_x), Inches(glow_y), Inches(glow_w), Inches(glow_h))
            glow_right.fill.solid(); glow_right.fill.fore_color.rgb = RGBColor(*_blend_rgb(bg_rgb, accent, 0.25))
            _set_transparency(glow_right, 0.78)
            glow_right.line.fill.background()

            divider = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(center_x - 0.01), Inches(card_y - 0.15), Inches(0.02), Inches(card_h + 0.30))
            divider.fill.solid(); divider.fill.fore_color.rgb = RGBColor(*_blend_rgb(bg_rgb, muted, 0.40))
            _set_transparency(divider, 0.70)
            divider.line.fill.background()

            def _card(x0: float, hdr: str, pts: List[str], *, header_fill: Tuple[int, int, int], header_text: Tuple[int, int, int]) -> None:
                card = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x0), Inches(card_y), Inches(card_w), Inches(card_h))
                card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
                card.line.fill.background()
                try:
                    card.shadow.inherit = False
                    card.shadow.visible = True
                    card.shadow.blur = 18
                    card.shadow.distance = 3
                except Exception:
                    pass

                band = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x0), Inches(card_y), Inches(card_w), Inches(header_h))
                band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*header_fill)
                band.line.fill.background()
                try:
                    band.shadow.inherit = False
                    band.shadow.visible = True
                    band.shadow.blur = 12
                    band.shadow.distance = 2
                except Exception:
                    pass

                _add_textbox(slide, x0 + 0.30, card_y + 0.14, card_w - 0.60, 0.40, hdr, font_size_pt=16, bold=True, color=header_text, word_wrap=True, auto_fit=True)

                y0 = card_y + header_h + 0.35
                row_h = 0.52
                dot_d = 0.085
                dot_x = x0 + 0.45
                txt_x = x0 + 0.62
                max_rows = 4
                pts2 = (pts or [])[:max_rows]
                for i, t in enumerate(pts2):
                    yy = y0 + i * row_h
                    dot = slide.shapes.add_shape(MsoShape.OVAL, Inches(dot_x), Inches(yy + 0.16), Inches(dot_d), Inches(dot_d))
                    dot.fill.solid(); dot.fill.fore_color.rgb = RGBColor(*accent)
                    dot.line.fill.background()
                    _add_textbox(slide, txt_x, yy + 0.05, card_w - (txt_x - x0) - 0.35, row_h, str(t), font_size_pt=13, color=text_rgb, word_wrap=True, auto_fit=True)

            # Left card: primary header
            _card(left_x, left_heading, left_pts, header_fill=accent, header_text=(255, 255, 255))
            # Right card: light header
            right_fill = _blend_rgb(bg_rgb, pal.get("secondary", bg_rgb), 0.70)
            _card(right_x, right_heading, right_pts, header_fill=right_fill, header_text=_shade(accent, -40))

        variant_raw = str(content.get("before_after_variant") or content.get("variant") or "").strip().lower()
        variant_map = {
            "columns": "modern",
            "two_columns": "modern",
            "two-column": "modern",
            "chevrons": "modern",
            "arrows": "modern",
            "connector": "modern",
            "modern": "modern",
        }
        forced = variant_map.get(variant_raw)
        chosen = choose_variant("before_after", ["modern"], forced=forced)
        _debug_variant(family="before_after", chosen=chosen, eligible=["modern"], forced=forced)
        _render_before_after_modern(s, content)
        return

    def _render_architecture(sl) -> None:
        """Render architecture diagram with boxes and directional arrows."""
        content = _content(sl)
        title = (str(sl.get("title") or "").strip() or str(content.get("title") or "").strip() or "Architecture Diagram")
        boxes = content.get("boxes", [])
        connections = content.get("connections", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Create box positions in a grid layout
        box_positions = {}

        box_list = boxes if isinstance(boxes, list) else []
        n_boxes = min(8, len(box_list))
        if n_boxes <= 0:
            return

        cols = min(4, n_boxes)
        rows = int(math.ceil(n_boxes / max(1, cols)))

        margin_x = 0.8
        top_y = 2.05
        bottom_margin = 0.75
        gap_x = 0.60
        gap_y = 0.55

        avail_w = max(7.0, SLIDE_W_IN - 2 * margin_x)
        avail_h = max(2.0, SLIDE_H_IN - bottom_margin - top_y)

        box_w = (avail_w - (cols - 1) * gap_x) / max(1, cols)
        box_w = max(2.2, min(3.1, box_w))
        box_h = 1.25 if rows == 1 else 1.20

        total_w = cols * box_w + (cols - 1) * gap_x
        start_x = max(margin_x, (SLIDE_W_IN - total_w) / 2.0)

        total_h = rows * box_h + (rows - 1) * gap_y
        start_y = top_y + max(0.0, (avail_h - total_h) / 2.0)

        draw_boxes: List[tuple[float, float, float, float, str, str]] = []
        for i, box in enumerate(box_list[:8]):  # Max 8 boxes
            row, col = i // cols, i % cols
            x = start_x + col * (box_w + gap_x)
            y = start_y + row * (box_h + gap_y)

            box_label = str(box.get("label", ""))[:30]
            desc = str(box.get("description", ""))[:60]
            box_positions[box_label] = (x, y, box_w, box_h)
            draw_boxes.append((x, y, box_w, box_h, box_label, desc))
        
        # Draw connections first so they sit behind the boxes.
        for conn in connections:
            from_label = str(conn.get("from_box", ""))[:30]
            to_label = str(conn.get("to_box", ""))[:30]
            
            if from_label in box_positions and to_label in box_positions:
                from_x, from_y, from_w, from_h = box_positions[from_label]
                to_x, to_y, to_w, to_h = box_positions[to_label]
                
                # Connect between box edges (not centers) so lines don't cross the boxes/text.
                fx = from_x + from_w / 2
                fy = from_y + from_h / 2
                tx = to_x + to_w / 2
                ty = to_y + to_h / 2
                dx = tx - fx
                dy = ty - fy
                pad = 0.03

                mid_y = (fy + ty) / 2.0
                mid_x = (fx + tx) / 2.0

                if abs(dx) >= abs(dy):
                    # Horizontal connection
                    if dx >= 0:
                        start_x_conn = from_x + from_w + pad
                        end_x_conn = to_x - pad
                    else:
                        start_x_conn = from_x - pad
                        end_x_conn = to_x + to_w + pad
                    start_y_conn = mid_y
                    end_y_conn = mid_y
                else:
                    # Vertical connection
                    if dy >= 0:
                        start_y_conn = from_y + from_h + pad
                        end_y_conn = to_y - pad
                    else:
                        start_y_conn = from_y - pad
                        end_y_conn = to_y + to_h + pad
                    start_x_conn = mid_x
                    end_x_conn = mid_x
                
                try:
                    arrow = s.shapes.add_connector(
                        MSO_CONNECTOR_TYPE.STRAIGHT,
                        Inches(start_x_conn),
                        Inches(start_y_conn),
                        Inches(end_x_conn),
                        Inches(end_y_conn),
                    )
                    arrow.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                    arrow.line.width = Pt(2)
                except Exception:
                    # Skip connection if it fails
                    continue

    def _collect_summary_from_slides(sl_list: List[Dict[str, Any]]) -> List[str]:
        # Reuse the existing takeaways heuristics (dedupe + pad to >=4).
        # This avoids placeholder content and ensures the summary slide has enough items.
        pts = _collect_takeaways(sl_list)
        return pts[:4]

    def render_summary_slide(items: List[str]):
        s = prs.slides.add_slide(blank); apply_bg(s)
        # Round-robin rotation across approved templates (flow chevrons removed)
        global SUMMARY_STYLE_INDEX
        styles = ["banner", "steps"]
        choice = styles[SUMMARY_STYLE_INDEX % len(styles)]
        SUMMARY_STYLE_INDEX += 1
        if choice == "banner":
            _render_summary_banner(s, items, pal=pal)
        else:
            _render_summary_steps(s, items, pal=pal)

    # Render the smart summary just before the final Thank You slide
    # Avoid duplicating the earlier takeaways/at-a-glance content
    if not TAKEAWAYS_RENDERED:
        summary_pts = _collect_summary_from_slides(slides_norm)
        # If summary points would be identical to takeaways (rare), skip silently
        # by checking for low diversity
        uniq_norm = {str(x).strip().lower() for x in summary_pts}
        if len(uniq_norm) >= 3:
            render_summary_slide(summary_pts)

    # After rendering the deck, rotate the double-column bullet style for the next deck
    try:
        DOUBLE_BULLET_STYLE_INDEX = (DOUBLE_BULLET_STYLE_INDEX + 1) % 4
    except Exception:
        pass

    # Always ensure a Thank You at the end
    thanks_sl = next((s for s in slides_norm if _ctype(s) in {"thank_you", "cta"}), None)
    render_thanks(thanks_sl or {"type": "thank_you", "cta": ""})

    prs.save(out_path)
    return out_path
