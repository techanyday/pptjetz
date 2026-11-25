from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt


RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class EditorialTheme:
    background: RGB = (246, 240, 232)
    ink: RGB = (43, 26, 15)
    muted: RGB = (107, 74, 51)
    blue: RGB = (31, 62, 140)
    rust: RGB = (184, 56, 26)
    white: RGB = (255, 255, 255)
    display_font: str = "Georgia"
    body_font: str = "Calibri"

    @classmethod
    def from_name(cls, theme_name: str | None = None, *, display_font: str | None = None, body_font: str | None = None):
        palettes = {
            "warm_editorial": ((246, 240, 232), (43, 26, 15), (107, 74, 51), (31, 62, 140), (184, 56, 26), "Georgia", "Calibri"),
            "midnight_gold": ((14, 23, 38), (242, 238, 230), (170, 180, 195), (29, 53, 87), (224, 176, 60), "Cambria", "Calibri"),
            "forest_cream": ((243, 241, 232), (30, 42, 34), (86, 101, 91), (44, 95, 45), (194, 87, 26), "Bookman Old Style", "Calibri"),
            "slate_teal": ((238, 242, 243), (27, 42, 51), (82, 100, 110), (15, 92, 107), (228, 87, 46), "Century Schoolbook", "Arial"),
            "ink_citrus": ((28, 28, 30), (245, 245, 240), (181, 181, 173), (51, 51, 58), (198, 241, 53), "Georgia", "Arial"),
            "berry_blush": ((251, 241, 238), (46, 26, 36), (122, 86, 102), (109, 46, 70), (217, 137, 59), "Cambria", "Calibri"),
            "ocean_coral": ((242, 247, 250), (18, 38, 58), (79, 100, 117), (11, 60, 93), (242, 107, 91), "Georgia", "Calibri"),
        }
        aliases = {
            "EDITORIAL": "warm_editorial",
            "SLEEK": "slate_teal",
            "PULSE": "ocean_coral",
            "BUSINESS": "midnight_gold",
            "CLEAN": "forest_cream",
            "CREATIVE": "berry_blush",
            "DARK": "ink_citrus",
            "MINIMAL FLUX": "forest_cream",
            "BLUE HORIZON": "midnight_gold",
            "PRIME_MERIDIAN": "ink_citrus",
        }
        requested = str(theme_name or "warm_editorial").strip()
        values = palettes.get(aliases.get(requested.upper(), requested.lower()), palettes["warm_editorial"])
        return cls(
            background=values[0], ink=values[1], muted=values[2], blue=values[3], rust=values[4],
            display_font=display_font or values[5],
            body_font=body_font or values[6],
        )


def rgb(value: RGB) -> RGBColor:
    return RGBColor(*value)


def contrast_ratio(foreground: RGB, background: RGB) -> float:
    def linear(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    def luminance(value: RGB) -> float:
        return 0.2126 * linear(value[0]) + 0.7152 * linear(value[1]) + 0.0722 * linear(value[2])

    a, b = sorted((luminance(foreground), luminance(background)))
    return (b + 0.05) / (a + 0.05)


def grid_x(column: int, *, margin: float = 0.667, gutter: float = 0.333) -> float:
    return margin + column * ((13.333 - 2 * margin + gutter) / 12)


def grid_width(columns: int, *, gutter: float = 0.333) -> float:
    unit = (13.333 - 2 * 0.667 + gutter) / 12
    return columns * unit - gutter


def ensure_editorial_contrast(theme: EditorialTheme) -> None:
    if contrast_ratio(theme.ink, theme.background) < 4.5:
        raise ValueError("Editorial ink does not meet 4.5:1 contrast on background")
    if contrast_ratio(theme.white, theme.blue) < 4.5:
        raise ValueError("Editorial white does not meet 4.5:1 contrast on blue")


def readable_text(background: RGB, theme: EditorialTheme, preferred: RGB | None = None) -> RGB:
    """Choose a text color that remains readable on a specific surface."""
    candidates = [preferred, theme.white, theme.ink, theme.rust, (18, 20, 24)]
    ranked = [color for color in candidates if color is not None]
    best = max(ranked, key=lambda color: contrast_ratio(color, background))
    return best


__all__ = ["EditorialTheme", "contrast_ratio", "ensure_editorial_contrast", "readable_text", "grid_x", "grid_width", "rgb"]
