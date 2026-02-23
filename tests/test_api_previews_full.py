from pathlib import Path
import re


def test_api_previews_supports_full_override() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    routes_py = (repo_root / "app" / "routes.py").read_text(encoding="utf-8")

    # Ensure /api/previews reads a 'full' query param and uses it to bypass clamping
    assert re.search(r"request\.args\.get\(\s*['\"]full['\"]\s*\)", routes_py)
    assert "if (not full)" in routes_py


def test_preview_loading_requests_full_previews() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tpl = (repo_root / "app" / "templates" / "preview_loading.html").read_text(encoding="utf-8")

    # preview_loading should request full previews (not limited by PREVIEW_MAX_SLIDES)
    assert "q.set('full', '1')" in tpl
    assert "q2.set('full', '1')" in tpl


def test_loading_polls_generate_status() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tpl = (repo_root / "app" / "templates" / "loading.html").read_text(encoding="utf-8")
    assert "/api/generate/" in tpl
