from __future__ import annotations

from typing import Any, Dict, List, Tuple
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


def _add_bullets(slide, x, y, w, h, bullets: List[str], *, font_size_pt: int = 20, min_items: int = 0, synth_fill: str = "TBD", color: Tuple[int, int, int] | None = None):
    items = [s.strip() for s in (bullets or []) if str(s).strip()]
    while len(items) < min_items:
        items.append(synth_fill)
    left, top, width, height = map(Inches, (x, y, w, h))
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.clear()
    first = True
    for txt in items:
        if first:
            p = tf.paragraphs[0]
            first = False
        else:
            p = tf.add_paragraph()
        p.text = txt
        p.level = 0
        for r in p.runs:
            r.font.size = Pt(font_size_pt)
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
    if intro:
        stages.append("Manual → Assisted")
    stages += (d3.get("bullets") or d3.get("left_points") or [])
    if not stages and d3.get("right_points"):
        stages = d3["right_points"][:4]

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

    # Slide 7 – Thank You / CTA
    s7 = prs.slides.add_slide(blank)
    apply_bg(s7)
    d7 = pos.get(7, {})
    _add_textbox(s7, 1.0, 2.8, 11.3, 1.2, slide_heading(7), font_size_pt=40, bold=True, align="center", color=pal["primary"])
    if d7.get("cta"):
        _add_textbox(s7, 3.0, 4.2, 7.3, 0.8, d7.get("cta", ""), font_size_pt=18, align="center", color=pal["text"])

    prs.save(out_path)
    return out_path
