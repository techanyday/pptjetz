from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def main() -> None:
    deck_id = "smoke_timeline_ring_nodes"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"deck_{deck_id}.pptx")

    slides = [
        {
            "type": "title",
            "title": "Timeline Infographic — Ring Nodes",
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
            "subtitle": "From Planning to Improve across four milestone years",
            "content": {
                "timeline_variant": "ring_nodes",
                "timeline": [
                    {
                        "date_range": "2016",
                        "label": "Planning",
                        "description": "Define goals, constraints, and success criteria.",
                    },
                    {
                        "date_range": "2017",
                        "label": "Execution",
                        "description": "Deliver key workstreams and remove blockers.",
                    },
                    {
                        "date_range": "2018",
                        "label": "Competition",
                        "description": "Differentiate with measurable value and positioning.",
                    },
                    {
                        "date_range": "2019",
                        "label": "Improve",
                        "description": "Optimize with feedback loops and KPI reviews.",
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
