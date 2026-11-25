import pytest
from pydantic import ValidationError

from app.services.editorial.schema import (
    DeckSpec,
    EditorialSlideAdapter,
    EDITORIAL_LAYOUTS,
)


NOTES = (
    "These speaker notes explain the central claim, provide context for the audience, "
    "summarize the evidence, clarify why the insight matters, and connect this slide "
    "to the next part of the story. The presenter can use them to add nuance without "
    "reading the slide verbatim, emphasize the practical implication, and guide a "
    "short discussion before moving forward. Close by reinforcing the audience decision "
    "this claim should influence."
)


def _base(layout, fields):
    return {
        "layout": layout,
        "action_title": "A clear editorial claim",
        "eyebrow": "Strategy",
        "so_what": "The audience should act on this insight next.",
        "notes": NOTES,
        "fields": fields,
    }


def test_deck_spec_validates_discriminated_layouts():
    slides = [
        _base("cover_photo_block", {"title": "A clear claim", "subtitle": "A concise editorial subtitle", "footer_line": "Editorial brief"}),
        _base("table", {"columns": ["One", "Two", "Three"], "rows": [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]]}),
        _base("loop_diagram", {"steps": ["Notice the signal", "Test the idea", "Build the offer"]}),
    ]
    deck = DeckSpec(title="Editorial deck", audience="Executives", thesis="A clear thesis", slides=slides)
    assert [slide.layout for slide in deck.slides] == ["cover_photo_block", "table", "loop_diagram"]


def test_notes_require_sixty_to_ninety_words():
    with pytest.raises(ValidationError, match="40 to 120 words"):
        DeckSpec(
            title="Editorial deck",
            audience="Executives",
            thesis="A clear thesis",
            slides=[_base("quote", {"quote": "A claim", "attribution": "Source"}) | {"notes": "Too short"}],
        )


def test_action_title_and_card_lengths_are_bounded():
    with pytest.raises(ValidationError):
        DeckSpec(
            title="Editorial deck",
            audience="Executives",
            thesis="A clear thesis",
            slides=[
                _base(
                    "split_photo_cards",
                    {
                        "cards": [
                            {"eyebrow": "One", "heading": "Too many words " * 5, "body": "Body"},
                            {"eyebrow": "Two", "heading": "Fine", "body": "Body"},
                        ],
                        "takeaway": "Takeaway",
                    },
                )
            ],
        )


def test_all_layout_names_are_declared():
    assert len(EDITORIAL_LAYOUTS) == 22
    assert len(set(EDITORIAL_LAYOUTS)) == len(EDITORIAL_LAYOUTS)
