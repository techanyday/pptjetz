from __future__ import annotations

import re


def test_outline_template_prefers_slides_out_and_renders_all_slides():
    # Lightweight regression test: make sure outline.html derives outline items
    # from slides_out when present and does not use agenda bullets as the primary
    # topic source.
    path = r"c:\\Users\\ELITEBOOK\\CascadeProjects\\pptjetz\\app\\templates\\outline.html"
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    assert "deck.slides_out" in html
    assert "slides_out.length" in html

    # Ensure we are not using agenda bullets to build the outline list.
    # (It's fine if deriveTitle handles agenda slide title; we just shouldn't build
    # the topics list from agenda.content.bullets.)
    assert "agenda.content.bullets" not in html

    # Ensure we iterate slides to render items.
    assert "slides.forEach((sl, i)" in html or re.search(r"slides\.forEach\(\s*\(sl,\s*i\)", html) is not None

    # Ensure thank_you slides are excluded from the outline list.
    assert "!== 'thank_you'" in html
