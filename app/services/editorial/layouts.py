from __future__ import annotations

import io
import math
from lxml import etree
from typing import Any, Callable

from PIL import Image, ImageFont
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

from .schema import EditorialSlide
from .theme import EditorialTheme, ensure_editorial_contrast, readable_text, rgb

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _remove_theme_style(shape):
    style = shape._element.find(f"{{{P_NS}}}style")
    if style is not None:
        shape._element.remove(style)


def _shape(slide, name, x, y, w, h, fill, *, line=None, kind=MSO_AUTO_SHAPE_TYPE.RECTANGLE, radius=False):
    shape = slide.shapes.add_shape(
        MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE if radius else kind,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    shape.name = name
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line or fill)
    shape.shadow.inherit = False
    _remove_theme_style(shape)
    return shape


def fit_text(text, font, size_pt, box_w_in, box_h_in, min_pt=12):
    value = str(text or "").strip()
    size = float(size_pt)
    while size > min_pt:
        chars_per_line = max(8, int(box_w_in * 72 / (size * 0.55)))
        lines = sum(max(1, math.ceil(len(line) / chars_per_line)) for line in value.splitlines() or [""])
        if lines * size * 1.2 <= box_h_in * 72:
            break
        size -= 1
    chars_per_line = max(8, int(box_w_in * 72 / (size * 0.55)))
    max_chars = max(chars_per_line, int(chars_per_line * max(1, box_h_in * 72 / (size * 1.2))))
    words = value.split()
    output = ""
    for word in words:
        candidate = f"{output} {word}".strip()
        if len(candidate) > max_chars:
            break
        output = candidate
    return output or value[:max_chars], size


def distribute(n, top, bottom, max_pitch):
    if n <= 1:
        return [top]
    pitch = min(max_pitch, (bottom - top) / (n - 1))
    start = (top + bottom - pitch * (n - 1)) / 2
    return [start + index * pitch for index in range(n)]


def _text(slide, name, value, x, y, w, h, theme, *, size=14, color=None, bold=False, font=None, align=PP_ALIGN.LEFT, min_pt=12):
    value, size = fit_text(value, font or theme.body_font, size, w, h, min_pt)
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.name = name
    box.shadow.inherit = False
    _remove_theme_style(box)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    p = frame.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = str(value or "")
    run.font.name = font or theme.body_font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = rgb(color or theme.ink)
    return box


def _stack(slide, name, x, y, w, paragraphs, theme, h=None):
    h = h or max(0.55, sum(float(item.get("size", 12)) / 72 * 1.35 + 0.08 for item in paragraphs))
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.name = name
    box.shadow.inherit = False
    _remove_theme_style(box)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.TOP
    for index, item in enumerate(paragraphs):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.space_after = Pt(item.get("after", 6))
        run = paragraph.add_run()
        run.text = str(item.get("text") or "")
        run.font.name = item.get("font") or theme.body_font
        run.font.size = Pt(item.get("size", 12))
        run.font.bold = item.get("bold", False)
        run.font.color.rgb = rgb(item.get("color") or theme.ink)
    return box


def _rule(slide, name, x1, y1, x2, y2, theme):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    line.name = name
    line.line.color.rgb = rgb(theme.muted)
    line.line.width = Pt(1)
    line.shadow.inherit = False
    _remove_theme_style(line)
    return line


def _title(slide, spec: EditorialSlide, theme: EditorialTheme, x=0.667, w=12.0):
    if spec.section:
        _text(slide, "section", spec.section, x, 0.28, min(4.5, w), 0.22, theme, size=11, color=readable_text(theme.background, theme, theme.blue), bold=True, min_pt=11)
    _text(slide, "action_title", spec.action_title, x, 0.62, w, 0.58, theme, size=28, color=theme.ink, font=theme.display_font, min_pt=18)
    _rule(slide, "title_rule", x, 1.30, x + w, 1.30, theme)


def _footer(slide, spec, theme, y=7.08):
    if spec.so_what:
        _text(slide, "so_what", spec.so_what, 0.667, y - 0.38, 12.0, 0.25, theme, size=11, color=theme.muted, min_pt=11)
    if spec.source:
        _text(slide, "source", f"Source: {spec.source}", 0.667, y, 12.0, 0.2, theme, size=10, color=theme.muted, min_pt=10)


def _background(slide, theme):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(theme.background)


def _variant(spec) -> str:
    return str(getattr(spec, "variant", "") or "").lower()


def _photo(slide, name, image_bytes, x, y, w, h, alt_text="Editorial photograph"):
    if image_bytes:
        with Image.open(io.BytesIO(image_bytes)) as image:
            src_ratio = image.width / max(image.height, 1)
        target_ratio = w / max(h, 0.01)
        picture = slide.shapes.add_picture(io.BytesIO(image_bytes), Inches(x), Inches(y), Inches(w), Inches(h))
        if src_ratio > target_ratio:
            picture.crop_left = picture.crop_right = (1 - target_ratio / src_ratio) / 2
        elif src_ratio < target_ratio:
            extra = 1 - src_ratio / target_ratio
            picture.crop_top = extra * 0.4
            picture.crop_bottom = extra * 0.6
        picture.name = name
        _remove_theme_style(picture)
        try:
            picture._element.nvPicPr.cNvPr.set("descr", alt_text)
        except Exception:
            pass
        return picture
    return None


def cover_photo_block(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    cover_surface = theme.blue if image_bytes else theme.rust
    cover_text = readable_text(cover_surface, theme)
    if image_bytes:
        _photo(slide, "cover_photo", image_bytes, 0, 0, 13.333, 7.5, getattr(spec.image, "alt_text", "Editorial cover"))
        _shape(slide, "cover_scrim", 0.4, 3.1, 6.7, 3.7, theme.blue)
    else:
        _shape(slide, "cover_background", 0, 0, 13.333, 7.5, theme.blue)
        _shape(slide, "cover_scrim", 0.4, 3.1, 6.7, 3.7, theme.rust)
    fields = spec.fields
    _text(slide, "cover_eyebrow", spec.eyebrow, 0.72, 3.42, 5.8, 0.25, theme, size=11, color=cover_text, bold=True, min_pt=11)
    _text(slide, "cover_title", fields.title, 0.72, 3.8, 5.9, 1.45, theme, size=32, color=cover_text, font=theme.display_font)
    _text(slide, "cover_subtitle", fields.subtitle, 0.72, 5.48, 5.7, 0.75, theme, size=15, color=cover_text)
    _text(slide, "cover_footer", fields.footer_line, 0.72, 6.55, 5.8, 0.25, theme, size=10, color=cover_text, min_pt=10)


def split_photo_cards(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    mirrored = _variant(spec).endswith("v2")
    photo_x = 8.44 if mirrored else 0
    content_x = 0.8 if mirrored else 5.35
    if image_bytes:
        _photo(slide, "split_photo", image_bytes, photo_x, 0, 4.89, 7.5, getattr(spec.image, "alt_text", "Editorial photograph"))
        title_x, title_w = content_x, 7.3
    else:
        title_x, title_w = 0.8, 11.7
    _title(slide, spec, theme, title_x, title_w)
    for index, card in enumerate(spec.fields.cards):
        x = title_x + index * 3.83
        y = 2.0
        w = 3.5
        _shape(slide, f"card_{index}", x, y, w, 3.7, theme.blue, line=theme.blue, radius=True)
        _stack(slide, f"card_{index}_stack", x + 0.25, y + 0.25, w - 0.5, [
            {"text": card.eyebrow, "size": 11, "color": theme.white, "bold": True, "after": 9},
            {"text": card.heading, "size": 18, "color": theme.white, "font": theme.display_font, "after": 12},
            {"text": card.body, "size": 14, "color": theme.white, "after": 0},
        ], theme, h=3.1)
    _shape(slide, "takeaway_banner", title_x, 5.96, title_w, 0.55, theme.rust)
    _text(slide, "takeaway", spec.fields.takeaway, title_x + 0.2, 6.1, title_w - 0.4, 0.22, theme, size=12, color=readable_text(theme.rust, theme), bold=True)
    _footer(slide, spec, theme)


def stack_with_photo(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    photo_left = _variant(spec).endswith("v2")
    content_x = 5.35 if photo_left else 0.85
    photo_x = 0 if photo_left else 8.45
    _title(slide, spec, theme, content_x, 7.4)
    _photo(slide, "stack_photo", image_bytes, photo_x, 0, 4.89, 7.5, getattr(spec.image, "alt_text", "Editorial photograph"))
    if not image_bytes:
        _shape(slide, "stack_no_photo", photo_x, 0, 4.89, 7.5, theme.rust)
    positions = distribute(len(spec.fields.rows), 1.8, 5.9, 0.95)
    for index, row in enumerate(spec.fields.rows):
        y = positions[index]
        _text(slide, f"stack_label_{index}", row.label, content_x, y, 2.2, 0.25, theme, size=14, color=readable_text(theme.background, theme, theme.blue), bold=True, min_pt=11)
        _text(slide, f"stack_detail_{index}", row.detail, content_x + 2.25, y, 4.5, 0.35, theme, size=14, color=readable_text(theme.background, theme, theme.ink), min_pt=12)
        _rule(slide, f"stack_rule_{index}", content_x, y + 0.5, content_x + 6.75, y + 0.5, theme)
    arrow_x = 4.9 if photo_left else 7.82
    arrow = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.UP_ARROW, Inches(arrow_x), Inches(2.0), Inches(0.42), Inches(3.6))
    arrow.name = "stack_up_arrow"
    arrow.fill.solid(); arrow.fill.fore_color.rgb = rgb(theme.rust); arrow.line.color.rgb = rgb(theme.rust); arrow.shadow.inherit = False
    _remove_theme_style(arrow)
    _text(slide, "arrow_caption", spec.fields.arrow_caption, 7.55, 5.75, 0.95, 0.5, theme, size=10, color=theme.rust, bold=True, align=PP_ALIGN.CENTER)
    _footer(slide, spec, theme)


def loop_diagram(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    count = len(spec.fields.steps)
    positions = {
        3: [(4.65, 1.85), (1.1, 5.05), (8.2, 5.05)],
        4: [(1.25, 2.05), (8.05, 2.05), (8.05, 4.65), (1.25, 4.65)],
        5: [(1.25, 2.05), (7.85, 2.05), (9.15, 4.55), (4.55, 5.2), (0.0, 4.55)],
    }.get(count, [(1.25 + (index % 2) * 6.8, 2.1 + (index // 2) * 1.8) for index in range(count)])
    nodes = []
    node_size = (4.0, 1.2)
    for index, step in enumerate(spec.fields.steps):
        x, y = positions[index]
        node = _shape(slide, f"loop_node_{index}", x, y, node_size[0], node_size[1], theme.white, line=theme.blue, radius=True)
        frame = node.text_frame
        frame.clear(); frame.word_wrap = True; frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        paragraph = frame.paragraphs[0]; paragraph.alignment = PP_ALIGN.CENTER
        run = paragraph.add_run(); run.text = step; run.font.name = theme.display_font; run.font.size = Pt(15); run.font.color.rgb = rgb(theme.ink)
        nodes.append(node)
        _shape(slide, f"loop_badge_{index}", x - 0.16, y - 0.16, 0.34, 0.34, theme.rust, line=theme.rust, kind=MSO_AUTO_SHAPE_TYPE.OVAL)
        _text(slide, f"loop_badge_text_{index}", f"{index + 1}.", x - 0.13, y - 0.11, 0.28, 0.18, theme, size=9, color=theme.white, bold=True, align=PP_ALIGN.CENTER, min_pt=9)
    for index in range(len(nodes)):
        next_index = (index + 1) % len(nodes)
        x1, y1 = positions[index]
        x2, y2 = positions[next_index]
        dx, dy = x2 - x1, y2 - y1
        if count == 3:
            site_pair = {
                (0, 1): (2, 0),  # top centre -> bottom left
                (1, 2): (3, 1),  # bottom left -> bottom right
                (2, 0): (0, 2),  # bottom right -> top centre
            }[(index, next_index)]
        elif abs(dx) >= abs(dy):
            site_pair = (3, 1) if dx >= 0 else (1, 3)
        else:
            site_pair = (2, 0) if dy >= 0 else (0, 2)
        begin_site, end_site = site_pair
        connector = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            Inches(x1 + node_size[0] / 2),
            Inches(y1 + node_size[1] / 2),
            Inches(x2 + node_size[0] / 2),
            Inches(y2 + node_size[1] / 2),
        )
        connector.name = f"loop_connector_{index}"
        connector.line.color.rgb = rgb(theme.rust); connector.line.width = Pt(1.5); connector.shadow.inherit = False
        _remove_theme_style(connector)
        try:
            connector.begin_connect(nodes[index], begin_site)
            connector.end_connect(nodes[(index + 1) % len(nodes)], end_site)
            tail = connector._element.spPr.ln.find(f"{{{P_NS}}}tailEnd")
            if tail is None:
                tail = etree.SubElement(connector._element.spPr.ln, f"{{{P_NS}}}tailEnd")
            tail.set("type", "triangle")
            tail.set("w", "med")
            tail.set("len", "med")
        except Exception:
            pass
        try:
            tree = slide.shapes._spTree
            tree.remove(connector._element)
            first_node = min(tree.index(node._element) for node in nodes)
            tree.insert(first_node, connector._element)
        except Exception:
            pass
    _footer(slide, spec, theme)


def photo_overlay(slide, spec, theme, image_bytes=None):
    if image_bytes:
        _photo(slide, "overlay_photo", image_bytes, 0, 0, 13.333, 7.5, getattr(spec.image, "alt_text", "Editorial photograph"))
    else:
        _shape(slide, "overlay_background", 0, 0, 13.333, 7.5, theme.blue)
    scrim = _shape(slide, "overlay_scrim", 0, 4.82, 13.333, 2.68, theme.ink)
    try:
        from lxml import etree
        solid = scrim.fill._xPr.srgbClr
        solid.append(etree.Element("{http://schemas.openxmlformats.org/drawingml/2006/main}alpha", val="70000"))
    except Exception:
        pass
    fields = spec.fields
    _text(slide, "overlay_title", fields.title, 0.8, 5.12, 11.5, 0.62, theme, size=30, color=theme.white, font=theme.display_font)
    _text(slide, "overlay_lines", "\n".join(fields.lines), 0.8, 5.92, 11.0, 0.82, theme, size=15, color=theme.white)


def stat_pair_banner(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    for index, stat in enumerate(spec.fields.stats):
        x = 0.85 + index * 6.1
        _text(slide, f"stat_{index}_value", stat.value, x, 2.0, 5.3, 0.8, theme, size=42, color=theme.rust, font=theme.display_font, bold=True)
        _text(slide, f"stat_{index}_caption", stat.caption, x, 2.95, 5.3, 0.55, theme, size=16, color=theme.ink, min_pt=12)
    _shape(slide, "stat_banner", 0.85, 4.2, 11.65, 1.45, theme.blue)
    _text(slide, "stat_banner_heading", spec.fields.banner_heading, 1.15, 4.48, 3.2, 0.3, theme, size=16, color=theme.white, bold=True)
    _text(slide, "stat_banner_body", spec.fields.banner_body, 4.25, 4.42, 7.7, 0.55, theme, size=13, color=theme.white)
    _footer(slide, spec, theme)


def table(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    rows = len(spec.fields.rows) + 1
    cols = len(spec.fields.columns)
    shape = slide.shapes.add_table(rows, cols, Inches(0.8), Inches(1.8), Inches(11.75), Inches(min(4.7, max(3.0, rows * 0.72))))
    shape.name = "editorial_native_table"
    tbl = shape.table
    for col, value in enumerate(spec.fields.columns):
        tbl.cell(0, col).text = value
    for row, values in enumerate(spec.fields.rows, 1):
        for col, value in enumerate(values):
            if col < cols:
                tbl.cell(row, col).text = value
    for row in range(rows):
        for col in range(cols):
            cell = tbl.cell(row, col)
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(theme.background)
            cell.margin_top = Inches(0.08)
            cell.margin_bottom = Inches(0.08)
            for paragraph in cell.text_frame.paragraphs:
                paragraph.alignment = PP_ALIGN.LEFT
                paragraph.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.name = theme.body_font
                    run.font.size = Pt(14 if row else 12)
                    run.font.bold = row == 0
                    run.font.color.rgb = rgb(theme.muted if row == 0 else theme.ink)
            if row == 0:
                cell.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    for row in range(rows):
        y = 1.8 + (min(4.7, max(3.0, rows * 0.72)) / rows) * (row + 1)
        _rule(slide, f"table_rule_{row}", 0.8, y, 12.55, y, theme)
    _footer(slide, spec, theme)


def numbered_columns(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    count = len(spec.fields.items)
    width = 11.8 / count
    block_y = 2.1
    card_variant = _variant(spec) in {"numbered_cards", "cards"}
    for index, item in enumerate(spec.fields.items):
        x = 0.75 + index * width
        if card_variant:
            _shape(
                slide,
                f"numbered_card_{index}",
                x,
                block_y - 0.12,
                width - 0.3,
                3.55,
                theme.blue,
                line=theme.muted,
                radius=True,
            )
        if index:
            if not card_variant:
                _shape(slide, f"column_rule_{index}", x - 0.1, 1.8, 0.012, 4.6, theme.muted)
        text_color = readable_text(theme.blue, theme, theme.white) if card_variant else theme.ink
        muted_color = readable_text(theme.blue, theme, theme.white) if card_variant else theme.muted
        _text(slide, f"column_number_{index}", f"{index + 1:02}", x + (0.18 if card_variant else 0), block_y, width - 0.3, 0.5, theme, size=24, color=theme.rust, font=theme.display_font, min_pt=18)
        _text(slide, f"column_heading_{index}", item.heading, x + (0.18 if card_variant else 0), block_y + 0.75, width - 0.3, 0.5, theme, size=18, color=text_color, bold=True, min_pt=14)
        _text(slide, f"column_body_{index}", item.body, x + (0.18 if card_variant else 0), block_y + 1.45, width - 0.3, 1.4, theme, size=14, color=muted_color, min_pt=12)
    _footer(slide, spec, theme)


def color_block_list(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    fields = spec.fields
    if _variant(spec) == "tier_cards":
        _title(slide, spec, theme)
        card_width = 3.75
        for index, point in enumerate(fields.points[:3]):
            x = 0.75 + index * 4.15
            _shape(slide, f"tier_card_{index}", x, 2.25, card_width, 3.15, theme.blue, line=theme.muted, radius=True)
            _text(slide, f"tier_label_{index}", point.heading, x + 0.25, 2.55, card_width - 0.5, 0.35, theme, size=13, color=theme.white, bold=True)
            _text(slide, f"tier_body_{index}", point.detail, x + 0.25, 3.15, card_width - 0.5, 1.6, theme, size=15, color=theme.white, min_pt=12)
        _footer(slide, spec, theme)
        return
    _shape(slide, "color_block", 0, 0, 5.6, 7.5, theme.blue)
    _text(slide, "block_eyebrow", fields.block_eyebrow, 0.75, 1.45, 4.2, 0.25, theme, size=10, color=theme.white, bold=True)
    _text(slide, "big_phrase", fields.big_phrase, 0.75, 2.1, 4.2, 1.7, theme, size=36, color=theme.white, font=theme.display_font)
    _text(slide, "block_body", fields.block_body, 0.75, 4.45, 4.1, 1.0, theme, size=14, color=theme.white)
    for index, point in enumerate(fields.points):
        y = 1.55 + index * 1.55
        _text(slide, f"point_heading_{index}", point.heading, 6.3, y, 5.9, 0.3, theme, size=16, color=readable_text(theme.background, theme, theme.ink), bold=True)
        _text(slide, f"point_detail_{index}", point.detail, 6.3, y + 0.45, 5.9, 0.55, theme, size=12, color=readable_text(theme.background, theme, theme.muted))
        _shape(slide, f"point_rule_{index}", 6.3, y + 1.15, 5.8, 0.012, theme.muted)


def numbered_list_photo(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme, 0.8, 7.4)
    _photo(slide, "numbered_photo", image_bytes, 8.45, 0, 4.89, 7.5, getattr(spec.image, "alt_text", "Editorial photograph"))
    if not image_bytes:
        _shape(slide, "numbered_no_photo", 8.45, 0, 4.89, 7.5, theme.blue)
    for index, item in enumerate(spec.fields.items):
        y = distribute(len(spec.fields.items), 1.8, 5.8, 1.15)[index]
        _text(slide, f"list_number_{index}", f"{index + 1:02}", 0.8, y, 0.75, 0.3, theme, size=15, color=theme.rust, bold=True)
        _text(slide, f"list_heading_{index}", item.heading, 1.65, y - 0.02, 6.1, 0.3, theme, size=18, color=theme.ink, bold=True, min_pt=14)
        _text(slide, f"list_detail_{index}", item.detail, 1.65, y + 0.4, 6.1, 0.3, theme, size=14, color=theme.muted, min_pt=12)
        if _variant(spec) == "photo_table":
            _shape(slide, f"list_rule_{index}", 0.8, y + 0.95, 7.1, 0.012, theme.muted)
    _footer(slide, spec, theme)


def quote(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _shape(slide, "quote_panel", 0.8, 1.55, 11.75, 4.8, theme.blue)
    _text(slide, "quote_mark", "“", 1.25, 1.9, 1.0, 0.9, theme, size=60, color=theme.rust, font=theme.display_font)
    _text(slide, "quote_text", spec.fields.quote, 1.65, 2.5, 9.8, 1.8, theme, size=28, color=theme.white, font=theme.display_font)
    _text(slide, "quote_attribution", f"— {spec.fields.attribution}", 1.7, 5.25, 8.5, 0.3, theme, size=12, color=theme.white)


def section_break(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _shape(slide, "section_block", 0, 0, 13.333, 7.5, theme.rust)
    accent_text = readable_text(theme.rust, theme)
    _text(slide, "section_eyebrow", spec.eyebrow or "SECTION", 0.9, 2.2, 4.5, 0.3, theme, size=11, color=accent_text, bold=True)
    _text(slide, "section_phrase", spec.fields.phrase, 0.9, 2.75, 10.8, 1.8, theme, size=42, color=accent_text, font=theme.display_font)


def comparison(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    width = 11.5 / len(spec.fields.columns)
    for index, column in enumerate(spec.fields.columns):
        x = 0.85 + index * (width + 0.2)
        _shape(slide, f"comparison_panel_{index}", x, 1.75, width, 4.5, theme.white, line=theme.blue, radius=True)
        _text(slide, f"comparison_heading_{index}", column.heading, x + 0.25, 2.05, width - 0.5, 0.5, theme, size=18, color=theme.blue, font=theme.display_font, bold=True)
        points = list(column.points)
        point_gap = min(0.68, 2.85 / max(1, len(points)))
        for row, point in enumerate(points):
            y = 2.82 + row * point_gap
            _shape(slide, f"comparison_marker_{index}_{row}", x + 0.28, y + 0.08, 0.12, 0.12, theme.rust, line=theme.rust, kind=MSO_AUTO_SHAPE_TYPE.OVAL)
            _text(slide, f"comparison_point_{index}_{row}", str(point), x + 0.55, y, width - 0.8, 0.42, theme, size=13, color=readable_text(theme.white, theme, theme.ink), min_pt=11)
    _footer(slide, spec, theme)


def timeline(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1), Inches(3.6), Inches(12.2), Inches(3.6))
    line.name = "timeline_axis"
    line.line.color.rgb = rgb(theme.blue)
    line.line.width = Pt(2)
    _remove_theme_style(line)
    for index, item in enumerate(spec.fields.items):
        x = 1.0 + index * (11.2 / max(1, len(spec.fields.items) - 1))
        _shape(slide, f"timeline_marker_{index}", x - 0.18, 3.42, 0.36, 0.36, theme.rust, line=theme.rust, kind=MSO_AUTO_SHAPE_TYPE.OVAL)
        y = 2.05 if index % 2 == 0 else 4.05
        _text(slide, f"timeline_label_{index}", item.label, x - 0.6, y, 1.2, 0.3, theme, size=14, color=readable_text(theme.background, theme, theme.blue), bold=True, align=PP_ALIGN.CENTER)
        _text(slide, f"timeline_detail_{index}", item.detail, x - 0.85, y + 0.4, 1.7, 0.6, theme, size=10, color=theme.muted, align=PP_ALIGN.CENTER)
    _footer(slide, spec, theme)


def hub_spoke(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    _shape(slide, "hub", 5.15, 2.75, 3.0, 1.25, theme.blue, line=theme.blue, kind=MSO_AUTO_SHAPE_TYPE.OVAL)
    _text(slide, "hub_text", spec.fields.center, 5.45, 3.1, 2.4, 0.4, theme, size=18, color=theme.white, bold=True, align=PP_ALIGN.CENTER)
    import math
    count = len(spec.fields.spokes)
    positions = [
        (6.65 + 5.0 * math.cos(-math.pi / 2 + index * 2 * math.pi / count) - 1.3,
         3.38 + 2.35 * math.sin(-math.pi / 2 + index * 2 * math.pi / count) - 0.5)
        for index in range(count)
    ]
    connectors = []
    for index, spoke in enumerate(spec.fields.spokes):
        x, y = positions[index]
        dx, dy = x + 1.3 - 6.65, y + 0.5 - 3.38
        length = max((dx * dx + dy * dy) ** 0.5, 0.1)
        ux, uy = dx / length, dy / length
        connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(6.65 + ux * 1.5), Inches(3.38 + uy * 0.65), Inches(x + 1.3 - ux * 1.3), Inches(y + 0.5 - uy * 0.5))
        connector.name = f"hub_connector_{index}"
        connector.line.color.rgb = rgb(theme.rust); connector.line.width = Pt(1.2); connector.shadow.inherit = False
        _remove_theme_style(connector)
        connectors.append(connector)
        _shape(slide, f"spoke_{index}", x, y, 2.6, 1.0, theme.white, line=theme.blue, radius=True)
        _stack(slide, f"spoke_stack_{index}", x + 0.15, y + 0.14, 2.3, [
            {"text": spoke.heading, "size": 12, "color": readable_text(theme.white, theme, theme.blue), "bold": True, "after": 4},
            {"text": spoke.body, "size": 10, "color": readable_text(theme.white, theme, theme.muted), "after": 0},
        ], theme, h=0.7)
    for connector in connectors:
        tree = slide.shapes._spTree
        tree.remove(connector._element); tree.insert(2, connector._element)


def closing_actions(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    for index, action in enumerate(spec.fields.actions):
        y = distribute(len(spec.fields.actions), 2.0, 6.0, 1.3)[index]
        _text(slide, f"action_number_{index}", f"{index + 1:02}", 0.85, y, 0.7, 0.35, theme, size=16, color=theme.rust, bold=True, min_pt=12)
        _stack(slide, f"action_stack_{index}", 1.75, y, 10.2, [
            {"text": action.heading, "size": 16, "color": readable_text(theme.background, theme, theme.ink), "bold": True, "after": 4},
            {"text": action.detail, "size": 14, "color": readable_text(theme.background, theme, theme.muted), "after": 0},
        ], theme, h=0.75)
    _footer(slide, spec, theme)


def big_number(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    _text(slide, "big_number_value", spec.fields.value, 0.9, 2.1, 5.0, 1.4, theme, size=54, color=theme.rust, font=theme.display_font, min_pt=24)
    _text(slide, "big_number_sentence", spec.fields.sentence, 0.95, 4.0, 10.5, 0.9, theme, size=18, color=theme.ink, min_pt=14)
    _footer(slide, spec, theme)


def image_grid(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    captions = spec.fields.captions
    columns = 3 if len(captions) <= 3 else 2
    panel_width = 3.6 if columns == 3 else 5.65
    panel_height = 3.25 if columns == 3 else 2.25
    horizontal_step = 4.1 if columns == 3 else 6.0
    vertical_step = 3.55 if columns == 3 else 2.65
    for index, caption in enumerate(captions):
        x = 0.8 + (index % columns) * horizontal_step
        y = 2.0 + (index // columns) * vertical_step
        if image_bytes and index == 0:
            _photo(slide, f"grid_photo_{index}", image_bytes, x, y, panel_width, panel_height, caption)
            _shape(slide, f"grid_scrim_{index}", x, y + panel_height - 1.05, panel_width, 1.05, theme.ink)
            caption_color = theme.white
        else:
            fill = theme.blue if index % 2 else theme.rust
            _shape(slide, f"grid_panel_{index}", x, y, panel_width, panel_height, fill, line=theme.muted, radius=True)
            _shape(slide, f"grid_marker_{index}", x + 0.22, y + 0.24, 0.42, 0.08, theme.white, line=theme.white)
            caption_color = readable_text(fill, theme, theme.white)
        _text(slide, f"grid_index_{index}", f"{index + 1:02}", x + 0.22, y + 0.42, 0.55, 0.25, theme, size=11, color=caption_color, bold=True)
        _text(slide, f"grid_caption_{index}", caption, x + 0.22, y + 1.1, panel_width - 0.44, panel_height - 1.35, theme, size=20, color=caption_color, bold=True, min_pt=13)
    _footer(slide, spec, theme)


def process_chevrons(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    width = 11.4 / max(1, len(spec.fields.steps))
    details = list(spec.fields.step_details or [])
    if len(details) < len(spec.fields.steps):
        details.extend([""] * (len(spec.fields.steps) - len(details)))
    for index, step in enumerate(spec.fields.steps):
        x = 0.8 + index * width
        surface = theme.blue if index % 2 else theme.rust
        _shape(
            slide,
            f"chevron_{index}",
            x,
            2.35,
            width - 0.12,
            1.35,
            surface,
            line=None,
            kind=MSO_AUTO_SHAPE_TYPE.CHEVRON,
        )
        _text(
            slide,
            f"chevron_label_{index}",
            step,
            x + 0.14,
            2.57,
            max(0.8, width - 0.56),
            0.9,
            theme,
            size=13,
            color=readable_text(surface, theme),
            bold=True,
            align=PP_ALIGN.CENTER,
            min_pt=9,
        )
        if details[index]:
            _text(slide, f"chevron_detail_{index}", details[index], x + 0.08, 4.05, width - 0.28, 0.78, theme, size=14, color=theme.ink, align=PP_ALIGN.CENTER, min_pt=12)
    _footer(slide, spec, theme)


def quote_photo(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    if image_bytes:
        _photo(slide, "quote_photo_image", image_bytes, 8.5, 0, 4.833, 7.5, getattr(spec.image, "alt_text", "Editorial photograph"))
    else:
        _shape(slide, "quote_photo_panel", 8.5, 0, 4.833, 7.5, theme.blue)
    _title(slide, spec, theme, w=7.2)
    _text(slide, "quote_photo_quote", f"“{spec.fields.quote}”", 0.9, 2.2, 6.8, 2.0, theme, size=26, color=theme.ink, font=theme.display_font, min_pt=14)
    _text(slide, "quote_photo_attribution", spec.fields.attribution, 0.95, 5.2, 6.2, 0.4, theme, size=14, color=theme.muted, min_pt=10)
    _footer(slide, spec, theme)


def bar_chart(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    maximum = max(spec.fields.values) or 1
    width = 10.8 / len(spec.fields.values)
    for index, (label, value) in enumerate(zip(spec.fields.labels, spec.fields.values)):
        height = 3.7 * max(0, value) / maximum
        x = 1.0 + index * width
        _shape(slide, f"bar_{index}", x, 5.8 - height, width - 0.25, height, theme.blue if index % 2 else theme.rust)
        _text(slide, f"bar_label_{index}", label, x, 6.0, width - 0.25, 0.45, theme, size=12, color=theme.ink, align=PP_ALIGN.CENTER, min_pt=10)
    _text(slide, "bar_source", spec.fields.source, 0.9, 6.85, 11.5, 0.3, theme, size=10, color=theme.muted, min_pt=10)


def matrix_2x2(slide, spec, theme, image_bytes=None):
    _background(slide, theme)
    _title(slide, spec, theme)
    details = list(spec.fields.quadrant_details or [])
    if len(details) < len(spec.fields.quadrants):
        details.extend([""] * (len(spec.fields.quadrants) - len(details)))
    for index, quadrant in enumerate(spec.fields.quadrants):
        x = 1.0 + (index % 2) * 5.7
        y = 2.0 + (index // 2) * 2.25
        _shape(slide, f"matrix_{index}", x, y, 5.1, 1.8, theme.white, line=theme.blue, radius=True)
        _text(slide, f"matrix_heading_{index}", quadrant, x + 0.25, y + 0.25, 4.6, 0.35, theme, size=16, color=readable_text(theme.white, theme, theme.blue), bold=True, align=PP_ALIGN.CENTER, min_pt=12)
        if details[index]:
            _text(slide, f"matrix_detail_{index}", details[index], x + 0.35, y + 0.82, 4.4, 0.65, theme, size=14, color=readable_text(theme.white, theme, theme.ink), align=PP_ALIGN.CENTER, min_pt=12)
    _footer(slide, spec, theme)


LAYOUT_RENDERERS: dict[str, Callable[..., None]] = {
    "cover_photo_block": cover_photo_block,
    "split_photo_cards": split_photo_cards,
    "stack_with_photo": stack_with_photo,
    "loop_diagram": loop_diagram,
    "photo_overlay": photo_overlay,
    "stat_pair_banner": stat_pair_banner,
    "table": table,
    "numbered_columns": numbered_columns,
    "color_block_list": color_block_list,
    "numbered_list_photo": numbered_list_photo,
    "quote": quote,
    "section_break": section_break,
    "comparison": comparison,
    "timeline": timeline,
    "hub_spoke": hub_spoke,
    "closing_actions": closing_actions,
    "big_number": big_number,
    "image_grid": image_grid,
    "process_chevrons": process_chevrons,
    "quote_photo": quote_photo,
    "bar_chart": bar_chart,
    "matrix_2x2": matrix_2x2,
}


def render_editorial_slide(slide, spec: EditorialSlide, *, theme: EditorialTheme | None = None, image_bytes: bytes | None = None):
    selected_theme = theme or EditorialTheme()
    ensure_editorial_contrast(selected_theme)
    renderer = LAYOUT_RENDERERS.get(spec.layout)
    if renderer is None:
        raise ValueError(f"Unsupported Editorial layout: {spec.layout}")
    renderer(slide, spec, selected_theme, image_bytes)


__all__ = ["EditorialTheme", "LAYOUT_RENDERERS", "render_editorial_slide"]
