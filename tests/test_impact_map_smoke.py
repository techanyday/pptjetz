from __future__ import annotations

import os
import sys
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.pptx_builder import build_pptx_from_slides


def _read_all_slide_xml(pptx_path: str) -> str:
    buf: list[str] = []
    with zipfile.ZipFile(pptx_path, "r") as z:
        for name in sorted(z.namelist()):
            if not name.startswith("ppt/slides/slide") or not name.endswith(".xml"):
                continue
            buf.append(z.read(name).decode("utf-8", errors="ignore"))
    return "\n".join(buf)


def test_impact_map_radial_smoke_no_midword_and_connectors(tmp_path):
    out_override = str(os.environ.get("PPTJET_SMOKE_OUT", "")).strip()
    if out_override:
        p = Path(out_override)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        out = p
    else:
        out = tmp_path / "impact_map_smoke.pptx"

    print(f"SMOKE_PPTX={out}")

    slides = [
        {"type": "title", "title": "Deck"},
        {
            "type": "infographic",
            "title": "Impact Map",
            "content": {
                "infographic_intent": "IMPACT",
                "impact_map_variant": "classic_circles",
                "center_label": "Impact on Communication",
                "bullets": [
                    "Improve customer experience",
                    "Increase conversion rate",
                    "Reduce operational cost",
                    "Accelerate delivery cadence",
                    "Strengthen security posture",
                    "Enhance team alignment",
                ],
            },
        },
        {"type": "thank_you", "title": "Thanks"},
    ]

    build_pptx_from_slides(slides, str(out), theme_name="SLEEK")

    assert out.exists()
    assert out.stat().st_size > 0

    xml = _read_all_slide_xml(str(out))

    # Regression guard: we should never introduce mid-word splitting like "Communica tion".
    assert "Communica tion" not in xml

    # Connectors in PPTX slide XML are represented as <p:cxnSp>.
    # Expect at least 6 for the 6-spoke hub layout.
    assert xml.count("<p:cxnSp") >= 6
