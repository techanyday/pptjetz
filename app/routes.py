from __future__ import annotations

from typing import Any, Dict, List
import re
import os
import requests

from flask import Blueprint, jsonify, request, render_template, redirect, url_for, current_app, session, send_from_directory
from flask_login import login_user, logout_user, current_user

from app.services.ai_content import create_deck_slides
# Preview PNG generation is removed; only PPTX export remains
from app.services.pptx_builder import build_pptx_from_positions
from app import db
from app.models import User


bp = Blueprint("main", __name__)


THEME_OPTIONS = [
    "SLEEK",
    "PULSE",
    "MINIMAL FLUX",
    "BLUE HORIZON",
]

# Root directory for per-request preview images (legacy temp + static web-served)
PREVIEW_ROOT = os.path.join(os.getcwd(), "tmp_previews")
os.makedirs(PREVIEW_ROOT, exist_ok=True)

# Static previews removed; we only serve PPTX files from PREVIEW_ROOT


# Legacy static previews endpoint removed


@bp.get("/download/<folder>/<fname>")
def download_file(folder: str, fname: str):
    """Serve the generated PPTX for download from PREVIEW_ROOT."""
    return send_from_directory(os.path.join(PREVIEW_ROOT, folder), fname, as_attachment=True)

# Note: Google Slides template IDs are no longer used; export is local PPTX via python-pptx.


@bp.get("/")
def home() -> Any:
    """Render a minimal UI to generate and export decks."""
    return render_template("index.html", themes=THEME_OPTIONS)


@bp.get("/themes")
def themes() -> Any:
    """Return available theme names."""
    return jsonify({"ok": True, "themes": [{"name": n} for n in THEME_OPTIONS]})


# Simple static page routes to satisfy existing template links
@bp.get("/how-it-works")
def how_it_works() -> Any:
    return render_template("how_it_works.html")


# Pricing page removed for free app


@bp.get("/faq")
def faq() -> Any:
    return render_template("faq.html")


@bp.get("/privacy")
def privacy() -> Any:
    return render_template("privacy.html")


@bp.get("/terms")
def terms() -> Any:
    return render_template("terms.html")


@bp.get("/ads.txt")
def ads_txt() -> Any:
    """Serve ads.txt from the static directory at the site root."""
    return send_from_directory(current_app.static_folder, "ads.txt")


@bp.get("/about")
def about() -> Any:
    return render_template("about_us.html")


@bp.get("/blog")
def blog_index() -> Any:
    return render_template("blog_index.html")


@bp.get("/dashboard")
def dashboard() -> Any:
    return render_template("dashboard.html")


@bp.get("/admin")
def admin_dashboard_page() -> Any:
    return render_template("admin/dashboard.html")


@bp.get("/generate")
def generate_page() -> Any:
    """Render generator UI; app is free with unlimited usage."""
    disable_limits = True
    return render_template(
        "generate.html",
        disable_limits=disable_limits,
        user=current_user,
    )

@bp.get("/login")
def login() -> Any:
    """Start Google OAuth2 login by redirecting to Google's consent screen."""
    client = current_app.config.get("OAUTH_CLIENT")
    if client is None:
        return redirect(url_for("main.home"))

    # Build the authorization URL
    authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    redirect_uri = url_for("main.login_callback", _external=True)
    # Request only non-sensitive scopes to avoid verification prompts.
    # The app does not call Drive/Slides APIs anymore; PPTX export is local.
    scopes = [
        "openid",
        "email",
        "profile",
    ]
    # Remember where to go after login; default to generator page
    session["post_login_redirect"] = request.args.get("next") or url_for("main.generate_page")

    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=redirect_uri,
        scope=scopes,
        access_type="offline",
        prompt="consent",
    )
    return redirect(request_uri)


@bp.get("/login/callback")
def login_callback() -> Any:
    """Handle Google's OAuth2 callback, exchange code, fetch user info, and log in."""
    client = current_app.config.get("OAUTH_CLIENT")
    if client is None:
        return redirect(url_for("main.home"))

    code = request.args.get("code")
    if not code:
        return redirect(url_for("main.home"))

    token_endpoint = "https://oauth2.googleapis.com/token"
    redirect_uri = url_for("main.login_callback", _external=True)
    client_id = current_app.config.get("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_OAUTH_CLIENT_SECRET") or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

    token_url, headers, body = client.prepare_token_request(
        token_endpoint,
        authorization_response=request.url,
        redirect_url=redirect_uri,
        code=code,
    )
    token_response = requests.post(
        token_url,
        headers=headers,
        data=body,
        auth=(client_id, client_secret),
        timeout=20,
    )
    client.parse_request_body_response(token_response.text)
    tokens = token_response.json()
    # Persist tokens in session for downstream Google API use
    session["google_access_token"] = tokens.get("access_token")
    if tokens.get("refresh_token"):
        session["google_refresh_token"] = tokens.get("refresh_token")

    # Fetch userinfo
    userinfo_endpoint = "https://www.googleapis.com/oauth2/v3/userinfo"
    uri, headers, body = client.add_token(userinfo_endpoint)
    userinfo_resp = requests.get(uri, headers=headers, timeout=20)
    profile = userinfo_resp.json()

    # Build or update user
    user_id = profile.get("sub") or profile.get("id")
    name = profile.get("name") or "User"
    email = (profile.get("email") or "").lower()
    picture = profile.get("picture")
    is_admin = email in (current_app.config.get("ADMIN_EMAILS") or [])

    user = User.get(user_id)
    if user:
        user.name = name
        user.email = email
        user.profile_pic = picture
        user.is_admin = is_admin
    else:
        user = User(id_=user_id, name=name, email=email, profile_pic=picture, is_admin=is_admin)
        db.session.add(user)
    db.session.commit()

    login_user(user)
    dest = session.pop("post_login_redirect", None) or url_for("main.generate_page")
    return redirect(dest)


@bp.get("/logout")
def logout() -> Any:
    """Log out the current user and clear session tokens."""
    try:
        logout_user()
    except Exception:
        pass
    session.pop("google_access_token", None)
    session.pop("google_refresh_token", None)
    return redirect(url_for("main.home"))


@bp.route("/generate", methods=["POST"])
def generate() -> Any:
    """Generate slides via ChatGPT 3.5 Turbo (strict JSON, no fallback).

    Request JSON body:
    {
      "title": str,
      "subtitle": str | null,
      "presenter": str | null,
      "date": str | null,
      "theme": str,  # theme name
      "input_text": str,  # prompt/content
      "num_slides": int
    }
    """
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    subtitle = (payload.get("subtitle") or "").strip() or None
    presenter = (payload.get("presenter") or "").strip() or None
    date = (payload.get("date") or "").strip() or None
    # New template-driven themes: SLEEK, PULSE, MINIMAL FLUX, BLUE HORIZON
    theme_name = (payload.get("theme") or payload.get("theme_name") or "SLEEK").strip()
    input_text = str(payload.get("input_text") or "").strip()
    try:
        num_slides = int(payload.get("num_slides") or 6)
    except Exception:  # noqa: BLE001
        num_slides = 6

    # Default presenter to logged-in user's Google profile name if absent
    if not presenter and getattr(current_user, "is_authenticated", False):
        try:
            presenter = (current_user.name or "").strip() or None
        except Exception:
            presenter = None

    slides, errors = create_deck_slides(
        title=title,
        subtitle=subtitle,
        presenter=presenter,
        date=date,
        theme_name=theme_name,
        input_text=input_text,
        num_slides=num_slides,
    )
    if errors or not slides:
        return jsonify({"ok": False, "errors": errors or ["No slides generated"]}), 422

    # Echo selected theme name so the client can carry it to /export
    return jsonify({"ok": True, "slides": slides, "theme_name": theme_name})


@bp.route("/export", methods=["POST"])
def export_presentation() -> Any:
    """Create a local PPTX presentation from slide content and theme.

    Request JSON body:
    {
      "deck_title": str,
      "slides": [ ... AI slides array ... ],
      "theme_name": "SLEEK"|"PULSE"|"MINIMAL FLUX"|"BLUE HORIZON"
    }
    """
    data = request.get_json(silent=True) or {}
    deck_title = (data.get("deck_title") or "Untitled").strip()
    slides = data.get("slides") or []
    theme_name = (data.get("theme_name") or "SLEEK").strip()
    # Sanitization helpers
    BANNED = {"tbd", "metric 1", "metric 2", "metric 3", "step 1", "step 2", "step 3", "step 4"}

    def clean_list(items):
        out, seen = [], set()
        for s in items or []:
            t = (s or "").strip()
            if not t:
                continue
            low = t.lower()
            if low in BANNED:
                continue
            if low in seen:
                continue
            seen.add(low)
            out.append(t)
        return out

    def ensure_min(items, n, filler="(to be refined)"):
        items = list(items or [])
        while len(items) < n:
            items.append(filler)
        return items

    def meaningful_headers(h1, h2, d1="Traditional", d2="AI-Driven"):
        h1 = (h1 or "").strip() or d1
        h2 = (h2 or "").strip() or d2
        if re.fullmatch(r"option\s*[ab]", h1.lower()):
            h1 = d1
        if re.fullmatch(r"option\s*[ab]", h2.lower()):
            h2 = d2
        return h1, h2

    def sanitize_steps(steps):
        steps = [d.get("label") if isinstance(d, dict) else str(d) for d in (steps or [])]
        steps = [re.sub(r"(?i)^step\s*\d+\s*$", "", (s or "").strip()) for s in steps]
        steps = clean_list(steps)
        return ensure_min(steps[:6], 4)

    def sanitize_for_takeaways(labels, fallback_sources):
        labels = clean_list(labels)
        if len(labels) < 4:
            for src in fallback_sources:
                labels += [x for x in clean_list(src) if x not in labels]
                if len(labels) >= 4:
                    break
        labels = ensure_min(labels[:5], 4)[:5]
        labels = [re.sub(r"(?i)^step\s*\d+\s*$", "(to be refined)", x).strip() for x in labels]
        return labels

    # Build 1..10 positions from AI slides with sanitization
    def _ctype(sl: Dict[str, Any]) -> str:
        return (sl.get("type") or "").strip().lower()

    def _find_first(*types: str) -> Dict[str, Any] | None:
        types_l = [t.lower() for t in types]
        for sl in slides:
            if isinstance(sl, dict) and _ctype(sl) in types_l:
                return sl
        return None

    def _content(sl: Dict[str, Any]) -> Dict[str, Any]:
        return (sl.get("content") or {}) if isinstance(sl.get("content"), dict) else {}

    # Position 1: Title
    pos: Dict[int, Dict[str, Any]] = {}
    s1 = _find_first("title") or {}
    pos[1] = {
        "title": (s1.get("title") if isinstance(s1.get("title"), str) else data.get("deck_title")) or deck_title,
        "subtitle": (s1.get("subtitle") if isinstance(s1.get("subtitle"), str) else "") or (data.get("subtitle") or ""),
    }
    # Position 2: Agenda
    s2 = _find_first("agenda") or {}
    c2 = _content(s2)
    bullets2 = clean_list([str(x) for x in (c2.get("bullets") or [])])
    bullets2 = ensure_min(bullets2[:6], 4)
    pos[2] = {"title": "Agenda", "bullets": bullets2}

    # Position 3: Intro/Background
    s3 = _find_first("background", "overview", "intro", "timeline", "bullets") or {}
    c3 = _content(s3)
    bullets3 = clean_list([str(x) for x in (c3.get("bullets") or [])])
    bullets3 = ensure_min(bullets3[:4], 2)
    # Use first bullet as a sentence if paragraph empty
    paragraph3 = (s3.get("paragraph") or "").strip()
    if not paragraph3 and bullets3:
        paragraph3 = bullets3[0]
        bullets3 = bullets3[1:]
    pos[3] = {"title": "Background", "paragraph": str(paragraph3), "bullets": bullets3}

    # Position 4: Key Insights (two-column)
    s4 = _find_first("double_column", "deep_dive", "bullets") or {}
    c4 = _content(s4)
    left_points = clean_list([str(x) for x in (c4.get("left_points") or [])])
    right_points = clean_list([str(x) for x in (c4.get("right_points") or [])])
    if not (left_points or right_points):
        # Try table-style content
        table = c4.get("table") or {}
        left_points = clean_list([str(x) for x in (table.get("left", {}).get("points") or [])])
        right_points = clean_list([str(x) for x in (table.get("right", {}).get("points") or [])])
    left_points = ensure_min(left_points[:6], 4)
    right_points = ensure_min(right_points[:6], 4)
    pos[4] = {"title": "Key Concepts", "left": {"heading": "", "points": left_points}, "right": {"heading": "", "points": right_points}}

    # Position 5: Comparison / Breakdown
    s5 = _find_first("comparison", "table") or {}
    c5 = _content(s5)
    table5 = c5.get("table") or {}
    left5 = table5.get("left") or {"heading": s5.get("left_title") or "Option A", "points": c5.get("left_points") or []}
    right5 = table5.get("right") or {"heading": s5.get("right_title") or "Option B", "points": c5.get("right_points") or []}
    # Sanitize headers
    lh, rh = meaningful_headers(left5.get("heading"), right5.get("heading"))
    lpts = ensure_min(clean_list([str(x) for x in (left5.get("points") or [])])[:6], 3)
    rpts = ensure_min(clean_list([str(x) for x in (right5.get("points") or [])])[:6], 3)
    pos[5] = {
        "title": "Comparison",
        "headers": [lh, rh],
        "left": {"heading": lh, "points": lpts},
        "right": {"heading": rh, "points": rpts},
    }

    # Position 6: Infographic Summary
    s6 = _find_first("infographic", "key_takeaways", "bullets") or {}
    c6 = _content(s6)
    bullets6 = ensure_min(clean_list([str(x) for x in (c6.get("bullets") or [])])[:6], 3)
    pos[6] = {"title": "KPI Snapshot", "bullets": bullets6}

    # Position 7: Thank You / Final
    s7 = _find_first("thank_you", "cta") or {}
    # Steps come from either provided steps or bullets
    steps_src = s7.get("steps") or s7.get("bullets") or []
    steps_clean = sanitize_steps(steps_src)
    pos[7] = {"title": "Process", "steps": steps_clean}

    try:
        # Extend positions to 10 slides expected by python-pptx builder
        # Slide 8 (Use Cases): cards (synthesize if thin)
        pos[8] = pos.get(8) or {"title": "Use Cases", "cards": []}
        uc_raw = pos[8].get("cards") or []
        uc_cards = clean_list([str(x) for x in uc_raw])
        if len(uc_cards) < 4:
            # borrow from agenda and two-column bullets
            donors = [pos[2]["bullets"], pos[4]["left"]["points"], pos[4]["right"]["points"]]
            for src in donors:
                for x in clean_list(src):
                    if x not in uc_cards:
                        uc_cards.append(x)
                        if len(uc_cards) >= 6:
                            break
                if len(uc_cards) >= 4:
                    break
        uc_cards = ensure_min(uc_cards[:6], 4)
        pos[8]["cards"] = uc_cards
        # Slide 9 (Takeaways): points
        # Takeaways: ensure captions available
        pos[9] = pos.get(9) or {"title": "Takeaways", "points": []}
        pos[9]["points"] = sanitize_for_takeaways(
            pos[9].get("points") or [],
            fallback_sources=[pos[4]["left"]["points"], pos[4]["right"]["points"], pos[2]["bullets"]],
        )
        # Slide 10 (Thank You)
        pos[10] = pos.get(10) or {"title": "Thank You", "cta": (data.get("cta") or "")}

        # Build PPTX locally
        import uuid
        folder = uuid.uuid4().hex[:8]
        out_dir = os.path.join(PREVIEW_ROOT, folder)
        os.makedirs(out_dir, exist_ok=True)
        pptx_name = f"deck_{folder}.pptx"
        pptx_path = os.path.join(out_dir, pptx_name)
        build_pptx_from_positions(pos, pptx_path, theme_name=theme_name)

        download_url = url_for("main.download_file", folder=folder, fname=pptx_name, _external=True)

        return jsonify({
            "ok": True,
            "download_url": download_url,
            "pptx_path": pptx_name,
            "folder": folder,
            # deck_id retained for future features but not used for previews anymore
            "deck_id": folder
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# Preview-related endpoints removed
