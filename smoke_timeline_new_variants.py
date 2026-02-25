from __future__ import annotations

import os

from app.services.pptx_builder import build_pptx_from_slides


def _timeline_points(*, with_years: bool):
    base = [
        {"label": "Project", "description": "Align scope, owners, and initial plan."},
        {"label": "Planning", "description": "Define milestones, risks, and resourcing."},
        {"label": "Improve", "description": "Standardize delivery and remove bottlenecks."},
        {"label": "Profit", "description": "Optimize performance and unit economics."},
        {"label": "Sales", "description": "Scale distribution and customer adoption."},
    ]
    if not with_years:
        return base
    years = ["2016", "2017", "2018", "2019", "2020"]
    out = []
    for y, it in zip(years, base):
        out.append({"year": y, **it})
    return out


def _base_slides(deck_id: str, timeline_variant: str, *, with_years: bool):
    return [
        {
            "type": "title",
            "title": f"Timeline — {timeline_variant}",
            "subtitle": "Smoke test",
            "presenter": "PPTJet",
            "date": "",
            "_deck_id": deck_id,
        },
        {
            "type": "timeline",
            "title": "Timeline Infographic",
            "subtitle": "Key phases over time",
            "content": {
                "variant": timeline_variant,
                "timeline": _timeline_points(with_years=with_years),
            },
            "_render_exact": True,
        },
        {"type": "thank_you", "title": "Thank You", "cta": "", "_render_exact": True},
    ]


def main() -> None:
    deck_id = "smoke_timeline_new_variants"
    out_dir = os.path.join(os.getcwd(), "tmp_previews", deck_id)
    os.makedirs(out_dir, exist_ok=True)

    variants = ["ring_nodes", "tri_pennants", "callout_chevrons", "strip_columns", "arc_progress"]
    out_paths = []

    for v in variants:
        out_path = os.path.join(out_dir, f"deck_{v}_with_years.pptx")
        slides = _base_slides(deck_id + "_" + v + "_with_years", v, with_years=True)
        build_pptx_from_slides(slides, out_path, theme_name="SLEEK")
        out_paths.append(out_path)

        out_path2 = os.path.join(out_dir, f"deck_{v}_no_years.pptx")
        slides2 = _base_slides(deck_id + "_" + v + "_no_years", v, with_years=False)
        build_pptx_from_slides(slides2, out_path2, theme_name="SLEEK")
        out_paths.append(out_path2)

    for p in out_paths:
        print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
