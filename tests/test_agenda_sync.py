from __future__ import annotations

from app.services.ai_content import _sync_agenda_to_slide_titles


def test_sync_agenda_includes_all_slide_titles_except_thank_you():
    slides = [
        {"type": "title", "title": "Deck"},
        {"type": "agenda", "content": {"bullets": ["Old 1"]}},
        {"type": "cards", "title": "Topic A", "content": {"cards": ["x", "y", "z"]}},
        {"type": "comparison", "title": "Topic B", "content": {"table": {"left": {"heading": "A", "points": ["p1"]}, "right": {"heading": "B", "points": ["q1"]}}}},
        {"type": "cta", "title": "Next Steps", "content": {"bullets": ["Do thing"]}},
        {"type": "thank_you", "title": "Thanks"},
    ]

    out = _sync_agenda_to_slide_titles(slides)
    assert isinstance(out, list)

    agenda = next(s for s in out if (s.get("type") or "").lower() == "agenda")
    bullets = agenda.get("content", {}).get("bullets")
    assert bullets == ["Topic A", "Topic B", "Next Steps"]
