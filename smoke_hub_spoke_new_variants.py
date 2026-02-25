from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def _slides(deck_id: str, variant: str):
    return [
        {
            "type": "title",
            "title": f"Hub & Spoke — {variant}",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "infographic",
            "title": "Hub & Spoke",
            "content": {
                "infographic_intent": "IMPACT",
                "impact_map_variant": variant,
                "center_label": "Hub",
                "bullets": [
                    "Project",
                    "Planning",
                    "Improve",
                    "Profit",
                    "Sales",
                    "Execution",
                ],
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]


def main() -> None:
    deck_id = "smoke_hub_spoke_new_variants"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)

    variants = ["classic_circles", "circular_ring", "lr_boxes", "orbit"]
    out_paths = []

    for v in variants:
        out_path = os.path.join(out_dir, f"deck_{v}.pptx")
        build_pptx_from_slides(_slides(deck_id + "_" + v, v), out_path, theme_name="SLEEK")
        out_paths.append(out_path)

    for p in out_paths:
        print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
