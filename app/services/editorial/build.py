"""Editorial specification-to-PPTX build entry point."""

from __future__ import annotations

import os
import logging
from pathlib import Path

from pptx import Presentation

from .brief import EditorialBriefError, build_editorial_spec
from .images import get_slide_photo
from .layouts import render_editorial_slide
from .qa import run_editorial_qa
from .theme import EditorialTheme

logger = logging.getLogger(__name__)


def _frame(layout: str) -> tuple[float, float]:
    if layout in {"split_photo_cards", "stack_with_photo", "numbered_list_photo"}:
        return 4.89, 7.5
    if layout == "photo_overlay":
        return 13.333, 7.5
    return 13.333, 7.5


def _write_notes(slide, notes: str) -> None:
    try:
        notes_slide = slide.notes_slide
        notes_slide.notes_text_frame.text = notes
    except Exception:
        # Notes support varies across python-pptx versions; never fail the deck
        # after the native slide content has been created.
        pass


def build_editorial_pptx(deck_spec, out_path: str | os.PathLike[str], *, theme_name: str | None = None) -> str:
    prs = Presentation()
    prs.slide_width = 12192000
    prs.slide_height = 6858000
    blank = prs.slide_layouts[6]
    selected_theme = theme_name or getattr(deck_spec.style_guide, "theme_id", None) or "warm_editorial"
    theme = EditorialTheme.from_name(selected_theme)
    used_ids: set[str] = set()
    photo_count = 0
    photo_layout_count = 0
    for index, spec in enumerate(deck_spec.slides):
        slide = prs.slides.add_slide(blank)
        image_bytes = None
        if spec.image:
            photo_layout_count += 1
            frame_w, frame_h = _frame(spec.layout)
            image_payload = spec.image.model_dump()
            image_payload["image_style"] = deck_spec.style_guide.image_style
            image_bytes = get_slide_photo(image_payload, frame_w, frame_h, used_ids)
            if image_bytes:
                photo_count += 1
        render_editorial_slide(slide, spec, theme=theme, image_bytes=image_bytes)
        _write_notes(slide, spec.notes)
        slide.name = f"Editorial Slide {index + 1}: {spec.layout}"
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output))
    logger.info("Editorial build complete slides=%s photo_slides=%s/%s", len(deck_spec.slides), photo_count, photo_layout_count)
    findings = run_editorial_qa(prs)
    for finding in findings:
        logger.warning("Editorial QA: %s", finding)
    return str(output)


__all__ = ["EditorialBriefError", "build_editorial_spec", "build_editorial_pptx"]
