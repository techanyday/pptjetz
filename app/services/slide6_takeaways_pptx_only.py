from pptx.util import Inches, Pt
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE
from pptx.dml.color import RGBColor
import platform

MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = Inches(1.0), Inches(1.0), Inches(0.9), Inches(0.8)
SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)

PALETTE = [
    RGBColor(66,133,244),   # blue
    RGBColor(52,168,83),    # green
    RGBColor(251,188,5),    # amber
    RGBColor(234,67,53),    # red
    RGBColor(171,71,188),   # purple
]
FILL = RGBColor(238, 245, 255)
INK  = RGBColor(35, 35, 35)
MUTED= RGBColor(120, 130, 145)
LINE = RGBColor(210, 220, 235)
TITLE= RGBColor(255, 255, 255)

"""
Emoji helpers: choose a color emoji font per OS and force emoji presentation with VS-16.
"""
def _emoji_font():
    sys = platform.system()
    if sys == "Windows":
        return "Segoe UI Emoji"
    if sys == "Darwin":
        return "Apple Color Emoji"
    return "Noto Color Emoji"

def _emoji_vs16(s: str) -> str:
    if not s:
        return s
    VS16 = "\ufe0f"
    return s if s.endswith(VS16) else s + VS16

def _title(slide, text, kicker=None):
    tf = slide.shapes.add_textbox(MARGIN_L, MARGIN_T, Inches(10.5), Inches(0.95)).text_frame
    p = tf.paragraphs[0]; p.text = text
    p.font.size = Pt(42); p.font.bold = True; p.font.color.rgb = TITLE
    if kicker:
        kt = slide.shapes.add_textbox(MARGIN_L, MARGIN_T+Inches(0.75), Inches(10.5), Inches(0.4)).text_frame
        kp = kt.paragraphs[0]; kp.text = kicker
        kp.font.size = Pt(14); kp.font.color.rgb = MUTED


def _baseline(slide, x1, x2, y):
    line = slide.shapes.add_connector(1, x1, y, x2, y)
    line.line.color.rgb = LINE; line.line.width = Pt(1.25)


def _caption(slide, left, top, width, text, size=15, align=PP_ALIGN.CENTER):
    tb = slide.shapes.add_textbox(left, top, width, Inches(0.9)).text_frame
    tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tb.paragraphs[0]; p.text = text; p.alignment = align
    p.font.size = Pt(size); p.font.color.rgb = INK


def _emoji(slide, cx, cy, emoji_text=""):
    """Draw a small vector icon fallback, then try to overlay a colored emoji text."""
    if not emoji_text:
        return

    # Always draw a vector fallback so previews without color emoji still show an icon.
    _icon_fallback(slide, cx, cy, emoji_text)

    # Overlay colored emoji text via system font (PowerPoint on Win/mac renders color fonts).
    tb = slide.shapes.add_textbox(cx - Inches(0.16), cy - Inches(0.20), Inches(0.32), Inches(0.36))
    tf = tb.text_frame; tf.clear()
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = _emoji_vs16(emoji_text)
    r.font.size = Pt(18)
    r.font.name = _emoji_font()

def _icon_fallback(slide, cx, cy, emoji_text):
    """
    Vector pictogram fallback (no images):
      ⚙ -> DONUT   ✨ -> STAR_5_POINT   🎬 -> RIGHT_TRIANGLE
      📈 -> UP_ARROW   🔮 -> MOON
    """
    mapping = {
        "⚙": MSO_AUTO_SHAPE_TYPE.DONUT,
        "✨": MSO_AUTO_SHAPE_TYPE.STAR_5_POINT,
        "🎬": MSO_AUTO_SHAPE_TYPE.RIGHT_TRIANGLE,
        "📈": MSO_AUTO_SHAPE_TYPE.UP_ARROW,
        "🔮": MSO_AUTO_SHAPE_TYPE.MOON,
    }
    size = Inches(0.34)
    left, top = cx - size/2, cy - size/2
    base = emoji_text.replace("\ufe0f", "")
    shape_id = mapping.get(base, MSO_AUTO_SHAPE_TYPE.STAR_5_POINT)

    shp = slide.shapes.add_shape(shape_id, left, top, size, size)
    shp.fill.solid(); shp.fill.fore_color.rgb = RGBColor(66, 133, 244)
    shp.line.fill.background()


def _node_circle(slide, x, y, d, label, color, emoji=None):
    circ = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, x, y, d, d)
    circ.fill.solid(); circ.fill.fore_color.rgb = FILL
    circ.line.color.rgb = color; circ.line.width = Pt(2.25)
    circ.shadow.inherit = False; circ.shadow.blur_radius = Pt(6); circ.shadow.distance = Pt(2)
    # place emoji/icon near the top inside the circle
    _emoji(slide, x + d/2, y + Inches(0.20), emoji or "")
    _caption(slide, x - Inches(0.2), y + d + Inches(0.08), d + Inches(0.4), label)


def _node_pill(slide, x, y, w, h, label, color, emoji=None):
    shp = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, x, y, w, h)
    shp.fill.solid(); shp.fill.fore_color.rgb = FILL
    shp.line.color.rgb = color; shp.line.width = Pt(2.25); shp.adjustments[0] = 0.7
    shp.shadow.inherit = False; shp.shadow.blur_radius = Pt(6); shp.shadow.distance = Pt(2)
    _emoji(slide, x + Inches(0.25), y + h/2, emoji)
    tb = slide.shapes.add_textbox(x + Inches(0.55), y + Inches(0.15), w - Inches(0.8), h - Inches(0.3)).text_frame
    tb.word_wrap = True; tb.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    p = tb.paragraphs[0]; p.text = label; p.alignment = PP_ALIGN.LEFT
    p.font.size = Pt(15); p.font.color.rgb = INK


def render_key_takeaways(slide, labels, emojis=None, subtitle="At-a-glance summary"):
    items = [(s or "").strip() for s in (labels or []) if s and s.strip()]
    while len(items) < 4: items.append("Refine with user input")
    items = items[:5]
    emojis = (emojis or []) + [""] * max(0, len(items) - len(emojis or []))

    _title(slide, "Key Takeaways", kicker=subtitle)
    top_area = MARGIN_T + Inches(1.1)
    d = Inches(1.9)
    n = len(items)
    usable_w = SLIDE_W - MARGIN_L - MARGIN_R
    gap = max(Inches(0.4), (usable_w - n*d) / (n - 1)) if n > 1 else 0
    y = top_area + Inches(0.1)

    x_left  = MARGIN_L + d/2
    x_right = x_left + (n-1)*(d + gap)
    _baseline(slide, x_left, x_right, y + d/2)

    for i, label in enumerate(items):
        color = PALETTE[i % len(PALETTE)]
        emoji = emojis[i] if i < len(emojis) else ""
        if len(label) > 35:
            w = min(Inches(3.6), d + Inches(1.2))
            x = MARGIN_L + i*(d + gap) + d/2 - w/2
            _node_pill(slide, x, y + Inches(0.25), w, Inches(0.9), label, color, emoji=emoji)
        else:
            x = MARGIN_L + i*(d + gap)
            _node_circle(slide, x, y, d, label, color, emoji=emoji)
