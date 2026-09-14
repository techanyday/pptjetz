from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def main() -> None:
    deck_id = "smoke_roadmap_richer"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"deck_{deck_id}.pptx")

    slides = [
        {
            "type": "title",
            "title": "Mental Health Initiatives",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "roadmap",
            "title": "Roadmap for Mental Health Initiatives",
            "content": {
                "subtitle": "A phased approach to build, evaluate, and scale meaningful mental health support across our community.",
                "timeline": [
                    {
                        "title": "Assess Needs",
                        "description": "Conduct needs assessment and gather data to understand key priorities and gaps.",
                    },
                    {
                        "title": "Develop & Implement",
                        "description": "Develop and implement targeted support programs that address identified needs.",
                    },
                    {
                        "title": "Evaluate & Adjust",
                        "description": "Evaluate effectiveness using data and feedback, and make adjustments for greater impact.",
                    },
                    {
                        "title": "Sustain & Expand",
                        "description": "Sustain effective programs and expand successful initiatives to reach more people.",
                    },
                ],
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]

    build_pptx_from_slides(slides, out_path, theme_name="SLEEK")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
