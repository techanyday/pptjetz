from __future__ import annotations

import re
from pathlib import Path

path = Path(r"app/services/pptx_builder.py")
src = path.read_text(encoding="utf-8")

if "def render_timeline_hex_chain" not in src:
    marker = "    def render_process_variant("
    idx = src.find(marker)
    if idx == -1:
        raise SystemExit("Could not find insertion marker for timeline variant")

    fn = """

    def render_timeline_hex_chain(title_txt: str, points: List[str], topic_src: str, subtitle_override: str | None = None):
        \"\"\"Hexagon chain timeline: alternating hex nodes on a baseline (matches reference design).\"\"\"
        s = prs.slides.add_slide(blank); apply_bg(s)

        title_up = (title_txt or \"Timeline\").strip() or \"Timeline\"
        _add_textbox(s, 1.0, 0.55, 11.3, 0.75, title_up.upper(), font_size_pt=24, bold=True, color=TITLE_COL, align=\"center\", word_wrap=True, auto_fit=True)

        sub = str(subtitle_override or \"\").strip()
        if sub:
            _add_textbox(s, 1.0, 1.20, 11.3, 0.40, sub.upper(), font_size_pt=11, color=pal.get(\"muted\", (71,85,105)), align=\"center\", word_wrap=True, auto_fit=True)

        # Slide index bubble (top-right)
        try:
            idx_txt = str(int(current_render_idx))
        except Exception:
            idx_txt = \"\"
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
            r.font.color.rgb = RGBColor(*pal.get(\"muted\", (71,85,105)))

        items = [str(x).strip() for x in (points or []) if str(x).strip()]
        if not items:
            items = [\"Identification\", \"Initiation\", \"Planning\", \"Execution & Control\", \"Closeout\"]

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

        accent_a = tuple(pal.get(\"primary\", (14, 165, 233)))
        accent_b = tuple(pal.get(\"text\", (15, 23, 42)))
        light_fill = (243, 244, 246)

        positions: List[tuple[float, float]] = []
        for i in range(n):
            x = start_x + i * (hex_w + gap)
            y = top_y if (i % 2 == 0) else bot_y
            positions.append((x, y))

        for i, (x, y) in enumerate(positions):
            head, tail = _split_head_tail(items[i])
            label = (head or items[i]).strip()
            desc = (tail or \"\").strip()

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
                _add_textbox(s, tx, ty, w, 0.55, desc[:110], font_size_pt=8, color=pal.get(\"muted\", (71,85,105)), align=\"center\", word_wrap=True, auto_fit=True)
"""

    src = src[:idx] + fn + src[idx:]

# Patch _render_timeline: variant_map, eligible, dispatch
start = src.find("def _render_timeline(sl)")
if start == -1:
    raise SystemExit("Could not find _render_timeline")
end = src.find("def _render_roadmap", start)
if end == -1:
    raise SystemExit("Could not find end of _render_timeline")
block = src[start:end]

if "hex_chain" not in block:
    # 2a) variant_map insert
    vm_line = '            "chevrons": "vchevrons",\n'
    if vm_line in block:
        insert = (
            '            "hex": "hex_chain",\n'
            '            "hex_chain": "hex_chain",\n'
            '            "hex-chain": "hex_chain",\n'
            '            "hexagon": "hex_chain",\n'
            '            "hexagons": "hex_chain",\n'
        )
        block = block.replace(vm_line, vm_line + insert, 1)

    # 2b) eligibility gating
    elig_pat = '        eligible: List[str] = ["horizontal", "vchevrons", "linear"]\n'
    if elig_pat in block and "eligible.append(\"hex_chain\")" not in block:
        gate = (
            elig_pat
            + '        if n >= 4 and n <= 6 and max_len <= 65:\n'
            + '            eligible.append("hex_chain")\n'
        )
        block = block.replace(elig_pat, gate, 1)

    # 2c) dispatch branch
    vdisp = '        elif chosen == "vchevrons":\n            render_timeline_vchevrons(title_txt, pts, title_txt, subtitle_override)\n'
    if vdisp in block and 'elif chosen == "hex_chain":' not in block:
        block = block.replace(
            vdisp,
            vdisp + '        elif chosen == "hex_chain":\n            render_timeline_hex_chain(title_txt, pts, title_txt, subtitle_override)\n',
            1,
        )

src = src[:start] + block + src[end:]

path.write_text(src, encoding="utf-8")
print("Patched timeline hex_chain variant successfully")
