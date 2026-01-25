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
    ArchitectureSlide,
    BeforeAfterSlide,
    ChartSlide,
    ComparisonSlide,
    HeaderSlide,
    ImageSlide,
    KPISlide,
    PricingSlide,
    QuoteSlide,
    RiskMatrixSlide,
    RoadmapSlide,
    SlideObject,
    SWOTSlide,
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

        if isinstance(slide, RoadmapSlide):
            phases_data = slide.content.phases or []
            phases = []
            for phase in phases_data:
                phase_text = f"{phase.title}"
                if phase.date_range:
                    phase_text += f" ({phase.date_range})"
                if phase.description:
                    phase_text += f": {phase.description}"
                phases.append(phase_text)
            body = "\n".join(phases)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, SWOTSlide):
            swot_data = [
                ("Strengths", slide.content.strengths.points or []),
                ("Weaknesses", slide.content.weaknesses.points or []),
                ("Opportunities", slide.content.opportunities.points or []),
                ("Threats", slide.content.threats.points or [])
            ]
            swot_sections = []
            for label, points in swot_data:
                if points:
                    swot_sections.append(f"{label}:\n" + "\n".join(f"• {p}" for p in points))
            body = "\n\n".join(swot_sections)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, KPISlide):
            metrics_data = slide.content.metrics or []
            metrics = []
            for metric in metrics_data:
                metric_text = f"{metric.label}: {metric.value}"
                if metric.caption:
                    metric_text += f" - {metric.caption}"
                metrics.append(metric_text)
            body = "\n".join(metrics)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, PricingSlide):
            plans_data = slide.content.plans or []
            pricing_sections = []
            for plan in plans_data:
                plan_text = f"{plan.name}"
                if plan.price:
                    plan_text += f" - {plan.price}"
                if plan.features:
                    plan_text += "\n" + "\n".join(f"• {f}" for f in plan.features)
                pricing_sections.append(plan_text)
            body = "\n\n".join(pricing_sections)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, RiskMatrixSlide):
            risk_data = [
                ("Low Likelihood / Low Impact", slide.content.low_likelihood_low_impact.points or []),
                ("Low Likelihood / High Impact", slide.content.low_likelihood_high_impact.points or []),
                ("High Likelihood / Low Impact", slide.content.high_likelihood_low_impact.points or []),
                ("High Likelihood / High Impact", slide.content.high_likelihood_high_impact.points or [])
            ]
            risk_sections = []
            for label, points in risk_data:
                if points:
                    risk_sections.append(f"{label}:\n" + "\n".join(f"• {p}" for p in points))
            body = "\n\n".join(risk_sections)
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, BeforeAfterSlide):
            before_data = slide.content.before
            after_data = slide.content.after
            
            before_text = f"{before_data.heading}:\n" + "\n".join(f"• {p}" for p in (before_data.points or []))
            after_text = f"{after_data.heading}:\n" + "\n".join(f"• {p}" for p in (after_data.points or []))
            
            body = f"{before_text}\n\n{after_text}"
            simple.append({"title": slide.content.title, "content": body})
            continue

        if isinstance(slide, ArchitectureSlide):
            boxes_data = slide.content.boxes or []
            connections_data = slide.content.connections or []
            
            # Box information
            boxes = []
            for box in boxes_data:
                box_text = box.label
                if box.description:
                    box_text += f": {box.description}"
                boxes.append(box_text)
            
            # Connection information
            connections = []
            for conn in connections_data:
                conn_text = f"{conn.from_box} → {conn.to_box}"
                if conn.label:
                    conn_text += f" ({conn.label})"
                connections.append(conn_text)
            
            body = "Components:\n" + "\n".join(f"• {b}" for b in boxes)
            if connections:
                body += "\n\nConnections:\n" + "\n".join(f"• {c}" for c in connections)
            
            simple.append({"title": slide.content.title, "content": body})
            continue

        # Fallback: generic stringification
        simple.append({"title": getattr(slide, 'kind', 'Slide'), "content": str(slide.content)})

    return simple

__all__ = ["slide_objects_to_simple"]
