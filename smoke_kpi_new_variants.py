from __future__ import annotations

import argparse
import os

from app.services.pptx_builder import build_pptx_from_slides


def _metrics_for_topic(topic: str) -> list[dict[str, str]]:
    label = (topic or "Business Performance").strip() or "Business Performance"
    return [
        {
            "label": f"Adoption Rate ({label})",
            "value": "82%",
            "caption": "Share of activated business users running at least one live transaction weekly.",
            "basis": "of target user base (1,240 of 1,512 users)",
            "period": "Q1 2026",
            "scope": "Global",
            "trend": "up",
            "progress": 82,
        },
        {
            "label": "Process Cycle Time",
            "value": "-28%",
            "caption": "Reduction in quote-to-cash completion time across the three highest-volume process families.",
            "basis": "against FY2025 baseline median",
            "period": "Jan-Mar 2026",
            "scope": "Europe",
            "trend": "down",
            "progress": 56,
        },
        {
            "label": "Cost-to-Serve",
            "value": "-15%",
            "caption": "Lower run-rate cost per serviced order from automation and policy-driven exception handling.",
            "basis": "vs prior operating model cost per order",
            "period": "Rolling 12 months ending Mar 2026",
            "scope": "Germany",
            "trend": "down",
            "progress": 45,
        },
        {
            "label": "Data Quality Score",
            "value": "96%",
            "caption": "Weighted quality index for customer, supplier, and material master records in productive use.",
            "basis": "against quality target threshold of 95%",
            "period": "Monthly close - Mar 2026",
            "scope": "EMEA",
            "trend": "up",
            "progress": 96,
        },
    ]


def _slides(deck_id: str, variant: str, topic: str):
    return [
        {
            "type": "title",
            "title": f"{topic} — KPI ({variant})",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "kpi",
            "title": "Key Metrics",
            "content": {
                "kpi_variant": variant,
                "subtitle": "Measuring impact across adoption, efficiency, cost, and data quality.",
                "metrics": _metrics_for_topic(topic),
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="Business Performance", help="Topic to use for KPI smoke outputs")
    args = parser.parse_args()

    topic = str(args.topic or "Business Performance").strip() or "Business Performance"
    deck_id = "smoke_kpi_new_variants"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)

    variants = ["grid", "strip", "list", "story"]
    out_paths: list[str] = []

    for variant in variants:
        out_path = os.path.join(out_dir, f"deck_{variant}.pptx")
        build_pptx_from_slides(_slides(f"{deck_id}_{variant}", variant, topic), out_path, theme_name="SLEEK")
        out_paths.append(out_path)

    for p in out_paths:
        print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
