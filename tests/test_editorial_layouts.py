import io

from pptx import Presentation

from app.services.editorial.layouts import LAYOUT_RENDERERS, render_editorial_slide
from app.services.editorial.schema import DeckSpec
from app.services.editorial.theme import EditorialTheme, contrast_ratio


NOTES = (
    "These speaker notes explain the central claim, provide context for the audience, "
    "summarize the evidence, clarify why the insight matters, and connect this slide "
    "to the next part of the story. The presenter can use them to add nuance without "
    "reading the slide verbatim, emphasize the decision consequence, and guide a "
    "short discussion before moving forward. Close by reinforcing the audience decision "
    "this claim should influence."
)


def _fields(layout):
    if layout == "cover_photo_block":
        return {"title": "A clear editorial claim", "subtitle": "A concise story for decision makers", "footer_line": "Editorial brief"}
    if layout == "split_photo_cards":
        return {"cards": [{"eyebrow": "Signal", "heading": "Owned demand", "body": "Direct relationships create durable value."}, {"eyebrow": "Shift", "heading": "New economics", "body": "The operating model changes with trust."}], "takeaway": "Build the bridge from attention to ownership."}
    if layout == "stack_with_photo":
        return {"rows": [{"label": "Signal", "detail": "Audience demand"} for _ in range(4)], "arrow_caption": "Then act"}
    if layout == "loop_diagram":
        return {"steps": ["Notice the signal", "Test the idea", "Build the offer"]}
    if layout == "photo_overlay":
        return {"title": "The operating model is changing", "lines": ["Trust compounds over time."]}
    if layout == "stat_pair_banner":
        return {"stats": [{"value": "50%", "caption": "A supported source claim"}, {"value": "2x", "caption": "Another supported claim"}], "banner_heading": "The pivot", "banner_body": "Move from rented reach toward owned relationships."}
    if layout == "table":
        return {"columns": ["Signal", "Meaning", "Action"], "rows": [["Demand", "Evidence", "Respond"], ["Trust", "Retention", "Build"], ["Value", "Outcome", "Scale"]]}
    if layout == "numbered_columns":
        return {"items": [{"heading": "Focus", "body": "Choose one narrow promise."}, {"heading": "Prove", "body": "Gather direct customer evidence."}, {"heading": "Expand", "body": "Build the next layer carefully."}]}
    if layout == "color_block_list":
        return {"block_eyebrow": "The shift", "big_phrase": "Trust wins", "block_body": "The advantage comes from useful relationships.", "points": [{"heading": "Listen", "detail": "Observe the audience closely."}, {"heading": "Build", "detail": "Create a focused offer."}, {"heading": "Learn", "detail": "Use outcomes to improve."}]}
    if layout == "numbered_list_photo":
        return {"items": [{"heading": "Find the audience", "detail": "Start with one clear group."}, {"heading": "Make the promise", "detail": "Solve one repeated problem."}, {"heading": "Ship the proof", "detail": "Learn from real use."}]}
    if layout == "quote":
        return {"quote": "A clear claim changes how people decide.", "attribution": "Editorial source"}
    if layout == "section_break":
        return {"phrase": "The next chapter starts with ownership"}
    if layout == "comparison":
        return {"columns": [{"heading": "Rented", "points": ["Reach can change without warning", "Limited control over distribution", "Audience access depends on platform rules"]}, {"heading": "Owned", "points": ["Direct trust compounds over time", "Durable value stays with the team", "Customer insight improves each cycle"]}]}
    if layout == "timeline":
        return {"items": [{"label": "Now", "detail": "See the signal"}, {"label": "Next", "detail": "Test the response"}, {"label": "Then", "detail": "Scale what works"}]}
    if layout == "hub_spoke":
        return {"center": "Core idea", "spokes": [{"heading": "Audience", "body": "Who matters"}, {"heading": "Offer", "body": "What changes"}, {"heading": "Proof", "body": "What supports it"}]}
    if layout == "closing_actions":
        return {"actions": [{"heading": "Choose", "detail": "Commit to one next move."}, {"heading": "Test", "detail": "Learn from direct evidence."}, {"heading": "Build", "detail": "Turn the proof into a system."}]}
    if layout == "big_number":
        return {"value": "50%", "sentence": "A sourced measure changes the decision."}
    if layout == "image_grid":
        return {"captions": ["Observe the signal", "Test the idea", "Build the offer"]}
    if layout == "process_chevrons":
        return {"steps": ["Notice the signal", "Test the idea", "Build the offer"]}
    if layout == "quote_photo":
        return {"quote": "A clear claim changes how people decide.", "attribution": "Editorial source"}
    if layout == "bar_chart":
        return {"labels": ["Now", "Next", "Then"], "values": [1, 2, 3], "source": "Editorial source"}
    if layout == "matrix_2x2":
        return {"quadrants": ["High trust", "Low trust", "High reach", "Low reach"]}
    raise AssertionError(layout)


def _deck(layout):
    return DeckSpec(
        title="Editorial test",
        audience="Leaders",
        thesis="A clear claim changes the decision.",
        slides=[{
            "layout": layout,
            "action_title": "A clear editorial claim",
            "eyebrow": "Strategy",
            "so_what": "Use this insight to decide what to do next.",
            "notes": NOTES,
            "fields": _fields(layout),
        }],
    ).slides[0]


def test_every_editorial_layout_renders_native_shapes():
    theme = EditorialTheme()
    assert contrast_ratio(theme.ink, theme.background) >= 4.5
    assert set(LAYOUT_RENDERERS) == {
        "cover_photo_block", "split_photo_cards", "stack_with_photo", "loop_diagram",
        "photo_overlay", "stat_pair_banner", "table", "numbered_columns",
        "color_block_list", "numbered_list_photo", "quote", "section_break",
        "comparison", "timeline", "hub_spoke", "closing_actions",
        "big_number", "image_grid", "process_chevrons", "quote_photo", "bar_chart", "matrix_2x2",
    }
    for layout in LAYOUT_RENDERERS:
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        render_editorial_slide(slide, _deck(layout), theme=theme)
        assert len(slide.shapes) >= 1, layout
        output = io.BytesIO()
        prs.save(output)
        assert output.getvalue().startswith(b"PK"), layout
