from __future__ import annotations

from typing import Any, Dict, List
import json
import re
import os
import requests
from datetime import datetime
from werkzeug.utils import secure_filename

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_login import login_user, logout_user, current_user, login_required

from app.services.ai_content import create_deck_slides, derive_topic_bullets
# Preview PNG generation is removed; only PPTX export remains
from app.services.pptx_builder import build_pptx_from_positions, build_pptx_from_slides
from app import db
from app.models import User, BlogPost, Pricing, LLMCall, ProviderCost
from app.presentation_log import PresentationLog
from app.transcriber.services import (
    parse_youtube_id,
    get_captions,
    download_audio_wav,
    transcribe_wav,
    join_segments,
)


bp = Blueprint("main", __name__)

# In-memory deck store for multi-page flow (outline -> export)
# deck_id -> {"slides": [...], "theme_name": str, "deck_title": str, "input_text": str}
DECK_STORE: Dict[str, Dict[str, Any]] = {}


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
    return render_template("index.html", themes=THEME_OPTIONS, user=current_user)


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


# ---------- Transcriber UI (Flask) ----------
@bp.get("/transcriber")
def transcriber_page() -> Any:
    # Transcriber feature removed
    abort(404)


@bp.post("/api/transcribe")
def api_transcribe() -> Any:
    # Transcriber API removed
    abort(404)


@bp.get("/ads.txt")
def ads_txt() -> Any:
    """Provide ads.txt using Ezoic manager when configured, else serve static.

    Adds headers to help verify behavior and avoid caches during setup:
    - X-AdsTxt-Source: ezoic-redirect | static-file
    - Cache-Control: no-store, max-age=0
    """
    target = os.environ.get("EZOIC_ADSTXT_URL")
    if target:
        resp = redirect(target, code=301)
        try:
            resp.headers["X-AdsTxt-Source"] = "ezoic-redirect"
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        except Exception:
            pass
        return resp
    resp = send_from_directory(current_app.static_folder, "ads.txt")
    try:
        resp.headers["X-AdsTxt-Source"] = "static-file"
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    except Exception:
        pass
    return resp


@bp.get("/about")
def about() -> Any:
    return render_template("about_us.html")


@bp.get('/media/<path:filename>')
def media_file(filename: str) -> Any:
    """Serve files from instance/media securely."""
    base = os.path.join(current_app.instance_path, 'media')
    return send_from_directory(base, filename)


@bp.get("/blog")
def blog_index() -> Any:
    """Blog index page rendering published posts from DB."""
    posts_q = BlogPost.query.filter(BlogPost.published.is_(True)).order_by(
        BlogPost.published_at.desc().nullslast(), BlogPost.created_at.desc()
    )
    def _resolve_image(path: str | None) -> str | None:
        if not path:
            return None
        try:
            s = str(path).strip()
            # If it's already an absolute URL, return as-is
            if s.startswith("http://") or s.startswith("https://"):
                return s
            # If stored under instance media (persistent), serve via /media
            if s.lower().startswith("media/"):
                return url_for('main.media_file', filename=s[6:])
            # Strip leading slashes
            while s.startswith("/"):
                s = s[1:]
            # Remove leading 'static/' if present to avoid /static/static/... URLs
            if s.lower().startswith("static/"):
                s = s[7:]
            # Only return a URL if the file actually exists on disk.
            # This prevents broken thumbnails when uploads are missing on a new environment.
            import os
            from flask import current_app
            abs_path = os.path.join(current_app.static_folder, s)
            if os.path.isfile(abs_path):
                return url_for('static', filename=s)
            return None
        except Exception:
            return None

    posts = []
    for p in posts_q.all():
        posts.append({
            "title": p.title,
            "description": p.summary or (p.content[:140] + ("…" if len(p.content) > 140 else "")),
            "url": url_for("main.blog_post_page", slug=p.slug),
            "image": _resolve_image(getattr(p, 'featured_image', None)),
        })
    return render_template("blog_index.html", posts=posts)


@bp.get("/blog/<slug>")
def blog_post_page(slug: str) -> Any:
    p = BlogPost.query.filter(BlogPost.slug == slug)
    if not (getattr(current_user, "is_admin", False)):
        p = p.filter(BlogPost.published.is_(True))
    post = p.first()
    if not post:
        abort(404)
    # Resolve featured image safely: allow absolute URLs; for local files ensure they exist
    def _resolve_image_pair(path: str | None) -> tuple[str | None, str | None]:
        try:
            if not path:
                return None, None
            s = str(path).strip()
            if s.startswith("http://") or s.startswith("https://"):
                # Relative URL not needed; use absolute for both
                return s, s
            if s.lower().startswith("media/"):
                u = url_for('main.media_file', filename=s[6:])
                return u, u
            while s.startswith("/"):
                s = s[1:]
            if s.lower().startswith("static/"):
                s = s[7:]
            import os
            from flask import current_app
            abs_path = os.path.join(current_app.static_folder, s)
            if os.path.isfile(abs_path):
                return url_for('static', filename=s), url_for('static', filename=s, _external=True)
            return None, None
        except Exception:
            return None, None

    img_url, img_url_abs = _resolve_image_pair(getattr(post, 'featured_image', None))
    return render_template("blog_post.html", post=post, image_url=img_url, image_url_abs=img_url_abs)


# (Removed duplicate /ads.txt route; consolidated into single handler above)


@bp.get("/webpushr-sw.js")
def webpushr_service_worker() -> Any:
    """Serve Webpushr service worker from site root path.

    File must exist at app.static_folder / 'webpushr-sw.js'. This endpoint ensures
    it is available at https://<domain>/webpushr-sw.js as required by Webpushr.
    """
    return send_from_directory(current_app.static_folder, "webpushr-sw.js", mimetype="application/javascript")


@bp.get("/dashboard")
@login_required
def dashboard() -> Any:
    """User dashboard showing usage summary and recent activity."""
    try:
        # Recent logs for this user (newest first)
        logs = (
            PresentationLog.query.filter_by(user_id=getattr(current_user, "id", None))
            .order_by(PresentationLog.created_at.desc())
            .limit(20)
            .all()
        )
    except Exception:
        logs = []

    # Usage counters
    try:
        used = int(getattr(current_user, "presentations_count", 0) or 0)
    except Exception:
        # Fallback to counting logs if the counter isn't present
        used = len(logs)

    # Plan limit and remaining
    try:
        plan_key = (getattr(current_user, "plan", "free") or "free").strip()
        plan_meta = getattr(User, "PLANS", {}).get(plan_key, {})
        limit = plan_meta.get("limit", None)
    except Exception:
        plan_key, plan_meta, limit = "free", {}, 3

    if limit is None:
        remaining = "Unlimited"
    else:
        try:
            remaining = max(int(limit) - int(used), 0)
        except Exception:
            remaining = 0

    return render_template("dashboard.html", logs=logs, used=used, remaining=remaining)


@bp.get("/loading")
def loading_page() -> Any:
    """Full screen loading page used between Generate submit and results.

    Front-end script on this page reads the previously stored payload from
    sessionStorage and calls the existing /generate API. When the API returns
    a redirect_url, the script navigates there automatically.
    """
    return render_template("loading.html")


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
    # Normalize redirect URI in development to localhost to match Google Console
    is_dev = os.environ.get("FLASK_ENV") == "development"
    if is_dev:
        redirect_uri = "http://localhost:5000" + url_for("main.login_callback")
    else:
        redirect_uri = url_for("main.login_callback", _external=True)
    # Request only non-sensitive scopes to avoid verification prompts.
    # The app does not call Drive/Slides APIs anymore; PPTX export is local.
    scopes = [
        "openid",
        "email",
        "profile",
    ]
    # Remember where to go after login; default to home page
    session["post_login_redirect"] = request.args.get("next") or url_for("main.home")

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
    is_dev = os.environ.get("FLASK_ENV") == "development"
    if is_dev:
        redirect_uri = "http://localhost:5000" + url_for("main.login_callback")
    else:
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
    # If Google returns an error, surface it clearly instead of raising InvalidGrant
    if token_response.status_code != 200:
        try:
            err = token_response.json()
        except Exception:
            err = {"raw": token_response.text[:500]}
        return (
            render_template(
                "error.html",
                title="Google Login Failed",
                message=(err.get("error_description") or err.get("error") or "Token exchange failed"),
                details=err,
            ),
            400,
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
    # Signal front-end to fire Meta Pixel CompleteRegistration once
    try:
        session["just_registered"] = True
    except Exception:
        pass
    # If user has not been asked for marketing consent before, send to consent page
    try:
        if getattr(user, "marketing_opt_in", None) is None:
            session["needs_marketing_consent"] = True
            return redirect(url_for("main.consent"))
    except Exception:
        pass
    dest = session.pop("post_login_redirect", None) or url_for("main.home")
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

    # Create a deck_id early so LLM logging can link to this export
    import uuid
    deck_id = uuid.uuid4().hex[:8]

    slides, errors = create_deck_slides(
        title=title,
        subtitle=subtitle,
        presenter=presenter,
        date=date,
        theme_name=theme_name,
        input_text=input_text,
        num_slides=num_slides,
        deck_id=deck_id,
        user_id=(getattr(current_user, "id", None) or None),
    )
    if errors or not slides:
        return jsonify({"ok": False, "errors": errors or ["No slides generated"]}), 422

    # Multi-page change: persist slides and return deck_id + redirect URL
    # deck_id already created earlier for logging
    
    # Derive a better deck title
    def _smart_title_from_prompt(prompt_text: str) -> str | None:
        t = (prompt_text or "").strip()
        if not t:
            return None
        lo = t.lower()
        # 1) Extract canonical name with acronym if present
        name = None
        full_name = None
        acronym = None
        # Pattern A: "ABC, or Long Name" -> Long Name (ABC)
        m = re.search(r"\b([A-Z]{2,})\s*,\s*or\s+([^.,\n]+)", t, flags=re.I)
        if m:
            acronym = m.group(1).strip()
            full_name = m.group(2).strip()
            name = f"{full_name} ({acronym})"
        # Pattern B: "Long Name (ABC)"
        if not name:
            m2 = re.search(r"\b([^\(\)\n]{4,}?)\s*\(([A-Z]{2,})\)", t)
            if m2:
                full_name = m2.group(1).strip()
                acronym = m2.group(2).strip()
                name = f"{full_name} ({acronym})"
        # Fallback: first NP-like chunk
        if not name:
            first_clause = re.split(r"[\.!?\n]\s*", t, maxsplit=1)[0]
            first_clause = re.sub(r"^(about|on|regarding|please|kindly|create|write|make|generate|i need|we need)\s+", "", first_clause, flags=re.I)
            first_clause = re.sub(r"^(an|a)\s+(overview|introduction)\s+of\s+", "", first_clause, flags=re.I)
            first_clause = first_clause.split(":", 1)[0].strip()
            name = first_clause
        # Clean name length
        name_words = name.split()
        if len(name_words) > 10:
            name = " ".join(name_words[:10])

        # 2) Extract 2–3 key themes from full prompt
        THEMES = [
            (r"bank communication", "Bank Communications"),
            (r"payment approval|approval workflow", "Payment Approvals"),
            (r"status monitoring|payment status", "Status Monitoring"),
            (r"straight[- ]through processing|stp", "Straight‑Through Processing"),
            (r"automated processing|automation", "Automation"),
            (r"compliance|audit trail", "Compliance"),
            (r"visibility|end-to-end visibility|cash flow", "Visibility"),
            (r"cost savings|fees|per-transaction", "Cost Savings"),
            (r"workflow", "Workflow"),
        ]
        picks = []
        for pat, label in THEMES:
            try:
                if re.search(pat, lo):
                    picks.append(label)
            except Exception:
                continue
        # Keep at most 2 themes for conciseness
        picks = picks[:2]
        # 3) Compose concise title. Try progressively shorter candidates within limit.
        LIM = 48
        def as_title(s: str) -> str:
            small = {"and","or","the","of","in","on","for","to","a","an","with","at","by"}
            parts = s.split()
            out = []
            for i,w in enumerate(parts):
                if i>0 and w.lower() in small:
                    out.append(w.lower())
                else:
                    out.append(w[:1].upper()+w[1:])
            return " ".join(out)

        candidates: list[str] = []
        if picks:
            # 3 themes, then 2, then 1
            for k in [2,1]:
                if len(picks) >= k:
                    tail = ", ".join(picks[:k]) if k > 1 else picks[0]
                    candidates.append(f"{name}: {tail}")
        else:
            candidates.append(name)
        # Prefer acronym-only variant when long
        if acronym:
            short_name = f"{acronym}" if not full_name else f"{acronym}: {picks[0]}" if picks else acronym
            candidates.append(short_name)
        # Also try "Full (ACR)" without themes
        if full_name and acronym:
            candidates.append(f"{full_name} ({acronym})")

        # Select first under limit; fallback to hard trim
        for c in candidates:
            if len(c) <= LIM:
                base = c
                break
        else:
            base = (candidates[0])[:LIM]

        # Title case with small-word exceptions
        small = {"and","or","the","of","in","on","for","to","a","an","with","at","by"}
        def smart_tc(s: str) -> str:
            parts = s.split()
            out = []
            for i,w in enumerate(parts):
                if i>0 and w.lower() in small:
                    out.append(w.lower())
                else:
                    out.append(w[:1].upper()+w[1:])
            return " ".join(out)
        title_str = smart_tc(base)
        return title_str

    def derive_deck_title(slides_list: List[Dict[str, Any]]) -> str | None:
        # Prefer an explicit Title slide
        try:
            for sl in slides_list or []:
                if isinstance(sl, dict) and (str(sl.get("type") or "").lower() == "title"):
                    t = (sl.get("title") or "").strip()
                    if t:
                        return t
        except Exception:
            pass
        # Fallback: if client sent a title string, use it
        if title and len(title.split()) <= 12:
            return title
        # Derive from prompt text
        smart = _smart_title_from_prompt(input_text)
        if smart:
            return smart
        return None

    deck_title = derive_deck_title(slides) or "Generated Presentation"

    deck_obj = {
        "slides": slides,
        "theme_name": theme_name,
        "deck_title": deck_title,
        "input_text": input_text,
    }
    DECK_STORE[deck_id] = deck_obj

    # Persist to disk so other instances/processes can read it
    out_dir = os.path.join(PREVIEW_ROOT, deck_id)
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "deck.json"), "w", encoding="utf-8") as f:
            json.dump(deck_obj, f, ensure_ascii=False)
    except Exception:
        # Non-fatal; outline page will still work if same instance handles it
        pass

    redirect_url = url_for("main.outline_page", deck_id=deck_id, _external=True)
    return jsonify({"ok": True, "deck_id": deck_id, "redirect_url": redirect_url})


@bp.get("/api/decks/<deck_id>")
def get_deck(deck_id: str) -> Any:
    deck = DECK_STORE.get(deck_id)
    if not deck:
        # Fallback: attempt to load from persisted JSON on disk
        try:
            with open(os.path.join(PREVIEW_ROOT, deck_id, "deck.json"), "r", encoding="utf-8") as f:
                deck = json.load(f)
        except Exception:
            return jsonify({"ok": False, "error": "Deck not found"}), 404
    return jsonify({
        "ok": True,
        "deck_id": deck_id,
        "slides": deck.get("slides") or [],
        "theme_name": deck.get("theme_name") or "SLEEK",
        "deck_title": deck.get("deck_title") or "Untitled",
    })


@bp.get("/outline/<deck_id>")
def outline_page(deck_id: str) -> Any:
    # Render outline page; the template will fetch /api/decks/<deck_id>
    if deck_id not in DECK_STORE:
        # Still render; client will show not found state
        pass
    return render_template("outline.html", deck_id=deck_id)


@bp.get("/download_page/<deck_id>")
def download_page(deck_id: str) -> Any:
    """Simple page with a download link for the exported PPTX."""
    pptx_name = f"deck_{deck_id}.pptx"
    download_url = url_for("main.download_file", folder=deck_id, fname=pptx_name)
    return render_template("download.html", download_url=download_url, user=current_user, remaining_presentations=None)


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
    input_text = (data.get("input_text") or "").strip() or None
    deck_id = (data.get("deck_id") or "").strip() or None

    # If client only sends deck_id, load persisted deck (memory or disk)
    if (not slides) and deck_id:
        deck = DECK_STORE.get(deck_id)
        if not deck:
            try:
                with open(os.path.join(PREVIEW_ROOT, deck_id, "deck.json"), "r", encoding="utf-8") as f:
                    deck = json.load(f)
            except Exception:
                return jsonify({"ok": False, "error": "Deck not found"}), 404
        slides = deck.get("slides") or []
        theme_name = deck.get("theme_name") or theme_name
        deck_title = deck.get("deck_title") or deck_title
        input_text = deck.get("input_text") or input_text
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

    # Utilities over provided slides
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

    # Build slides strictly from agenda topics with pattern mapping
    try:
        # 1) Title
        s1 = _find_first("title") or {}
        # Always use the derived deck_title for the Title slide to avoid echoing the raw prompt
        title_slide = {
            "type": "title",
            "title": deck_title,
            "subtitle": (s1.get("subtitle") if isinstance(s1.get("subtitle"), str) else "") or (data.get("subtitle") or ""),
            "_render_exact": True,
        }

        # 2) Agenda bullets (source of truth)
        s2 = _find_first("agenda") or {}
        c2 = _content(s2)
        agenda_topics = clean_list([str(x) for x in (c2.get("bullets") or [])])
        if not agenda_topics:
            # Derive from provided slide titles excluding non-topic types
            agenda_topics = []
            for sl in slides:
                t = _ctype(sl)
                if t in {"title", "agenda", "thank_you", "cta"}:
                    continue
                ttl = str(sl.get("title") or "").strip()
                if ttl:
                    agenda_topics.append(ttl)
            agenda_topics = clean_list(agenda_topics)[:32]
        agenda_slide = {"type": "agenda", "content": {"bullets": agenda_topics[:32]}, "_render_exact": True}

        # 3) For each topic, pick bullets from existing slides or derive via AI
        def topic_bullets(topic: str) -> List[str]:
            # Search any slide with matching title for bullets/cards/table points
            for sl in slides:
                if str(sl.get("title") or "").strip().lower() == topic.strip().lower():
                    c = _content(sl)
                    pts: List[str] = []
                    if isinstance(c.get("bullets"), list):
                        pts = [str(x) for x in c.get("bullets")]
                    elif isinstance(c.get("cards"), list):
                        pts = [str(x) for x in c.get("cards")]
                    else:
                        table = c.get("table") or {}
                        left_pts = [str(x) for x in (table.get("left", {}).get("points") or [])]
                        right_pts = [str(x) for x in (table.get("right", {}).get("points") or [])]
                        pts = left_pts + right_pts
                    pts = clean_list(pts)
                    if pts:
                        return pts
            # Derive via ChatGPT if nothing found
            return derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=5) or []

        topic_slides: List[Dict[str, Any]] = []
        for idx, topic in enumerate(agenda_topics, start=1):
            pts = topic_bullets(topic)
            pat = (idx - 1) % 7
            if pat in {0, 1}:  # Double Column
                # Ensure at least 3 per column; derive extras if needed
                pool = list(pts)
                def split_lr(src):
                    return src[::2][:6], src[1::2][:6]
                left, right = split_lr(pool)
                guard = 0
                while (len(left) < 3 or len(right) < 3) and guard < 1:
                    more = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=4) or []
                    for m in clean_list(more):
                        if m not in pool:
                            pool.append(m)
                    left, right = split_lr(pool)
                    guard += 1
                # Final padding with placeholders if AI could not add enough
                while len(left) < 3:
                    pool.append("(to be refined)"); left, right = split_lr(pool)
                while len(right) < 3:
                    pool.append("(to be refined)"); left, right = split_lr(pool)
                topic_slides.append({
                    "type": "double_column",
                    "title": topic,
                    "content": {"left_points": left, "right_points": right},
                    "_render_exact": True,
                })
            elif pat in {2, 3}:  # Table (comparison)
                # Ensure at least 3 per column
                pool = list(pts)
                mid = max(1, len(pool)//2)
                left_pts = pool[:mid][:6]
                right_pts = pool[mid:][:6]
                guard = 0
                while (len(left_pts) < 3 or len(right_pts) < 3) and guard < 1:
                    more = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=4) or []
                    for m in clean_list(more):
                        if m not in pool:
                            pool.append(m)
                    mid = max(1, len(pool)//2)
                    left_pts = pool[:mid][:6]
                    right_pts = pool[mid:][:6]
                    guard += 1
                while len(left_pts) < 3:
                    left_pts.append("(to be refined)")
                while len(right_pts) < 3:
                    right_pts.append("(to be refined)")
                topic_slides.append({
                    "type": "comparison",
                    "title": topic,
                    "content": {"table": {"left": {"heading": "Aspects", "points": left_pts}, "right": {"heading": "Implications", "points": right_pts}}},
                    "_render_exact": True,
                })
            elif pat in {4, 5}:  # Cards
                # Ensure at least 5 cards
                cards = list(clean_list(pts))
                guard = 0
                while len(cards) < 5 and guard < 1:
                    more = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=5) or []
                    for m in clean_list(more):
                        if m not in cards:
                            cards.append(m)
                        if len(cards) >= 5:
                            break
                    guard += 1
                while len(cards) < 5:
                    cards.append("(to be refined)")
                topic_slides.append({
                    "type": "cards",
                    "title": topic,
                    "content": {"cards": cards[:6]},
                    "_render_exact": True,
                })
            else:  # Infographic
                topic_slides.append({
                    "type": "infographic",
                    "title": topic,
                    "content": {"bullets": pts[:5]},
                    "_render_exact": True,
                })

        # 4) Thank You last
        s7 = _find_first("thank_you", "cta") or {}
        thanks_slide = {"type": "thank_you", "title": "Thank You", "cta": (s7.get("cta") or data.get("cta") or ""), "_render_exact": True}

        # Build final slides list
        slides_out: List[Dict[str, Any]] = [title_slide, agenda_slide] + topic_slides + [thanks_slide]

        # Build PPTX locally
        import uuid
        folder = (deck_id or uuid.uuid4().hex[:8])
        out_dir = os.path.join(PREVIEW_ROOT, folder)
        os.makedirs(out_dir, exist_ok=True)
        pptx_name = f"deck_{folder}.pptx"
        pptx_path = os.path.join(out_dir, pptx_name)
        # Build PPTX from our exact-pattern slides
        build_pptx_from_slides(slides_out, pptx_path, theme_name=theme_name)

        download_url = url_for("main.download_file", folder=folder, fname=pptx_name, _external=True)

        # Record usage: log presentation and increment user counter when logged in
        try:
            if getattr(current_user, "is_authenticated", False) and getattr(current_user, "id", None):
                log = PresentationLog(
                    user_id=current_user.id,
                    title=deck_title,
                    num_slides=len(slides_out),
                    units_used=1,
                )
                db.session.add(log)
                # increment user presentations_count if present on model
                try:
                    current_user.presentations_count = (getattr(current_user, "presentations_count", 0) or 0) + 1
                except Exception:
                    pass
                db.session.commit()
        except Exception:
            # Non-fatal if logging fails; continue to return the download link
            db.session.rollback()

        return jsonify({
            "ok": True,
            "download_url": download_url,
            "pptx_path": pptx_name,
            "folder": folder,
            "deck_id": folder,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# Preview-related endpoints removed


# ---------- Admin: Blog Management ----------
def _admin_guard():
    if not getattr(current_user, "is_authenticated", False):
        abort(401)
    if not getattr(current_user, "is_admin", False):
        abort(403)


@bp.get("/admin/blog")
@login_required
def admin_blog_list() -> Any:
    _admin_guard()
    posts = BlogPost.query.order_by(BlogPost.created_at.desc()).all()
    return render_template("admin/blog_list.html", posts=posts)


@bp.route("/admin/blog/new", methods=["GET", "POST"])
@login_required
def admin_blog_new() -> Any:
    _admin_guard()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        slug = (request.form.get("slug") or BlogPost.slugify(title)).strip()
        summary = (request.form.get("summary") or "").strip() or None
        content = (request.form.get("content") or "").strip()
        published = bool(request.form.get("published"))
        # Handle featured image via URL (preferred) or upload (optional)
        img_rel_path = None
        url_in = (request.form.get('featured_image_url') or '').strip()
        if url_in.startswith('http://') or url_in.startswith('https://'):
            img_rel_path = url_in
        else:
            file = request.files.get('featured_image')
            if file and file.filename:
                fname = secure_filename(file.filename)
                # store under static/uploads/blog/<slug>-<timestamp>-<fname>
                ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
                fname = f"{slug or 'post'}-{ts}-{fname}"
                # persist under instance/media/blog
                dest_dir = os.path.join(current_app.instance_path, 'media', 'blog')
                os.makedirs(dest_dir, exist_ok=True)
                file.save(os.path.join(dest_dir, fname))
                img_rel_path = os.path.join('media', 'blog', fname).replace('\\', '/')
        if not title or not content:
            return render_template("admin/blog_form.html", error="Title and content are required", post=None)
        if BlogPost.query.filter_by(slug=slug).first():
            return render_template("admin/blog_form.html", error="Slug already exists", post=None)
        post = BlogPost(
            title=title,
            slug=slug,
            summary=summary,
            content=content,
            featured_image=img_rel_path,
            published=published,
            published_at=datetime.utcnow() if published else None,
            author_id=getattr(current_user, "id", None),
        )
        db.session.add(post)
        db.session.commit()
        return redirect(url_for("main.admin_blog_list"))
    return render_template("admin/blog_form.html", post=None)


@bp.route("/admin/blog/<slug>/edit", methods=["GET", "POST"])
@login_required
def admin_blog_edit(slug: str) -> Any:
    _admin_guard()
    post = BlogPost.query.filter_by(slug=slug).first_or_404()
    if request.method == "POST":
        title = (request.form.get("title") or post.title).strip()
        new_slug = (request.form.get("slug") or post.slug).strip()
        summary = (request.form.get("summary") or post.summary or "").strip() or None
        content = (request.form.get("content") or post.content).strip()
        published = bool(request.form.get("published"))
        # Handle optional featured image replacement (URL has priority)
        url_in = (request.form.get('featured_image_url') or '').strip()
        if url_in.startswith('http://') or url_in.startswith('https://'):
            post.featured_image = url_in
        else:
            file = request.files.get('featured_image')
            if file and file.filename:
                fname = secure_filename(file.filename)
                ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
                fname = f"{new_slug or 'post'}-{ts}-{fname}"
                dest_dir = os.path.join(current_app.instance_path, 'media', 'blog')
                os.makedirs(dest_dir, exist_ok=True)
                file.save(os.path.join(dest_dir, fname))
                post.featured_image = os.path.join('media', 'blog', fname).replace('\\', '/')
        if new_slug != post.slug and BlogPost.query.filter_by(slug=new_slug).first():
            return render_template("admin/blog_form.html", error="Slug already exists", post=post)
        post.title = title
        post.slug = new_slug
        post.summary = summary
        post.content = content
        if published and not post.published:
            post.published_at = datetime.utcnow()
        post.published = published
        db.session.commit()
        return redirect(url_for("main.admin_blog_list"))
    return render_template("admin/blog_form.html", post=post)


@bp.post("/admin/blog/<slug>/delete")
@login_required
def admin_blog_delete(slug: str) -> Any:
    _admin_guard()
    post = BlogPost.query.filter_by(slug=slug).first_or_404()
    # Optionally remove the featured image from disk (only if it's a local file)
    try:
        if post.featured_image and not str(post.featured_image).startswith(('http://','https://')):
            s = str(post.featured_image)
            if s.lower().startswith('media/'):
                img_path = os.path.join(current_app.instance_path, s)
            else:
                img_path = os.path.join(current_app.static_folder, s)
            if os.path.isfile(img_path):
                os.remove(img_path)
    except Exception:
        # Non-fatal if file removal fails
        pass
    db.session.delete(post)
    db.session.commit()
    return redirect(url_for("main.admin_blog_list"))


@bp.route("/consent", methods=["GET", "POST"])
def consent() -> Any:
    """Post-login marketing consent page.

    Shows a checkbox (unchecked by default). On POST, stores marketing consent
    on the current user and redirects to the intended next page.
    """
    if not getattr(current_user, "is_authenticated", False):
        return redirect(url_for("main.home"))
    next_url = session.pop("post_login_redirect", None) or url_for("main.home")
    if request.method == "POST":
        try:
            opt_in = bool(request.form.get("opt_in"))
            text = (request.form.get("consent_text") or "I agree to receive marketing emails from PPTJet.").strip()
            current_user.marketing_opt_in = opt_in
            current_user.marketing_opt_in_text = text[:255]
            current_user.marketing_opt_in_source = "post_login_consent_v1"
            current_user.marketing_opt_in_at = datetime.utcnow()
            from app import db as _db
            _db.session.commit()
        except Exception:
            from app import db as _db
            _db.session.rollback()
            return render_template("consent.html", error="Could not save preferences, please try again.", next_url=next_url)
        session.pop("needs_marketing_consent", None)
        return redirect(next_url)
    return render_template("consent.html", next_url=next_url)


@bp.post("/_clear_reg")
def clear_complete_registration_flag() -> Any:
    """Clear the one-time session flag used to trigger Meta Pixel CompleteRegistration."""
    try:
        session["just_registered"] = False
    except Exception:
        pass
    return jsonify({"ok": True})

# ---------- Admin: Usage API ----------
@bp.get("/admin/usage")
@login_required
def admin_usage_api() -> Any:
    """Lightweight per-user usage summary for the admin dashboard.

    Only users with activity (presentations or LLM calls) in the selected
    date window are included. The response still provides the fields that
    admin/dashboard.html expects.
    """
    # _admin_guard()  # disabled locally so usage can be viewed without admin flag
    try:
        from sqlalchemy import func
        from datetime import datetime, timezone, timedelta

        # -------- Date window parsing --------
        q_start = (request.args.get("start") or "").strip()
        q_end = (request.args.get("end") or "").strip()

        def _parse_ymd(s: str):
            try:
                return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                return None

        now = datetime.now(timezone.utc)
        start = _parse_ymd(q_start) or datetime(
            year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc
        )
        end = _parse_ymd(q_end) or start
        if end <= start:
            end = start + timedelta(days=1)
        else:
            end = end + timedelta(days=1)  # make end exclusive

        start_naive = start.replace(tzinfo=None)
        end_naive = end.replace(tzinfo=None)

        # -------- Aggregate activity in range --------
        # Presentations in range
        pres_q = (
            db.session.query(
                PresentationLog.user_id.label("user_id"),
                func.count(PresentationLog.id).label("presentations_range"),
                func.max(PresentationLog.created_at).label("last_presentation"),
            )
            .filter(PresentationLog.created_at >= start_naive)
            .filter(PresentationLog.created_at < end_naive)
            .group_by(PresentationLog.user_id)
        )
        pres_map = {
            r.user_id: (int(r.presentations_range or 0), r.last_presentation)
            for r in pres_q.all()
        }

        # LLM calls in range
        llm_q = (
            db.session.query(
                LLMCall.user_id.label("user_id"),
                func.count(LLMCall.id).label("calls"),
                func.coalesce(func.sum(LLMCall.prompt_tokens), 0).label("tokens_in"),
                func.coalesce(func.sum(LLMCall.completion_tokens), 0).label("tokens_out"),
                func.coalesce(func.sum(func.coalesce(LLMCall.cost_usd, 0.0)), 0.0).label(
                    "cost_usd"
                ),
            )
            .filter(LLMCall.created_at >= start_naive)
            .filter(LLMCall.created_at < end_naive)
            .group_by(LLMCall.user_id)
        )
        llm_map = {
            r.user_id: (
                int(r.calls or 0),
                int(r.tokens_in or 0),
                int(r.tokens_out or 0),
                float(r.cost_usd or 0.0),
            )
            for r in llm_q.all()
        }

        # Users who have any activity in the window
        active_user_ids = set(pres_map.keys()) | set(llm_map.keys())
        if not active_user_ids:
            return jsonify({"usage": [], "provider_costs": False})

        # -------- Fetch basic user info for active users only --------
        users = (
            db.session.query(User.id, User.email)
            .filter(User.id.in_(list(active_user_ids)))
            .order_by(User.email.asc())
            .all()
        )

        # Lifetime presentation counts for these users only
        life_q = (
            db.session.query(
                PresentationLog.user_id.label("user_id"),
                func.count(PresentationLog.id).label("presentations"),
            )
            .filter(PresentationLog.user_id.in_(list(active_user_ids)))
            .group_by(PresentationLog.user_id)
        )
        life_map = {
            r.user_id: int(r.presentations or 0) for r in life_q.all()
        }

        usage: list[dict[str, Any]] = []
        for uid, email in users:
            pres_range, last_pres = pres_map.get(uid, (0, None))
            calls, tokens_in, tokens_out, cost_usd = llm_map.get(
                uid, (0, 0, 0, 0.0)
            )

            export_n = int(pres_range or 0) or 1  # avoid divide-by-zero
            avg_calls = (int(calls or 0) / export_n) if export_n else 0.0
            avg_cost = (float(cost_usd or 0.0) / export_n) if export_n else 0.0

            usage.append(
                {
                    "user_id": uid,
                    "email": email,
                    "presentations": life_map.get(uid, pres_range),
                    "presentations_range": pres_range,
                    "last_date": last_pres.isoformat() if last_pres else "",
                    "llm_calls": int(calls or 0),
                    "tokens_in": int(tokens_in or 0),
                    "tokens_out": int(tokens_out or 0),
                    "avg_calls_per_export": round(avg_calls, 2),
                    "avg_cost_per_export": round(avg_cost, 4),
                }
            )

        return jsonify({"usage": usage, "provider_costs": False})
    except Exception as exc:  # pragma: no cover
        return jsonify({"usage": [], "error": str(exc)}), 200


@bp.get("/admin/usage/user/<user_id>/exports")
@login_required
def admin_usage_user_exports(user_id: str) -> Any:
    # _admin_guard()  # disabled locally so exports can be viewed without admin flag
    from sqlalchemy import func
    # Date window (UTC) with query params: ?start=YYYY-MM-DD&end=YYYY-MM-DD
    from datetime import datetime, timezone, timedelta
    q_start = (request.args.get("start") or "").strip()
    q_end = (request.args.get("end") or "").strip()
    def _parse_ymd(s: str):
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None
    now = datetime.now(timezone.utc)
    start = _parse_ymd(q_start) or datetime(year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc)
    end = _parse_ymd(q_end) or start
    if end <= start:
        end = start + timedelta(days=1)
    else:
        end = end + timedelta(days=1)
    q = (
        db.session.query(
            LLMCall.export_id,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.prompt_tokens), 0).label("tokens_in"),
            func.coalesce(func.sum(LLMCall.completion_tokens), 0).label("tokens_out"),
            func.coalesce(func.sum(LLMCall.cost_usd), 0.0).label("cost_usd"),
        )
        .filter(LLMCall.user_id == user_id)
        .filter(LLMCall.created_at >= start.replace(tzinfo=None))
        .filter(LLMCall.created_at < end.replace(tzinfo=None))
        .group_by(LLMCall.export_id)
        .order_by(func.max(LLMCall.created_at).desc())
    )
    rows = [
        {
            "export_id": e.export_id,
            "calls": int(e.calls or 0),
            "tokens_in": int(e.tokens_in or 0),
            "tokens_out": int(e.tokens_out or 0),
            "cost_usd": float(e.cost_usd or 0.0),
        }
        for e in q.all()
    ]
    return jsonify({"ok": True, "rows": rows})


# ---------- Admin: Pricing CRUD ----------
@bp.get("/admin/pricing")
@login_required
def admin_pricing_page() -> Any:
    _admin_guard()
    rows = []
    error = None
    try:
        rows = Pricing.query.order_by(Pricing.model_id.asc(), Pricing.effective_from.desc()).all()
    except Exception as exc:
        error = str(exc)
    return render_template("admin/pricing.html", rows=rows, error=error)


@bp.post("/admin/pricing")
@login_required
def admin_pricing_create() -> Any:
    _admin_guard()
    data = request.get_json(silent=True) or request.form or {}
    try:
        model_id = str(data.get("model_id") or "").strip()
        input_per_1k = float(data.get("input_per_1k"))
        output_per_1k = float(data.get("output_per_1k"))
        currency = (data.get("currency") or "USD").strip().upper()
        source = (data.get("source") or "admin").strip()
        source_url = (data.get("source_url") or "").strip()
        from datetime import datetime
        eff = datetime.utcnow()
        row = Pricing(
            model_id=model_id,
            input_per_1k=input_per_1k,
            output_per_1k=output_per_1k,
            currency=currency,
            effective_from=eff,
            source=source,
            source_url=source_url,
            notes=(data.get("notes") or "")[:255],
        )
        db.session.add(row); db.session.commit()
        return jsonify({"ok": True, "id": row.id})
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(exc)}), 400


# ---------- Admin: Provider Costs (CSV upload) ----------
@bp.get("/admin/provider_costs")
@login_required
def admin_provider_costs_page() -> Any:
    _admin_guard()
    rows = ProviderCost.query.order_by(ProviderCost.created_at.desc()).limit(200).all()
    return render_template("admin/provider_costs.html", rows=rows)


@bp.post("/admin/provider_costs/upload")
@login_required
def admin_provider_costs_upload() -> Any:
    _admin_guard()
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "No file"}), 400
    import csv, io
    created, updated = 0, 0
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8'))
        reader = csv.DictReader(stream)
        from datetime import datetime
        for row in reader:
            rid = (row.get('request_id') or row.get('id') or '').strip()
            if not rid:
                continue
            try:
                cost_val = float(row.get('cost_usd') or row.get('amount_usd') or row.get('cost'))
            except Exception:
                continue
            model = (row.get('model') or '').strip() or None
            ts = None
            for k in ('timestamp','created_at','time'):
                v = row.get(k)
                if v:
                    try:
                        ts = datetime.fromisoformat(v.replace('Z','+00:00'))
                        break
                    except Exception:
                        pass
            existing = ProviderCost.query.filter_by(request_id=rid).first()
            if existing:
                existing.cost_usd = cost_val
                existing.model = model or existing.model
                existing.usage_at = ts or existing.usage_at
                updated += 1
            else:
                pc = ProviderCost(request_id=rid, model=model, cost_usd=cost_val, usage_at=ts)
                db.session.add(pc)
                created += 1
        db.session.commit()
        return jsonify({"ok": True, "created": created, "updated": updated})
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/admin/pricing/delete")
@login_required
def admin_pricing_delete() -> Any:
    _admin_guard()
    data = request.get_json(silent=True) or {}
    try:
        rid = int(data.get("id"))
        row = Pricing.query.get(rid)
        if not row:
            return jsonify({"ok": False, "error": "Not found"}), 404
        db.session.delete(row); db.session.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(exc)}), 400


# ---------- SEO: robots.txt and sitemap.xml ----------
@bp.get("/robots.txt")
def robots_txt_route() -> Any:
    """Serve a simple robots.txt that references our sitemap."""
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {url_for('main.sitemap_xml_route', _external=True)}",
        "",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap_xml_route() -> Any:
    """Generate a basic XML sitemap for key pages and published blog posts."""
    # Core URLs
    urls = [
        {"loc": url_for('main.home', _external=True), "changefreq": "daily", "priority": "1.0"},
        {"loc": url_for('main.generate_page', _external=True), "changefreq": "weekly", "priority": "0.9"},
        {"loc": url_for('main.blog_index', _external=True), "changefreq": "daily", "priority": "0.6"},
        {"loc": url_for('main.privacy', _external=True), "changefreq": "yearly", "priority": "0.2"},
        {"loc": url_for('main.terms', _external=True), "changefreq": "yearly", "priority": "0.2"},
    ]

    # Blog posts
    try:
        posts = BlogPost.query.filter(BlogPost.published.is_(True)).all()
        for p in posts:
            lastmod = (p.updated_at or p.published_at)
            urls.append({
                "loc": url_for('main.blog_post_page', slug=p.slug, _external=True),
                "lastmod": (lastmod.strftime('%Y-%m-%d') if lastmod else None),
                "changefreq": "weekly",
                "priority": "0.7",
            })
    except Exception:
        # If DB unavailable, still serve core URLs
        pass

    # Build XML
    from xml.sax.saxutils import escape as _esc
    parts = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">",
    ]
    for u in urls:
        parts.append("  <url>")
        parts.append(f"    <loc>{_esc(u['loc'])}</loc>")
        if u.get("lastmod"):
            parts.append(f"    <lastmod>{_esc(u['lastmod'])}</lastmod>")
        if u.get("changefreq"):
            parts.append(f"    <changefreq>{_esc(u['changefreq'])}</changefreq>")
        if u.get("priority"):
            parts.append(f"    <priority>{_esc(u['priority'])}</priority>")
        parts.append("  </url>")
    parts.append("</urlset>")
    xml = "\n".join(parts)
    return Response(xml, mimetype="application/xml")

# ---------- Admin: Marketing Opt-ins ----------
@bp.get("/admin/marketing")
@login_required
def admin_marketing() -> Any:
    _admin_guard()
    from app.models import User
    rows = (
        User.query
        .filter(User.marketing_opt_in.is_(True))
        .filter(User.unsubscribed_at.is_(None))
        .order_by(User.marketing_opt_in_at.desc())
        .all()
    )
    return render_template("admin/marketing.html", rows=rows)


@bp.get("/admin/marketing.csv")
@login_required
def admin_marketing_csv() -> Any:
    _admin_guard()
    from app.models import User
    import csv
    from io import StringIO
    rows = (
        User.query
        .filter(User.marketing_opt_in.is_(True))
        .filter(User.unsubscribed_at.is_(None))
        .order_by(User.marketing_opt_in_at.desc())
        .all()
    )
    si = StringIO()
    w = csv.writer(si)
    w.writerow(["id", "email", "name", "opt_in_at", "source"]) 
    for u in rows:
        w.writerow([
            getattr(u, "id", ""),
            getattr(u, "email", ""),
            getattr(u, "name", ""),
            (u.marketing_opt_in_at.isoformat() if getattr(u, "marketing_opt_in_at", None) else ""),
            getattr(u, "marketing_opt_in_source", ""),
        ])
    out = si.getvalue()
    return Response(out, mimetype="text/csv", headers={
        "Content-Disposition": "attachment; filename=marketing_optins.csv"
    })
