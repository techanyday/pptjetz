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
from app.models import User, BlogPost
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
    """Serve ads.txt from the static directory at the site root."""
    return send_from_directory(current_app.static_folder, "ads.txt")


@bp.get("/about")
def about() -> Any:
    return render_template("about_us.html")


@bp.get("/blog")
def blog_index() -> Any:
    """Blog index page rendering published posts from DB."""
    posts_q = BlogPost.query.filter(BlogPost.published.is_(True)).order_by(
        BlogPost.published_at.desc().nullslast(), BlogPost.created_at.desc()
    )
    posts = [
        {
            "title": p.title,
            "description": p.summary or (p.content[:140] + ("…" if len(p.content) > 140 else "")),
            "url": url_for("main.blog_post_page", slug=p.slug),
            "image": (url_for('static', filename=p.featured_image) if p.featured_image else None),
        }
        for p in posts_q.all()
    ]
    return render_template("blog_index.html", posts=posts)


@bp.get("/blog/<slug>")
def blog_post_page(slug: str) -> Any:
    p = BlogPost.query.filter(BlogPost.slug == slug)
    if not (getattr(current_user, "is_admin", False)):
        p = p.filter(BlogPost.published.is_(True))
    post = p.first()
    if not post:
        abort(404)
    return render_template("blog_post.html", post=post)


# ---------- Ads.txt (Ezoic-managed) ----------
@bp.get("/ads.txt")
def ads_txt_redirect() -> Any:
    """Redirect /ads.txt to Ezoic-managed ads.txt.

    If EZOIC_ADSTXT_URL is set in the environment, use it. Otherwise, default
    to the conventional Ezoic manager URL for pptjet.com.
    """
    import os
    target = os.environ.get(
        "EZOIC_ADSTXT_URL",
        "https://srv.adstxtmanager.com/19390/pptjet.com",
    )
    return redirect(target, code=301)


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

    # Multi-page change: persist slides and return deck_id + redirect URL
    import uuid
    deck_id = uuid.uuid4().hex[:8]
    
    # Derive a better deck title
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
        if title:
            return title
        # Last resort: derive from prompt text (first clause, title case-ish)
        prompt = (input_text or "").strip()
        if prompt:
            base = re.split(r"[\.!?\n]\s*", prompt, maxsplit=1)[0].strip()
            base = re.sub(r"^about\s+|^on\s+|^regarding\s+", "", base, flags=re.IGNORECASE)
            if len(base) > 80:
                base = base[:80].rsplit(" ", 1)[0]
            return base[:1].upper() + base[1:]
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
        title_slide = {
            "type": "title",
            "title": (s1.get("title") if isinstance(s1.get("title"), str) else data.get("deck_title")) or deck_title,
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
                while (len(left) < 3 or len(right) < 3) and guard < 6:
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
                while (len(left_pts) < 3 or len(right_pts) < 3) and guard < 6:
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
                while len(cards) < 5 and guard < 6:
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
        # Handle featured image upload (optional)
        img_rel_path = None
        file = request.files.get('featured_image')
        if file and file.filename:
            fname = secure_filename(file.filename)
            # store under static/uploads/blog/<slug>-<timestamp>-<fname>
            ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
            fname = f"{slug or 'post'}-{ts}-{fname}"
            dest_dir = os.path.join(current_app.static_folder, 'uploads', 'blog')
            os.makedirs(dest_dir, exist_ok=True)
            file.save(os.path.join(dest_dir, fname))
            img_rel_path = os.path.join('uploads', 'blog', fname).replace('\\', '/')
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
        # Handle optional featured image replacement
        file = request.files.get('featured_image')
        if file and file.filename:
            fname = secure_filename(file.filename)
            ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
            fname = f"{new_slug or 'post'}-{ts}-{fname}"
            dest_dir = os.path.join(current_app.static_folder, 'uploads', 'blog')
            os.makedirs(dest_dir, exist_ok=True)
            file.save(os.path.join(dest_dir, fname))
            post.featured_image = os.path.join('uploads', 'blog', fname).replace('\\', '/')
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
    # Optionally remove the featured image from disk
    try:
        if post.featured_image:
            img_path = os.path.join(current_app.static_folder, post.featured_image)
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
    """Aggregate per-user usage for the admin dashboard.

    Returns JSON like:
    { ok: true, rows: [{id, email, presentations, last_presentation}] }
    """
    _admin_guard()
    try:
        from sqlalchemy import func
        q = (
            db.session.query(
                User.id.label("id"),
                User.email.label("email"),
                func.count(PresentationLog.id).label("presentations"),
                func.max(PresentationLog.created_at).label("last_presentation"),
            )
            .outerjoin(PresentationLog, PresentationLog.user_id == User.id)
            .group_by(User.id)
            .order_by(func.max(PresentationLog.created_at).desc().nullslast(), User.email.asc())
        )
        usage = [
            {
                "user_id": r.id,
                "email": r.email,
                "presentations": int(r.presentations or 0),
                "last_date": (r.last_presentation.isoformat() if r.last_presentation else ""),
            }
            for r in q.all()
        ]
        # Match template expectation: { usage: [...] }
        return jsonify({"usage": usage})
    except Exception as exc:  # pragma: no cover
        # In dev, the table may not yet exist; avoid 500ing the dashboard
        return jsonify({"usage": [], "error": str(exc)}), 200


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
