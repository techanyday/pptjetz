from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def main() -> None:
    deck_id = "smoke_impact_effort_matrix"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"deck_{deck_id}.pptx")

    slides = [
        {
            "type": "title",
            "title": "Simple Impact Effort Matrix — Example",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
        },
        {
            "type": "agenda",
            "title": "Agenda",
            "content": {
                "bullets": [
                    "Context",
                    "Prioritization matrix",
                    "Recommended next steps",
                    "Q&A",
                ]
            },
            "_render_exact": True,
        },
        {
            "type": "impact_effort_matrix",
            "title": "Prioritization Matrix",
            "content": {
                "quick_easy_wins": [
                    "Install solar panels on charging stations for green energy",
                    "Launch community outreach to educate consumers about EV benefits",
                    "Improve onboarding flows for first-time users",
                    "Automate weekly KPI reporting",
                    "(this 5th item should be truncated)",
                ],
                "major_projects": [
                    "Develop a mobile app for easy station location and usage",
                    "Establish partnerships with local governments for EV infrastructure",
                    "Build a centralized data platform",
                    "Roll out predictive maintenance across all sites",
                ],
                "fill_in": [
                    "Update and refresh website content regularly",
                    "Implement recycling programs at charging locations",
                    "Add FAQs and support docs",
                ],
                "thankless_tasks": [
                    "Conduct comprehensive environmental impact assessments",
                    "Maintain older charging stations to ensure reliability",
                    "Support legacy integrations with low ROI",
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
