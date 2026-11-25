from __future__ import annotations

import logging
from typing import Any

from .brief import BANNED_PHRASES

logger = logging.getLogger(__name__)
EMU_PER_INCH = 914400


def run_editorial_qa(prs) -> list[str]:
    findings: list[str] = []
    width = prs.slide_width / EMU_PER_INCH
    height = prs.slide_height / EMU_PER_INCH
    for slide_index, slide in enumerate(prs.slides, 1):
        lowest = 0.0
        text_shapes = []
        containers = []
        serialized = []
        for shape in slide.shapes:
            x, y = shape.left / EMU_PER_INCH, shape.top / EMU_PER_INCH
            w, h = shape.width / EMU_PER_INCH, shape.height / EMU_PER_INCH
            lowest = max(lowest, y + h)
            if x < -0.01 or y < -0.01 or x + w > width + 0.01 or y + h > height + 0.01:
                findings.append(f"slide {slide_index}: shape outside slide bounds ({shape.name})")
            if getattr(shape, "has_text_frame", False) and shape.text.strip():
                text_shapes.append((shape, x, y, w, h))
                serialized.append(shape.text.lower())
            elif not getattr(shape, "has_text_frame", False) or not shape.text.strip():
                containers.append((x, y, w, h))
            try:
                if shape.shadow.inherit:
                    findings.append(f"slide {slide_index}: visible shadow ({shape.name})")
            except Exception:
                pass
            if shape._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}style") is not None:
                findings.append(f"slide {slide_index}: theme style remains ({shape.name})")
        for index, (_, x1, y1, w1, h1) in enumerate(text_shapes):
            for _, x2, y2, w2, h2 in text_shapes[index + 1:]:
                if any(
                    (cx <= x1 and cy <= y1 and cx + cw >= x1 + w1 and cy + ch >= y1 + h1)
                    or (cx <= x2 and cy <= y2 and cx + cw >= x2 + w2 and cy + ch >= y2 + h2)
                    for cx, cy, cw, ch in containers
                ):
                    continue
                overlap = max(0, min(x1 + w1, x2 + w2) - max(x1, x2)) * max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
                smaller = min(w1 * h1, w2 * h2)
                if smaller and overlap / smaller > 0.02:
                    findings.append(f"slide {slide_index}: overlapping text boxes")
        combined = " ".join(serialized)
        for phrase in BANNED_PHRASES:
            if phrase in combined:
                findings.append(f"slide {slide_index}: banned phrase {phrase}")
        if height - lowest > height * 0.30 and slide_index not in {1}:
            findings.append(f"slide {slide_index}: excessive bottom empty space")
    return findings


__all__ = ["run_editorial_qa"]
