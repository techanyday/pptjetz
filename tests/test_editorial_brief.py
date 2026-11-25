import json

from app.services.editorial.brief import _clean_style_guide, _normalize_layout, _normalize_slide_plan, _parse_object, build_editorial_spec, lint_slide


NOTES = (
    "These speaker notes explain the central claim, provide context for the audience, "
    "summarize the evidence, clarify why the insight matters, and connect this slide "
    "to the next part of the story. The presenter can use them to add nuance without "
    "reading the slide verbatim, emphasize the decision consequence, and guide a "
    "short discussion before moving forward. Close by reinforcing the audience decision "
    "this claim should influence."
)


def fake_request(*, instructions, input_text, max_output_tokens, deadline):
    if '"topic": "Editorial topic"' in input_text and '"slide_count": 2' in input_text:
        return {
            "title": "Editorial Topic",
            "subtitle": "A concise editorial story",
            "audience": "Leaders",
            "thesis": "A clear claim changes how the audience decides.",
            "style_guide": {"palette_hint": "warm paper", "image_style": "natural light", "mood": "confident"},
            "slides": [
                {"layout": "quote", "action_title": "The claim changes the decision"},
                {"layout": "table", "action_title": "The evidence has a pattern"},
            ],
        }
    if '"slide":' in input_text and "error" in input_text:
        return {}
    if '"slide_plan": {"layout": "quote"' in input_text:
        return {
            "layout": "quote",
            "action_title": "The claim changes the decision",
            "notes": NOTES,
            "fields": {"quote": "A clear claim changes how people decide.", "attribution": "Editorial source"},
        }
    return {
        "layout": "table",
        "action_title": "The evidence has a pattern",
        "notes": NOTES,
        "fields": {"columns": ["Signal", "Meaning", "Action"], "rows": [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]]},
    }


def test_build_editorial_spec_uses_parallel_slide_generation():
    deck = build_editorial_spec(topic="Editorial topic", slide_count=2, request=fake_request)
    assert len(deck.slides) >= 10
    assert deck.slides[0].layout == "cover_photo_block"
    assert deck.slides[-1].layout == "closing_actions"
    assert lint_slide(deck.slides[0]) == []


def test_parse_object_handles_responses_message_text():
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"title":"Editorial","audience":"Leaders","thesis":"A claim","slides":[]}',
                    }
                ],
            }
        ]
    }
    assert _parse_object(payload)["title"] == "Editorial"


def test_unknown_layouts_are_closed_over_to_editorial_layouts():
    assert _normalize_layout("photo_layout") == "photo_overlay"
    assert _normalize_layout("model_invented_layout") == "cover_photo_block"


def test_style_guide_filters_unknown_model_fields():
    style = _clean_style_guide({"font": "Arial", "palette_hint": "warm paper", "color_palette": ["#fff"]})
    assert style == {"palette_hint": "warm paper"}


def test_slide_plan_is_varied_when_model_repeats_one_layout():
    plan = _normalize_slide_plan([{"layout": "title_slide", "action_title": f"Claim {i}"} for i in range(10)])
    layouts = [item["layout"] for item in plan]
    assert len(set(layouts)) == 10
    assert all(a != b for a, b in zip(layouts, layouts[1:]))
