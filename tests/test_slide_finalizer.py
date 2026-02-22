from __future__ import annotations

from app.services.slide_finalizer import finalize_slides
from app.services.ai_content import choose_slide_count, build_slide_type_plan


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


def test_finalize_slides_merges_short_bullet_fragments():
    slides = [
        {
            "type": "text",
            "title": "Applications of AI in Business",
            "content": {
                "bullets": [
                    "Enhancing Customer Experience with",
                    "AI",
                    "Automation of Routine Tasks",
                ]
            },
        },
    ]

    out = finalize_slides(slides)
    assert len(out) == 1
    s0 = out[0]
    assert s0.get("type") == "infographic"
    bullets = (s0.get("content") or {}).get("bullets")
    assert isinstance(bullets, list)
    assert len(bullets) >= 2
    assert bullets[:2] == [
        "Enhancing Customer Experience with AI",
        "Automation of Routine Tasks",
    ]


def test_choose_slide_count_clamps_user_request_to_min_max():
    assert choose_slide_count("Topic", user_requested_count=1, content_signal="") == 10
    assert choose_slide_count("Topic", user_requested_count=10, content_signal="") == 10
    assert choose_slide_count("Topic", user_requested_count=15, content_signal="") == 15
    assert choose_slide_count("Topic", user_requested_count=99, content_signal="") == 15


def test_choose_slide_count_infers_from_topic_length():
    short = "A brief update on Q1 results"
    medium = " ".join(["word"] * 140)
    long = " ".join(["word"] * 400)
    assert 10 <= choose_slide_count(short, user_requested_count=None, content_signal=short) <= 12
    assert 12 <= choose_slide_count(medium, user_requested_count=None, content_signal=medium) <= 14
    assert choose_slide_count(long, user_requested_count=None, content_signal=long) == 15


def test_build_slide_type_plan_enforces_length_and_anchors_and_rotation():
    topic = "Executive Briefing"
    input_text = "Discuss strategy, timeline, KPIs, and risks."
    plan, meta = build_slide_type_plan(topic=topic, input_text=input_text, slide_count=12, theme_name="SLEEK")
    assert len(plan) == 12
    assert plan[0] == "title"
    assert plan[1] == "agenda"
    assert plan[-1] == "thank_you"
    # Coverage: should have at least 8 distinct types for 12 slides
    assert meta.get("distinct_types") >= 8
    # Avoid long runs (no 3-in-a-row)
    for i in range(2, len(plan)):
        assert not (plan[i] == plan[i - 1] == plan[i - 2])


def test_build_slide_type_plan_matrix_gating_includes_matrix_only_with_evidence():
    topic = "Prioritization"
    no_ev = "Summary of key findings and recommendations"
    plan1, meta1 = build_slide_type_plan(topic=topic, input_text=no_ev, slide_count=12, theme_name="SLEEK")
    assert meta1.get("allow_matrix") is False
    assert "impact_effort_matrix" not in plan1

    yes_ev = "Build a 2x2 impact vs effort prioritization matrix for initiatives"
    plan2, meta2 = build_slide_type_plan(topic=topic, input_text=yes_ev, slide_count=12, theme_name="SLEEK")
    assert meta2.get("allow_matrix") is True
    assert "impact_effort_matrix" in plan2


def test_finalize_slides_removes_to_be_refined_placeholders_everywhere():
    slides = [
        {"type": "bullets", "title": "Test", "content": {"bullets": ["(to be refined)", "Real point"]}},
        {"type": "cards", "title": "Steps", "content": {"cards": ["to be refined", "Step A", "Step B", "(to be refined)"]}},
        {
            "type": "comparison",
            "title": "Compare",
            "content": {"table": {"left": {"heading": "Left", "points": ["(to be refined)", "L1"]}, "right": {"heading": "Right", "points": ["R1", "to be refined"]}}},
        },
    ]

    out = finalize_slides(slides)
    assert isinstance(out, list)
    flat = str(out).lower()
    assert "to be refined" not in flat


def test_finalize_slides_double_column_requires_min_4_per_side_or_falls_back():
    slides = [
        {
            "type": "double_column",
            "title": "DC",
            "content": {
                "left_points": ["L1", "L2", "L3"],
                "right_points": ["R1", "R2", "R3"],
            },
        }
    ]
    out = finalize_slides(slides)
    assert len(out) == 1
    t = out[0].get("type")
    assert t in {"infographic", "cards", "bullets"}
    assert "to be refined" not in str(out).lower()


def test_finalize_slides_kpi_tops_up_to_4_metrics():
    slides = [
        {
            "type": "kpi",
            "title": "Key Metrics",
            "content": {
                "metrics": [
                    {"label": "Workforce in gig roles", "value": "36%", "caption": "As of 2023"},
                    {"label": "Growth rate", "value": "15% annually", "caption": "Increasing acceptance"},
                ]
            },
        }
    ]
    out = finalize_slides(slides)
    assert len(out) == 1
    s0 = out[0]
    assert s0.get("type") == "kpi"
    c = s0.get("content") or {}
    metrics = c.get("metrics") or []
    assert isinstance(metrics, list)
    assert len(metrics) >= 4
    for m in metrics[:4]:
        assert isinstance(m, dict)
        assert str(m.get("label") or "").strip()
        assert str(m.get("value") or "").strip()
    assert "to be refined" not in str(out).lower()


def test_finalize_slides_tops_up_scanty_bullets_to_min_4_without_placeholders():
    slides = [
        {"type": "bullets", "title": "Risk Controls", "content": {"bullets": ["Implement monitoring"]}},
    ]
    out = finalize_slides(slides)
    assert len(out) == 1
    c = out[0].get("content") or {}
    bullets = c.get("bullets") or []
    assert isinstance(bullets, list)
    assert len(bullets) >= 4
    assert "to be refined" not in str(out).lower()


def test_finalize_slides_converts_plain_bullets_slide_to_infographic():
    slides = [
        {"type": "bullets", "title": "Before and After the Gig Economy", "content": {"bullets": ["A", "B", "C", "D"]}},
    ]
    out = finalize_slides(slides)
    assert len(out) == 1
    assert out[0].get("type") == "infographic"


def test_finalize_slides_key_takeaways_is_singleton_and_last_before_thanks():
    slides = [
        {"type": "title", "title": "Deck", "subtitle": ""},
        {"type": "agenda", "title": "Agenda", "content": {"bullets": ["A", "B", "C", "D"]}},
        {"type": "key_takeaways", "title": "Key Takeaways", "content": {"bullets": ["One", "Two", "Three", "Four"]}},
        {"type": "infographic", "title": "Key Takeaways", "content": {"bullets": ["Two", "Five", "Six"]}},
        {"type": "cards", "title": "Steps", "content": {"cards": ["S1", "S2", "S3", "S4"]}},
        {"type": "thank_you", "title": "Thank You", "cta": ""},
    ]

    out = finalize_slides(slides)
    kt_idxs = [i for i, s in enumerate(out) if (s.get("type") == "key_takeaways")]
    assert len(kt_idxs) == 1

    kt_i = kt_idxs[0]
    assert kt_i + 1 < len(out)
    assert out[kt_i + 1].get("type") in {"thank_you", "cta"}

    c = out[kt_i].get("content") or {}
    bullets = c.get("bullets") or []
    assert isinstance(bullets, list)
    # Merged and deduped
    low = {str(x).strip().lower() for x in bullets}
    assert "one" in low
    assert "two" in low
    assert "five" in low


def test_finalize_slides_tops_up_scanty_cards_to_min_4_without_placeholders():
    slides = [
        {"type": "cards", "title": "Rollout Plan", "content": {"cards": ["Pilot", "Scale"]}},
    ]
    out = finalize_slides(slides)
    assert len(out) == 1
    c = out[0].get("content") or {}
    cards = c.get("cards") or []
    assert isinstance(cards, list)
    assert len(cards) >= 4
    assert "to be refined" not in str(out).lower()


def test_finalize_slides_tops_up_table_points_to_min_2_per_side():
    slides = [
        {
            "type": "table",
            "title": "Current vs Target",
            "content": {
                "table": {
                    "left": {"heading": "Current", "points": ["Manual handoffs"]},
                    "right": {"heading": "Target", "points": []},
                }
            },
        }
    ]
    out = finalize_slides(slides)
    assert len(out) == 1
    tbl = (out[0].get("content") or {}).get("table") or {}
    left = tbl.get("left") or {}
    right = tbl.get("right") or {}
    lp = left.get("points") or []
    rp = right.get("points") or []
    assert isinstance(lp, list) and isinstance(rp, list)
    assert len(lp) >= 2
    assert len(rp) >= 2
    assert "to be refined" not in str(out).lower()


def test_finalize_slides_swot_requires_min_3_points_per_quadrant():
    slides = [
        {
            "type": "swot",
            "title": "SWOT Analysis",
            "content": {
                "strengths": {"points": ["S1"]},
                "weaknesses": {"points": []},
                "opportunities": {"points": ["O1", "O2"]},
                "threats": {"points": ["T1"]},
            },
        }
    ]
    out = finalize_slides(slides)
    assert len(out) == 1
    s0 = out[0]
    assert s0.get("type") == "swot"
    c = s0.get("content") or {}
    for k in ("strengths", "weaknesses", "opportunities", "threats"):
        q = c.get(k) or {}
        pts = q.get("points") or []
        assert isinstance(pts, list)
        assert len(pts) >= 3
    assert "to be refined" not in str(out).lower()
