from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def main() -> None:
    deck_id = "smoke_timeline_pill_band"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"deck_{deck_id}.pptx")

    slides = [
        {
            "type": "title",
            "title": "Timeline Infographic — Pill Band",
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
            "subtitle": "Make a big impact with professional slides and charts",
            "content": {
                "timeline_variant": "pill_band",
                "timeline": [
                    {
                        "date_range": "2016",
                        "label": "Planning",
                        "description": "Define scope, goals, and constraints for the initiative.",
                    },
                    {
                        "date_range": "2017",
                        "label": "Innovation",
                        "description": "Prototype, validate, and iterate on new approaches.",
                    },
                    {
                        "date_range": "2018",
                        "label": "Competition",
                        "description": "Differentiate with clear value and measurable advantages.",
                    },
                    {
                        "date_range": "2019",
                        "label": "Execution",
                        "description": "Deliver capabilities, track KPIs, and remove blockers.",
                    },
                    {
                        "date_range": "2020",
                        "label": "Success",
                        "description": "Scale results and optimize continuously with feedback loops.",
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
