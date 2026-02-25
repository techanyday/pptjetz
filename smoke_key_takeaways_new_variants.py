from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def _slides(deck_id: str, variant: str):
    return [
        {
            "type": "title",
            "title": f"Key Takeaways — {variant}",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "key_takeaways",
            "title": "Key Takeaways",
            "content": {
                "takeaways_variant": variant,
                "bullets": [
                    "Prioritize thorough needs assessment: align ERP with business goals.",
                    "Engage stakeholders early: ensure buy-in and support.",
                    "Invest in training: enhance user adoption and minimize resistance.",
                    "Monitor KPIs regularly: track success and identify improvements.",
                ],
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]


def main() -> None:
    deck_id = "smoke_key_takeaways_new_variants"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)

    variants = ["pill_rows", "minimal_keys", "grid_blocks", "focus_rows"]
    out_paths = []

    for v in variants:
        out_path = os.path.join(out_dir, f"deck_{v}.pptx")
        build_pptx_from_slides(_slides(deck_id + "_" + v, v), out_path, theme_name="SLEEK")
        out_paths.append(out_path)

    for p in out_paths:
        print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
