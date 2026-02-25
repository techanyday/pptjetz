from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def main() -> None:
    deck_id = "smoke_timeline_tri_pennants"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"deck_{deck_id}.pptx")

    slides = [
        {
            "type": "title",
            "title": "Timeline Infographic — Triangle Pennants",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "agenda",
            "title": "Agenda",
            "content": {"bullets": ["Context", "Timeline", "Next steps"]},
            "_render_exact": True,
        },
        {
            "type": "timeline",
            "title": "Timeline Infographic",
            "subtitle": "From Project to Target across alternating milestone years",
            "content": {
                "timeline_variant": "tri_pennants",
                "timeline": [
                    {
                        "date_range": "2015",
                        "label": "Project",
                        "description": "Kick off scope, stakeholders, and success criteria.",
                    },
                    {
                        "date_range": "2016",
                        "label": "Planning",
                        "description": "Define milestones, owners, dependencies, and risks.",
                    },
                    {
                        "date_range": "2017",
                        "label": "Execution",
                        "description": "Ship deliverables and validate outcomes with users.",
                    },
                    {
                        "date_range": "2018",
                        "label": "Control",
                        "description": "Monitor KPIs, quality, and adjust course quickly.",
                    },
                    {
                        "date_range": "2019",
                        "label": "Competition",
                        "description": "Differentiate with clear positioning and advantages.",
                    },
                    {
                        "date_range": "2020",
                        "label": "Target",
                        "description": "Hit goals and standardize repeatable practices.",
                    },
                ],
            },
            "_render_exact": True,
        },
        {
            "type": "thank_you",
            "title": "Thank You",
            "cta": "Questions?",
            "_render_exact": True,
        },
    ]

    theme_name = "SLEEK"
    build_pptx_from_slides(slides, out_path, theme_name=theme_name)
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
