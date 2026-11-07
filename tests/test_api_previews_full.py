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


def test_homepage_allows_generation_without_login() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tpl = (repo_root / "app" / "templates" / "index.html").read_text(encoding="utf-8")

    assert "No sign-in required" in tpl
    assert "Login with Google (optional)" in tpl
    assert "url_for('main.generate_page')" in tpl
    assert "Get Started" not in tpl


def test_public_templates_do_not_claim_google_sign_in_is_required() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    templates_dir = repo_root / "app" / "templates"
    forbidden = [
        "Google sign-in required",
        "Google sign-in to start",
        "with Google sign-in to start",
        "Sign in with Google to start creating decks",
        "Sign in with Google to create and download decks",
        "uses Google sign-in to start",
        "requires no sign-in required to start",
    ]

    for path in templates_dir.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if path.name == "index.html":
            assert "Login with Google (optional)" in text
        for phrase in forbidden:
            assert phrase not in text, f"{path} still contains: {phrase}"


def test_theme_selection_uses_separate_theme_routes() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    routes_py = (repo_root / "app" / "routes.py").read_text(encoding="utf-8")
    select_theme_tpl = (repo_root / "app" / "templates" / "select_theme.html").read_text(encoding="utf-8")
    generate_tpl = (repo_root / "app" / "templates" / "generate.html").read_text(encoding="utf-8")

    assert '@bp.get("/select-theme/<theme_slug>")' in routes_py
    assert 'redirect(url_for("main.select_theme_detail_page"' in routes_py
    assert "url_for('main.select_theme_detail_page', theme_slug=prev_entry.slug)" in select_theme_tpl
    assert "url_for('main.select_theme_detail_page', theme_slug=next_entry.slug)" in select_theme_tpl
    assert "Why choose" not in select_theme_tpl
    assert 'id="generateBtn"' in select_theme_tpl
    assert "const currentTheme = JSON.parse('{{ current_theme | tojson | safe }}');" in select_theme_tpl
    assert "window.location.assign({{ url_for('main.select_theme_page') | tojson | safe }});" in generate_tpl
