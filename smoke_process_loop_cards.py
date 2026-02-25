from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def main() -> None:
    deck_id = "smoke_process_loop_cards"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"deck_{deck_id}.pptx")

    slides = [
        {
            "type": "title",
            "title": "Process Infographic — Loop Cards",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "agenda",
            "title": "Agenda",
            "content": {"bullets": ["Context", "Process", "Next steps"]},
            "_render_exact": True,
        },
        {
            "type": "cards",
            "title": "From Identify to Management",
            "subtitle": "From Identify to Management with clear outputs at each step",
            "content": {
                "process_variant": "loop_cards",
                "cards": [
                    "Identify — Define the problem, scope, and success metrics.",
                    "Improve — Remove friction, standardize workflows, and reduce rework.",
                    "Innovation — Validate new approaches with rapid experiments.",
                    "Management — Operationalize with KPIs, owners, and cadences.",
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
