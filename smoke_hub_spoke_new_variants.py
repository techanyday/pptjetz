from __future__ import annotations

import argparse
import os

from app.services.pptx_builder import build_pptx_from_slides


def _spokes_for_topic(topic: str) -> list[str]:
    t = (topic or "").strip().lower()
    label = (topic or "").strip() or "the topic"
    if "5g" in t:
        return [
            "mmWave spectrum",
            "Network slicing",
            "Ultra-low latency",
            "Massive IoT",
            "Edge computing",
            "Security",
        ]
    if "sap" in t and "s/4hana" in t:
        return [
            "Business process standardization",
            "Data migration and quality readiness",
            "Integration and extensibility model",
            "Role-based security and compliance",
            "Change management and adoption",
            "Value realization and KPI tracking",
        ]
    return [
        f"Strategic priorities for {label}",
        f"Operating model for {label}",
        f"Execution roadmap for {label}",
        f"Risk controls for {label}",
        f"Adoption plan for {label}",
        f"KPIs and outcomes for {label}",
    ]


def _slides(deck_id: str, variant: str, *, topic: str):
    return [
        {
            "type": "title",
            "title": f"{topic} — Hub & Spoke ({variant})",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "infographic",
            "title": topic,
            "content": {
                "infographic_intent": "IMPACT",
                "impact_map_variant": variant,
                "center_label": topic,
                "bullets": _spokes_for_topic(topic),
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="Hub & Spoke", help="Topic to use for the hub/spoke smoke deck")
    args = parser.parse_args()
    topic = str(args.topic or "Hub & Spoke").strip() or "Hub & Spoke"

    deck_id = "smoke_hub_spoke_new_variants"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)

    variants = ["quadrant_ring_4", "diamond_capsules_4", "brainstorming_infographic_4", "brainstorming_infographic_5", "side_by_side_infographic_6"]
    out_paths = []

    for v in variants:
        out_path = os.path.join(out_dir, f"deck_{v}.pptx")
        build_pptx_from_slides(_slides(deck_id + "_" + v, v, topic=topic), out_path, theme_name="SLEEK")
        out_paths.append(out_path)

    for p in out_paths:
        print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
