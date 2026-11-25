from __future__ import annotations

import io
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt


PALETTES = {
    "SLEEK": ((15, 23, 42), (30, 41, 59), (56, 189, 248), (241, 245, 249)),
    "MINIMAL FLUX": ((17, 24, 39), (31, 41, 55), (167, 139, 250), (249, 250, 251)),
    "BUSINESS": ((15, 23, 42), (30, 64, 175), (14, 165, 233), (248, 250, 252)),
    "CREATIVE": ((38, 20, 67), (88, 28, 135), (244, 114, 182), (253, 244, 255)),
}


def _palette(theme: str) -> tuple[tuple[int, int, int], ...]:
    return PALETTES.get((theme or "").upper(), PALETTES["SLEEK"])


def _rgb(value: tuple[int, int, int]) -> RGBColor:
    return RGBColor(*value)


def _text(slide, x, y, w, h, value, *, size=18, color=(15, 23, 42), bold=False, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = frame.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = str(value or "")
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color)
    return box


def _shape(slide, kind, x, y, w, h, *, fill, line=None, radius=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else kind
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(fill)
    shape.line.color.rgb = _rgb(line or fill)
    return shape


def _background(slide, bg):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(bg)


def _accent(index: int, base: tuple[int, int, int]) -> tuple[int, int, int]:
    accents = [
        base,
        (45, 212, 191),
        (251, 146, 60),
        (244, 114, 182),
        (129, 140, 248),
        (250, 204, 21),
    ]
    return accents[index % len(accents)]


def _content(spec: dict[str, Any]) -> dict[str, Any]:
    value = spec.get("content")
    return value if isinstance(value, dict) else {}


def _items(content: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        value = content.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def _label(item: Any, default: str = "Item") -> tuple[str, str]:
    if isinstance(item, dict):
        return (
            str(item.get("label") or item.get("heading") or item.get("title") or item.get("name") or default),
            str(item.get("description") or item.get("text") or item.get("value") or ""),
        )
    text = str(item or default)
    if ":" in text:
        return tuple(part.strip() for part in text.split(":", 1))
    return text, ""


def _title(slide, value, accent, dark=False):
    title_color = (248, 250, 252) if dark else (15, 23, 42)
    _text(slide, 0.8, 0.25, 4.0, 0.25, "PPTJET  /  VISUAL BRIEF", size=9, color=accent, bold=True)
    _text(slide, 0.75, 0.55, 11.9, 0.65, value, size=30, color=title_color, bold=True)
    _shape(slide, MSO_SHAPE.RECTANGLE, 0.78, 1.28, 1.25, 0.06, fill=accent)
    _shape(slide, MSO_SHAPE.RECTANGLE, 2.2, 1.28, 9.9, 0.015, fill=(71, 85, 105))


def _cards(slide, items, colors):
    bg, panel, accent, light = colors
    for index, item in enumerate(items[:6]):
        label, desc = _label(item)
        col, row = index % 2, index // 2
        x, y = 0.8 + col * 6.0, 1.65 + row * 1.65
        tone = _accent(index, accent)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 5.35, 1.25, fill=panel, line=(71, 85, 105), radius=True)
        _shape(slide, MSO_SHAPE.RECTANGLE, x, y, 0.12, 1.25, fill=tone)
        badge = _shape(slide, MSO_SHAPE.OVAL, x + 0.28, y + 0.18, 0.42, 0.42, fill=tone, line=tone)
        _text(slide, x + 0.28, y + 0.2, 0.42, 0.3, f"{index + 1:02}", size=9, color=bg, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x + 0.85, y + 0.14, 4.1, 0.35, label, size=17, color=light, bold=True)
        _text(slide, x + 0.85, y + 0.56, 4.1, 0.45, desc or "Key idea and practical implication.", size=12, color=(203, 213, 225))


def _flow(slide, items, colors):
    bg, panel, accent, light = colors
    count = max(1, min(len(items), 6))
    width = 1.75
    gap = (12.0 - count * width) / max(1, count - 1)
    for index, item in enumerate(items[:6]):
        label, desc = _label(item, f"Step {index + 1}")
        x = 0.75 + index * (width + gap)
        tone = _accent(index, accent)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.25, width, 1.45, fill=panel, line=(71, 85, 105), radius=True)
        _shape(slide, MSO_SHAPE.OVAL, x + width / 2 - 0.2, 1.92, 0.4, 0.4, fill=tone, line=tone)
        _text(slide, x + width / 2 - 0.2, 2.0, 0.4, 0.2, str(index + 1), size=10, color=bg, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x + 0.12, 2.48, width - 0.24, 0.35, label, size=14, color=light, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x + 0.12, 2.9, width - 0.24, 0.55, desc, size=10, color=(203, 213, 225), align=PP_ALIGN.CENTER)
        if index < count - 1:
            line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x + width), Inches(2.97), Inches(x + width + gap), Inches(2.97))
            line.line.color.rgb = _rgb(tone)
            line.line.width = Pt(2)


def _timeline(slide, items, colors):
    bg, panel, accent, light = colors
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1.0), Inches(3.0), Inches(12.0), Inches(3.0))
    line.line.color.rgb = _rgb(accent)
    line.line.width = Pt(3)
    for index, item in enumerate(items[:6]):
        label, desc = _label(item, f"Phase {index + 1}")
        x = 0.8 + index * (11.2 / max(1, len(items[:6]) - 1))
        tone = _accent(index, accent)
        _shape(slide, MSO_SHAPE.OVAL, x, 2.75, 0.5, 0.5, fill=tone, line=tone)
        y = 1.55 if index % 2 == 0 else 3.55
        _text(slide, x - 0.45, y, 1.45, 0.35, label, size=14, color=light, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x - 0.65, y + 0.38, 1.85, 0.65, desc, size=10, color=(203, 213, 225), align=PP_ALIGN.CENTER)


def _architecture(slide, content, colors):
    bg, panel, accent, light = colors
    boxes = _items(content, ("boxes", "nodes", "items"))
    boxes = boxes[:6] or ["Core", "Audience", "Data", "Actions"]
    center_x, center_y = 5.1, 2.55
    _shape(slide, MSO_SHAPE.OVAL, center_x, center_y, 2.0, 1.0, fill=accent, line=accent)
    _text(slide, center_x + 0.15, center_y + 0.25, 1.7, 0.45, str(content.get("center") or "Core"), size=16, color=bg, bold=True, align=PP_ALIGN.CENTER)
    positions = [(1.0, 1.55), (9.8, 1.55), (1.0, 4.45), (9.8, 4.45), (5.0, 5.1), (5.0, 0.85)]
    for index, item in enumerate(boxes):
        label, desc = _label(item)
        x, y = positions[index % len(positions)]
        line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(center_x + 1), Inches(center_y + 0.5), Inches(x + 1), Inches(y + 0.45))
        line.line.color.rgb = _rgb(accent)
        line.line.width = Pt(1.5)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 2.0, 0.9, fill=panel, line=accent, radius=True)
        _text(slide, x + 0.1, y + 0.13, 1.8, 0.3, label, size=13, color=light, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x + 0.1, y + 0.45, 1.8, 0.25, desc, size=9, color=(203, 213, 225), align=PP_ALIGN.CENTER)


def _comparison(slide, content, colors):
    bg, panel, accent, light = colors
    columns = content.get("columns") or content.get("table") or []
    if isinstance(columns, dict):
        columns = list(columns.values())
    columns = columns[:3] if isinstance(columns, list) else []
    if not columns:
        columns = [{"heading": "Option A", "points": ["Key point"]}, {"heading": "Option B", "points": ["Key point"]}]
    width = 11.4 / len(columns)
    for index, column in enumerate(columns):
        heading, _ = _label(column, f"Option {index + 1}")
        points = column.get("points", []) if isinstance(column, dict) else []
        x = 0.8 + index * (width + 0.25)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.7, width, 4.55, fill=panel, line=accent, radius=True)
        _text(slide, x + 0.25, 1.95, width - 0.5, 0.5, heading, size=19, color=light, bold=True, align=PP_ALIGN.CENTER)
        for row, point in enumerate(points[:6]):
            _shape(slide, MSO_SHAPE.OVAL, x + 0.35, 2.75 + row * 0.52, 0.16, 0.16, fill=accent, line=accent)
            _text(slide, x + 0.65, 2.62 + row * 0.52, width - 0.9, 0.38, point, size=12, color=(203, 213, 225))


def _agenda(slide, items, colors):
    bg, panel, accent, light = colors
    for index, item in enumerate(items[:8]):
        label, desc = _label(item, f"Section {index + 1}")
        y = 1.55 + index * 0.58
        _shape(slide, MSO_SHAPE.OVAL, 1.0, y, 0.38, 0.38, fill=accent, line=accent)
        _text(slide, 1.0, y + 0.01, 0.38, 0.32, str(index + 1), size=11, color=bg, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, 1.65, y - 0.02, 5.0, 0.32, label, size=17, color=light, bold=True)
        _text(slide, 7.0, y - 0.01, 4.9, 0.3, desc or "Key section of the presentation", size=12, color=(203, 213, 225))
        if index < min(len(items), 8) - 1:
            line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1.19), Inches(y + 0.38), Inches(1.19), Inches(y + 0.58))
            line.line.color.rgb = _rgb((71, 85, 105))


def _swot(slide, content, colors):
    bg, panel, accent, light = colors
    sections = [
        ("Strengths", content.get("strengths", []), (34, 197, 94)),
        ("Weaknesses", content.get("weaknesses", []), (251, 146, 60)),
        ("Opportunities", content.get("opportunities", []), (56, 189, 248)),
        ("Threats", content.get("threats", []), (248, 113, 113)),
    ]
    for index, (heading, points, color) in enumerate(sections):
        col, row = index % 2, index // 2
        x, y = 0.8 + col * 6.0, 1.65 + row * 2.35
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 5.35, 1.9, fill=panel, line=color, radius=True)
        _shape(slide, MSO_SHAPE.RECTANGLE, x, y, 5.35, 0.42, fill=color, line=color)
        _text(slide, x + 0.2, y + 0.03, 4.9, 0.3, heading, size=15, color=bg, bold=True)
        for item_index, point in enumerate(points[:3]):
            _text(slide, x + 0.3, y + 0.58 + item_index * 0.35, 4.75, 0.28, f"• {point}", size=12, color=light)


def _kpi(slide, metrics, colors):
    bg, panel, accent, light = colors
    for index, metric in enumerate(metrics[:4]):
        label, value = _label(metric, f"Metric {index + 1}")
        caption = metric.get("caption", "") if isinstance(metric, dict) else ""
        col, row = index % 2, index // 2
        x, y = 0.8 + col * 6.0, 1.7 + row * 2.2
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 5.35, 1.7, fill=panel, line=accent, radius=True)
        _text(slide, x + 0.3, y + 0.2, 4.7, 0.32, label, size=14, color=(203, 213, 225), bold=True)
        _text(slide, x + 0.3, y + 0.62, 4.7, 0.58, value, size=28, color=light, bold=True)
        _text(slide, x + 0.3, y + 1.25, 4.7, 0.25, caption, size=10, color=(148, 163, 184))


def _infographic(slide, content, colors):
    bg, panel, accent, light = colors
    items = _items(content, ("cards", "items", "bullets"))[:6]
    _shape(slide, MSO_SHAPE.OVAL, 5.25, 2.55, 2.8, 1.35, fill=accent, line=accent)
    _text(slide, 5.55, 2.87, 2.2, 0.6, str(content.get("center") or "The big idea"), size=18, color=bg, bold=True, align=PP_ALIGN.CENTER)
    positions = [(0.9, 1.55), (4.0, 1.4), (8.7, 1.55), (0.9, 4.5), (4.0, 4.75), (8.7, 4.5)]
    for index, item in enumerate(items):
        label, desc = _label(item, f"Signal {index + 1}")
        x, y = positions[index % len(positions)]
        tone = _accent(index, accent)
        line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(6.65), Inches(3.22), Inches(x + 1.0), Inches(y + 0.45))
        line.line.color.rgb = _rgb(tone)
        line.line.width = Pt(1.5)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 2.15, 0.9, fill=panel, line=tone, radius=True)
        _shape(slide, MSO_SHAPE.OVAL, x + 0.13, y + 0.22, 0.42, 0.42, fill=tone, line=tone)
        _text(slide, x + 0.13, y + 0.28, 0.42, 0.2, str(index + 1), size=9, color=bg, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x + 0.7, y + 0.13, 1.28, 0.26, label, size=11, color=light, bold=True)
        _text(slide, x + 0.7, y + 0.43, 1.28, 0.25, desc, size=8, color=(203, 213, 225))


def _key_takeaways(slide, content, colors):
    bg, panel, accent, light = colors
    items = _items(content, ("cards", "bullets", "items"))[:4]
    for index, item in enumerate(items):
        label, desc = _label(item, f"Takeaway {index + 1}")
        x = 0.9 + index * 3.05
        y = 2.0 + (index % 2) * 1.65
        tone = _accent(index, accent)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 2.55, 1.2, fill=panel, line=(71, 85, 105), radius=True)
        _shape(slide, MSO_SHAPE.RECTANGLE, x, y, 2.55, 0.1, fill=tone, line=tone)
        _text(slide, x + 0.18, y + 0.23, 2.15, 0.35, label, size=15, color=light, bold=True)
        _text(slide, x + 0.18, y + 0.68, 2.15, 0.3, desc or "The practical implication", size=10, color=(203, 213, 225))


def _roadmap(slide, items, colors):
    bg, panel, accent, light = colors
    items = items[:6]
    # A stepped path is deliberately different from the straight timeline.
    points = [(1.0, 5.2), (3.1, 4.35), (5.2, 3.5), (7.3, 2.65), (9.4, 1.8), (11.3, 1.15)]
    for index, item in enumerate(items):
        label, desc = _label(item, f"Phase {index + 1}")
        x, y = points[index % len(points)]
        if index:
            prev_x, prev_y = points[(index - 1) % len(points)]
            line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(prev_x + 0.38), Inches(prev_y + 0.38), Inches(x + 0.38), Inches(y + 0.38))
            line.line.color.rgb = _rgb(_accent(index, accent))
            line.line.width = Pt(3)
        tone = _accent(index, accent)
        _shape(slide, MSO_SHAPE.OVAL, x, y, 0.76, 0.76, fill=tone, line=tone)
        _text(slide, x, y + 0.22, 0.76, 0.25, str(index + 1), size=12, color=bg, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x - 0.3, y - 0.45, 1.4, 0.28, label, size=12, color=light, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x - 0.55, y + 0.85, 1.9, 0.35, desc, size=9, color=(203, 213, 225), align=PP_ALIGN.CENTER)


def _risk_matrix(slide, risks, colors):
    bg, panel, accent, light = colors
    x0, y0, w, h = 1.3, 1.7, 7.0, 4.8
    _shape(slide, MSO_SHAPE.RECTANGLE, x0, y0, w, h, fill=panel, line=(71, 85, 105))
    for i in range(1, 3):
        line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x0 + i * w / 3), Inches(y0), Inches(x0 + i * w / 3), Inches(y0 + h))
        line.line.color.rgb = _rgb((71, 85, 105))
        line2 = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x0), Inches(y0 + i * h / 3), Inches(x0 + w), Inches(y0 + i * h / 3))
        line2.line.color.rgb = _rgb((71, 85, 105))
    _text(slide, x0, y0 + h + 0.15, w, 0.3, "Likelihood →", size=12, color=(203, 213, 225), align=PP_ALIGN.CENTER)
    _text(slide, 0.25, y0 + 1.7, 0.8, 0.3, "Impact", size=12, color=(203, 213, 225), align=PP_ALIGN.CENTER)
    for index, risk in enumerate(risks[:6]):
        label, _ = _label(risk, f"Risk {index + 1}")
        x = x0 + 0.4 + (index % 3) * 2.2
        y = y0 + 0.45 + (index // 3) * 2.2
        _shape(slide, MSO_SHAPE.OVAL, x, y, 0.34, 0.34, fill=accent, line=accent)
        _text(slide, x + 0.45, y - 0.02, 1.65, 0.45, label, size=11, color=light, bold=True)


def _table(slide, content, colors):
    bg, panel, accent, light = colors
    headers = [str(x) for x in content.get("headers", [])][:5]
    rows = content.get("rows", [])
    if not headers and isinstance(content.get("table"), dict):
        headers = [str(x) for x in content["table"].get("headers", [])][:5]
        rows = content["table"].get("rows", [])
    headers = headers or ["Category", "Detail", "Implication"]
    rows = rows[:7] if isinstance(rows, list) else []
    cols = len(headers)
    x0, y0, width, row_h = 0.8, 1.7, 11.7, 0.55
    col_w = width / cols
    for col, header in enumerate(headers):
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x0 + col * col_w, y0, col_w - 0.04, row_h, fill=accent, line=accent, radius=True)
        _text(slide, x0 + col * col_w + 0.08, y0 + 0.08, col_w - 0.2, 0.3, header, size=12, color=bg, bold=True, align=PP_ALIGN.CENTER)
    for row, values in enumerate(rows):
        values = values if isinstance(values, list) else [values]
        for col in range(cols):
            value = values[col] if col < len(values) else ""
            fill = panel if row % 2 == 0 else (31, 41, 59)
            _shape(slide, MSO_SHAPE.RECTANGLE, x0 + col * col_w, y0 + (row + 1) * row_h, col_w - 0.04, row_h - 0.04, fill=fill, line=(71, 85, 105))
            _text(slide, x0 + col * col_w + 0.08, y0 + (row + 1) * row_h + 0.08, col_w - 0.2, 0.3, value, size=11, color=light)


def _deep_dive(slide, content, colors):
    bg, panel, accent, light = colors
    left = content.get("summary") or content.get("body") or content.get("description") or ""
    points = content.get("points") or content.get("bullets") or []
    _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 0.8, 1.7, 4.1, 4.55, fill=accent, line=accent, radius=True)
    _text(slide, 1.15, 2.1, 3.4, 0.5, "THE CORE IDEA", size=11, color=bg, bold=True)
    _text(slide, 1.15, 2.8, 3.4, 2.2, left or "A closer look at the central idea and why it matters.", size=23, color=bg, bold=True)
    _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 5.35, 1.7, 7.15, 4.55, fill=panel, line=(71, 85, 105), radius=True)
    for index, point in enumerate(points[:6]):
        label, desc = _label(point, f"Insight {index + 1}")
        y = 2.05 + index * 0.62
        _shape(slide, MSO_SHAPE.OVAL, 5.75, y, 0.3, 0.3, fill=_accent(index, accent), line=_accent(index, accent))
        _text(slide, 6.25, y - 0.03, 5.8, 0.28, label, size=14, color=light, bold=True)
        if desc:
            _text(slide, 6.25, y + 0.25, 5.8, 0.24, desc, size=10, color=(203, 213, 225))


def _before_after(slide, content, colors):
    bg, panel, accent, light = colors
    columns = content.get("columns", [])
    if not columns:
        columns = [{"heading": "Before", "points": content.get("before", [])}, {"heading": "After", "points": content.get("after", [])}]
    _comparison(slide, {"columns": columns[:2]}, colors)
    _shape(slide, MSO_SHAPE.CHEVRON, 6.22, 3.05, 0.9, 0.8, fill=accent, line=accent)


def _pricing(slide, tiers, colors):
    bg, panel, accent, light = colors
    tiers = tiers[:4]
    width = 11.5 / max(1, len(tiers))
    for index, tier in enumerate(tiers):
        tier = tier if isinstance(tier, dict) else {"name": str(tier)}
        x = 0.8 + index * (width + 0.18)
        tone = _accent(index, accent)
        _shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.7, width, 4.75, fill=panel, line=tone, radius=True)
        _shape(slide, MSO_SHAPE.RECTANGLE, x, 1.7, width, 0.55, fill=tone, line=tone)
        _text(slide, x + 0.15, 1.83, width - 0.3, 0.25, tier.get("name") or f"Tier {index + 1}", size=15, color=bg, bold=True, align=PP_ALIGN.CENTER)
        _text(slide, x + 0.2, 2.55, width - 0.4, 0.65, tier.get("price") or "Custom", size=25, color=light, bold=True, align=PP_ALIGN.CENTER)
        for row, feature in enumerate((tier.get("features") or [])[:6]):
            _text(slide, x + 0.25, 3.45 + row * 0.42, width - 0.5, 0.28, f"• {feature}", size=11, color=(203, 213, 225))


def _matrix(slide, content, colors, *, mode="impact"):
    bg, panel, accent, light = colors
    items = content.get("items") or content.get("risks") or []
    _shape(slide, MSO_SHAPE.RECTANGLE, 1.4, 1.7, 7.2, 4.8, fill=panel, line=(71, 85, 105))
    for i in range(1, 4):
        vx = 1.4 + i * 1.8
        hy = 1.7 + i * 1.2
        v = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(vx), Inches(1.7), Inches(vx), Inches(6.5))
        h = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1.4), Inches(hy), Inches(8.6), Inches(hy))
        v.line.color.rgb = _rgb((71, 85, 105))
        h.line.color.rgb = _rgb((71, 85, 105))
    _text(slide, 1.4, 6.65, 7.2, 0.3, "Effort / complexity →", size=11, color=(203, 213, 225), align=PP_ALIGN.CENTER)
    _text(slide, 0.2, 3.6, 1.0, 0.3, "Impact", size=11, color=(203, 213, 225), align=PP_ALIGN.CENTER)
    for index, item in enumerate(items[:8]):
        label, _ = _label(item, f"Item {index + 1}")
        x = 1.75 + (index % 4) * 1.72
        y = 2.1 + (index // 4) * 2.0
        _shape(slide, MSO_SHAPE.OVAL, x, y, 0.34, 0.34, fill=_accent(index, accent), line=_accent(index, accent))
        _text(slide, x + 0.45, y - 0.02, 1.1, 0.42, label, size=10, color=light, bold=True)


def render_visual_spec(slides: list[dict[str, Any]], *, theme_name: str = "") -> bytes:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    colors = _palette(theme_name)
    bg, panel, accent, light = colors
    for index, spec in enumerate(slides):
        slide = prs.slides.add_slide(blank)
        _background(slide, bg)
        kind = str(spec.get("type") or "cards").lower()
        content = _content(spec)
        if isinstance(spec.get("content"), str):
            content = {"subtitle": spec.get("content")}
        title = str(spec.get("title") or content.get("title") or f"Slide {index + 1}")
        if kind in {"title", "cover"}:
            _shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 13.333, 7.5, fill=panel)
            _shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 0.35, 7.5, fill=accent)
            _text(slide, 1.0, 2.1, 11.0, 1.2, title, size=38, color=light, bold=True)
            _text(slide, 1.05, 3.5, 9.8, 0.7, content.get("subtitle") or content.get("description") or "", size=20, color=(203, 213, 225))
        elif kind == "agenda":
            _title(slide, title, accent, dark=True)
            _agenda(slide, _items(content, ("bullets", "items", "sections")), colors)
        elif kind in {"card_style", "cards"}:
            _title(slide, title, accent, dark=True)
            _cards(slide, _items(content, ("cards", "bullets", "items")), colors)
        elif kind in {"process", "flow"}:
            _title(slide, title, accent, dark=True)
            _flow(slide, _items(content, ("steps", "stages", "items", "milestones")), colors)
        elif kind == "roadmap":
            _title(slide, title, accent, dark=True)
            _roadmap(slide, _items(content, ("steps", "stages", "items", "milestones")), colors)
        elif kind in {"timeline"}:
            _title(slide, title, accent, dark=True)
            _timeline(slide, _items(content, ("events", "milestones", "items")), colors)
        elif kind in {"architecture", "hub_spoke", "hub-and-spoke"}:
            _title(slide, title, accent, dark=True)
            _architecture(slide, content, colors)
        elif kind in {"comparison", "double_column"}:
            _title(slide, title, accent, dark=True)
            _comparison(slide, content, colors)
        elif kind == "before_after":
            _title(slide, title, accent, dark=True)
            _before_after(slide, content, colors)
        elif kind in {"deep_dive"}:
            _title(slide, title, accent, dark=True)
            _deep_dive(slide, content, colors)
        elif kind == "table":
            _title(slide, title, accent, dark=True)
            _table(slide, content, colors)
        elif kind == "infographic":
            _title(slide, title, accent, dark=True)
            _infographic(slide, content, colors)
        elif kind == "key_takeaways":
            _title(slide, title, accent, dark=True)
            _key_takeaways(slide, content, colors)
        elif kind == "swot":
            _title(slide, title, accent, dark=True)
            _swot(slide, content, colors)
        elif kind == "risk_matrix":
            _title(slide, title, accent, dark=True)
            _risk_matrix(slide, _items(content, ("risks", "items")), colors)
        elif kind in {"kpi", "metrics"}:
            _title(slide, title, accent, dark=True)
            _kpi(slide, _items(content, ("metrics", "items")), colors)
        elif kind == "pricing":
            _title(slide, title, accent, dark=True)
            _pricing(slide, _items(content, ("tiers", "plans", "items")), colors)
        elif kind in {"impact_effort_matrix", "impact_matrix", "prioritization_matrix"}:
            _title(slide, title, accent, dark=True)
            _matrix(slide, content, colors, mode=kind)
        elif kind in {"thank_you", "conclusion", "next_steps"}:
            _title(slide, title, accent, dark=True)
            _cards(slide, _items(content, ("cards", "bullets", "next_steps", "items")) or ["Review", "Decide", "Act"], colors)
        else:
            _title(slide, title, accent, dark=True)
            _cards(slide, _items(content, ("cards", "bullets", "metrics", "risks", "items")) or ["Key idea: practical implication"], colors)
        _text(slide, 11.8, 7.05, 0.7, 0.25, str(index + 1), size=9, color=(148, 163, 184), align=PP_ALIGN.RIGHT)
    if not prs.slides:
        raise ValueError("Visual specification produced no slides")
    output = io.BytesIO()
    prs.save(output)
    return output.getvalue()
