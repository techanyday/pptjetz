from __future__ import annotations

from app.services.slide_finalizer import finalize_slides


def test_finalize_slides_enforces_known_types_and_render_exact_and_fallback_metadata():
    slides = [
        {"type": "title", "title": "Deck", "subtitle": ""},
        {"type": "text", "title": "Plain", "content": ["A", "B", "C", "D"]},
        {"type": "timeline", "title": "Bad Timeline", "content": {"bullets": ["a", "b"]}},
        {"type": "unknown_type", "title": "Weird", "content": {"bullets": ["x", "y", "z", "w"]}},
        {"type": "double_column", "title": "DC1", "content": {"left_points": ["a", "b"], "right_points": ["c", "d"]}},
        {"type": "double_column", "title": "DC2", "content": {"left_points": ["a", "b"], "right_points": ["c", "d"]}},
        {"type": "double_column", "title": "DC3", "content": {"left_points": ["a", "b"], "right_points": ["c", "d"]}},
    ]

    out = finalize_slides(slides)
    assert isinstance(out, list)
    assert len(out) == len(slides)

    allowed = {
        "title",
        "agenda",
        "one_idea",
        "cards",
        "double_column",
        "comparison",
        "table",
        "infographic",
        "key_takeaways",
        "timeline",
        "roadmap",
        "swot",
        "kpi",
        "risk_matrix",
        "before_after",
        "architecture",
        "pricing",
        "impact_effort_matrix",
        "thank_you",
        "cta",
        "bullets",
    }

    for s in out:
        assert s.get("type") in allowed
        assert s.get("_render_exact") is True

    # Bad timeline should have fallback metadata and not remain timeline
    bad = next(x for x in out if x.get("title") == "Bad Timeline")
    assert bad.get("type") != "timeline"
    assert bad.get("_fallback_reason")
    assert bad.get("_fallback_to") == bad.get("type")

    # Unknown type should fall back (and record from)
    weird = next(x for x in out if x.get("title") == "Weird")
    assert weird.get("_fallback_from")
    assert weird.get("_fallback_to") == weird.get("type")

    # Cap double column to max 2
    dc_count = sum(1 for s in out if s.get("type") == "double_column")
    assert dc_count <= 2
