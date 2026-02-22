from __future__ import annotations

import os
import re

from app.services.pptx_builder import build_pptx_from_slides


def _variant_lines(capsys):
    out = capsys.readouterr().out
    return [ln for ln in out.splitlines() if ln.startswith("slide_debug:variant ")]


def _family_variants(lines, family: str) -> list[str]:
    got = []
    for ln in lines:
        if f"family={family} " not in ln:
            continue
        m = re.search(r"\bchosen=([^\s]+)", ln)
        if m:
            got.append(m.group(1))
    return got


def test_variant_rotation_timeline_is_deterministic_and_resets_per_build(tmp_path, capsys):
    os.environ["SLIDE_DEBUG"] = "1"

    slides = [
        {"type": "title", "title": "Deck"},
        {"type": "timeline", "title": "TL1", "content": {"timeline": ["a", "b", "c", "d"]}},
        {"type": "timeline", "title": "TL2", "content": {"timeline": ["a", "b", "c", "d"]}},
        {"type": "timeline", "title": "TL3", "content": {"timeline": ["a", "b", "c", "d"]}},
        {"type": "thank_you", "title": "Thanks"},
    ]

    out1 = tmp_path / "a.pptx"
    build_pptx_from_slides(slides, str(out1), theme_name="SLEEK")
    lines1 = _variant_lines(capsys)
    seq1 = _family_variants(lines1, "timeline")
    assert len(seq1) >= 3
    assert len(set(seq1[:3])) >= 2

    out2 = tmp_path / "b.pptx"
    build_pptx_from_slides(slides, str(out2), theme_name="SLEEK")
    lines2 = _variant_lines(capsys)
    seq2 = _family_variants(lines2, "timeline")
    assert seq2[:3] == seq1[:3]


def test_forced_variant_is_respected_when_eligible(tmp_path, capsys):
    os.environ["SLIDE_DEBUG"] = "1"

    slides = [
        {"type": "title", "title": "Deck"},
        {
            "type": "timeline",
            "title": "TL",
            "content": {"timeline": ["a", "b", "c", "d"], "variant": "vertical"},
        },
        {"type": "thank_you", "title": "Thanks"},
    ]

    out = tmp_path / "x.pptx"
    build_pptx_from_slides(slides, str(out), theme_name="SLEEK")
    lines = _variant_lines(capsys)
    seq = _family_variants(lines, "timeline")
    assert seq
    assert seq[0] == "vertical"


def test_ineligible_forced_variant_is_not_selected(tmp_path, capsys):
    os.environ["SLIDE_DEBUG"] = "1"

    slides = [
        {"type": "title", "title": "Deck"},
        {
            "type": "kpi",
            "title": "KPIs",
            "content": {
                "variant": "strip",
                "metrics": [
                    {"label": "A", "value": "1"},
                    {"label": "B", "value": "2"},
                    {"label": "C", "value": "3"},
                ],
            },
        },
        {"type": "thank_you", "title": "Thanks"},
    ]

    out = tmp_path / "k.pptx"
    build_pptx_from_slides(slides, str(out), theme_name="SLEEK")
    lines = _variant_lines(capsys)
    seq = _family_variants(lines, "kpi")
    assert seq
    assert seq[0] == "grid"


def test_comparison_rotates(tmp_path, capsys):
    os.environ["SLIDE_DEBUG"] = "1"

    slides = [
        {"type": "title", "title": "Deck"},
        {
            "type": "comparison",
            "title": "C1",
            "content": {
                "table": {
                    "left": {"heading": "A", "points": ["p1", "p2", "p3"]},
                    "right": {"heading": "B", "points": ["q1", "q2", "q3"]},
                }
            },
        },
        {
            "type": "comparison",
            "title": "C2",
            "content": {
                "table": {
                    "left": {"heading": "A", "points": ["p1", "p2", "p3"]},
                    "right": {"heading": "B", "points": ["q1", "q2", "q3"]},
                }
            },
        },
        {"type": "thank_you", "title": "Thanks"},
    ]

    out = tmp_path / "c.pptx"
    build_pptx_from_slides(slides, str(out), theme_name="SLEEK")
    lines = _variant_lines(capsys)
    seq = _family_variants(lines, "comparison")
    assert len(seq) >= 2
    assert seq[0] != seq[1]
