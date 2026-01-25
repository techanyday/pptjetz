"""Slide object JSON schema definitions.

These Pydantic models define the **contract** between the AI content generator
(OpenAI) and the backend.  Each slide produced by the language model must be a
JSON *object* that conforms to one of the content schemas below and is wrapped
in a top-level ``SlideObject`` with a ``kind`` discriminator.

This allows:
• Strict validation / clearer error messages if the LLM returns malformed JSON.
• Typed access when rendering Google Slides via ``app/utils/slides_api.py``.
• Future expansion (add new kinds without breaking existing ones).
"""
from __future__ import annotations

from typing import List, Literal, Annotated, Union

from pydantic import BaseModel, Field, TypeAdapter

# ---------------------------------------------------------------------------
# Content models (per slide type)
# ---------------------------------------------------------------------------


class HeaderContent(BaseModel):
    title: str = Field(..., max_length=255)
    subtitle: str | None = Field(default=None, max_length=255)


class TextBulletsContent(BaseModel):
    title: str = Field(..., max_length=255)
    bullets: List[str] = Field(..., min_length=1)
    explainers: List[str] | None = None  # optional 1-1 mapping to bullets


class ComparisonColumn(BaseModel):
    heading: str = Field(..., max_length=100)
    points: List[str] = Field(..., min_length=1)


class ComparisonContent(BaseModel):
    title: str = Field(..., max_length=255)
    columns: List[ComparisonColumn] = Field(..., min_length=2)


class QuoteContent(BaseModel):
    quote: str = Field(..., max_length=500)
    attribution: str | None = Field(default=None, max_length=100)


class TableContent(BaseModel):
    title: str = Field(..., max_length=255)
    headers: List[str] = Field(..., min_length=1)
    rows: List[List[str]] = Field(..., min_length=1)

    def model_validate(self, *args, **kwargs):  # noqa: D401  (override for row length check)
        model = super().model_validate(*args, **kwargs)
        header_len = len(model.headers)
        for row in model.rows:
            if len(row) != header_len:
                raise ValueError("Each row length must match number of headers")
        return model


class ChartContent(BaseModel):
    title: str = Field(..., max_length=255)
    chart_type: Literal["bar", "line", "pie"]
    labels: List[str] = Field(..., min_length=1)
    values: List[float] = Field(..., min_length=1)


class TimelineEvent(BaseModel):
    label: str = Field(..., max_length=100)
    description: str | None = None


class TimelineContent(BaseModel):
    title: str = Field(..., max_length=255)
    events: List[TimelineEvent] = Field(..., min_length=2)


class ImageContent(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    prompt: str = Field(..., max_length=400)  # text sent to DALL·E
    alt_text: str | None = Field(default=None, max_length=255)


class RoadmapPhase(BaseModel):
    title: str = Field(..., max_length=100)
    date_range: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=200)


class RoadmapContent(BaseModel):
    title: str = Field(..., max_length=255)
    phases: List[RoadmapPhase] = Field(..., min_length=2, max_length=6)


class SWOTQuadrant(BaseModel):
    title: str = Field(..., max_length=50)
    points: List[str] = Field(..., min_length=1, max_length=5)


class SWOTContent(BaseModel):
    title: str = Field(..., max_length=255)
    strengths: SWOTQuadrant
    weaknesses: SWOTQuadrant
    opportunities: SWOTQuadrant
    threats: SWOTQuadrant


class KPICard(BaseModel):
    label: str = Field(..., max_length=100)
    value: str = Field(..., max_length=50)
    caption: str | None = Field(default=None, max_length=150)


class KPIContent(BaseModel):
    title: str = Field(..., max_length=255)
    metrics: List[KPICard] = Field(..., min_length=2, max_length=4)


class PricingPlan(BaseModel):
    name: str = Field(..., max_length=100)
    price: str | None = Field(default=None, max_length=50)
    features: List[str] = Field(..., min_length=1)


class PricingContent(BaseModel):
    title: str = Field(..., max_length=255)
    plans: List[PricingPlan] = Field(..., min_length=2, max_length=4)


class RiskMatrixQuadrant(BaseModel):
    title: str = Field(..., max_length=50)
    points: List[str] = Field(..., min_length=1, max_length=3)


class RiskMatrixContent(BaseModel):
    title: str = Field(..., max_length=255)
    low_likelihood_low_impact: RiskMatrixQuadrant
    low_likelihood_high_impact: RiskMatrixQuadrant
    high_likelihood_low_impact: RiskMatrixQuadrant
    high_likelihood_high_impact: RiskMatrixQuadrant


class BeforeAfterColumn(BaseModel):
    heading: str = Field(..., max_length=100)
    points: List[str] = Field(..., min_length=1)


class BeforeAfterContent(BaseModel):
    title: str = Field(..., max_length=255)
    before: BeforeAfterColumn
    after: BeforeAfterColumn


class ArchitectureBox(BaseModel):
    label: str = Field(..., max_length=100)
    description: str | None = Field(default=None, max_length=150)


class ArchitectureConnection(BaseModel):
    from_box: str = Field(..., max_length=100)
    to_box: str = Field(..., max_length=100)
    label: str | None = Field(default=None, max_length=50)


class ArchitectureContent(BaseModel):
    title: str = Field(..., max_length=255)
    boxes: List[ArchitectureBox] = Field(..., min_length=2, max_length=8)
    connections: List[ArchitectureConnection] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Slide variants – each carries a *kind* discriminator plus *content*
# ---------------------------------------------------------------------------

class HeaderSlide(BaseModel):
    kind: Literal["header"] = "header"
    content: HeaderContent


class TextSlide(BaseModel):
    kind: Literal["text"] = "text"
    content: TextBulletsContent


class ComparisonSlide(BaseModel):
    kind: Literal["comparison"] = "comparison"
    content: ComparisonContent


class QuoteSlide(BaseModel):
    kind: Literal["quote"] = "quote"
    content: QuoteContent


class TableSlide(BaseModel):
    kind: Literal["table"] = "table"
    content: TableContent


class ChartSlide(BaseModel):
    kind: Literal["chart"] = "chart"
    content: ChartContent


class TimelineSlide(BaseModel):
    kind: Literal["timeline"] = "timeline"
    content: TimelineContent


class ImageSlide(BaseModel):
    kind: Literal["image"] = "image"
    content: ImageContent


class RoadmapSlide(BaseModel):
    kind: Literal["roadmap"] = "roadmap"
    content: RoadmapContent


class SWOTSlide(BaseModel):
    kind: Literal["swot"] = "swot"
    content: SWOTContent


class KPISlide(BaseModel):
    kind: Literal["kpi"] = "kpi"
    content: KPIContent


class PricingSlide(BaseModel):
    kind: Literal["pricing"] = "pricing"
    content: PricingContent


class RiskMatrixSlide(BaseModel):
    kind: Literal["risk_matrix"] = "risk_matrix"
    content: RiskMatrixContent


class BeforeAfterSlide(BaseModel):
    kind: Literal["before_after"] = "before_after"
    content: BeforeAfterContent


class ArchitectureSlide(BaseModel):
    kind: Literal["architecture"] = "architecture"
    content: ArchitectureContent


# ---------------------------------------------------------------------------
# Discriminated union (SlideObject)
# ---------------------------------------------------------------------------

SlideObject = Annotated[
    Union[
        HeaderSlide,
        TextSlide,
        ComparisonSlide,
        QuoteSlide,
        TableSlide,
        ChartSlide,
        TimelineSlide,
        ImageSlide,
        RoadmapSlide,
        SWOTSlide,
        KPISlide,
        PricingSlide,
        RiskMatrixSlide,
        BeforeAfterSlide,
        ArchitectureSlide,
    ],
    Field(discriminator="kind"),
]

SLIDE_KINDS: List[str] = [
    "header",
    "text",
    "comparison",
    "quote",
    "table",
    "chart",
    "timeline",
    "image",
    "roadmap",
    "swot",
    "kpi",
    "pricing",
    "risk_matrix",
    "before_after",
    "architecture",
]

# Create a TypeAdapter once for runtime validation convenience
SlideAdapter: TypeAdapter[List[SlideObject]] = TypeAdapter(list[SlideObject])

__all__ = [
    # content
    "HeaderContent",
    "TextBulletsContent",
    "ComparisonContent",
    "QuoteContent",
    "TableContent",
    "ChartContent",
    "TimelineContent",
    "ImageContent",
    "RoadmapPhase",
    "RoadmapContent",
    "SWOTQuadrant",
    "SWOTContent",
    "KPICard",
    "KPIContent",
    "PricingPlan",
    "PricingContent",
    "RiskMatrixQuadrant",
    "RiskMatrixContent",
    "BeforeAfterColumn",
    "BeforeAfterContent",
    "ArchitectureBox",
    "ArchitectureConnection",
    "ArchitectureContent",
    # slide wrappers
    "HeaderSlide",
    "TextSlide",
    "ComparisonSlide",
    "QuoteSlide",
    "TableSlide",
    "ChartSlide",
    "TimelineSlide",
    "ImageSlide",
    "RoadmapSlide",
    "SWOTSlide",
    "KPISlide",
    "PricingSlide",
    "RiskMatrixSlide",
    "BeforeAfterSlide",
    "ArchitectureSlide",
    # union helpers
    "SlideObject",
    "SLIDE_KINDS",
    "SlideAdapter",
]
