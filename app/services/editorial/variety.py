from __future__ import annotations

from itertools import combinations
import random

ROLE_LAYOUTS = {
    "hook": ("cover_photo_block", "photo_overlay"),
    "context": ("numbered_columns", "timeline", "color_block_list", "matrix_2x2"),
    "evidence": ("stat_pair_banner", "table", "numbered_columns", "big_number", "bar_chart"),
    "process": ("loop_diagram", "timeline", "numbered_columns", "process_chevrons"),
    "comparison": ("comparison", "table", "matrix_2x2"),
    "example": ("photo_overlay", "split_photo_cards", "quote", "image_grid", "quote_photo"),
    "tension": ("comparison", "quote", "color_block_list"),
    "solution": ("stack_with_photo", "numbered_list_photo", "numbered_columns"),
    "action": ("closing_actions", "numbered_columns", "timeline"),
    "breather": ("quote", "section_break"),
}

LAYOUT_VARIANTS = {
    "numbered_columns": ("numbered_cards", "columns"),
    "photo_overlay": ("full_bleed_hero", "photo_overlay"),
    "process_chevrons": ("process_chevrons",),
    "numbered_list_photo": ("photo_list", "photo_table"),
    "color_block_list": ("tier_cards", "split_panel"),
    "timeline": ("milestones", "timeline"),
}

ARCS = {
    "problem_solution": ("hook", "context", "tension", "evidence", "solution", "process", "example", "action"),
    "story_timeline": ("hook", "context", "example", "tension", "process", "evidence", "solution", "action"),
    "myth_vs_reality": ("hook", "tension", "comparison", "evidence", "example", "solution", "breather", "action"),
    "how_to_steps": ("hook", "context", "process", "process", "example", "solution", "action", "action"),
    "case_study_led": ("hook", "example", "tension", "process", "evidence", "solution", "comparison", "action"),
    "decision_pros_cons": ("hook", "context", "comparison", "evidence", "tension", "solution", "breather", "action"),
    "framework_tour": ("hook", "context", "process", "process", "comparison", "example", "solution", "action"),
    "trend_and_implications": ("hook", "evidence", "context", "example", "tension", "solution", "comparison", "action"),
}


def seeded_plan(topic: str, count: int, seed: int | None = None) -> tuple[str, list[tuple[str, str, str]]]:
    actual_seed = seed if seed is not None else random.SystemRandom().randrange(0, 2**32)
    rng = random.Random(actual_seed)
    arc_name = rng.choice(tuple(ARCS))
    roles = list(ARCS[arc_name])
    while len(roles) < max(0, count - 2):
        roles.extend(roles[1:-1])
    roles = roles[: max(0, count - 2)]
    used: dict[str, int] = {}
    result = [("hook", "cover_photo_block", "cover")]
    for role in roles:
        choices = list(ROLE_LAYOUTS[role])
        rng.shuffle(choices)
        layout = min(choices, key=lambda item: used.get(item, 0))
        if result[-1][1] == layout:
            layout = next((item for item in choices if item != layout), layout)
        used[layout] = used.get(layout, 0) + 1
        variants = LAYOUT_VARIANTS.get(layout, (f"{layout}_v1",))
        result.append((role, layout, rng.choice(variants)))
    result.append(("action", "closing_actions", "closing"))
    return arc_name, result


def layout_sequence(deck) -> list[str]:
    slides = getattr(deck, "slides", deck)
    return [
        slide.layout if hasattr(slide, "layout") else slide.get("layout")
        for slide in slides
    ]


def positional_match(first, second) -> float:
    left, right = layout_sequence(first)[1:-1], layout_sequence(second)[1:-1]
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    return sum(a == b for a, b in zip(left[:length], right[:length])) / length


def distinct_layouts(deck) -> set[str]:
    return set(layout_sequence(deck))


def distinct_variants(deck) -> set[str | None]:
    slides = getattr(deck, "slides", deck)
    return {getattr(slide, "variant", None) for slide in slides}


def pairwise_match_matrix(decks) -> list[list[float]]:
    return [[positional_match(a, b) for b in decks] for a in decks]


__all__ = ["layout_sequence", "positional_match", "distinct_layouts", "distinct_variants", "pairwise_match_matrix"]
