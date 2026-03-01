from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def main() -> None:
    deck_id = "smoke_before_after_modern"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"deck_{deck_id}.pptx")

    slides = [
        {
            "type": "title",
            "title": "Before / After — Modern",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "before_after",
            "title": "Lead Conversion: Before vs After",
            "content": {
                "before_after_variant": "modern",
                "before": {
                    "heading": "Before",
                    "points": [
                        "Generic messaging with low relevance",
                        "Manual follow-up and inconsistent cadence",
                        "Limited visibility into funnel drop-offs",
                        "Slow iteration on landing page experiments",
                    ],
                },
                "after": {
                    "heading": "After",
                    "points": [
                        "Persona-specific messaging and positioning",
                        "Automated nurture sequences with clear triggers",
                        "Full-funnel tracking and shared dashboards",
                        "Weekly test-and-learn optimization loop",
                    ],
                },
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]

    build_pptx_from_slides(slides, out_path, theme_name="SLEEK")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
