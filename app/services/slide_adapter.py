"""Utility to convert rich `SlideObject` items into the simple structure
expected by the existing `PPTGenerator` (title + body text).

This acts as a **bridge** while we progressively refactor the generator to
support richer content types.  For kinds we don't explicitly handle yet we fall
back to a basic representation so users still receive a slide instead of an
error.
"""
from __future__ import annotations

from typing import List, Dict

from app.schemas.slide_object import (
    ChartSlide,
    ComparisonSlide,
    HeaderSlide,
    ImageSlide,
    QuoteSlide,
    SlideObject,
    TableSlide,
    TextSlide,
    TimelineSlide,
)


def slide_objects_to_simple(slides: List[SlideObject]) -> List[Dict[str, str]]:
    """Convert `SlideObject`s into `[{title, content}]` items for PPTGenerator.

    We **skip** the first slide if it is a header because `PPTGenerator`
    already expects a separate *title* and *presenter* argument for the cover
    slide.
    """
    simple: List[Dict[str, str]] = []

    for idx, slide in enumerate(slides):
        # Header slide becomes the cover (first) slide
        if isinstance(slide, HeaderSlide):
            simple.append({
                "title": slide.content.title,
                "content": slide.content.subtitle or "",
            })
            continue

        if isinstance(slide, TextSlide):
            # Defensive: some generated slides may omit bullets; default to empty list
            bullets = slide.content.bullets or []
            body = "\n".join(bullets)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, QuoteSlide):
            quote_text = f"\"{slide.content.quote}\""
            if slide.content.attribution:
                quote_text += f"\n— {slide.content.attribution}"
            simple.append({"title": "Quote", "content": quote_text})
            continue

        if isinstance(slide, ComparisonSlide):
            cols = []
            for col in slide.content.columns or []:
                points = col.points or []
                col_points = "; ".join(points)
                cols.append(f"{col.heading}: {col_points}")
            body = "\n".join(cols)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, TableSlide):
            headers = slide.content.headers or []
            rows = [" | ".join(headers)] if headers else []
            for r in (slide.content.rows or []):
                rows.append(" | ".join(r or []))
            body = "\n".join(rows)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, ChartSlide):
            # For now describe chart verbally – later we can embed actual charts
            labels = slide.content.labels or []
            values = slide.content.values or []
            body = (
                f"Chart type: {slide.content.chart_type}\n"
                f"Labels: {', '.join(labels)}\n"
                f"Values: {', '.join(str(v) for v in values)}"
            )
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, TimelineSlide):
            events_data = slide.content.events or []
            events = [f"{e.label}: {e.description or ''}".strip() for e in events_data]
            body = "\n".join(events)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, ImageSlide):
            # We'll rely on PPTGenerator.include_images flag + prompt
            simple.append({"title": slide.content.title or "Image", "content": slide.content.prompt})
            continue

        # Fallback: generic stringification
        simple.append({"title": getattr(slide, 'kind', 'Slide'), "content": str(slide.content)})

    return simple

__all__ = ["slide_objects_to_simple"]
