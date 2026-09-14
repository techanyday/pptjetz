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
    prompt: str | None = Field(default=None, max_length=400)
    query: str | None = Field(default=None, max_length=180)
    layout: Literal["full_bleed", "split", "image_card", "quote_image"] = "split"
    image_url: str | None = Field(default=None, max_length=2000)
    thumbnail_url: str | None = Field(default=None, max_length=2000)
    source_url: str | None = Field(default=None, max_length=2000)
    source_name: str | None = Field(default=None, max_length=100)
    creator: str | None = Field(default=None, max_length=255)
    license: str | None = Field(default=None, max_length=100)
    license_url: str | None = Field(default=None, max_length=2000)
    attribution: str | None = Field(default=None, max_length=500)
    alt_text: str | None = Field(default=None, max_length=255)


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
    # slide wrappers
    "HeaderSlide",
    "TextSlide",
    "ComparisonSlide",
    "QuoteSlide",
    "TableSlide",
    "ChartSlide",
    "TimelineSlide",
    "ImageSlide",
    # union helpers
    "SlideObject",
    "SLIDE_KINDS",
    "SlideAdapter",
]
