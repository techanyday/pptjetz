from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


EDITORIAL_LAYOUTS = [
    "cover_photo_block",
    "split_photo_cards",
    "stack_with_photo",
    "loop_diagram",
    "photo_overlay",
    "stat_pair_banner",
    "table",
    "numbered_columns",
    "color_block_list",
    "numbered_list_photo",
    "quote",
    "section_break",
    "comparison",
    "timeline",
    "hub_spoke",
    "closing_actions",
    "big_number", "image_grid", "process_chevrons", "quote_photo", "bar_chart", "matrix_2x2",
]


def _words(value: str) -> int:
    return len(str(value or "").split())


def _word_limit(limit: int):
    def validator(value: str) -> str:
        if _words(value) > limit:
            raise ValueError(f"must contain at most {limit} words")
        return str(value or "").strip()

    return validator


class EditorialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StyleGuide(EditorialModel):
    palette_hint: str = Field(default="warm paper, editorial blue, rust", max_length=120)
    image_style: str = Field(default="warm natural light, candid, tactile", max_length=160)
    mood: str = Field(default="confident, thoughtful, magazine-style", max_length=120)
    theme_id: str = Field(default="warm_editorial", max_length=40)


class ImageSpec(EditorialModel):
    queries: list[str] = Field(..., min_length=2, max_length=3)
    alt_text: str = Field(..., min_length=3, max_length=180)

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, value: list[str]) -> list[str]:
        for query in value:
            if not 2 <= _words(query) <= 6:
                raise ValueError("each image query must contain 2 to 6 words")
        return value


class SlideBase(EditorialModel):
    action_title: str = Field(..., min_length=1, max_length=100)
    variant: str | None = Field(default=None, max_length=40)
    section: str | None = Field(default=None, max_length=30)
    eyebrow: str | None = Field(default=None, max_length=30)
    so_what: str | None = Field(default=None, max_length=160)
    source: str | None = Field(default=None, max_length=240)
    notes: str = Field(..., min_length=1, max_length=900)
    image: ImageSpec | None = None

    _action_words = field_validator("action_title")(_word_limit(12))
    _eyebrow_words = field_validator("eyebrow")(_word_limit(3))
    _so_what_words = field_validator("so_what")(_word_limit(20))

    @field_validator("notes")
    @classmethod
    def notes_must_be_40_to_120_words(cls, value: str) -> str:
        count = _words(value)
        if not 40 <= count <= 120:
            raise ValueError("notes must contain 40 to 120 words")
        return str(value or "").strip()


class CoverPhotoBlockFields(EditorialModel):
    title: str = Field(..., max_length=80)
    subtitle: str = Field(..., max_length=180)
    footer_line: str = Field(..., max_length=180)

    _title_words = field_validator("title")(_word_limit(10))
    _subtitle_words = field_validator("subtitle")(_word_limit(22))


class CoverPhotoBlockSlide(SlideBase):
    layout: Literal["cover_photo_block"] = "cover_photo_block"
    eyebrow: str = Field(..., max_length=30)
    fields: CoverPhotoBlockFields


class TextCard(EditorialModel):
    eyebrow: str = Field(..., max_length=24)
    heading: str = Field(..., max_length=40)
    body: str = Field(..., max_length=240)

    _eyebrow_words = field_validator("eyebrow")(_word_limit(2))
    _heading_words = field_validator("heading")(_word_limit(4))
    _body_words = field_validator("body")(_word_limit(35))


class SplitPhotoCardsFields(EditorialModel):
    cards: list[TextCard] = Field(..., min_length=2, max_length=2)
    takeaway: str = Field(..., max_length=160)

    _takeaway_words = field_validator("takeaway")(_word_limit(20))


class SplitPhotoCardsSlide(SlideBase):
    layout: Literal["split_photo_cards"] = "split_photo_cards"
    fields: SplitPhotoCardsFields


class StackRow(EditorialModel):
    label: str = Field(..., max_length=30)
    detail: str = Field(..., max_length=50)

    _label_words = field_validator("label")(_word_limit(3))
    _detail_words = field_validator("detail")(_word_limit(5))


class StackWithPhotoFields(EditorialModel):
    rows: list[StackRow] = Field(..., min_length=4, max_length=6)
    arrow_caption: str = Field(..., max_length=40)

    _arrow_words = field_validator("arrow_caption")(_word_limit(4))


class StackWithPhotoSlide(SlideBase):
    layout: Literal["stack_with_photo"] = "stack_with_photo"
    fields: StackWithPhotoFields


class LoopDiagramFields(EditorialModel):
    steps: list[str] = Field(..., min_length=3, max_length=5)

    @field_validator("steps")
    @classmethod
    def step_lengths(cls, value: list[str]) -> list[str]:
        if any(_words(step) > 9 for step in value):
            raise ValueError("loop steps must contain at most 9 words")
        return value


class LoopDiagramSlide(SlideBase):
    layout: Literal["loop_diagram"] = "loop_diagram"
    fields: LoopDiagramFields


class PhotoOverlayFields(EditorialModel):
    title: str = Field(..., max_length=70)
    lines: list[str] = Field(..., min_length=1, max_length=2)

    _title_words = field_validator("title")(_word_limit(8))


class PhotoOverlaySlide(SlideBase):
    layout: Literal["photo_overlay"] = "photo_overlay"
    fields: PhotoOverlayFields


class Stat(EditorialModel):
    value: str = Field(..., max_length=8)
    caption: str = Field(..., max_length=140)

    _caption_words = field_validator("caption")(_word_limit(18))


class StatPairBannerFields(EditorialModel):
    stats: list[Stat] = Field(..., min_length=2, max_length=2)
    banner_heading: str = Field(..., max_length=40)
    banner_body: str = Field(..., max_length=180)

    _heading_words = field_validator("banner_heading")(_word_limit(5))
    _body_words = field_validator("banner_body")(_word_limit(28))


class StatPairBannerSlide(SlideBase):
    layout: Literal["stat_pair_banner"] = "stat_pair_banner"
    fields: StatPairBannerFields


class TableFields(EditorialModel):
    columns: list[str] = Field(..., min_length=3, max_length=5)
    rows: list[list[str]] = Field(..., min_length=3, max_length=6)

    @field_validator("columns")
    @classmethod
    def column_lengths(cls, value: list[str]) -> list[str]:
        if any(_words(item) > 8 for item in value):
            raise ValueError("table columns must contain at most 8 words")
        return value

    @field_validator("rows")
    @classmethod
    def row_lengths(cls, value: list[list[str]]) -> list[list[str]]:
        if any(len(row) == 0 or len(row) > 5 for row in value):
            raise ValueError("table rows must contain 1 to 5 cells")
        if any(_words(cell) > 8 for row in value for cell in row):
            raise ValueError("table cells must contain at most 8 words")
        return value


class TableSlide(SlideBase):
    layout: Literal["table"] = "table"
    fields: TableFields


class NumberedColumn(EditorialModel):
    heading: str = Field(..., max_length=40)
    body: str = Field(..., max_length=140)

    _heading_words = field_validator("heading")(_word_limit(3))
    _body_words = field_validator("body")(_word_limit(20))


class NumberedColumnsFields(EditorialModel):
    items: list[NumberedColumn] = Field(..., min_length=3, max_length=5)


class NumberedColumnsSlide(SlideBase):
    layout: Literal["numbered_columns"] = "numbered_columns"
    fields: NumberedColumnsFields


class ColorBlockPoint(EditorialModel):
    heading: str = Field(..., max_length=40)
    detail: str = Field(..., max_length=120)

    _heading_words = field_validator("heading")(_word_limit(4))
    _detail_words = field_validator("detail")(_word_limit(18))


class ColorBlockListFields(EditorialModel):
    block_eyebrow: str = Field(..., max_length=30)
    big_phrase: str = Field(..., max_length=30)
    block_body: str = Field(..., max_length=160)
    points: list[ColorBlockPoint] = Field(..., min_length=3, max_length=3)

    _eyebrow_words = field_validator("block_eyebrow")(_word_limit(3))
    _phrase_words = field_validator("big_phrase")(_word_limit(4))
    _body_words = field_validator("block_body")(_word_limit(24))


class ColorBlockListSlide(SlideBase):
    layout: Literal["color_block_list"] = "color_block_list"
    fields: ColorBlockListFields


class NumberedListItem(EditorialModel):
    heading: str = Field(..., max_length=60)
    detail: str = Field(..., max_length=100)

    _heading_words = field_validator("heading")(_word_limit(6))
    _detail_words = field_validator("detail")(_word_limit(12))


class NumberedListPhotoFields(EditorialModel):
    items: list[NumberedListItem] = Field(..., min_length=3, max_length=5)


class NumberedListPhotoSlide(SlideBase):
    layout: Literal["numbered_list_photo"] = "numbered_list_photo"
    fields: NumberedListPhotoFields


class QuoteFields(EditorialModel):
    quote: str = Field(..., max_length=300)
    attribution: str = Field(..., max_length=100)


class QuoteSlide(SlideBase):
    layout: Literal["quote"] = "quote"
    fields: QuoteFields


class SectionBreakFields(EditorialModel):
    phrase: str = Field(..., max_length=100)

    _phrase_words = field_validator("phrase")(_word_limit(12))


class SectionBreakSlide(SlideBase):
    layout: Literal["section_break"] = "section_break"
    fields: SectionBreakFields


class ComparisonColumn(EditorialModel):
    heading: str = Field(..., max_length=50)
    points: list[str] = Field(..., min_length=3, max_length=6)

    @field_validator("points")
    @classmethod
    def points_must_be_substantive(cls, value: list[str]) -> list[str]:
        if any(_words(point) < 4 for point in value):
            raise ValueError("comparison points must contain at least 4 words")
        return value


class ComparisonFields(EditorialModel):
    columns: list[ComparisonColumn] = Field(..., min_length=2, max_length=3)


class ComparisonSlide(SlideBase):
    layout: Literal["comparison"] = "comparison"
    fields: ComparisonFields


class TimelineItem(EditorialModel):
    label: str = Field(..., max_length=40)
    detail: str = Field(..., max_length=120)


class TimelineFields(EditorialModel):
    items: list[TimelineItem] = Field(..., min_length=2, max_length=6)


class TimelineSlide(SlideBase):
    layout: Literal["timeline"] = "timeline"
    fields: TimelineFields


class HubSpokeFields(EditorialModel):
    center: str = Field(..., max_length=60)
    spokes: list[NumberedColumn] = Field(..., min_length=3, max_length=6)


class HubSpokeSlide(SlideBase):
    layout: Literal["hub_spoke"] = "hub_spoke"
    fields: HubSpokeFields


class ClosingActionsFields(EditorialModel):
    actions: list[NumberedListItem] = Field(..., min_length=3, max_length=5)


class ClosingActionsSlide(SlideBase):
    layout: Literal["closing_actions"] = "closing_actions"
    fields: ClosingActionsFields


class BigNumberFields(EditorialModel):
    value: str = Field(..., max_length=20)
    sentence: str = Field(..., max_length=180)


class BigNumberSlide(SlideBase):
    layout: Literal["big_number"] = "big_number"
    fields: BigNumberFields


class ImageGridFields(EditorialModel):
    captions: list[str] = Field(..., min_length=3, max_length=4)


class ImageGridSlide(SlideBase):
    layout: Literal["image_grid"] = "image_grid"
    fields: ImageGridFields


class ProcessChevronsFields(EditorialModel):
    steps: list[str] = Field(..., min_length=3, max_length=5)
    step_details: list[str] | None = Field(default=None, max_length=5)

    @field_validator("step_details")
    @classmethod
    def detail_lengths(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(_words(item) > 18 for item in value):
            raise ValueError("chevron descriptions must contain at most 18 words")
        return value


class ProcessChevronsSlide(SlideBase):
    layout: Literal["process_chevrons"] = "process_chevrons"
    fields: ProcessChevronsFields


class QuotePhotoFields(EditorialModel):
    quote: str = Field(..., max_length=300)
    attribution: str = Field(..., max_length=100)


class QuotePhotoSlide(SlideBase):
    layout: Literal["quote_photo"] = "quote_photo"
    fields: QuotePhotoFields


class BarChartFields(EditorialModel):
    labels: list[str] = Field(..., min_length=2, max_length=6)
    values: list[float] = Field(..., min_length=2, max_length=6)
    source: str = Field(..., min_length=1, max_length=240)


class BarChartSlide(SlideBase):
    layout: Literal["bar_chart"] = "bar_chart"
    fields: BarChartFields


class MatrixFields(EditorialModel):
    quadrants: list[str] = Field(..., min_length=4, max_length=4)
    quadrant_details: list[str] | None = Field(default=None, max_length=4)

    @field_validator("quadrant_details")
    @classmethod
    def quadrant_detail_lengths(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(_words(item) > 18 for item in value):
            raise ValueError("matrix quadrant details must contain at most 18 words")
        return value


class Matrix2x2Slide(SlideBase):
    layout: Literal["matrix_2x2"] = "matrix_2x2"
    fields: MatrixFields


EditorialSlide = Annotated[
    Union[
        CoverPhotoBlockSlide,
        SplitPhotoCardsSlide,
        StackWithPhotoSlide,
        LoopDiagramSlide,
        PhotoOverlaySlide,
        StatPairBannerSlide,
        TableSlide,
        NumberedColumnsSlide,
        ColorBlockListSlide,
        NumberedListPhotoSlide,
        QuoteSlide,
        SectionBreakSlide,
        ComparisonSlide,
        TimelineSlide,
        HubSpokeSlide,
        ClosingActionsSlide,
        BigNumberSlide,
        ImageGridSlide,
        ProcessChevronsSlide,
        QuotePhotoSlide,
        BarChartSlide,
        Matrix2x2Slide,
    ],
    Field(discriminator="layout"),
]

Slide = EditorialSlide
EditorialSlideAdapter = TypeAdapter(list[EditorialSlide])


class DeckSpec(EditorialModel):
    title: str = Field(..., min_length=1, max_length=180)
    subtitle: str | None = Field(default=None, max_length=240)
    audience: str = Field(..., min_length=1, max_length=120)
    thesis: str = Field(..., min_length=1, max_length=280)
    arc: str | None = Field(default=None, max_length=40)
    style_guide: StyleGuide = Field(default_factory=StyleGuide)
    slides: list[EditorialSlide] = Field(..., min_length=1, max_length=30)


__all__ = [
    "EDITORIAL_LAYOUTS",
    "StyleGuide",
    "ImageSpec",
    "DeckSpec",
    "Slide",
    "EditorialSlide",
    "EditorialSlideAdapter",
]
