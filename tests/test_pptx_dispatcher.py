from __future__ import annotations

import os
import pytest
from PIL import Image
from pptx import Presentation

from app.services import pptx_builder
from app.services.pptx_builder import _choose_impact_map_variant, build_pptx_from_slides


def test_unknown_slide_type_raises_in_slide_debug(tmp_path):
    os.environ["SLIDE_DEBUG"] = "1"
    out = tmp_path / "x.pptx"
    slides = [
        {"type": "title", "title": "Deck"},
        {"type": "unknown_type", "title": "Bad", "content": {"bullets": ["a", "b", "c"]}},
    ]

    with pytest.raises(ValueError):
        build_pptx_from_slides(slides, str(out), theme_name="SLEEK")


def test_choose_impact_map_variant_rotates_deterministically_without_roles():
    # Default pool: classic, radial, pyramid, two_sided
    c = {}
    got = [_choose_impact_map_variant(content=c, has_roles=False, index=i) for i in range(6)]
    assert got == ["classic", "radial", "pyramid", "two_sided", "classic", "radial"]


def test_choose_impact_map_variant_includes_swimlane_when_roles_present():
    # Pool with roles: classic, radial, pyramid, two_sided, swimlane
    c = {}
    got = [_choose_impact_map_variant(content=c, has_roles=True, index=i) for i in range(7)]
    assert got == ["classic", "radial", "pyramid", "two_sided", "swimlane", "classic", "radial"]


def test_choose_impact_map_variant_respects_forced_variant_when_allowed():
    c = {"impact_map_variant": "pyramid"}
    for i in range(10):
        assert _choose_impact_map_variant(content=c, has_roles=False, index=i) == "pyramid"


def test_build_pptx_from_slides_resets_impact_map_variant_index_per_deck(tmp_path):
    # First build: create at least one infographic so it advances the index.
    out1 = tmp_path / "a.pptx"
    slides = [
        {"type": "title", "title": "Deck"},
        {"type": "infographic", "title": "Hub", "content": {"bullets": ["A", "B", "C", "D"]}},
        {"type": "thank_you", "title": "Thanks"},
    ]
    build_pptx_from_slides(slides, str(out1), theme_name="SLEEK")
    assert pptx_builder.IMPACT_MAP_VARIANT_INDEX >= 1

    # Second build should reset the index back to 0 at the start, then advance.
    out2 = tmp_path / "b.pptx"
    build_pptx_from_slides(slides, str(out2), theme_name="SLEEK")
    assert pptx_builder.IMPACT_MAP_VARIANT_INDEX >= 1


def test_build_pptx_from_slides_injected_image_caption_is_coherent(tmp_path, monkeypatch):
    img = tmp_path / "stub.png"
    Image.new("RGB", (32, 32), (255, 255, 255)).save(img)

    monkeypatch.setattr(
        pptx_builder,
        "resolve_stock_image",
        lambda query, output_dir=None, used_urls=None: {"local_path": str(img)},
    )

    out = tmp_path / "images.pptx"
    slides = [
        {"type": "title", "title": "Unleashing the Future: The Impact and Potential of 5G Technology"},
        {"type": "thank_you", "title": "Thank You"},
    ]

    build_pptx_from_slides(slides, str(out), theme_name="SLEEK")

    prs = Presentation(str(out))
    full_text = "\n".join(
        shape.text
        for slide in prs.slides
        for shape in slide.shapes
        if hasattr(shape, "text") and str(shape.text).strip()
    )

    assert "The Impact and Potential of 5G Technology creates the most value" in full_text
    assert "This visual" not in full_text
    assert "creates measurable business value" not in full_text
