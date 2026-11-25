from app.services.editorial.schema import DeckSpec
from app.services.editorial.variety import (
    LAYOUT_VARIANTS,
    distinct_layouts,
    layout_sequence,
    pairwise_match_matrix,
    seeded_plan,
)


def test_seeded_plan_repeats_for_same_seed_and_changes_for_other_seed():
    first = seeded_plan("same topic", 10, 11)
    second = seeded_plan("same topic", 10, 11)
    third = seeded_plan("same topic", 10, 12)
    assert first == second
    assert first != third
    assert first[1][0][1] == "cover_photo_block"
    assert first[1][-1][1] == "closing_actions"


def test_variety_metrics_compare_middle_layouts():
    a = DeckSpec(
        title="A", audience="Leaders", thesis="A claim",
        slides=[{"layout": "cover_photo_block", "action_title": "A", "eyebrow": "A", "notes": " ".join(["note"] * 40), "fields": {"title": "A", "subtitle": "B", "footer_line": "C"}},
                {"layout": "closing_actions", "action_title": "B", "notes": " ".join(["note"] * 40), "fields": {"actions": [{"heading": "A", "detail": "B"}, {"heading": "C", "detail": "D"}, {"heading": "E", "detail": "F"}]}}],
    )
    assert layout_sequence(a)[0] == "cover_photo_block"
    assert "closing_actions" in distinct_layouts(a)
    assert pairwise_match_matrix([a, a]) == [[0.0, 0.0], [0.0, 0.0]]


def test_reference_composition_variants_are_in_seeded_pool():
    _, plan = seeded_plan("responsible AI", 10, 123)
    selected = {(layout, variant) for _, layout, variant in plan}
    assert "numbered_cards" in LAYOUT_VARIANTS["numbered_columns"]
    assert any(layout == "process_chevrons" for layout, _ in selected)
    assert any(variant in {"tier_cards", "numbered_cards", "photo_table", "full_bleed_hero"} for _, variant in selected)
