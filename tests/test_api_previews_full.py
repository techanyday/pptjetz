from __future__ import annotations

from pathlib import Path


def test_preview_loading_requests_full_render():
    html = Path("app/templates/preview_loading.html").read_text(encoding="utf-8")
    assert "q.set('full', '1')" in html
    assert "q2.set('full', '1')" in html


def test_api_previews_supports_full_override():
    src = Path("app/routes.py").read_text(encoding="utf-8")
    assert "request.args.get(\"full\")" in src
    assert "if (not full) and expected_count and expected_count > preview_max" in src
