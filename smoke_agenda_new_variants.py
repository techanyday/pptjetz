from __future__ import annotations

import argparse
import os
import time

from app.services.pptx_builder import build_pptx_from_slides


def _agenda_items_for_topic(topic: str):
    t = (topic or "").strip().lower()
    if "5g" in t:
        return [
            "Understanding 5G Technology",
            "5G Implementation Timeline",
            "Comparative Analysis",
            "5G Use Cases Across Industries",
            "Key Features of 5G Technology",
            "5G Service Pricing Models",
            "Key Takeaways",
        ]
    return [
        {"title": "Project", "description": "Define scope, goals, and success metrics."},
        {"title": "Planning", "description": "Align stakeholders, timeline, and resources."},
        {"title": "Execution", "description": "Deliver milestones with clear ownership."},
        {"title": "Control", "description": "Track KPIs, risks, and corrective actions."},
        "Competition — Review landscape and differentiation.",
        {"title": "Go-To-Market", "description": "Positioning, channels, and launch plan."},
        {"title": "Measurement", "description": "Define reporting cadence and success signals."},
        "Next Steps — Owners, dates, and immediate actions.",
    ]


def _slides(deck_id: str, variant: str, topic: str):
    agenda_items = _agenda_items_for_topic(topic)

    return [
        {
            "type": "title",
            "title": f"Agenda — {variant}",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
            "_render_exact": True,
        },
        {
            "type": "agenda",
            "title": "Agenda",
            "subtitle": "Make a big impact with our professional slides and charts.",
            "content": {
                "agenda_variant": variant,
                "bullets": agenda_items,
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="", help="Topic used to generate agenda items")
    ap.add_argument("--variant", default="", help="If set, render only this agenda variant")
    args = ap.parse_args()
    topic = str(args.topic or "").strip()
    only_variant = str(args.variant or "").strip()

    deck_id = "smoke_agenda_new_variants"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)
    run_tag = str(int(time.time()))

    variants = ["step_squares", "center_chevrons", "chevron_badges", "numbered_rules", "numbered_grid", "minimal_rule", "stacked_circles", "split_panel"]
    if only_variant:
        variants = [only_variant]
    out_paths = []

    for v in variants:
        out_path = os.path.join(out_dir, f"deck_{v}_{run_tag}.pptx")
        build_pptx_from_slides(_slides(deck_id + "_" + v, v, topic), out_path, theme_name="SLEEK")
        out_paths.append(out_path)

    for p in out_paths:
        print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
