from __future__ import annotations

import os
import pytest

from app.services.pptx_builder import build_pptx_from_slides


def test_unknown_slide_type_raises_in_slide_debug(tmp_path):
    os.environ["SLIDE_DEBUG"] = "1"
    out = tmp_path / "x.pptx"
    slides = [
        {"type": "title", "title": "Deck"},
        {"type": "unknown_type", "title": "Bad", "content": {"bullets": ["a", "b", "c"]}},
    ]

    with pytest.raises(ValueError):
        build_pptx_from_slides(slides, str(out), theme_name="SLEEK")
