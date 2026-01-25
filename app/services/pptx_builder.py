from __future__ import annotations

from typing import Any, Dict, List, Tuple
import random
import re
import math
from pptx import Presentation  # type: ignore
from pptx.util import Inches, Pt
from .content_synthesis import synthesize_key_concepts, is_placeholder, BANNED
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE  # type: ignore
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE as MsoShape, MSO_CONNECTOR_TYPE  # type: ignore
from pptx.dml.color import RGBColor  # type: ignore
from .slide6_takeaways_chevrons import render_takeaways_chevrons

SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5

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

        # Harmonise font sizes, bolding, and colors across runs/paragraphs
        for idx, run in enumerate(p.runs):
            run.font.size = Pt(font_size_pt)
            if idx >= 1:  # r1 is the headline
                run.font.bold = True
            if color is not None:
                run.font.color.rgb = RGBColor(*color)

        if explainer:
            for run in p2.runs:
                run.font.size = Pt(max(12, font_size_pt - 4))
                run.font.bold = False
                if color is not None:
                    run.font.color.rgb = RGBColor(*color)

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

def _render_agenda_zebra_autofit(prs: Presentation, slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Agenda", is_cont: bool = False) -> List[str]:
    items = _clean_agenda_items(items, limit=32)
    if not items:
        items = ["Overview", "Discussion", "Q&A", "Next Steps"]

    # Title (do not append '(cont.)' – keep a stable heading across pages)
    title_h = _agenda_title(slide, title, pal=pal)

    # Layout constants
    row_h_base = 0.90
    row_h_min = 0.62
    gap = 0.12
    top = 0.9 + title_h + 0.25
    avail_h = SLIDE_H_IN - 0.8 - top

    def fits_single(n: int, row_h: float) -> bool:
        return (n * row_h + (n - 1) * gap) <= avail_h

    n = len(items)
    row_h = row_h_base
    if not fits_single(n, row_h):
        scale = max(row_h_min / row_h_base, (avail_h - (n - 1) * gap) / (n * row_h_base))
        row_h = max(row_h_min, row_h_base * min(1.0, scale))

    if fits_single(n, row_h):
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
    r = pnum.add_run(); r.text = str(number); r.font.size = Pt(22); r.font.bold = True; r.font.color.rgb = RGBColor(*pal["primary"])

    # Caption under circle (auto-fit)
    cap_tf = slide.shapes.add_textbox(Inches(x_in - 0.35), Inches(y_in + d_in + 0.12), Inches(d_in + 0.7), Inches(0.9)).text_frame
    cap_tf.word_wrap = True
    cap_tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    cp = cap_tf.paragraphs[0]; cp.alignment = PP_ALIGN.CENTER
    cp.text = (label or "(to be refined)")
    cp.font.size = Pt(18); cp.font.bold = True; cp.font.color.rgb = RGBColor(*pal["text"]) if isinstance(pal["text"], tuple) else cp.font.color.rgb

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

def _render_background_timeline_autofit(prs: Presentation, slide, stages: List[str], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Background — Evolution", is_cont: bool = False) -> List[str]:
    items = _clean_agenda_items(stages, limit=20)
    if len(items) < 3:
        items += ["Foundational Phase", "Tools Adoption", "Scaled Analytics"][: 3 - len(items)]

    th = _timeline_title(slide, title, pal=pal)
    # Subtitle for context
    sub = _timeline_subtitle(title, items, None)
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
    _render_agenda_zebra_autofit(prs, s2, d2.get("bullets", [])[:32], pal=pal, title="Agenda", is_cont=False)

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


def build_pptx_from_slides(slides: List[Dict[str, Any]], out_path: str, *, theme_name: str | None = None) -> str:
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
    # DEBUG: Log what slides this function receives
    try:
        slide_types = [s.get("type", "unknown") for s in slides]
        print(f"DEBUG: build_pptx_from_slides received slide types: {slide_types}")
    except Exception:
        pass
    
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W_IN)
    prs.slide_height = Inches(SLIDE_H_IN)

    blank = prs.slide_layouts[6]
    pal = _theme(theme_name)

    # Normalize theme key so we can branch behavior per theme.
    theme_key = (theme_name or "SLEEK").strip().upper()
    is_sleek_theme = theme_key == "SLEEK"
    uses_single_bullet_color = theme_key in {"SLEEK", "MINIMAL FLUX", "BLUE HORIZON"}
    pulse_bullet_slide_index = 0

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
                        font_size_pt=18, bold=True, color=pal["text"])
            
            # Date range
            date_range = str(phase.get("date_range", ""))[:30]
            if date_range:
                _add_textbox(s, x + 0.15, start_y + 0.65, phase_w - 0.3, 0.4, date_range,
                            font_size_pt=12, color=pal.get("muted", pal["text"]))
            
            # Description
            desc = str(phase.get("description", ""))[:150]  # Limit description length
            if desc:
                _add_textbox(s, x + 0.15, start_y + 1.1, phase_w - 0.3, phase_h - 1.25, desc,
                            font_size_pt=11, color=pal["text"])
            
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
        
        # Grid layout: 2x2
        start_x, start_y = 1.0, 2.0
        cell_w, cell_h = 5.5, 2.8
        gap_x, gap_y = 0.8, 0.6
        
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
            _add_textbox(s, x + 0.15, y + 0.1, cell_w - 0.3, 0.4, label,
                        font_size_pt=16, bold=True, color=pal["primary"])
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:60]}" for p in points[:5])  # Max 5 points, truncate long text
                _add_textbox(s, x + 0.15, y + 0.5, cell_w - 0.3, cell_h - 0.65, bullet_text,
                            font_size_pt=11, color=pal["text"])

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
                        font_size_pt=16, bold=True, color=(255, 255, 255))
            
            # Price
            price = str(plan.get("price", ""))[:20]
            if price:
                _add_textbox(s, x + 0.05, start_y + 0.4, col_w - 0.2, 0.3, price,
                            font_size_pt=14, color=(255, 255, 255))
        
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
                        font_size_pt=11, color=pal["text"])
            
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
        
        # Grid layout: 2x2
        start_x, start_y = 1.0, 2.0
        cell_w, cell_h = 5.5, 2.5
        gap_x, gap_y = 0.8, 0.6
        
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
            _add_textbox(s, x + 0.15, y + 0.1, cell_w - 0.3, 0.6, label,
                        font_size_pt=12, bold=True, color=pal["text"])
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:50]}" for p in points[:3])  # Max 3 points
                _add_textbox(s, x + 0.15, y + 0.7, cell_w - 0.3, cell_h - 0.85, bullet_text,
                            font_size_pt=10, color=pal["text"])
        
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
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Two-column layout
        start_x, start_y = 1.0, 2.2
        col_w, col_h = 5.5, 4.0
        gap = 0.8
        
        columns = [
            ("before", "Before", 0),
            ("after", "After", 1)
        ]
        
        for key, label, col_idx in columns:
            x = start_x + col_idx * (col_w + gap)
            col_data = content.get(key, {})
            heading = str(col_data.get("heading", label))[:50]
            points = col_data.get("points", [])
            
            # Column container
            col_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(start_y), Inches(col_w), Inches(col_h))
            col_box.fill.solid(); col_box.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            col_box.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Column heading
            _add_textbox(s, x + 0.15, start_y + 0.15, col_w - 0.3, 0.5, heading,
                        font_size_pt=18, bold=True, color=pal["primary"])
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:60]}" for p in points[:8])  # Max 8 points
                _add_textbox(s, x + 0.15, start_y + 0.7, col_w - 0.3, col_h - 0.85, bullet_text,
                            font_size_pt=12, color=pal["text"])

    def _render_architecture(sl) -> None:
        """Render architecture diagram with boxes and directional arrows."""
        content = _content(sl)
        title = content.get("title", "Architecture Diagram")
        boxes = content.get("boxes", [])
        connections = content.get("connections", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Create box positions in a grid layout
        box_positions = {}
        start_x, start_y = 1.0, 2.0
        box_w, box_h = 2.5, 1.2
        gap_x, gap_y = 0.8, 0.6
        cols = 4
        
        for i, box in enumerate(boxes[:8]):  # Max 8 boxes
            row, col = i // cols, i % cols
            x = start_x + col * (box_w + gap_x)
            y = start_y + row * (box_h + gap_y)
            
            box_label = str(box.get("label", ""))[:30]
            box_positions[box_label] = (x, y, box_w, box_h)
            
            # Box shape
            box_shape = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(box_w), Inches(box_h))
            box_shape.fill.solid(); box_shape.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            box_shape.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Box label
            _add_textbox(s, x + 0.1, y + 0.1, box_w - 0.2, 0.4, box_label,
                        font_size_pt=12, bold=True, color=pal["text"])
            
            # Box description
            desc = str(box.get("description", ""))[:60]
            if desc:
                _add_textbox(s, x + 0.1, y + 0.5, box_w - 0.2, box_h - 0.6, desc,
                            font_size_pt=9, color=pal.get("muted", pal["text"]))
        
        # Draw connections
        for conn in connections:
            from_label = str(conn.get("from_box", ""))[:30]
            to_label = str(conn.get("to_box", ""))[:30]
            
            if from_label in box_positions and to_label in box_positions:
                from_x, from_y, from_w, from_h = box_positions[from_label]
                to_x, to_y, to_w, to_h = box_positions[to_label]
                
                # Connection from center of from-box to center of to-box
                start_x_conn = from_x + from_w / 2
                start_y_conn = from_y + from_h / 2
                end_x_conn = to_x + to_w / 2
                end_y_conn = to_y + to_h / 2
                
                try:
                    arrow = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, 
                                                  Inches(start_x_conn), Inches(start_y_conn),
                                                  Inches(end_x_conn), Inches(end_y_conn))
                    arrow.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                    arrow.line.width = Pt(2)
                    
                    # Connection label
                    conn_label = str(conn.get("label", ""))[:30]
                    if conn_label:
                        label_x = (start_x_conn + end_x_conn) / 2
                        label_y = (start_y_conn + end_y_conn) / 2
                        _add_textbox(s, label_x - 0.5, label_y - 0.2, 1.0, 0.4, conn_label,
                                    font_size_pt=8, color=pal["text"])
                except Exception:
                    # Skip connection if it fails
                    continue

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
        # Single-slide agenda per topic; extra items are ignored instead of paginated
        _render_agenda_zebra_autofit(prs, s, c.get("bullets", [])[:32], pal=pal, title="Agenda", is_cont=False)

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
        if len(labs) >= 2:
            a, b = labs[0], labs[-1]
            return f"From {a} to {b}"
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
        base_secondary = pal.get("secondary", pal.get("bg", (241, 245, 249)))
        base_muted = pal.get("muted", pal.get("text", base_primary))
        bg_rgb = pal.get("bg", (255, 255, 255))

        # For the SLEEK theme, keep all large step fills firmly in the blue
        # family (no grey/muted blocks). We now derive the entire progression
        # from the primary only, so arrows/blocks/cards are always clearly
        # blue rather than washed-out neutrals.
        if is_sleek_theme:
            p = base_primary
            return [
                _shade(p, 0),     # primary
                _shade(p, -18),   # darker
                _shade(p, 18),    # lighter
                _shade(p, -32),   # deepest
                _shade(p, 8),     # mid light
                _shade(p, -8),    # mid dark
            ]

        # Other themes continue to use the original progression, including
        # their muted tone, until per-theme rules are provided.
        if not _is_dark_rgb(bg_rgb) and not _is_dark_rgb(base_secondary):
            base_secondary = _shade(base_secondary, -45)

        accent_light = _shade(base_primary, 20)

        return [
            base_primary,
            base_secondary,
            accent_light,
            _shade(base_primary, -15),
            _shade(accent_light, -10),
            _shade(base_muted, 10),
        ]

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

    def render_timeline_linear(title_txt: str, points: List[str], topic_src: str):
        """Colorful linear timeline: chips on a soft baseline with captions."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        # Title
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=42, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        # Subtitle
        sub = _timeline_subtitle(topic_src, points, "linear")
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

    def render_timeline_horizontal(title_txt: str, points: List[str], topic_src: str):
        """Colorful horizontal chevrons timeline (steps as colored arrows)."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=40, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub_raw = _timeline_subtitle(topic_src, points, "horizontal")
        # Full subtitle, wrapped and auto-fit to stay within the presentation area
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
    def render_process_timeline_alt(title_txt: str, points: List[str], topic_src: str):
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Process", font_size_pt=40, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub = _timeline_subtitle(topic_src, points, "process-alt")
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

    def render_timeline_vchevrons(title_txt: str, points: List[str], topic_src: str):
        """Vertical chevron stack: left icon bar + right chevron rows (like provided sample)."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=40, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub = _timeline_subtitle(topic_src, points, "v-chevrons")
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

    def render_process_variant(title_txt: str, points: List[str], topic_src: str):
        global PROCESS_STYLE_INDEX
        sel = PROCESS_STYLE_INDEX % 3
        if sel == 0:
            render_timeline_horizontal(title_txt, points, topic_src)
        elif sel == 1:
            render_process_timeline_alt(title_txt, points, topic_src)
        else:
            render_timeline_vchevrons(title_txt, points, topic_src)
        PROCESS_STYLE_INDEX += 1

    def render_timeline_vertical(title_txt: str, points: List[str], topic_src: str):
        """Colorful vertical timeline with soft spine and numbered bubbles."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        dark_bg = _is_dark_rgb(pal.get("bg", (255,255,255)))
        # Title
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Timeline", font_size_pt=42, bold=True, color=(255, 255, 255) if dark_bg else TITLE_COL, word_wrap=True, auto_fit=True)
        sub = _timeline_subtitle(topic_src, points, "vertical")
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

    def render_timeline_roadmap(title_txt: str, points: List[str], topic_src: str):
        """Roadmap path with colored location pins along a meandering soft path."""
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 10.8, 0.95, title_txt or "Roadmap", font_size_pt=42, bold=True, color=pal["primary"], word_wrap=True, auto_fit=True)
        sub = _timeline_subtitle(topic_src, points, "roadmap")
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

            def _lighter(rgb: Tuple[int,int,int], pct: float = 0.35) -> Tuple[int,int,int]:
                r,g,b = rgb
                return (min(255, int(r + (255-r)*pct)), min(255, int(g + (255-g)*pct)), min(255, int(b + (255-b)*pct)))

            def render_col(items: List[str], base_x: float, colors: List[Tuple[int, int, int]], start_idx: int) -> int:
                y = top_y
                subset = items[:3]
                for i, lab in enumerate(subset):
                    idx = start_idx + i
                    # Use deck-level chosen style so all double-column slides in this deck match
                    global CURRENT_DOUBLE_BULLET_STYLE
                    style_id = (CURRENT_DOUBLE_BULLET_STYLE if 'CURRENT_DOUBLE_BULLET_STYLE' in globals() else (DOUBLE_BULLET_STYLE_INDEX % 4))
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

    def render_table(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        c = _content(sl)
        table_obj = c.get("table") or {}
        left = table_obj.get("left") or {}
        right = table_obj.get("right") or {}
        headers = [left.get("heading") or "Option A", right.get("heading") or "Option B"]
        left_pts = [str(x) for x in (left.get("points") or [])]
        right_pts = [str(x) for x in (right.get("points") or [])]
        # Title uses the provided slide title (topic-aligned)
        comp_title = sl.get("title") or "Comparison"
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, comp_title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)
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
            from pptx.util import Pt
            from pptx.enum.text import MSO_ANCHOR
            g1 = RGBColor(209, 213, 219)
            g2 = RGBColor(229, 231, 235)
            for rr in range(body_rows + 1):
                for cc in range(2):
                    cell = table.cell(rr, cc)
                    # padding
                    cell.margin_left = Pt(6)
                    cell.margin_right = Pt(6)
                    cell.margin_top = Pt(4)
                    cell.margin_bottom = Pt(4)
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
                        cell.border_top.color.rgb = g1; cell.border_top.width = Pt(0.5)
                        cell.border_bottom.color.rgb = g1; cell.border_bottom.width = Pt(0.5)
                        if cc == 0:
                            cell.border_left.color.rgb = g2; cell.border_left.width = Pt(0.5)
                        cell.border_right.color.rgb = g2; cell.border_right.width = Pt(0.5)
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
        if "risk" in txt or "risk assessment" in txt or "risk matrix" in txt:
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

    def _render_impact_map(simple: bool, sl) -> None:
        title, bullets, content = _infographic_items(sl)
        if not bullets:
            bullets = _synthesize_topic_bullets(title)[:5]
        n = max(3, min(5, len(bullets)))
        items = bullets[:n]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        # Soft background band behind the impact map area for visual structure
        band_x, band_y, band_w, band_h = 0.9, 2.0, SLIDE_W_IN - 1.8, 3.8
        band = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(band_y), Inches(band_w), Inches(band_h))
        band.fill.solid(); band.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
        band.line.fill.background()
        try:
            band.shadow.inherit = False; band.shadow.visible = True; band.shadow.blur = 18; band.shadow.distance = 3
        except Exception:
            pass

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
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        rc = pc.add_run(); rc.text = center_label or "Impact"
        rc.font.size = Pt(20); rc.font.bold = True; rc.font.color.rgb = RGBColor(255, 255, 255)

        # Positions for surrounding nodes: tuned for up to 5, fully inside the band
        node_w, node_h = 3.1, 1.30
        left_x = 1.1
        right_x = SLIDE_W_IN - 1.1 - node_w
        top_y = 2.4
        mid_y = 3.8
        bot_y = 4.9
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

            # Constrain heading and body length so text fits comfortably in the card
            # Even with very long AI bullets, this keeps content to ~3 short lines max.
            head_txt = head or raw
            if head_txt:
                hw = head_txt.split()
                if len(hw) > 5:
                    head_txt = " ".join(hw[:5])
                if len(head_txt) > 32:
                    cut = head_txt[:32]
                    head_txt = cut.rsplit(" ", 1)[0] if " " in cut else cut

            body_txt = tail or ""
            if body_txt:
                bw = body_txt.split()
                if len(bw) > 12:
                    body_txt = " ".join(bw[:12])
                if len(body_txt) > 70:
                    cut_b = body_txt[:70]
                    body_txt = (cut_b.rsplit(" ", 1)[0] if " " in cut_b else cut_b) + "..."

            # If the heading is already fairly long, prefer to drop the body
            # completely so we never exceed the card height.
            head_words = len(head_txt.split()) if head_txt else 0
            body_words = len(body_txt.split()) if body_txt else 0
            if head_words >= 4 or body_words > 10:
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
            tb = s.shapes.add_textbox(Inches(nx + 0.60), Inches(ny + 0.18), Inches(node_w - 0.95), Inches(node_h - 0.40)).text_frame
            tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            tb.margin_top = Pt(0); tb.margin_bottom = Pt(0)
            p = tb.paragraphs[0]; p.clear(); p.alignment = PP_ALIGN.LEFT
            p.line_spacing = 1.0
            title_txt = head_txt
            r1 = p.add_run(); r1.text = title_txt; r1.font.size = Pt(15); r1.font.bold = True; r1.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))
            if not simple and body_txt:
                p2 = tb.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
                p2.line_spacing = 1.05
                r2 = p2.add_run(); r2.text = body_txt; r2.font.size = Pt(10); r2.font.color.rgb = RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

        # Optional connectors from center to nodes in complex mode.
        # Draw straight straight-line connectors from the hexagon edges to the
        # card edges so that lines attach cleanly without cutting through shapes.
        if not simple and nodes:
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
                    conn.line.color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
                    conn.line.width = Pt(1.75)
                except Exception:
                    # If connectors cannot be drawn, skip gracefully without
                    # impacting the rest of the layout.
                    continue

    def _render_process_flow_chevron_infographic(sl) -> None:
        title, bullets, _ = _infographic_items(sl)
        if not bullets:
            bullets = _synthesize_topic_bullets(title)[:6]
        steps = bullets[:6]
        # Reuse the existing horizontal chevron process renderer for visual consistency
        render_timeline_horizontal(title or "Process", steps, topic_src=title)

    def _render_risk_matrix_advanced(sl) -> None:
        title, bullets, content = _infographic_items(sl)
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title or "Risk Assessment Matrix", font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        # Fixed 3x4 grid (Likelihood x Impact)
        rows, cols = 3, 4
        x_labels = content.get("x_labels") or ["Low", "Moderate", "High", "Critical"]
        y_labels = content.get("y_labels") or ["Rare", "Possible", "Likely"]

        grid_x, grid_y, grid_w, grid_h = 2.4, 2.3, 6.4, 3.3
        cell_w = grid_w / cols
        cell_h = grid_h / rows

        # Color scale from green (low) to red (high)
        col_scale = [
            (34, 197, 94),   # green
            (234, 179, 8),   # amber
            (249, 115, 22),  # orange
            (239, 68, 68),   # red
        ]

        # Draw cells
        for r in range(rows):
            for c_idx in range(cols):
                x = grid_x + c_idx * cell_w
                y = grid_y + (rows - 1 - r) * cell_h
                col_rgb = col_scale[c_idx]
                rect = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(x), Inches(y), Inches(cell_w), Inches(cell_h))
                rect.fill.solid(); rect.fill.fore_color.rgb = RGBColor(*col_rgb)
                rect.line.color.rgb = RGBColor(255, 255, 255)
        _draw_table_grid_rect(s, grid_x, grid_y, grid_w, grid_h, rows, cols, rgb=(209, 213, 219), weight_pt=0.75)

        # Axis labels
        # Impact (x-axis)
        for c_idx in range(cols):
            lab = str(x_labels[c_idx] if c_idx < len(x_labels) else "")
            _add_textbox(s, grid_x + c_idx * cell_w, grid_y + grid_h + 0.10, cell_w, 0.40, lab, font_size_pt=14, bold=True, align="center", color=pal.get("text", (24, 24, 27)))
        _add_textbox(s, grid_x, grid_y + grid_h + 0.60, grid_w, 0.40, "Impact", font_size_pt=14, bold=True, align="center", color=pal.get("muted", pal.get("text", (71, 85, 105))))

        # Likelihood (y-axis)
        for r in range(rows):
            lab = str(y_labels[rows - 1 - r] if r < len(y_labels) else "")
            _add_textbox(s, grid_x - 1.05, grid_y + r * cell_h, 1.0, 0.40, lab, font_size_pt=14, bold=True, align="right", color=pal.get("text", (24, 24, 27)))
        _add_textbox(s, grid_x - 1.45, grid_y - 0.10, 1.40, 0.60, "Likelihood", font_size_pt=14, bold=True, align="right", color=pal.get("muted", pal.get("text", (71, 85, 105))))

        # Bullet panel for specific risks (if provided)
        if bullets:
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
                bullets[:6],
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
        # Center the loop slightly below mid-height so the title has breathing room
        cx, cy = SLIDE_W_IN / 2.0, 4.0
        center_d = 1.9
        center = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - center_d / 2), Inches(cy - center_d / 2), Inches(center_d), Inches(center_d))
        center.fill.solid(); center.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal.get("bg", (240, 245, 255))))
        center.line.color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
        tfc = center.text_frame; tfc.clear(); tfc.word_wrap = True; tfc.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        pc = tfc.paragraphs[0]; pc.alignment = PP_ALIGN.CENTER
        rc = pc.add_run(); rc.text = center_lbl; rc.font.size = Pt(14); rc.font.bold = True; rc.font.color.rgb = RGBColor(*pal.get("text", (24, 24, 27)))

        # Subtle orbit ring to anchor the loop visually and use more horizontal space
        ring_outer_d = 5.2
        ring_inner_d = 4.2
        outer = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ring_outer_d / 2), Inches(cy - ring_outer_d / 2), Inches(ring_outer_d), Inches(ring_outer_d))
        outer.fill.solid(); outer.fill.fore_color.rgb = RGBColor(*(pal.get("secondary", pal.get("bg", (240, 245, 255)))))
        outer.line.fill.background()
        inner = s.shapes.add_shape(MsoShape.OVAL, Inches(cx - ring_inner_d / 2), Inches(cy - ring_inner_d / 2), Inches(ring_inner_d), Inches(ring_inner_d))
        inner.fill.solid(); inner.fill.fore_color.rgb = RGBColor(*(pal.get("bg", (255, 255, 255))))
        inner.line.fill.background()
        # Place stage nodes near the orbit edge so the diagram fills the band
        radius = 2.1
        step_cols = _step_colors()
        for i, raw in enumerate(stages, start=0):
            angle = 2.0 * math.pi * i / n
            nx = cx + radius * float(math.cos(angle))
            ny = cy + radius * float(math.sin(angle))
            node_d = 1.3
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
            # Keep bubble labels concise: cap to ~5 words to avoid vertical overflow
            words = label.split()
            if len(words) > 5:
                label = " ".join(words[:5])
            tfn = node.text_frame; tfn.clear(); tfn.word_wrap = True; tfn.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            pn = tfn.paragraphs[0]; pn.alignment = PP_ALIGN.CENTER
            rn = pn.add_run(); rn.text = label; rn.font.size = Pt(11); rn.font.bold = True; rn.font.color.rgb = RGBColor(*_contrast_text(col_rgb))

            # Arrow to next node (clockwise)
            nxt_angle = 2.0 * math.pi * ((i + 1) % n) / n
            tx = cx + radius * float(math.cos(nxt_angle))
            ty = cy + radius * float(math.sin(nxt_angle))
            conn = s.shapes.add_connector(1, Inches(nx), Inches(ny), Inches(tx), Inches(ty))
            conn.line.color.rgb = RGBColor(*pal.get("primary", (59, 130, 246)))
            conn.line.width = Pt(2)

    def _render_layered_benefits_stack(sl) -> None:
        title, bullets, _ = _infographic_items(sl)
        benefits = bullets or _synthesize_topic_bullets(title)[:6]
        n = max(4, min(6, len(benefits)))
        benefits = benefits[:n]

        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title or "Benefits", font_size_pt=32, bold=True, color=TITLE_COL, word_wrap=True, auto_fit=True)

        base_col = pal.get("primary", (59, 130, 246))
        depth_delta = -10
        layer_w, layer_h = 9.0, 0.90
        start_x = 2.0
        start_y = 2.4

        for idx, raw in enumerate(reversed(benefits), start=0):
            # Bottom layer rendered first; each higher layer shifted up and right
            x = start_x + idx * 0.25
            y = start_y + (n - 1 - idx) * (layer_h + 0.18)
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
            r1 = p.add_run(); r1.text = head or raw; r1.font.size = Pt(18); r1.font.bold = True; r1.font.color.rgb = RGBColor(*fg)
            if tail:
                p2 = tb.add_paragraph(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(12); r2.font.color.rgb = RGBColor(235, 244, 255) if fg == (255, 255, 255) else RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))

    def render_infographic(sl):
        t = _ctype(sl)
        title, bullets, content = _infographic_items(sl)
        intent = _infer_infographic_intent(title, content)
        count = len(bullets)

        # Deterministic selection rules based on intent and density
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
        if intent == "IMPACT" and count > 4:
            _render_impact_map(False, sl)  # complex
            return
        if intent == "IMPACT" and count <= 4:
            _render_impact_map(True, sl)   # simple
            return

        # Fallbacks: use simple impact map for general infographics or treat
        # key_takeaways as a compact layered stack.
        if t == "key_takeaways" and count >= 3:
            _render_layered_benefits_stack(sl)
        else:
            _render_impact_map(True, sl)

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
            bullets = ["(to be refined)"]
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

    for sl in slides_norm:
        t = _ctype(sl)
        # Force replacement: any slide carrying a 'cards' array should render as a horizontal process timeline
        try:
            cards_arr = _content(sl).get("cards")
            if isinstance(cards_arr, list) and any(str(x).strip() for x in cards_arr):
                stages = [str(x).strip() for x in cards_arr if str(x).strip()]
                render_process_variant((sl.get("title") or "Process"), stages, topic_src=(sl.get("title") or ""))
                continue
        except Exception:
            pass
        if t == "title":
            render_title(sl)
        elif t == "agenda":
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
            render_one_idea(sl)
        elif t in {"double_column", "deep_dive"}:
            render_double(sl)
        elif t in {"comparison", "table"}:
            render_table(sl)
        elif t in {"infographic", "key_takeaways"}:
            render_infographic(sl)
        elif t in {"cards", "card", "card_style", "process_timeline_alt"}:
            # Use the rotation for all process-like slide requests so all styles, including bubbles, appear
            c = _content(sl)
            stages = [str(x).strip() for x in (c.get("cards") or c.get("bullets") or []) if str(x).strip()]
            render_process_variant((sl.get("title") or "Process"), stages, topic_src=(sl.get("title") or ""))
        elif t == "roadmap":
            print(f"DEBUG: Rendering roadmap slide")
            _render_roadmap(sl)
        elif t == "swot":
            print(f"DEBUG: Rendering swot slide")
            _render_swot(sl)
        elif t == "kpi":
            print(f"DEBUG: Rendering kpi slide")
            _render_kpi(sl)
        elif t == "pricing":
            print(f"DEBUG: Rendering pricing slide")
            _render_pricing(sl)
        elif t == "risk_matrix":
            print(f"DEBUG: Rendering risk_matrix slide")
            _render_risk_matrix(sl)
        elif t == "before_after":
            print(f"DEBUG: Rendering before_after slide")
            _render_before_after(sl)
        elif t == "architecture":
            print(f"DEBUG: Rendering architecture slide")
            _render_architecture(sl)
        elif t in {"thank_you", "cta"}:
            # skip for now; rendered at end to guarantee last
            continue
        else:
            # Fallback: bullets slide if bullets available
            if _content(sl).get("bullets"):
                render_bullets(sl)
            # Ignore unsupported types silently

    # --- Smart Infographic Summary (rotate among 4 templates) ---
    def _collect_summary_from_slides(sl_list: List[Dict[str, Any]]) -> List[str]:
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
        if len(uniq) < 3:
            # pad with topic-driven generics
            topic = (slides_norm[0].get("title") or "Key Insights").strip()
            base = [f"{topic}: summary", "Outcomes", "Next steps", "Owners", "Risks/Effort"]
            for b in base:
                if len(uniq) >= 3:
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
                    r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(13); r2.font.color.rgb = RGBColor(*tail_rgb)
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
                r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(13); r2.font.color.rgb = RGBColor(235, 244, 255) if txt_rgb == (255, 255, 255) else RGBColor(*pal.get("muted", pal.get("text", (71, 85, 105))))
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
        
        # Grid layout: 2x2
        start_x, start_y = 1.0, 2.0
        cell_w, cell_h = 5.5, 2.8
        gap_x, gap_y = 0.8, 0.6
        
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
            _add_textbox(s, x + 0.15, y + 0.1, cell_w - 0.3, 0.4, label,
                        font_size_pt=16, bold=True, color=pal["primary"])
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:60]}" for p in points[:5])  # Max 5 points, truncate long text
                _add_textbox(s, x + 0.15, y + 0.5, cell_w - 0.3, cell_h - 0.65, bullet_text,
                            font_size_pt=11, color=pal["text"], word_wrap=True, auto_fit=True)

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
        
        # Grid layout: 2x2
        start_x, start_y = 1.0, 2.0
        cell_w, cell_h = 5.5, 2.5
        gap_x, gap_y = 0.8, 0.6
        
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
            _add_textbox(s, x + 0.15, y + 0.1, cell_w - 0.3, 0.6, label,
                        font_size_pt=12, bold=True, color=pal["text"], word_wrap=True)
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:50]}" for p in points[:3])  # Max 3 points
                _add_textbox(s, x + 0.15, y + 0.7, cell_w - 0.3, cell_h - 0.85, bullet_text,
                            font_size_pt=10, color=pal["text"], word_wrap=True, auto_fit=True)
        
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
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Two-column layout
        start_x, start_y = 1.0, 2.2
        col_w, col_h = 5.5, 4.0
        gap = 0.8
        
        columns = [
            ("before", "Before", 0),
            ("after", "After", 1)
        ]
        
        for key, label, col_idx in columns:
            x = start_x + col_idx * (col_w + gap)
            col_data = content.get(key, {})
            heading = str(col_data.get("heading", label))[:50]
            points = col_data.get("points", [])
            
            # Column container
            col_box = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(start_y), Inches(col_w), Inches(col_h))
            col_box.fill.solid(); col_box.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            col_box.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Column heading
            _add_textbox(s, x + 0.15, start_y + 0.15, col_w - 0.3, 0.5, heading,
                        font_size_pt=18, bold=True, color=pal["primary"], word_wrap=True)
            
            # Bullet points
            if points:
                bullet_text = "\n".join(f"• {str(p)[:60]}" for p in points[:8])  # Max 8 points
                _add_textbox(s, x + 0.15, start_y + 0.7, col_w - 0.3, col_h - 0.85, bullet_text,
                            font_size_pt=12, color=pal["text"], word_wrap=True, auto_fit=True)

    def _render_architecture(sl) -> None:
        """Render architecture diagram with boxes and directional arrows."""
        content = _content(sl)
        title = content.get("title", "Architecture Diagram")
        boxes = content.get("boxes", [])
        connections = content.get("connections", [])
        
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 0.9, title, font_size_pt=32, bold=True, color=TITLE_COL)
        
        # Create box positions in a grid layout
        box_positions = {}
        start_x, start_y = 1.0, 2.0
        box_w, box_h = 2.5, 1.2
        gap_x, gap_y = 0.8, 0.6
        cols = 4
        
        for i, box in enumerate(boxes[:8]):  # Max 8 boxes
            row, col = i // cols, i % cols
            x = start_x + col * (box_w + gap_x)
            y = start_y + row * (box_h + gap_y)
            
            box_label = str(box.get("label", ""))[:30]
            box_positions[box_label] = (x, y, box_w, box_h)
            
            # Box shape
            box_shape = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(box_w), Inches(box_h))
            box_shape.fill.solid(); box_shape.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            box_shape.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
            
            # Box label
            _add_textbox(s, x + 0.1, y + 0.1, box_w - 0.2, 0.4, box_label,
                        font_size_pt=12, bold=True, color=pal["text"], word_wrap=True)
            
            # Box description
            desc = str(box.get("description", ""))[:60]
            if desc:
                _add_textbox(s, x + 0.1, y + 0.5, box_w - 0.2, box_h - 0.6, desc,
                            font_size_pt=9, color=pal.get("muted", pal["text"]), word_wrap=True, auto_fit=True)
        
        # Draw connections
        for conn in connections:
            from_label = str(conn.get("from_box", ""))[:30]
            to_label = str(conn.get("to_box", ""))[:30]
            
            if from_label in box_positions and to_label in box_positions:
                from_x, from_y, from_w, from_h = box_positions[from_label]
                to_x, to_y, to_w, to_h = box_positions[to_label]
                
                # Connection from center of from-box to center of to-box
                start_x_conn = from_x + from_w / 2
                start_y_conn = from_y + from_h / 2
                end_x_conn = to_x + to_w / 2
                end_y_conn = to_y + to_h / 2
                
                try:
                    arrow = s.shapes.add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, 
                                                  Inches(start_x_conn), Inches(start_y_conn),
                                                  Inches(end_x_conn), Inches(end_y_conn))
                    arrow.line.color.rgb = RGBColor(*pal.get("primary", pal["text"]))
                    arrow.line.width = Pt(2)
                    
                    # Connection label
                    conn_label = str(conn.get("label", ""))[:30]
                    if conn_label:
                        label_x = (start_x_conn + end_x_conn) / 2
                        label_y = (start_y_conn + end_y_conn) / 2
                        _add_textbox(s, label_x - 0.5, label_y - 0.2, 1.0, 0.4, conn_label,
                                    font_size_pt=8, color=pal["text"], word_wrap=True)
                except Exception:
                    # Skip connection if it fails
                    continue

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
