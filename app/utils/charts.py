"""Utility functions for generating charts/graphs using matplotlib.

This module provides a single public helper `generate_chart` that accepts a chart
specification and writes a PNG file to disk, returning the absolute path.
It is deliberately kept lightweight so it can be unit-tested in isolation and
plugged into `PPTGenerator` when building slides that require charts/graphs.

Usage example:

>>> chart_spec = {
...     "type": "bar",
...     "title": "Sales by Region",
...     "data": {
...         "labels": ["North", "South", "East", "West"],
...         "values": [120, 90, 75, 110]
...     }
... }
>>> png_path = generate_chart(chart_spec, theme_colors={"primary": (52, 152, 219)})
"""
from __future__ import annotations

from typing import Dict, List, Tuple
import os
import uuid

import matplotlib

# Use a non-interactive backend suitable for servers and CI environments.
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt  # noqa: E402  (import after backend set)

# Path (relative to project root) where generated charts will be stored.
_OUTPUT_DIR_NAME = os.path.join("generated", "charts")


class ChartGenerationError(RuntimeError):
    """Raised when a chart cannot be generated from the provided data."""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _ensure_output_dir() -> str:
    """Create the chart output directory if it doesn't exist and return its path."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
    output_dir = os.path.join(project_root, _OUTPUT_DIR_NAME)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _apply_theme(ax: plt.Axes, theme_colors: Dict[str, Tuple[int, int, int]]) -> None:
    """Apply basic theming (colors, spines) to a matplotlib Axes object."""
    primary_rgb = theme_colors.get("primary", (33, 33, 33))
    # Convert 0-255 RGB to 0-1 float for matplotlib
    primary = tuple(c / 255 for c in primary_rgb)

    ax.tick_params(colors=primary, labelcolor=primary)
    for spine in ax.spines.values():
        spine.set_color(primary)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_chart(chart_spec: Dict, theme_colors: Dict[str, Tuple[int, int, int]] | None = None) -> str:
    """Generate a chart PNG based on *chart_spec*.

    Parameters
    ----------
    chart_spec: dict
        Required keys:
        - ``type``: str, one of "bar", "line", "pie".
        - ``data``: dict containing ``labels`` (list[str]) and ``values`` (list[float]).
        Optional keys:
        - ``title``: str chart title.
    theme_colors: dict | None
        Optional mapping of color names ("primary", "secondary", "accent") to RGB tuples (0-255 ints).

    Returns
    -------
    str
        The absolute path to the saved PNG file.

    Raises
    ------
    ChartGenerationError
        If the chart_spec is invalid or matplotlib fails to produce an image.
    """
    required = {"type", "data"}
    if not required.issubset(chart_spec):
        raise ChartGenerationError(f"chart_spec must contain keys {required}. Got: {chart_spec.keys()}")

    chart_type: str = chart_spec["type"].lower().strip()
    data: Dict = chart_spec["data"]
    labels: List[str] = data.get("labels")
    values: List[float] = data.get("values")

    if not labels or not values or len(labels) != len(values):
        raise ChartGenerationError("'data' must include 'labels' and 'values' lists of equal length.")

    title: str | None = chart_spec.get("title")
    theme_colors = theme_colors or {}

    try:
        fig, ax = plt.subplots(figsize=(6, 4))

        if chart_type == "bar":
            ax.bar(labels, values, color=[theme_colors.get("accent", (52, 152, 219)) for _ in values])
        elif chart_type == "line":
            ax.plot(labels, values, marker="o", color=theme_colors.get("primary", (33, 33, 33)))
        elif chart_type == "pie":
            ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=140,
                   colors=[theme_colors.get("accent", (52, 152, 219)) for _ in values])
            ax.axis("equal")  # Ensure circle
        else:
            raise ChartGenerationError(f"Unsupported chart type: {chart_type}")

        if title:
            ax.set_title(title, pad=16)

        _apply_theme(ax, theme_colors)

        output_dir = _ensure_output_dir()
        fname = f"chart_{uuid.uuid4().hex}.png"
        out_path = os.path.join(output_dir, fname)
        fig.tight_layout()
        fig.savefig(out_path, dpi=150, transparent=True)
        plt.close(fig)
        return out_path

    except Exception as exc:
        plt.close("all")
        raise ChartGenerationError(str(exc)) from exc
