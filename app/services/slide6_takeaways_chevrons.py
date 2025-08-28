from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE, MSO_ANCHOR
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.dml.color import RGBColor

# Layout constants
SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = Inches(0.7), Inches(0.7), Inches(1.0), Inches(0.8)
SAFE_W = SLIDE_W - MARGIN_L - MARGIN_R
EPS = Inches(0.05)  # small fudge to avoid pixel rounding bleed

# Palette (alternating light/dark; feel free to tweak)
PAL = [
    RGBColor(173, 216, 230),  # light blue
    RGBColor(248, 203, 173),  # peach
    RGBColor(255, 236, 179),  # light amber
    RGBColor(255, 204, 188),  # light coral
    RGBColor(135, 206, 250),  # sky
    RGBColor(255, 224, 178),  # sandy
]
INK = RGBColor(35, 35, 35)
TITLE = RGBColor(42, 94, 255)
STROKE = RGBColor(255, 255, 255)  # thin white divider


def _title(slide, text, kicker=None):
    """
    Draw title (+ optional kicker) and return bottom y (top of content region).
    """
    title_h = Inches(0.55)
    kicker_h = Inches(0.32) if kicker else Inches(0.0)
    title_top = MARGIN_T - Inches(0.05)  # tiny optical lift

    # Title
    tbox = slide.shapes.add_textbox(MARGIN_L, title_top, SLIDE_W - MARGIN_L - MARGIN_R, title_h)
    p = tbox.text_frame.paragraphs[0]
    p.text = text
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = TITLE

    # Kicker (optional)
    if kicker:
        kbox = slide.shapes.add_textbox(
            MARGIN_L,
            title_top + title_h - Inches(0.05),
            SLIDE_W - MARGIN_L - MARGIN_R,
            kicker_h,
        )
        kp = kbox.text_frame.paragraphs[0]
        kp.text = kicker
        kp.font.size = Pt(14)
        kp.font.color.rgb = RGBColor(120, 130, 145)

    return title_top + title_h + kicker_h


def _clean(items, min_items=4, max_items=8):
    out, seen = [], set()
    for s in items or []:
        t = (s or '').strip()
        if not t:
            continue
        lo = t.lower()
        if lo in seen:
            continue
        seen.add(lo); out.append(t)
        if len(out) >= max_items:
            break
    while len(out) < min_items:
        out.append("Refine with user input")
    return out


def _wrap_text(tf, text, max_chars=60, base_pt=18, min_pt=13):
    tf.clear(); tf.word_wrap = True; tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    p.text = text if len(text) <= max_chars else text[:max_chars-1] + "…"
    # font sizing will autoshrink; set an upper bound
    for r in p.runs:
        r.font.size = Pt(base_pt)
        r.font.color.rgb = INK
    return tf


def _chevron(slide, left, top, width, height, fill_rgb, text):
    shp = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.CHEVRON, left, top, width, height)
    shp.fill.solid(); shp.fill.fore_color.rgb = fill_rgb
    shp.line.color.rgb = STROKE; shp.line.width = Pt(2)
    shp.shadow.inherit = False; shp.shadow.blur_radius = Pt(6); shp.shadow.distance = Pt(2); shp.shadow.transparency = 0.75
    tf = shp.text_frame
    _wrap_text(tf, text, max_chars=64, base_pt=18, min_pt=12)
    # Nudge text inset a touch so it doesn't crowd the edges
    for _ in (tf.margin_left, tf.margin_right, tf.margin_top, tf.margin_bottom):
        pass
    tf.margin_left = tf.margin_right = Inches(0.08)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    try:
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    except Exception:
        pass
    return shp

def _row_width(n, w, gap):
    """Total width consumed by n chevrons with gap between them."""
    return n * w + (n - 1) * gap


def _fit_chevrons(n, gap, eps=EPS):
    """Compute single chevron width so n chevrons + gaps fit in SAFE_W minus eps."""
    return (SAFE_W - (n - 1) * gap - eps) / n


def _center_start(total_width, extra_offset=Inches(0.0)):
    """
    Horizontal start (x0) that centers total_width + extra_offset in the safe area.
    Then clamp to never overflow margins.
    """
    full = total_width + extra_offset
    x0 = MARGIN_L + max(0, (SAFE_W - full) / 2.0)
    # Clamp so the row stays within [MARGIN_L, MARGIN_L + SAFE_W]
    x0 = max(MARGIN_L, min(MARGIN_L + SAFE_W - full, x0))
    return x0


def _split_rows(items):
    # Distribute top-first like 3/2, 3/3, 4/3 for 5,6,7 etc. Aim to keep top row >= bottom row
    n = len(items)
    if n <= 4:
        return items, []
    if n == 5:
        return items[:3], items[3:]
    if n == 6:
        return items[:3], items[3:]
    if n == 7:
        return items[:4], items[4:]
    # n >= 8
    return items[:4], items[4:8]


def render_takeaways_chevrons(slide, items, title="Key Takeaways", kicker="At-a-glance summary"):
    items = _clean(items, 4, 8)

    title_bottom = _title(slide, title, kicker)

    # CONTENT REGION: between title_bottom and bottom margin
    content_top = title_bottom + Inches(0.12)
    content_bottom = SLIDE_H - MARGIN_B
    available_h = max(Inches(1.0), content_bottom - content_top)

    # Prepare rows
    top_items, bot_items = _split_rows(items)

    row_h = Inches(1.25)
    inter_row_g = Inches(0.30)
    chevron_gap = Inches(0.08)

    # Total height of our chevron block (1 or 2 rows)
    rows = 1 if not bot_items else 2
    block_h = row_h if rows == 1 else (2 * row_h + inter_row_g)

    # Vertically center the BLOCK within the content region
    y_top = content_top + max(0, (available_h - block_h) / 2.0)
    y_bot = y_top + row_h + inter_row_g if bot_items else None

    # ---------- TOP ROW (centered horizontally) ----------
    if top_items:
        n_top = len(top_items)
        w_top = _fit_chevrons(n_top, chevron_gap)
        total_top = _row_width(n_top, w_top, chevron_gap)
        x = _center_start(total_top)
        for i, txt in enumerate(top_items):
            fill = PAL[i % len(PAL)]
            shp = _chevron(slide, x, y_top, w_top, row_h, fill, txt)
            try:
                shp.adjustments[0] = 0.28
            except Exception:
                pass
            x += w_top + chevron_gap

    # ---------- BOTTOM ROW (centered + interlock offset, clamped) ----------
    if bot_items:
        n_bot = len(bot_items)
        w_bot = _fit_chevrons(n_bot, chevron_gap)
        total_bot = _row_width(n_bot, w_bot, chevron_gap)
        # Interlock offset (included in centering)
        offset = min(w_bot / 2.0, Inches(0.90))
        x = _center_start(total_bot, extra_offset=offset) + offset
        for j, txt in enumerate(bot_items):
            fill = PAL[(j + 1) % len(PAL)]
            shp = _chevron(slide, x, y_bot, w_bot, row_h, fill, txt)
            try:
                shp.adjustments[0] = 0.28
            except Exception:
                pass
            x += w_bot + chevron_gap
