from __future__ import annotations

from typing import Any, Dict, List, Tuple
import random
import re
from pptx import Presentation  # type: ignore
from pptx.util import Inches, Pt
from .content_synthesis import synthesize_key_concepts, is_placeholder, BANNED
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE  # type: ignore
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE as MsoShape  # type: ignore
from pptx.dml.color import RGBColor  # type: ignore
from .slide6_takeaways_chevrons import render_takeaways_chevrons

SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5

# Simple theme palettes
# Each theme maps to: bg, text, primary, secondary
THEME_PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "SLEEK": {
        # Subtle cool gray instead of pure white to match Sleek theme intent
        "bg": (242, 245, 249),
        "text": (33, 37, 41),
        "primary": (41, 98, 255),   # deep blue
        "secondary": (235, 242, 255),
    },
    "PULSE": {
        "bg": (255, 255, 255),
        "text": (24, 24, 27),
        "primary": (220, 38, 38),   # red
        "secondary": (254, 226, 226),
    },
    "MINIMAL FLUX": {
        "bg": (255, 255, 255),
        "text": (31, 41, 55),
        "primary": (99, 102, 241),  # indigo
        "secondary": (238, 242, 255),
    },
    "BLUE HORIZON": {
        "bg": (250, 253, 255),
        "text": (15, 23, 42),
        "primary": (2, 132, 199),   # sky
        "secondary": (219, 234, 254),
    },
}

# Global rotating index for summary template selection (round-robin)
SUMMARY_STYLE_INDEX: int = 0


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
    m = _re.search(r"[:;\u2013\u2014\-\.\,]", t)
    if m and m.start() > 0:
        return t[:m.start()].strip(), t[m.start():].lstrip()
    # fallback: first 4 words bold
    parts = t.split()
    head = " ".join(parts[:4])
    tail = " ".join(parts[4:])
    return head, tail


def _add_bullets(slide, x, y, w, h, bullets: List[str], *, font_size_pt: int = 20, min_items: int = 0, synth_fill: str = "TBD", color: Tuple[int, int, int] | None = None, word_wrap: bool = True, auto_fit: bool = False, line_spacing: float = 1.45, bold_first_phrase: bool = False, space_before_pt: int = 2, space_after_pt: int = 12):
    items = [s.strip() for s in (bullets or []) if str(s).strip()]
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
        # add bullet symbol
        r0 = p.add_run(); r0.text = "• "
        if bold_first_phrase:
            head, tail = _split_head_tail(txt)
            if head:
                r1 = p.add_run(); r1.text = head + (" " if tail else "")
                r1.font.bold = True
            if tail:
                r2 = p.add_run(); r2.text = tail
        else:
            r = p.add_run(); r.text = txt
        p.level = 0
        try:
            p.space_before = Pt(space_before_pt)
            p.space_after = Pt(space_after_pt)
        except Exception:
            pass
        try:
            p.line_spacing = line_spacing
        except Exception:
            pass
        for r in p.runs:
            r.font.size = Pt(font_size_pt)
            r.font.bold = True if (bold_first_phrase and r is not r0 and p.runs.index(r) == 1) else False
            if color is not None:
                r.font.color.rgb = RGBColor(*color)
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
    _add_textbox(slide, 1.0, 0.9, 9.0, 0.95, title, font_size_pt=44, bold=True, color=pal["primary"])
    return 0.95  # inches

def _agenda_accent(slide, top_in: float, height_in: float, *, pal: Dict[str, Tuple[int, int, int]]):
    bar_left = 1.0 - 0.35
    h = max(0.5, height_in)
    bar = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(bar_left), Inches(top_in), Inches(0.12), Inches(h))
    bar.fill.solid(); bar.fill.fore_color.rgb = RGBColor(*pal["primary"]) ; bar.line.fill.background()

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

    # Title
    title_h = _agenda_title(slide, f"{title} (cont.)" if is_cont else title, pal=pal)

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
    _add_textbox(slide, 1.0, 0.9, 10.5, 0.95, title, font_size_pt=42, bold=True, color=pal["primary"])
    return 0.95

def _timeline_baseline(slide, x1_in: float, x2_in: float, y_in: float, *, pal: Dict[str, Tuple[int, int, int]]):
    # STRAIGHT_CONNECTOR1 = 1
    line = slide.shapes.add_connector(1, Inches(x1_in), Inches(y_in), Inches(x2_in), Inches(y_in))
    line.line.color.rgb = RGBColor(*pal.get("secondary", pal["text"]))
    line.line.width = Pt(3)
    return line

def _timeline_node(slide, x_in: float, y_in: float, d_in: float, label: str, number: int, *, pal: Dict[str, Tuple[int, int, int]]):
    d = Inches(d_in)
    # OVAL = 1
    node = slide.shapes.add_shape(1, Inches(x_in), Inches(y_in), d, d)
    # Chip fill (light)
    chip_rgb = pal.get("secondary") or pal["bg"]
    node.fill.solid(); node.fill.fore_color.rgb = RGBColor(*chip_rgb)
    node.line.color.rgb = RGBColor(*pal["primary"])

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
    for i, label in enumerate(items):
        cx = (x_left_center + i * (d + gap)) - d / 2
        _timeline_node(slide, cx, y_in, d, label, numbered_start + i, pal=pal)
    return x_right_center + d / 2

def _render_background_timeline_autofit(prs: Presentation, slide, stages: List[str], *, pal: Dict[str, Tuple[int, int, int]], title: str = "Background — Evolution", is_cont: bool = False) -> List[str]:
    items = _clean_agenda_items(stages, limit=20)
    if len(items) < 3:
        items += ["Foundational Phase", "Tools Adoption", "Scaled Analytics"][: 3 - len(items)]

    th = _timeline_title(slide, f"{title} (cont.)" if is_cont else title, pal=pal)
    base_top = 0.9 + th + 0.25
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
    card.fill.solid(); card.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
    card.line.color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))

    # icon bubble
    ico = slide.shapes.add_shape(MsoShape.OVAL, Inches(x_in + 0.18), Inches(y_in + 0.18), Inches(0.36), Inches(0.36))
    ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(*pal["primary"]) ; ico.line.color.rgb = RGBColor(*pal["primary"]) 
    itf = ico.text_frame; itf.clear()
    ip = itf.paragraphs[0]; ip.alignment = PP_ALIGN.CENTER
    ir = ip.add_run(); ir.text = "✓"; ir.font.size = Pt(12)

    # title
    tbox = slide.shapes.add_textbox(Inches(x_in + 0.70), Inches(y_in + 0.12), Inches(w_in - 0.90), Inches(0.40)).text_frame
    p1 = tbox.paragraphs[0]; p1.text = title
    p1.font.size = Pt(20); p1.font.bold = True; p1.font.color.rgb = RGBColor(*pal["text"]) if isinstance(pal["text"], tuple) else p1.font.color.rgb

    # description
    db = slide.shapes.add_textbox(Inches(x_in + 0.70), Inches(y_in + 0.52), Inches(w_in - 0.90), Inches(h_in - 0.66)).text_frame
    db.word_wrap = True; db.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p2 = db.paragraphs[0]; p2.text = desc
    p2.font.size = Pt(14); p2.font.color.rgb = RGBColor(*pal.get("muted", pal["text"]))

def _render_key_concepts_cards_autofit(prs: Presentation, slide, concepts: List[str], desc_map: Dict[str, str] | None, *, pal: Dict[str, Tuple[int, int, int]], title: str = "Key Concepts", is_cont: bool = False) -> List[str]:
    titles = _clean_concepts(concepts)

    th = _kc_title(slide, f"{title} (cont.)" if is_cont else title, pal=pal)

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
    _add_textbox(slide, 1.0, 0.9, 10.0, 0.95, title, font_size_pt=42, bold=True, color=pal["primary"])
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
    th = _takeaways_title(slide, f"{title} (cont.)" if is_cont else title, pal=pal)

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

    # Slide 2 – Agenda
    s2 = prs.slides.add_slide(blank)
    apply_bg(s2)
    d2 = pos.get(2, {})
    overflow = _render_agenda_zebra_autofit(prs, s2, d2.get("bullets", [])[:32], pal=pal, title="Agenda", is_cont=False)
    while overflow:
        s2c = prs.slides.add_slide(blank)
        apply_bg(s2c)
        overflow = _render_agenda_zebra_autofit(prs, s2c, overflow, pal=pal, title="Agenda", is_cont=True)

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

    overflow_bg = _render_background_timeline_autofit(prs, s3, stages, pal=pal, title="Background — Evolution", is_cont=False)
    while overflow_bg:
        s3c = prs.slides.add_slide(blank)
        apply_bg(s3c)
        overflow_bg = _render_background_timeline_autofit(prs, s3c, overflow_bg, pal=pal, title="Background — Evolution", is_cont=True)

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
    overflow_kc = _render_key_concepts_cards_autofit(prs, s4, titles4, desc_map4, pal=pal, title="Key Concepts", is_cont=False)
    while overflow_kc:
        s4c = prs.slides.add_slide(blank)
        apply_bg(s4c)
        overflow_kc = _render_key_concepts_cards_autofit(prs, s4c, overflow_kc, desc_map4, pal=pal, title="Key Concepts", is_cont=True)

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

    _add_textbox(s5, 1.0, 0.9, 11.3, 1.0, comp_title, font_size_pt=32, bold=True, color=pal["primary"])
    # Style header row
    for c in range(2):
        cell = table.cell(0, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(*pal["primary"])
        _set_cell_text(cell, headers[c] or ("Option A" if c == 0 else "Option B"), size=20, bold=True, align_center=True, color=(255, 255, 255))
    # rows
    for r in range(body_rows):
        li = left_pts[r] if r < len(left_pts) else ""
        ri = right_pts[r] if r < len(right_pts) else ""
        _set_cell_text(table.cell(r + 1, 0), str(li), size=18, color=pal["text"])
        _set_cell_text(table.cell(r + 1, 1), str(ri), size=18, color=pal["text"])
        if r % 2 == 1:
            for c in range(2):
                table.cell(r + 1, c).fill.solid()
                table.cell(r + 1, c).fill.fore_color.rgb = RGBColor(*pal["secondary"])

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
    render_takeaways_chevrons(s6, labels, title="Key Takeaways", kicker="At-a-glance summary")

    # Capture Thank You content source early; render it at the very end
    d7 = pos.get(7, {})

    # --- Extra Slides (8+) Intelligent Rendering ---
    def _render_two_col_bullets(slide, left_pts: List[str], right_pts: List[str]):
        left_pts = [p for p in (left_pts or []) if str(p).strip()]
        right_pts = [p for p in (right_pts or []) if str(p).strip()]
        _add_bullets(slide, 1.0, 2.0, 5.3, 4.2, left_pts, font_size_pt=18, color=pal["text"])  # left
        _add_bullets(slide, 6.0, 2.0, 5.3, 4.2, right_pts, font_size_pt=18, color=pal["text"])  # right

    def _render_comparison_table(slide, headers: List[str], left_pts: List[str], right_pts: List[str]):
        body_rows = max(3, min(max(len(left_pts), len(right_pts)), 6))
        total_rows = body_rows + 1
        table = _add_table(slide, 1.6665, 2.2, 10.0, 3.0, rows=total_rows, cols=2)
        # headers
        if len(headers) < 2:
            headers = (headers + ["Option B"])[:2]
        for c in range(2):
            cell = table.cell(0, c)
            cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor(*pal["primary"])
            _set_cell_text(cell, headers[c] or ("Option A" if c == 0 else "Option B"), size=20, bold=True, align_center=True, color=(255, 255, 255))
        for r in range(body_rows):
            li = left_pts[r] if r < len(left_pts) else ""
            ri = right_pts[r] if r < len(right_pts) else ""
            _set_cell_text(table.cell(r + 1, 0), str(li), size=18, color=pal["text"])
            _set_cell_text(table.cell(r + 1, 1), str(ri), size=18, color=pal["text"])
            if r % 2 == 1:
                for c in range(2):
                    table.cell(r + 1, c).fill.solid(); table.cell(r + 1, c).fill.fore_color.rgb = RGBColor(*pal["secondary"])

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
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=pal["primary"])

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
    _add_textbox(s_last, 1.0, 2.8, 11.3, 1.2, "Thank You", font_size_pt=40, bold=True, align="center", color=pal["primary"])
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
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W_IN)
    prs.slide_height = Inches(SLIDE_H_IN)

    blank = prs.slide_layouts[6]
    pal = _theme(theme_name)

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

    # --- Render in order ---
    def render_title(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        title = (sl.get("title") or "Presentation Title").strip()
        subtitle = (sl.get("subtitle") or sl.get("presenter") or "").strip()
        _add_textbox(s, 1.0, 1.0, 11.3, 2.0, title, font_size_pt=46, bold=True, align="center", color=pal["primary"], word_wrap=True, auto_fit=True)
        # Add ~0.5" (~48px) vertical padding below the title before the presenter/subtitle
        _add_textbox(s, 1.0, 3.5, 11.3, 0.8, subtitle, font_size_pt=20, color=pal["text"], align="center", word_wrap=True, auto_fit=True)

    def render_agenda(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        c = _content(sl)
        overflow = _render_agenda_zebra_autofit(prs, s, c.get("bullets", [])[:32], pal=pal, title="Agenda", is_cont=False)
        while overflow:
            sc = prs.slides.add_slide(blank); apply_bg(sc)
            overflow = _render_agenda_zebra_autofit(prs, sc, overflow, pal=pal, title="Agenda", is_cont=True)

    def render_double(sl):
        c = _content(sl)
        lp_all = [str(x) for x in (c.get("left_points") or [])]
        rp_all = [str(x) for x in (c.get("right_points") or [])]

        # Pagination: 4 bullets max per column per slide
        def chunks(lst: List[str], n: int) -> List[List[str]]:
            return [lst[i:i+n] for i in range(0, len(lst), n)]

        left_pages = chunks(lp_all, 4) or [[]]
        right_pages = chunks(rp_all, 4) or [[]]
        pages = max(len(left_pages), len(right_pages))

        for page in range(pages):
            s = prs.slides.add_slide(blank); apply_bg(s)
            title = (sl.get("title") or "Key Concepts").strip()
            if page > 0:
                title = f"{title} (cont.)"
            _add_textbox(s, 1.0, 0.9, 11.3, 1.0, title, font_size_pt=32, bold=True, color=pal["primary"])

            # Layout metrics
            gutter = 0.6
            margin = 1.0
            col_w = (SLIDE_W_IN - 2 * margin - gutter) / 2.0
            left_x = margin
            right_x = margin + col_w + gutter

            lp = left_pages[page] if page < len(left_pages) else []
            rp = right_pages[page] if page < len(right_pages) else []

            # Subtle vertical divider
            divider_w = 0.06
            divider_x = left_x + col_w + (gutter - divider_w) / 2.0
            divider_top = 2.0
            divider_h = 4.6
            div = s.shapes.add_shape(MsoShape.RECTANGLE, Inches(divider_x), Inches(divider_top), Inches(divider_w), Inches(divider_h))
            div.fill.solid(); div.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            div.line.fill.background()

            _add_bullets(s, left_x, 2.1, col_w, 4.3, lp, font_size_pt=18, color=pal["text"], word_wrap=True, auto_fit=True, line_spacing=1.45, bold_first_phrase=False, space_before_pt=4, space_after_pt=14)
            _add_bullets(s, right_x, 2.1, col_w, 4.3, rp, font_size_pt=18, color=pal["text"], word_wrap=True, auto_fit=True, line_spacing=1.45, bold_first_phrase=False, space_before_pt=4, space_after_pt=14)

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
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, comp_title, font_size_pt=32, bold=True, color=pal["primary"])
        body_rows = max(3, min(max(len(left_pts), len(right_pts)), 6))
        table = _add_table(s, 1.6665, 2.2, 10.0, 3.0, rows=body_rows + 1, cols=2)
        # header row
        for cidx in range(2):
            cell = table.cell(0, cidx)
            cell.fill.solid(); cell.fill.fore_color.rgb = RGBColor(*pal["primary"])
            _set_cell_text(cell, headers[cidx] or ("Option A" if cidx == 0 else "Option B"), size=20, bold=True, align_center=True, color=(255, 255, 255))
        # rows
        for r in range(body_rows):
            li = left_pts[r] if r < len(left_pts) else ""
            ri = right_pts[r] if r < len(right_pts) else ""
            _set_cell_text(table.cell(r + 1, 0), str(li), size=18, color=pal["text"])
            _set_cell_text(table.cell(r + 1, 1), str(ri), size=18, color=pal["text"])
            if r % 2 == 1:
                for cidx in range(2):
                    table.cell(r + 1, cidx).fill.solid(); table.cell(r + 1, cidx).fill.fore_color.rgb = RGBColor(*pal["secondary"])

    def render_infographic(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        c = _content(sl)
        labels = _clean_takeaways(c.get("bullets") or c.get("takeaways") or [], limit=5)
        if not labels:
            labels = ["Summary", "Outcomes", "Next Steps", "Owners"]
        render_takeaways_chevrons(s, labels, title=(sl.get("title") or "Key Takeaways"), kicker="At-a-glance summary")

    def render_cards(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        c = _content(sl)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, (sl.get("title") or "Overview"), font_size_pt=32, bold=True, color=pal["primary"])
        items = [str(x) for x in (c.get("cards") or c.get("bullets") or []) if str(x).strip()]
        # Layout grid 3x2 with generous spacing
        cols, rows = 3, 2
        card_w, card_h = 3.7, 1.8
        gap_x, gap_y = 0.35, 0.5
        start_x, start_y = 0.7, 2.0
        pad_x, pad_y = 0.28, 0.24
        for i, text in enumerate(items[: cols * rows]):
            r = i // cols
            col = i % cols
            x = start_x + col * (card_w + gap_x)
            y = start_y + r * (card_h + gap_y)

            # Card background with rounded corners
            bg = s.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(card_w), Inches(card_h))
            use_alt = (i % 2 == 1)
            bg.fill.solid()
            bg.fill.fore_color.rgb = RGBColor(*pal.get("secondary", pal["bg"])) if use_alt else RGBColor(*pal.get("bg", (255,255,255)))
            bg.line.color.rgb = RGBColor(*pal.get("secondary", pal["bg"]))
            # subtle shadow
            try:
                bg.shadow.inherit = False
                bg.shadow.visible = True
                bg.shadow.blur = 3
                bg.shadow.distance = 1
            except Exception:
                pass

            # Inner text box for padding
            tb = s.shapes.add_textbox(Inches(x + pad_x), Inches(y + pad_y), Inches(card_w - 2*pad_x), Inches(card_h - 2*pad_y)).text_frame
            tb.word_wrap = True
            tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            # Typography hierarchy: bold first phrase then normal body
            head, tail = _split_head_tail(text)
            p = tb.paragraphs[0]
            p.clear()
            if head:
                r1 = p.add_run(); r1.text = head + (" " if tail else "")
                r1.font.bold = True; r1.font.size = Pt(16); r1.font.color.rgb = RGBColor(*pal["text"]) if isinstance(pal["text"], tuple) else r1.font.color.rgb
            if tail:
                r2 = p.add_run(); r2.text = tail
                r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(*pal.get("muted", pal["text"])) if isinstance(pal.get("muted", pal["text"]), tuple) else r2.font.color.rgb
            try:
                p.line_spacing = 1.35
            except Exception:
                pass

    def render_thanks(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 2.8, 11.3, 1.2, "Thank You", font_size_pt=40, bold=True, align="center", color=pal["primary"])
        cta = (sl.get("cta") or _content(sl).get("cta") or "").strip()
        if cta:
            _add_textbox(s, 3.0, 4.2, 7.3, 0.8, cta, font_size_pt=18, align="center", color=pal["text"])

    def render_bullets(sl):
        s = prs.slides.add_slide(blank); apply_bg(s)
        _add_textbox(s, 1.0, 0.9, 11.3, 1.0, (sl.get("title") or "Slide"), font_size_pt=32, bold=True, color=pal["primary"])
        c = _content(sl)
        _add_bullets(s, 1.0, 2.0, 11.0, 4.5, [str(x) for x in (c.get("bullets") or [])], font_size_pt=20, color=pal["text"], word_wrap=True, auto_fit=True)

    # Pre-pass: avoid empty bullets only if we are not in render_exact mode
    if not render_exact:
        for sl in slides_norm:
            if _ctype(sl) == "bullets":
                c = _content(sl)
                bs = [str(x).strip() for x in (c.get("bullets") or []) if str(x).strip()]
                if not bs:
                    c["bullets"] = _synthesize_topic_bullets(sl.get("title") or "")
                    sl["content"] = c

    for sl in slides_norm:
        t = _ctype(sl)
        if t == "title":
            render_title(sl)
        elif t == "agenda":
            render_agenda(sl)
        elif t in {"double_column", "deep_dive"}:
            render_double(sl)
        elif t in {"comparison", "table"}:
            render_table(sl)
        elif t in {"infographic", "key_takeaways"}:
            render_infographic(sl)
        elif t in {"cards", "card", "card_style"}:
            render_cards(sl)
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
        title_col = (11, 36, 71)  # deep navy
        sub_col = (100, 116, 139)  # slate-500
        _add_textbox(slide, 1.0, 0.9, 11.3, 0.9, "Key Takeaways", font_size_pt=44, bold=True, color=title_col)
        _add_textbox(slide, 1.0, 1.6, 11.3, 0.6, "", font_size_pt=22, color=sub_col, align="center")

        # Approximate colors from the sample: amber, teal, navy, coral
        PILL_COLORS = [
            ((251, 191, 36), (11, 36, 71)),   # amber bg, navy text
            ((45, 212, 191), (11, 36, 71)),   # teal bg, navy text
            ((10, 61, 98),   (255, 255, 255)),# navy bg, white text
            ((244, 114, 82), (11, 36, 71)),   # coral bg, navy text
        ]

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
            ico.fill.solid(); ico.fill.fore_color.rgb = RGBColor(255, 255, 255)
            # Ensure contrast for white preferred text (use deep navy for outline and dot)
            ico_border_rgb = preferred_txt if preferred_txt != (255, 255, 255) else (11, 36, 71)
            ico.line.color.rgb = RGBColor(*ico_border_rgb)
            # simple glyph placeholder
            itf = ico.text_frame; itf.clear(); ip = itf.paragraphs[0]; ip.alignment = PP_ALIGN.CENTER
            ir = ip.add_run(); ir.text = "●"; ir.font.size = Pt(20); ir.font.bold = True; ir.font.color.rgb = RGBColor(*ico_border_rgb)
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
            txt_rgb = preferred_txt if _luma(bg_rgb) < 170 else (11, 36, 71)
            if head:
                r1 = p.add_run(); r1.text = head + (" " if tail else ""); r1.font.size = Pt(22); r1.font.bold = True; r1.font.color.rgb = RGBColor(*txt_rgb)
            if tail:
                r2 = p.add_run(); r2.text = tail; r2.font.size = Pt(13); r2.font.color.rgb = RGBColor(235, 244, 255) if txt_rgb == (255, 255, 255) else RGBColor(71, 85, 105)
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
        # Accent colors for big numbers
        NUM_COLORS = [
            (234, 179, 8),   # amber-500
            (45, 212, 191),  # teal-400
            (99, 102, 241),  # indigo-500
            (244, 114, 82),  # orange-400
        ]
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
                r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(14); r2.font.color.rgb = RGBColor(71, 85, 105)

            # Subtle divider line under each row except the last
            if i < min(5, len(items)):
                line_y = start_y + row_h - 0.10
                # Divider aligned to grid (x=1.2) with width 10.8 to end around 12.0in
                line = slide.shapes.add_shape(MsoShape.RECTANGLE, Inches(1.2), Inches(line_y), Inches(10.8), Inches(0.02))
                line.fill.solid(); line.fill.fore_color.rgb = RGBColor(229, 231, 235)  # gray-200
                line.line.fill.background()

            start_y += row_h + gap

    def _render_summary_vertical_arrows(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        # Title block aligned to grid
        _add_textbox(slide, 1.2, 0.9, 10.8, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        # Colors similar to banner pills
        PILL_COLORS = [
            ((251, 191, 36), (11, 36, 71)),
            ((45, 212, 191), (11, 36, 71)),
            ((10, 61, 98),   (255, 255, 255)),
            ((244, 114, 82), (11, 36, 71)),
        ]
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
                p2 = tb.add_paragraph(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(13); r2.font.color.rgb = RGBColor(235, 244, 255) if txt_rgb == (255, 255, 255) else RGBColor(71, 85, 105)
            y += card_h + gap

    def _render_summary_horizontal_arrows(slide, items: List[str], *, pal: Dict[str, Tuple[int, int, int]]):
        # Title block
        _add_textbox(slide, 1.2, 0.9, 10.8, 0.9, "Key Takeaways", font_size_pt=36, bold=True, color=pal["primary"])
        # Base band and overlay chevron
        y = 2.0
        band_h, gap = 0.95, 0.28
        band_x, band_w = 1.2, 10.8
        CHEV_W = 6.4
        PILL_COLORS = [
            ((251, 191, 36), (11, 36, 71)),
            ((45, 212, 191), (11, 36, 71)),
            ((10, 61, 98),   (255, 255, 255)),
            ((244, 114, 82), (11, 36, 71)),
        ]
        for i, raw in enumerate(items[:4]):
            bg_rgb, txt_pref = PILL_COLORS[i % len(PILL_COLORS)]
            # White base rounded band
            band = slide.shapes.add_shape(MsoShape.ROUNDED_RECTANGLE, Inches(band_x), Inches(y), Inches(band_w), Inches(band_h))
            band.fill.solid(); band.fill.fore_color.rgb = RGBColor(255, 255, 255)
            band.line.color.rgb = RGBColor(229, 231, 235)
            # Colored chevron overlay from left
            chev = slide.shapes.add_shape(MsoShape.CHEVRON, Inches(band_x), Inches(y), Inches(CHEV_W), Inches(band_h))
            chev.fill.solid(); chev.fill.fore_color.rgb = RGBColor(*bg_rgb)
            chev.line.fill.background()
            # Head text on chevron
            head, tail = _split_head_tail(str(raw))
            tb1 = slide.shapes.add_textbox(Inches(band_x + 0.40), Inches(y + 0.14), Inches(CHEV_W - 0.80), Inches(band_h - 0.28)).text_frame
            tb1.word_wrap = True; tb1.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            p1 = tb1.paragraphs[0]; p1.clear(); r1 = p1.add_run(); r1.text = head or str(raw); r1.font.size = Pt(20); r1.font.bold = True; r1.font.color.rgb = RGBColor(*(txt_pref if txt_pref != (255, 255, 255) else (11,36,71)))
            # Tail text on right white area
            if tail:
                tb2 = slide.shapes.add_textbox(Inches(band_x + CHEV_W + 0.40), Inches(y + 0.14), Inches(band_w - CHEV_W - 0.80), Inches(band_h - 0.28)).text_frame
                tb2.word_wrap = True; tb2.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                p2 = tb2.paragraphs[0]; p2.clear(); r2 = p2.add_run(); r2.text = tail; r2.font.size = Pt(16); r2.font.color.rgb = RGBColor(71, 85, 105)
            y += band_h + gap

    def render_summary_slide(items: List[str]):
        s = prs.slides.add_slide(blank); apply_bg(s)
        # Round-robin rotation across approved templates (flow chevrons removed)
        global SUMMARY_STYLE_INDEX
        styles = ["banner", "steps", "v_arrows"]
        choice = styles[SUMMARY_STYLE_INDEX % len(styles)]
        SUMMARY_STYLE_INDEX += 1
        if choice == "banner":
            _render_summary_banner(s, items, pal=pal)
        elif choice == "steps":
            _render_summary_steps(s, items, pal=pal)
        elif choice == "v_arrows":
            _render_summary_vertical_arrows(s, items, pal=pal)
        else:
            _render_summary_vertical_arrows(s, items, pal=pal)

    # Render the smart summary just before the final Thank You slide
    summary_pts = _collect_summary_from_slides(slides_norm)
    render_summary_slide(summary_pts)

    # Always ensure a Thank You at the end
    thanks_sl = next((s for s in slides_norm if _ctype(s) in {"thank_you", "cta"}), None)
    render_thanks(thanks_sl or {"type": "thank_you", "cta": ""})

    prs.save(out_path)
    return out_path
