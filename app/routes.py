from __future__ import annotations



from typing import Any, Dict, List

import json

import re

import os

import glob

import shutil

import tempfile

import traceback

import threading

import time

import secrets

import requests

import sys

import platform

import uuid

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

    g,

)

from flask_login import login_user, logout_user, current_user, login_required



from app.services.ai_content import create_deck_slides, derive_topic_bullets, ENABLE_EXPORT_BULLETS_AI, derive_topic_comparison_table, ENABLE_EXPORT_COMPARISON_AI, derive_one_idea_intro, ENABLE_EXPORT_ONE_IDEA_AI

# Preview PNG generation is removed; only PPTX export remains

from app.services.pptx_builder import build_pptx_from_positions, build_pptx_from_slides

from app.services.preview import export_all_previews, _find_soffice, _soffice_diagnostics

from app.services.slide_finalizer import finalize_slides, debug_log_slides

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



PREVIEW_JOBS: Dict[str, Dict[str, Any]] = {}

PREVIEW_JOBS_LOCK = threading.Lock()


GENERATE_JOBS: Dict[str, Dict[str, Any]] = {}

GENERATE_JOBS_LOCK = threading.Lock()





THEME_OPTIONS = [

    "SLEEK",

    "PULSE",

    "MINIMAL FLUX",

    "BLUE HORIZON",

    "prime_meridian",

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





@bp.get("/api/diagnostics/soffice")

def api_diagnostics_soffice() -> Any:

    expected = (os.environ.get("DIAGNOSTICS_TOKEN") or "").strip()

    if not expected:

        abort(404)

    provided = (request.headers.get("X-Diagnostics-Token") or request.args.get("token") or "").strip()

    if not provided or provided != expected:

        abort(404)

    soffice = _find_soffice()

    diag = _soffice_diagnostics()

    return jsonify({

        "ok": True,

        "soffice": soffice,

        "diagnostics": diag,

    })





@bp.get("/download_pptx/<deck_id>")

def download_pptx(deck_id: str):

    theme = (request.args.get("theme") or "").strip()

    if not theme:

        try:

            with open(os.path.join(PREVIEW_ROOT, deck_id, "deck.json"), "r", encoding="utf-8") as f:

                deck = json.load(f)

            theme = (deck.get("theme_name") or "SLEEK").strip()

        except Exception:

            theme = "SLEEK"

    theme_key = theme.upper()



    pptx_name = f"deck_{deck_id}.pptx"

    export_dir = os.path.join(PREVIEW_ROOT, deck_id, "exports", theme_key)

    pptx_path = os.path.join(export_dir, pptx_name)

    if not os.path.isfile(pptx_path):

        # Backwards compatibility for older single-path exports

        legacy_dir = os.path.join(PREVIEW_ROOT, deck_id)

        legacy_path = os.path.join(legacy_dir, pptx_name)

        if os.path.isfile(legacy_path):

            export_dir = legacy_dir

        else:

            abort(404)



    resp = send_from_directory(export_dir, pptx_name, as_attachment=True)

    try:

        resp.headers["Cache-Control"] = "no-store, max-age=0"

    except Exception:

        pass

    return resp





@bp.get("/preview_assets/<deck_id>/<path:fname>")

def preview_asset(deck_id: str, fname: str):

    return send_from_directory(os.path.join(PREVIEW_ROOT, deck_id), fname, as_attachment=False)



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





@bp.get("/investor-pitch-deck-generator")

def investor_pitch_deck_generator_page() -> Any:

    return render_template("investor_pitch_deck_generator.html")





@bp.get("/board-presentation-generator")

def board_presentation_generator_page() -> Any:

    return render_template("board_presentation_generator.html")





@bp.get("/ai-business-presentation-generator")

def ai_business_presentation_generator_page() -> Any:

    return render_template("ai_business_presentation_generator.html")





@bp.get("/ai-presentation-generator")

def ai_presentation_generator_page() -> Any:

    return render_template("ai_presentation_generator.html")




@bp.get("/create-timeline-slides-with-ai")

def create_timeline_slides_with_ai_page() -> Any:

    return render_template("create_timeline_slides_with_ai.html")





@bp.get("/ai-product-presentation-generator")

def ai_product_presentation_generator_page() -> Any:

    return render_template("ai_product_presentation_generator.html")





@bp.get("/ai-technical-presentation-generator")

def ai_technical_presentation_generator_page() -> Any:

    return render_template("ai_technical_presentation_generator.html")





@bp.get("/presentation-outline-generator")

def presentation_outline_generator_page() -> Any:

    return render_template("presentation_outline_generator.html")





@bp.get("/convert-text-to-powerpoint")

def convert_text_to_powerpoint_page() -> Any:

    return render_template("convert_text_to_powerpoint.html")





@bp.get("/pitch-deck-generator")

def pitch_deck_generator_page() -> Any:

    return render_template("pitch_deck_generator.html")





@bp.get("/generate-presentation-from-outline")

def generate_presentation_from_outline_page() -> Any:

    return render_template("generate_presentation_from_outline.html")





@bp.get("/generate-powerpoint-from-text")

def generate_powerpoint_from_text_page() -> Any:

    return render_template("generate_powerpoint_from_text.html")





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


@bp.get("/select-theme")
def select_theme_page() -> Any:
    from app.services.pptx_builder import THEME_PALETTES
    return render_template(
        "select_theme.html",
        themes=THEME_OPTIONS,
        palettes=THEME_PALETTES,
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

        redirect_uri = request.host_url.rstrip("/") + url_for("main.login_callback")

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

        redirect_uri = request.host_url.rstrip("/") + url_for("main.login_callback")

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

    raw_num = payload.get("num_slides")

    num_slides = 0

    try:

        if raw_num is None or str(raw_num).strip() == "":

            num_slides = 0

        else:

            num_slides = int(raw_num)

    except Exception:  # noqa: BLE001

        num_slides = 0

    if num_slides == 7:

        num_slides = 0



    # Default presenter to logged-in user's Google profile name if absent

    if not presenter and getattr(current_user, "is_authenticated", False):

        try:

            presenter = (current_user.name or "").strip() or None

        except Exception:

            presenter = None



    # Create a deck_id early so LLM logging can link to this export

    import uuid

    deck_id = uuid.uuid4().hex[:8]



    trace_id = None
    try:
        trace_id = getattr(g, "trace_id", None)
    except Exception:
        trace_id = None

    user_id = (getattr(current_user, "id", None) or None)
    app_obj = None
    try:
        app_obj = current_app._get_current_object()
    except Exception:
        app_obj = None

    # Start async job to avoid request timeouts (platform may return 502 for long sync requests).
    def _set_job_state(deck_id_in: str, state: dict) -> None:
        with GENERATE_JOBS_LOCK:
            GENERATE_JOBS[deck_id_in] = dict(state)

    _set_job_state(deck_id, {"status": "running", "deck_id": deck_id, "trace_id": trace_id, "started_at": time.time()})

    def _run_generation_job() -> None:
        def _fail(msg: str, errs: List[str] | None = None) -> None:
            st = {
                "status": "error",
                "deck_id": deck_id,
                "trace_id": trace_id,
                "error": msg,
                "errors": (errs or [msg]),
                "ended_at": time.time(),
            }
            _set_job_state(deck_id, st)

        try:
            if app_obj is not None:
                ctx = app_obj.app_context()
            else:
                ctx = None

            if ctx is not None:
                ctx.push()

            try:
                slides, errors = create_deck_slides(
                    title=title,
                    subtitle=subtitle,
                    presenter=presenter,
                    date=date,
                    theme_name=theme_name,
                    input_text=input_text,
                    num_slides=num_slides,
                    deck_id=deck_id,
                    user_id=user_id,
                )
            except Exception as exc:  # noqa: BLE001
                try:
                    current_app.logger.exception("generate_job:unhandled trace_id=%s deck_id=%s", trace_id, deck_id)
                except Exception:
                    pass
                _fail(f"Generation failed: {exc}", [f"Generation failed: {exc}"])
                return

            if errors or not slides:
                errs = errors or ["No slides generated"]
                msg0 = None
                try:
                    msg0 = str(errs[0]) if errs else None
                except Exception:
                    msg0 = None
                _fail(msg0 or "Generation failed", [str(x) for x in (errs or [])])
                return

            debug_log_slides("ai_raw", slides)

            # Derive a better deck title
            def _smart_title_from_prompt(prompt_text: str) -> str | None:
                t = (prompt_text or "").strip()
                if not t:
                    return None
                lo = t.lower()

                name = None
                full_name = None
                acronym = None

                m = re.search(r"\b([A-Z]{2,})\s*,\s*or\s+([^.,\n]+)", t, flags=re.I)
                if m:
                    acronym = m.group(1).strip()
                    full_name = m.group(2).strip()
                    name = f"{full_name} ({acronym})"
                if not name:
                    m2 = re.search(r"\b([^\(\)\n]{4,}?)\s*\(([A-Z]{2,})\)", t)
                    if m2:
                        full_name = m2.group(1).strip()
                        acronym = m2.group(2).strip()
                        name = f"{full_name} ({acronym})"
                if not name:
                    first_clause = re.split(r"[\.!?\n]\s*", t, maxsplit=1)[0]
                    first_clause = re.sub(r"^(about|on|regarding|please|kindly|create|write|make|generate|i need|we need)\s+", "", first_clause, flags=re.I)
                    first_clause = re.sub(r"^(an|a)\s+(overview|introduction)\s+of\s+", "", first_clause, flags=re.I)
                    first_clause = first_clause.split(":", 1)[0].strip()
                    name = first_clause

                name_words = name.split()
                if len(name_words) > 10:
                    name = " ".join(name_words[:10])

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
                picks = picks[:2]

                LIM = 48

                candidates: list[str] = []
                if picks:
                    for k in [2, 1]:
                        if len(picks) >= k:
                            tail = ", ".join(picks[:k]) if k > 1 else picks[0]
                            candidates.append(f"{name}: {tail}")
                else:
                    candidates.append(name)
                if acronym:
                    short_name = f"{acronym}" if not full_name else f"{acronym}: {picks[0]}" if picks else acronym
                    candidates.append(short_name)
                if full_name and acronym:
                    candidates.append(f"{full_name} ({acronym})")

                for c in candidates:
                    if len(c) <= LIM:
                        base = c
                        break
                else:
                    base = (candidates[0])[:LIM]

                small = {"and", "or", "the", "of", "in", "on", "for", "to", "a", "an", "with", "at", "by"}

                def smart_tc(s: str) -> str:
                    parts = s.split()
                    out_parts = []
                    for i2, w in enumerate(parts):
                        if i2 > 0 and w.lower() in small:
                            out_parts.append(w.lower())
                        else:
                            out_parts.append(w[:1].upper() + w[1:])
                    return " ".join(out_parts)

                return smart_tc(base)

            def derive_deck_title(slides_list: List[Dict[str, Any]]) -> str | None:
                try:
                    for sl in slides_list or []:
                        if isinstance(sl, dict) and (str(sl.get("type") or "").lower() == "title"):
                            t0 = (sl.get("title") or "").strip()
                            if t0:
                                return t0
                except Exception:
                    pass
                if title and len(title.split()) <= 12:
                    return title
                smart = _smart_title_from_prompt(input_text)
                if smart:
                    return smart
                return None

            deck_title = derive_deck_title(slides) or "Generated Presentation"

            deck_obj = {
                "deck_id": deck_id,
                "slides": slides,
                "slides_out": finalize_slides(slides, stage="post_generate"),
                "theme_name": theme_name,
                "deck_title": deck_title,
                "input_text": input_text,
            }

            DECK_STORE[deck_id] = deck_obj

            out_dir = os.path.join(PREVIEW_ROOT, deck_id)
            try:
                os.makedirs(out_dir, exist_ok=True)
                with open(os.path.join(out_dir, "deck.json"), "w", encoding="utf-8") as f:
                    json.dump(deck_obj, f, ensure_ascii=False)
            except Exception:
                pass

            redirect_url = f"/outline/{deck_id}"
            _set_job_state(deck_id, {
                "status": "done",
                "deck_id": deck_id,
                "trace_id": trace_id,
                "redirect_url": redirect_url,
                "ended_at": time.time(),
            })
        except Exception as exc:  # noqa: BLE001
            try:
                current_app.logger.exception("generate_job:crash trace_id=%s deck_id=%s", trace_id, deck_id)
            except Exception:
                pass
            _fail(f"Generation failed: {exc}", [f"Generation failed: {exc}"])
        finally:
            try:
                if "ctx" in locals() and ctx is not None:
                    ctx.pop()
            except Exception:
                pass

    try:
        th = threading.Thread(target=_run_generation_job, daemon=True)
        th.start()
    except Exception as exc:  # noqa: BLE001
        _set_job_state(deck_id, {"status": "error", "deck_id": deck_id, "trace_id": trace_id, "error": f"Failed to start generation: {exc}", "errors": [f"Failed to start generation: {exc}"], "ended_at": time.time()})
        return jsonify({"ok": False, "error": f"Failed to start generation: {exc}", "trace_id": trace_id, "deck_id": deck_id}), 500

    poll_url = f"/api/generate/{deck_id}"
    return jsonify({"ok": True, "deck_id": deck_id, "status": "processing", "trace_id": trace_id, "poll_url": poll_url})



@bp.get("/api/generate/<deck_id>")
def api_generate_status(deck_id: str) -> Any:
    """Return async generation job status.

    This endpoint always responds quickly to support polling from the loading screen.
    """
    with GENERATE_JOBS_LOCK:
        st = dict(GENERATE_JOBS.get(deck_id) or {})

    if st:
        st.setdefault("ok", True)
        return jsonify(st)

    # Fallback: if the deck exists, consider it done.
    try:
        if deck_id in DECK_STORE:
            redirect_url = f"/outline/{deck_id}"
            return jsonify({"ok": True, "status": "done", "deck_id": deck_id, "redirect_url": redirect_url})
    except Exception:
        pass

    try:
        if os.path.isfile(os.path.join(PREVIEW_ROOT, deck_id, "deck.json")):
            redirect_url = f"/outline/{deck_id}"
            return jsonify({"ok": True, "status": "done", "deck_id": deck_id, "redirect_url": redirect_url})
    except Exception:
        pass

    return jsonify({"ok": False, "status": "not_found", "deck_id": deck_id, "error": "Job not found"}), 404

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

        "deck_id": deck_id,

        "slides": slides,

        "slides_out": finalize_slides(slides, stage="post_generate"),

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



    redirect_url = f"/outline/{deck_id}"

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

        "slides_out": deck.get("slides_out") or [],

        "theme_name": deck.get("theme_name") or "SLEEK",

        "deck_title": deck.get("deck_title") or "Untitled",

    })





@bp.post("/api/decks/<deck_id>/theme")

def set_deck_theme(deck_id: str) -> Any:

    data = request.get_json(silent=True) or {}

    theme_name = (data.get("theme_name") or "").strip()

    if theme_name not in THEME_OPTIONS:

        return jsonify({"ok": False, "error": "Invalid theme"}), 400



    deck = DECK_STORE.get(deck_id)

    if not deck:

        try:

            with open(os.path.join(PREVIEW_ROOT, deck_id, "deck.json"), "r", encoding="utf-8") as f:

                deck = json.load(f)

        except Exception:

            return jsonify({"ok": False, "error": "Deck not found"}), 404



    deck["theme_name"] = theme_name

    DECK_STORE[deck_id] = deck



    try:

        out_dir = os.path.join(PREVIEW_ROOT, deck_id)

        os.makedirs(out_dir, exist_ok=True)

        with open(os.path.join(out_dir, "deck.json"), "w", encoding="utf-8") as f:

            json.dump(deck, f, ensure_ascii=False)

    except Exception:

        pass



    return jsonify({"ok": True, "deck_id": deck_id, "theme_name": theme_name})





@bp.get("/api/previews/<deck_id>")

def api_previews(deck_id: str) -> Any:

    refresh = (request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes"}
    full = (request.args.get("full") or "").strip().lower() in {"1", "true", "yes"}



    deck = DECK_STORE.get(deck_id)
    if not deck:
        try:
            with open(os.path.join(PREVIEW_ROOT, deck_id, "deck.json"), "r", encoding="utf-8") as f:
                deck = json.load(f)
        except Exception:
            deck = None

    deck_missing = deck is None
    if deck_missing:
        deck = {}

    theme_arg = (request.args.get("theme") or "").strip()
    theme_name = (theme_arg or (deck.get("theme_name") or "")).strip() or "SLEEK"
    if (not theme_arg) and (not (deck.get("theme_name") or "")):
        try:
            exports_root = os.path.join(PREVIEW_ROOT, deck_id, "exports")
            if os.path.isdir(exports_root):
                cand = [d for d in os.listdir(exports_root) if os.path.isdir(os.path.join(exports_root, d))]
                if cand:
                    theme_name = sorted(cand)[0]
        except Exception:
            pass
        try:
            previews_root = os.path.join(PREVIEW_ROOT, deck_id, "previews")
            if os.path.isdir(previews_root):
                cand = [d for d in os.listdir(previews_root) if os.path.isdir(os.path.join(previews_root, d))]
                if cand:
                    theme_name = sorted(cand)[0]
        except Exception:
            pass

    theme_key = theme_name.upper()



    expected_count = None

    try:

        slides_src = deck.get("slides_out") or deck.get("slides") or []

        if isinstance(slides_src, list) and slides_src:

            expected_count = len(slides_src)

    except Exception:

        expected_count = None



    trace_id = None

    try:

        trace_id = getattr(g, "trace_id", None)

    except Exception:

        trace_id = None



    preview_max = 0

    try:

        preview_max = int(os.getenv("PREVIEW_MAX_SLIDES") or "20")

    except Exception:

        preview_max = 20

    max_pages = None

    if (not full) and expected_count and expected_count > preview_max:

        max_pages = preview_max

        expected_count = preview_max



    pptx_name = f"deck_{deck_id}.pptx"

    pptx_path = os.path.join(PREVIEW_ROOT, deck_id, "exports", theme_key, pptx_name)

    if not os.path.isfile(pptx_path):

        # Backwards compatibility for older single-path exports

        legacy = os.path.join(PREVIEW_ROOT, deck_id, pptx_name)

        if os.path.isfile(legacy):

            pptx_path = legacy

    pptx_exists = bool(pptx_path and os.path.isfile(pptx_path))



    out_dir = os.path.join(PREVIEW_ROOT, deck_id, "previews", theme_key)

    os.makedirs(out_dir, exist_ok=True)



    job_key = f"{deck_id}:{theme_key}"



    def _job_state() -> dict:

        with PREVIEW_JOBS_LOCK:

            return dict(PREVIEW_JOBS.get(job_key) or {})



    def _set_job_state(state: dict) -> None:

        with PREVIEW_JOBS_LOCK:

            PREVIEW_JOBS[job_key] = dict(state)



    def _start_job_if_needed() -> dict:

        with PREVIEW_JOBS_LOCK:

            st = PREVIEW_JOBS.get(job_key)

            if isinstance(st, dict) and st.get("status") in {"running", "done"}:

                return dict(st)

            if isinstance(st, dict) and st.get("status") == "error":

                pass

            st = {

                "status": "running",

                "started_at": time.time(),

                "error": None,

                "trace": None,

            }

            PREVIEW_JOBS[job_key] = dict(st)



        def _run() -> None:

            try:

                try:

                    current_app.logger.info(

                        "preview:job_start trace_id=%s deck_id=%s theme=%s expected_count=%s max_pages=%s pptx=%s",

                        trace_id,

                        deck_id,

                        theme_key,

                        expected_count,

                        max_pages,

                        pptx_path,

                    )

                except Exception:

                    pass



                t0 = time.perf_counter()

                export_all_previews(pptx_path, out_dir, dpi=170, expected_count=expected_count, max_pages=max_pages)

                try:

                    current_app.logger.info("preview:job_done trace_id=%s ms=%s", trace_id, int((time.perf_counter() - t0) * 1000))

                except Exception:

                    pass

                _set_job_state({

                    "status": "done",

                    "started_at": st.get("started_at"),

                    "finished_at": time.time(),

                    "error": None,

                    "trace": None,

                })

            except Exception as exc:  # noqa: BLE001

                _set_job_state({

                    "status": "error",

                    "started_at": st.get("started_at"),

                    "finished_at": time.time(),

                    "error": str(exc),

                    "trace": traceback.format_exc(limit=12),

                })



        t = threading.Thread(target=_run, name=f"preview:{job_key}", daemon=True)

        t.start()

        return dict(st)



    def _read_meta() -> dict:

        try:

            meta_path = os.path.join(out_dir, "_meta.json")

            if os.path.isfile(meta_path):

                with open(meta_path, "r", encoding="utf-8") as f:

                    data = json.load(f)

                if isinstance(data, dict):

                    return data

        except Exception:

            pass

        return {}



    def _all_pngs(folder: str) -> list[str]:

        try:

            files = glob.glob(os.path.join(folder, "*"))

            return sorted([p for p in files if os.path.isfile(p) and p.lower().endswith(".png")])

        except Exception:

            return []



    def _slide_pngs(folder: str) -> list[str]:

        try:

            files = glob.glob(os.path.join(folder, "*"))

            out = []

            for p in files:

                if not os.path.isfile(p):

                    continue

                base = os.path.basename(p)

                if re.search(r"slide_(\d+)\.png$", base, flags=re.IGNORECASE):

                    out.append(p)

            return sorted(out)

        except Exception:

            return []



    def _num(p: str) -> int:

        base = os.path.basename(p)

        m = re.search(r"(\d+)", base)

        try:

            return int(m.group(1)) if m else 0

        except Exception:

            return 0



    def _normalize_to_slide_pngs(files: list[str]) -> list[str]:

        if not files:

            return []

        tmp_norm = None

        try:

            tmp_norm = tempfile.mkdtemp(prefix="norm_api_", dir=out_dir)

        except Exception:

            tmp_norm = None



        ordered = list(files)

        ordered.sort(key=_num)

        moved = []

        for i, src in enumerate(ordered, start=1):

            tmp_dst = os.path.join(tmp_norm, f"slide_{i}.png") if tmp_norm else os.path.join(out_dir, f"__tmp_slide_{i}.png")

            try:

                shutil.copy2(src, tmp_dst)

                moved.append(tmp_dst)

            except Exception:

                pass



        final_paths = []

        for p in moved:

            dst = os.path.join(out_dir, os.path.basename(p).replace("__tmp_", ""))

            try:

                if os.path.exists(dst):

                    os.remove(dst)

            except Exception:

                pass

            try:

                os.replace(p, dst)

                final_paths.append(dst)

            except Exception:

                pass



        try:

            if tmp_norm:

                shutil.rmtree(tmp_norm, ignore_errors=True)

        except Exception:

            pass

        return final_paths



    if not refresh:

        existing = _slide_pngs(out_dir)

        if existing:

            existing.sort(key=_num)

            slides = []

            for p in existing:

                n = _num(p)

                rel = os.path.join("previews", theme_key, os.path.basename(p)).replace("\\", "/")

                idx = n or (len(slides) + 1)

                slides.append({"index": idx, "n": idx, "url": url_for("main.preview_asset", deck_id=deck_id, fname=rel)})

            meta = _read_meta()

            return jsonify({

                "ok": True,

                "deck_id": deck_id,

                "theme_name": theme_name,

                "theme": theme_key,

                "slides": slides,

                "count": len(slides),

                "render_id": meta.get("render_id"),

                "converter": meta.get("converter"),

                "pptx_path": meta.get("pptx_path") or pptx_path,

                "pdf_path": meta.get("pdf_path"),

                "out_dir": meta.get("outdir") or out_dir,

            })



        st = _job_state()

        if st.get("status") == "running":

            return jsonify({

                "ok": True,

                "status": "processing",

                "deck_id": deck_id,

                "theme": theme_key,

            }), 202

        if st.get("status") == "error":

            return jsonify({

                "ok": False,

                "error": st.get("error") or "Preview generation failed",

                "debug": {

                    "theme": theme_key,

                    "deck_id": deck_id,

                    "pptx_path": pptx_path,

                    "out_dir": out_dir,

                    "trace": st.get("trace"),

                },

            }), 500



    if refresh:
        if not pptx_exists:
            if deck_missing:
                return jsonify({"ok": False, "error": "Deck not found"}), 404
            return jsonify({"ok": False, "error": "PPTX not found. Export the deck first."}), 404

        try:

            for fn in _all_pngs(out_dir):

                try:

                    os.remove(fn)

                except Exception:

                    pass

        except Exception:

            pass



        _start_job_if_needed()

        return jsonify({

            "ok": True,

            "status": "processing",

            "deck_id": deck_id,

            "theme": theme_key,

        }), 202



    st = _job_state()

    if st.get("status") == "error":

        return jsonify({

            "ok": False,

            "error": st.get("error") or "Preview generation failed",

            "debug": {

                "theme": theme_key,

                "deck_id": deck_id,

                "pptx_path": pptx_path,

                "out_dir": out_dir,

                "trace": st.get("trace"),

            },

        }), 500



    if st.get("status") != "running":
        if not pptx_exists:
            if deck_missing:
                return jsonify({"ok": False, "error": "Deck not found"}), 404
            return jsonify({"ok": False, "error": "PPTX not found. Export the deck first."}), 404

        _start_job_if_needed()

        return jsonify({

            "ok": True,

            "status": "processing",

            "deck_id": deck_id,

            "theme": theme_key,

        }), 202



    produced = _slide_pngs(out_dir)

    if not produced:

        any_pngs = _all_pngs(out_dir)

        produced = _normalize_to_slide_pngs(any_pngs)

        if not produced:

            return jsonify({

                "ok": True,

                "status": "processing",

                "deck_id": deck_id,

                "theme": theme_key,

            }), 202

    if expected_count and expected_count > 1 and len(produced) < 2:

        return jsonify({

            "ok": True,

            "status": "processing",

            "deck_id": deck_id,

            "theme": theme_key,

        }), 202



    produced.sort(key=_num)

    slides = []

    for p in produced:

        n = _num(p)

        rel = os.path.join("previews", theme_key, os.path.basename(p)).replace("\\", "/")

        idx = n or (len(slides) + 1)

        slides.append({"index": idx, "n": idx, "url": url_for("main.preview_asset", deck_id=deck_id, fname=rel)})



    meta = _read_meta()

    return jsonify({

        "ok": True,

        "deck_id": deck_id,

        "theme_name": theme_name,

        "theme": theme_key,

        "slides": slides,

        "count": len(slides),

        "render_id": meta.get("render_id"),

        "converter": meta.get("converter") or "soffice_pdf+pymupdf",

        "pptx_path": meta.get("pptx_path") or pptx_path,

        "pdf_path": meta.get("pdf_path"),

        "out_dir": meta.get("outdir") or out_dir,

    })





@bp.get("/outline/<deck_id>")

def outline_page(deck_id: str) -> Any:

    # Render outline page; the template will fetch /api/decks/<deck_id>

    if deck_id not in DECK_STORE:

        # Still render; client will show not found state

        pass

    return render_template("outline.html", deck_id=deck_id)





@bp.get("/preview/<deck_id>")

def preview_page(deck_id: str) -> Any:

    return render_template("preview.html", deck_id=deck_id, themes=THEME_OPTIONS)







@bp.get("/preview_loading/<deck_id>")

def preview_loading_page(deck_id: str) -> Any:

    return render_template("preview_loading.html", deck_id=deck_id)





@bp.get("/download_page/<deck_id>")

def download_page(deck_id: str) -> Any:

    """Simple page with a download link for the exported PPTX."""

    theme = (request.args.get("theme") or "").strip()

    if not theme:

        try:

            with open(os.path.join(PREVIEW_ROOT, deck_id, "deck.json"), "r", encoding="utf-8") as f:

                deck = json.load(f)

            theme = (deck.get("theme_name") or "SLEEK").strip()

        except Exception:

            theme = "SLEEK"

    theme_key = theme.upper()



    download_url = url_for("main.download_pptx", deck_id=deck_id, theme=theme_key)

    return render_template("download.html", download_url=download_url, user=current_user, remaining_presentations=None)





@bp.route("/export", methods=["POST"])

def export_presentation() -> Any:

    return _export_presentation_impl()





def _export_presentation_impl() -> Any:

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

    requested_theme_name = (data.get("theme_name") or "").strip() or None

    theme_name = (requested_theme_name or "SLEEK").strip()

    input_text = (data.get("input_text") or "").strip() or None

    deck_id = (data.get("deck_id") or "").strip() or None

    dry_run = str(data.get("dry_run") or "").strip().lower() in {"1", "true", "yes"}

    no_log = str(data.get("no_log") or "").strip().lower() in {"1", "true", "yes"}

    # Optional, internal-only mode used to showcase all infographic types deterministically.

    # Default is normal behaviour (no special showcase handling).

    mode = (data.get("mode") or "").strip().lower()



    ENABLE_EXPORT_SEMANTIC_SLIDE_ROUTER = os.getenv("DISABLE_EXPORT_SEMANTIC_SLIDE_ROUTER", "0") != "1"



    trace_id = None

    try:

        trace_id = getattr(g, "trace_id", None)

    except Exception:

        trace_id = None



    def _t0() -> float:

        try:

            return time.perf_counter()

        except Exception:

            return time.time()



    def _ms(dt: float) -> int:

        try:

            return int(dt * 1000)

        except Exception:

            return -1



    def _types_summary(arr: Any) -> dict:

        out: dict = {}

        if not isinstance(arr, list):

            return out

        for s in arr:

            if isinstance(s, dict):

                t = str(s.get("type") or "unknown").strip().lower() or "unknown"

            else:

                t = "non_dict"

            out[t] = int(out.get(t, 0)) + 1

        return out



    # Preview-only guardrails: preview_loading calls /export with no_log=1.

    preview_guardrails = bool(no_log)

    preview_max_slides = 0

    try:

        preview_max_slides = int(os.getenv("PREVIEW_MAX_SLIDES") or "20")

    except Exception:

        preview_max_slides = 20



    def _clamp_for_preview(sl_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:

        if not preview_guardrails:

            return sl_list

        max_bullets = 0

        try:

            max_bullets = int(os.getenv("PREVIEW_MAX_BULLETS") or "8")

        except Exception:

            max_bullets = 8

        out: List[Dict[str, Any]] = []

        for s in (sl_list or []):

            if not isinstance(s, dict):

                continue

            s2 = dict(s)

            c = s2.get("content")

            if isinstance(c, dict):

                c2 = dict(c)

                for k in ["bullets", "cards", "points", "items", "takeaways"]:

                    v = c2.get(k)

                    if isinstance(v, list) and len(v) > max_bullets:

                        c2[k] = v[:max_bullets]

                for k in ["left_points", "right_points"]:

                    v = c2.get(k)

                    if isinstance(v, list) and len(v) > max_bullets:

                        c2[k] = v[:max_bullets]

                s2["content"] = c2

            out.append(s2)

        return out



    try:

        current_app.logger.info(

            "export:start trace_id=%s deck_id=%s theme=%s slides_in=%s dry_run=%s types=%s",

            trace_id,

            deck_id,

            theme_name,

            (len(slides) if isinstance(slides, list) else "?"),

            dry_run,

            _types_summary(slides),

        )

    except Exception:

        pass



    # If client only sends deck_id, load persisted deck (memory or disk)

    deck_obj: Dict[str, Any] | None = None

    if (not slides) and deck_id:

        deck = DECK_STORE.get(deck_id)

        if not deck:

            try:

                with open(os.path.join(PREVIEW_ROOT, deck_id, "deck.json"), "r", encoding="utf-8") as f:

                    deck = json.load(f)

            except Exception:

                return jsonify({"ok": False, "error": "Deck not found"}), 404

        slides = deck.get("slides") or []

        theme_name = requested_theme_name or deck.get("theme_name") or theme_name

        deck_title = deck.get("deck_title") or deck_title

        input_text = deck.get("input_text") or input_text

        deck_obj = deck



    if deck_id and deck_obj is not None and (not (data.get("slides") or [])):

        cached = deck_obj.get("slides_out")

        if isinstance(cached, list) and cached:

            # Only trust cache if it looks like our rich routed slide objects.

            # Older/other flows may store plain LLM slide schema (e.g., type=text), which bypasses the rich router.

            rich_types = {

                "one_idea",

                "cards",

                "double_column",

                "comparison",

                "infographic",

                "impact_effort_matrix",

                "timeline",

                "roadmap",

                "swot",

                "kpi",

                "risk_matrix",

                "before_after",

                "architecture",

                "pricing",

            }

            try:

                has_rich = any(

                    isinstance(s, dict)

                    and (

                        str(s.get("type") or "").strip().lower() in rich_types

                    )

                    for s in cached

                )

            except Exception:

                has_rich = False



            if has_rich:

                t_stage = _t0()

                slides_out = finalize_slides(cached, stage="export_cached")

                try:

                    current_app.logger.info("export:stage finalize_cached trace_id=%s ms=%s", trace_id, _ms(_t0() - t_stage))

                except Exception:

                    pass

                slides_out = _clamp_for_preview(slides_out)

                if dry_run:

                    return jsonify({

                        "ok": True,

                        "deck_id": deck_id,

                        "deck_title": deck_title,

                        "theme_name": theme_name,

                        "slides": slides_out,

                        "count": len(slides_out),

                    })



                try:

                    import uuid

                    folder = (deck_id or uuid.uuid4().hex[:8])

                    pptx_name = f"deck_{folder}.pptx"

                    theme_key = (theme_name or "SLEEK").strip().upper()

                    out_dir = os.path.join(PREVIEW_ROOT, folder)

                    export_dir = os.path.join(out_dir, "exports", theme_key)

                    os.makedirs(export_dir, exist_ok=True)

                    pptx_path = os.path.join(export_dir, pptx_name)

                    try:

                        current_app.logger.info(

                            "export:build_pptx deck_id=%s theme=%s slides_out=%s out=%s",

                            deck_id,

                            theme_key,

                            (len(slides_out) if isinstance(slides_out, list) else "?"),

                            pptx_path,

                        )

                    except Exception:

                        pass

                    t_build = _t0()

                    try:

                        build_pptx_from_slides(slides_out, pptx_path, theme_name=theme_name)

                    except Exception as exc:  # noqa: BLE001

                        raise RuntimeError(f"export_stage=build_pptx_cached failed: {exc}")

                    try:

                        current_app.logger.info("export:stage build_pptx_cached trace_id=%s ms=%s", getattr(g, "trace_id", None), _ms(_t0() - t_build))

                    except Exception:

                        pass

                    download_url = url_for("main.download_pptx", deck_id=folder, theme=theme_key, _external=True)



                    if not no_log:

                        try:

                            if getattr(current_user, "is_authenticated", False) and getattr(current_user, "id", None):

                                log = PresentationLog(

                                    user_id=current_user.id,

                                    title=deck_title,

                                    num_slides=len(slides_out),

                                    units_used=1,

                                )

                                db.session.add(log)

                                try:

                                    current_user.presentations_count = (getattr(current_user, "presentations_count", 0) or 0) + 1

                                except Exception:

                                    pass

                                db.session.commit()

                        except Exception:

                            db.session.rollback()



                    return jsonify({

                        "ok": True,

                        "download_url": download_url,

                        "pptx_path": pptx_name,

                        "theme": theme_key,

                        "folder": folder,

                        "deck_id": folder,

                    })

                except Exception:  # noqa: BLE001

                    raise



        # If we already have *rich* slide objects persisted on the deck (i.e., already routed),

        # export them directly instead of regenerating.

        try:

            persisted = deck_obj.get("slides")

            if isinstance(persisted, list) and persisted:

                rich_types = {

                    "one_idea",

                    "cards",

                    "double_column",

                    "comparison",

                    "infographic",

                    "impact_effort_matrix",

                    "timeline",

                    "roadmap",

                    "swot",

                    "kpi",

                    "risk_matrix",

                    "before_after",

                    "architecture",

                    "pricing",

                }

                try:

                    has_rich = any(

                        isinstance(s, dict)

                        and (

                            str(s.get("type") or "").strip().lower() in rich_types

                        )

                        for s in persisted

                    )

                except Exception:

                    has_rich = False



                if has_rich:

                    t_stage = _t0()

                    slides_out = finalize_slides(persisted, stage="export_persisted")

                    try:

                        current_app.logger.info("export:stage finalize_persisted trace_id=%s ms=%s", getattr(g, "trace_id", None), _ms(_t0() - t_stage))

                    except Exception:

                        pass

                    slides_out = _clamp_for_preview(slides_out)

                    try:

                        deck_obj["slides_out"] = slides_out

                        DECK_STORE[deck_id] = deck_obj

                        out_dir2 = os.path.join(PREVIEW_ROOT, deck_id)

                        os.makedirs(out_dir2, exist_ok=True)

                        with open(os.path.join(out_dir2, "deck.json"), "w", encoding="utf-8") as f:

                            json.dump(deck_obj, f, ensure_ascii=False)

                    except Exception:

                        pass



                    if dry_run:

                        return jsonify({

                            "ok": True,

                            "deck_id": deck_id,

                            "deck_title": deck_title,

                            "theme_name": theme_name,

                            "slides": slides_out,

                            "count": len(slides_out),

                        })



                    import uuid

                    folder = (deck_id or uuid.uuid4().hex[:8])

                    pptx_name = f"deck_{folder}.pptx"

                    theme_key = (theme_name or "SLEEK").strip().upper()

                    out_dir3 = os.path.join(PREVIEW_ROOT, folder)

                    export_dir = os.path.join(out_dir3, "exports", theme_key)

                    os.makedirs(export_dir, exist_ok=True)

                    pptx_path = os.path.join(export_dir, pptx_name)

                    try:

                        current_app.logger.info(

                            "export:stage build_pptx_persisted_start trace_id=%s deck_id=%s theme=%s slides_out=%s out=%s",

                            trace_id,

                            deck_id,

                            theme_key,

                            len(slides_out),

                            pptx_path,

                        )

                    except Exception:

                        pass

                    t_build = _t0()

                    try:

                        build_pptx_from_slides(slides_out, pptx_path, theme_name=theme_name)

                    except Exception as exc:  # noqa: BLE001

                        raise RuntimeError(f"export_stage=build_pptx_persisted failed: {exc}")

                    try:

                        current_app.logger.info("export:stage build_pptx_persisted_done trace_id=%s ms=%s", trace_id, _ms(_t0() - t_build))

                    except Exception:

                        pass

                    download_url = url_for("main.download_pptx", deck_id=folder, theme=theme_key, _external=True)



                    return jsonify({

                        "ok": True,

                        "download_url": download_url,

                        "pptx_path": pptx_name,

                        "theme": theme_key,

                        "folder": folder,

                        "deck_id": folder,

                    })

        except Exception:

            pass

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



    def ensure_min(items, n):

        items = list(items or [])

        if len(items) >= n:

            return items

        return items



    def meaningful_headers(h1, h2, d1="Traditional", d2="AI-Driven"):

        h1 = (h1 or "").strip() or d1

        h2 = (h2 or "").strip() or d2

        if re.fullmatch(r"option\s*[ab]", h1.lower()):

            h1 = d1

        if re.fullmatch(r"option\s*[ab]", h2.lower()):

            h2 = d2

        return h1, h2



    def topic_comparison_headers(topic: str, deck_title: str) -> tuple[str, str]:

        t = (topic or "").lower()

        dt = (deck_title or "").lower()

        if any(k in t for k in ["drug", "substance", "opioid", "addiction"]):

            return "Risk Factors", "Protective Factors"

        if any(k in t for k in ["health", "disease", "care", "clinic", "hospital"]):

            return "Symptoms", "Interventions"

        if any(k in t for k in ["education", "learning", "school", "students", "curriculum"]):

            return "Traditional Methods", "Modern Strategies"

        if any(k in t for k in ["skill", "skills", "competency", "competencies", "training"]):

            return "Skill Areas", "Outcomes"

        if any(k in t for k in ["trend", "trends", "future", "emerging"]):

            return "Key Trends", "Opportunities"

        if any(k in t for k in ["market", "finance", "economy", "pricing", "growth"]):

            return "Challenges", "Opportunities"

        if any(k in t for k in ["policy", "regulation", "compliance", "governance"]):

            return "Current State", "Proposed Reforms"

        if any(k in t for k in ["covid", "pandemic", "crisis"]):

            return "Pre-Crisis", "Post-Crisis"

        if any(k in t for k in ["challenge", "challenges", "barrier", "barriers", "risk", "risks"]):

            return "Barriers", "Concerns"

        if any(k in t for k in ["impact", "impacts", "effects", "outcomes", "results"]):

            return "Drivers", "Impacts"

        if any(k in t for k in ["applications", "use cases", "use-cases", "usecase", "adoption"]):

            return "Use Cases", "Benefits"

        if any(k in t for k in ["strategy", "roadmap", "plan"]):

            return "Current State", "Future Direction"

        if any(k in dt for k in ["market", "finance", "economy", "pricing", "growth"]):

            return "Challenges", "Opportunities"

        return "Key Factors", "Outcomes"



    # --- Semantic eligibility for infographic intents (deterministic, keyword-based) ---



    def _topic_blob(topic: str, bullets: list[str]) -> str:

        parts = [str(topic or "").lower()]

        parts.extend(str(b or "").lower() for b in (bullets or []))

        return " ".join(parts)



    def _eligible_impact(blob: str) -> bool:

        if not blob:

            return False

        actors = [

            "stakeholder", "stakeholders", "user", "users", "end user", "end-user",

            "customer", "customers", "client", "clients", "audience", "audiences",

            "persona", "personas", "beneficiary", "beneficiaries", "team", "teams",

        ]

        outcomes = [

            "impact", "impacts", "outcome", "outcomes", "result", "results",

            "value", "roi", "return on investment", "kpi", "kpis", "metric", "metrics",

            "benefit", "benefits", "success metric", "performance indicator",

        ]

        if any(k in blob for k in actors) and any(k in blob for k in outcomes):

            return True

        if any(phrase in blob for phrase in ["who is affected", "who benefits", "who we serve", "where value is created"]):

            return True

        return False



    def _eligible_process(blob: str, bullets: list[str]) -> bool:

        if len(bullets or []) < 3:

            return False

        keys = [

            "step ", "steps", "phase", "phases", "roadmap", "timeline", "milestone", "milestones",

            "implementation", "rollout", "roll-out", "sequence", "lifecycle", "life cycle",

            "journey", "workflow", "process", "pipeline", "from \"", "then ", "after that",

        ]

        return any(k in blob for k in keys)



    def _eligible_gantt(blob: str, bullets: list[str]) -> bool:

        if len(bullets or []) < 4:

            return False

        timeline_terms = [

            "timeline", "roadmap", "milestone", "milestones", "project", "delivery", "deliverable",

            "execution", "implementation", "rollout", "launch plan", "plan", "schedule", "gantt",

        ]

        axis_terms = ["q1", "q2", "q3", "q4", "week", "weeks", "month", "months", "phase 1", "phase 2", "phase 3", "sprint"]

        if not any(t in blob for t in timeline_terms):

            return False

        if any(a in blob for a in axis_terms):

            return True

        return False



    def _eligible_bmc(blob: str) -> bool:

        if not blob:

            return False

        keys = [

            "business model", "revenue", "pricing", "monetization", "saas", "subscription",

            "startup", "go-to-market", "go to market", "gtm", "value proposition", "channels",

            "customer segments", "partners", "key partners",

        ]

        score = sum(1 for k in keys if k in blob)

        return score >= 2



    def _eligible_process_flow(blob: str, bullets: list[str]) -> bool:

        if len(bullets or []) < 4:

            return False

        flow_terms = ["workflow", "process", "pipeline", "handoff", "handover", "steps", "sequence", "system", "intake", "approval", "review"]

        numbered = any(re.match(r"^\s*(\d+\.|step\s*\d+|phase\s*\d+)", str(b or "").strip(), flags=re.I) for b in (bullets or []))

        if any(t in blob for t in flow_terms) and (numbered or _eligible_process(blob, bullets)):

            return True

        return False



    def _eligible_comparison_matrix(blob: str, bullets: list[str]) -> bool:

        if len(bullets or []) < 4:

            return False

        if "swot" in blob:

            return False

        compare_terms = ["compare", "comparison", "versus", "vs", "options", "alternatives", "evaluate", "selection", "tool", "tools", "framework", "approach", "approaches"]

        return any(t in blob for t in compare_terms)



    def _eligible_risk(blob: str) -> bool:

        if not blob:

            return False

        risk_words = [

            "risk", "risks", "risk management", "mitigate", "mitigation", "threat", "threats",

            "vulnerability", "vulnerabilities", "exposure", "downtime", "incident", "failure",

        ]

        challenge_words = ["challenge", "challenges", "issue", "issues", "trade-off", "tradeoffs", "trade offs"]

        governance_words = ["governance", "compliance", "regulation", "regulations", "policy", "policies"]

        if any(k in blob for k in risk_words):

            return True

        if any(k in blob for k in governance_words) and any(k in blob for k in ["uncertain", "uncertainty", "complex"]):

            return True

        if any(k in blob for k in challenge_words) and "risk" in blob:

            return True

        return False



    def _eligible_cycle(blob: str) -> bool:

        if not blob:

            return False

        keys = [

            "optimize", "optimization", "optimisation", "iterate", "iteration", "iterative",

            "continuous improvement", "continuous-improvement", "feedback loop", "feedback loops",

            "feedback", "monitor", "monitoring", "refine", "refinement", "improve", "improving",

            "loop", "loops", "cycle", "cycles", "learn", "learning", "measure and improve",

        ]

        return any(k in blob for k in keys)



    def _eligible_benefits(blob: str) -> bool:

        if not blob:

            return False

        benefit_words = [

            "benefit", "benefits", "advantage", "advantages", "value", "values", "value proposition",

            "gain", "gains", "positive impact", "outcomes", "results",

        ]

        layering_words = [

            "foundational", "foundation", "baseline", "core", "advanced", "maturity", "mature",

            "tier", "tiers", "level 1", "level 2", "level 3", "phase 1", "phase 2", "phase 3",

            "scale", "scaling", "scaled", "stack", "layer", "layers",

        ]

        if any(k in blob for k in benefit_words) and any(k in blob for k in layering_words):

            return True

        return False



    # Priority: prefer higher-value infographic intents when multiple are eligible

    _INTENT_PRIORITY: dict[str, int] = {

        "GANTT": 0,

        "BMC": 1,

        "PROCESS_FLOW": 2,

        "COMPARE": 3,

        "IMPACT": 4,

        "PROCESS": 5,

        "OPTIMIZATION": 6,

        "RISK": 7,

        "BENEFITS": 8,

    }



    def _primary_infographic_intent(topic: str, bullets: list[str]) -> str | None:

        blob = _topic_blob(topic, bullets)

        intents: list[str] = []

        if "swot" in blob:

            return None

        if _eligible_gantt(blob, bullets):

            intents.append("GANTT")

        if _eligible_bmc(blob):

            intents.append("BMC")

        if _eligible_process_flow(blob, bullets):

            intents.append("PROCESS_FLOW")

        if _eligible_comparison_matrix(blob, bullets):

            intents.append("COMPARE")

        if _eligible_impact(blob):

            intents.append("IMPACT")

        if _eligible_process(blob, bullets):

            intents.append("PROCESS")

        if _eligible_cycle(blob):

            intents.append("OPTIMIZATION")

        if _eligible_risk(blob):

            intents.append("RISK")

        if _eligible_benefits(blob):

            intents.append("BENEFITS")

        if not intents:

            return None

        intents.sort(key=lambda k: _INTENT_PRIORITY.get(k, 99))

        return intents[0]



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

        labels = [re.sub(r"(?i)^step\s*\d+\s*$", "", x).strip() for x in labels]

        return [x for x in labels if x]



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



    # Internal, non-user-visible classification of the deck's primary intent.

    # Used only to bias layout selection; never written into slide data.

    CATEGORY_PATTERNS: Dict[str, List[str]] = {

        # Project / Planning – emphasize process/timeline + action bullets

        "project_planning": [

            "cards",          # process / steps

            "double_column",  # action-oriented bullets

            "cards",

            "double_column",

            "cards",

            "double_column",

            "comparison",     # occasional structured comparison

        ],

        # Marketing / Growth – big visuals, funnels, icon+explainer, light on grids

        "marketing_growth": [

            "infographic",

            "double_column",

            "infographic",

            "cards",          # funnel / flow

            "double_column",

            "infographic",

            "comparison",     # limited structured contrast slide

        ],

        # Educational / Training – concept→explanation bullets + simple diagrams/steps

        "educational_training": [

            "double_column",

            "infographic",

            "double_column",

            "cards",

            "double_column",

            "infographic",

            "cards",

        ],

        # Business / Strategy – comparisons and structured insights; minimal playful layouts

        "business_strategy": [

            "comparison",

            "double_column",

            "comparison",

            "infographic",

            "comparison",

            "double_column",

            "comparison",

        ],

        # Technical / Product – flows, sequences, feature breakdowns with structured bullets

        "technical_product": [

            "cards",

            "double_column",

            "comparison",

            "cards",

            "double_column",

            "cards",

            "comparison",

        ],

        # Executive / Reporting – summary-first tables and minimal bullets; avoid processes

        "executive_reporting": [

            "comparison",

            "infographic",

            "comparison",

            "comparison",

            "infographic",

            "comparison",

            "comparison",

        ],

        # General / Mixed – neutral blend of bullets + simple infographics

        "general_mixed": [

            "double_column",

            "infographic",

            "double_column",

            "infographic",

            "comparison",

            "double_column",

            "infographic",

        ],

    }



    def _classify_deck_category(deck_title: str, input_text: str | None, agenda_topics: List[str]) -> str:

        """Return a single dominant category string for the deck.



        Classification is lightweight and deterministic, based only on keywords

        in the deck title / input text / agenda topics. The returned key is an

        internal hint for layout selection; it is never surfaced to users or

        written into slide structures.

        """

        parts: List[str] = []

        if deck_title:

            parts.append(str(deck_title))

        if input_text:

            parts.append(str(input_text))

        parts.extend([str(x) for x in (agenda_topics or [])])

        blob = " ".join(parts).lower()



        # Executive / Reporting: KPIs, quarterly updates, formal reports

        if any(k in blob for k in [

            "kpi", "key performance indicator", "quarter", "q1", "q2", "q3", "q4",

            "board", "executive", "leadership", "status update", "monthly report",

            "weekly report", "okr", "objective and key result", "forecast",

            "revenue", "profit", "loss", "p&l", "kpis", "metrics dashboard",

        ]):

            return "executive_reporting"



        # Project / Planning: plans, roadmaps, milestones, implementations

        if any(k in blob for k in [

            "project plan", "implementation plan", "rollout plan", "roadmap",

            "milestone", "milestones", "sprint", "backlog", "timeline",

            "deployment plan", "execution plan", "project charter", "workstream",

            "gantt", "deliverable", "implementation timeline",

        ]):

            return "project_planning"



        # Marketing / Growth: campaigns, funnels, go-to-market, acquisition

        if any(k in blob for k in [

            "campaign", "marketing", "growth", "acquisition", "lead gen",

            "lead generation", "brand", "branding", "awareness", "funnel",

            "go-to-market", "gotomarket", "g2m", "promotion", "ad copy",

            "advertising", "paid search", "social media", "seo", "content marketing",

        ]):

            return "marketing_growth"



        # Educational / Training: lessons, curriculum, workshops, learning outcomes

        if any(k in blob for k in [

            "training", "course", "lesson", "curriculum", "module", "syllabus",

            "workshop", "classroom", "students", "learning objective",

            "learning outcome", "education", "tutorial", "onboarding training",

        ]):

            return "educational_training"



        # Technical / Product: architecture, APIs, engineering-focused language

        if any(k in blob for k in [

            "architecture", "system design", "api", "endpoint", "database",

            "schema", "microservice", "micro-service", "infra", "infrastructure",

            "deployment", "pipeline", "devops", "kubernetes", "docker",

            "latency", "scalability", "throughput", "backend", "front-end",

            "frontend", "sdk", "integration", "algorithm", "ml", "machine learning",

            "model", "feature flag", "release notes", "product spec", "technical",

        ]):

            return "technical_product"



        # Business / Strategy: positioning, business model, vision, strategy

        if any(k in blob for k in [

            "strategy", "strategic", "business model", "positioning",

            "governance", "operating model", "vision", "mission", "competitive",

            "market analysis", "value proposition", "okrs", "objectives and key results",

        ]):

            return "business_strategy"



        return "general_mixed"



    def _topic_layout_for_index(idx: int, category: str) -> str:

        pattern = CATEGORY_PATTERNS.get(category) or CATEGORY_PATTERNS["general_mixed"]

        if not pattern:

            pattern = CATEGORY_PATTERNS["general_mixed"]

        pos = max(0, idx - 1) % len(pattern)

        return pattern[pos]



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

        agenda_topics = [t for t in agenda_topics if "swot" not in t.lower()]

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

            agenda_topics = clean_list(agenda_topics)

            agenda_topics = [t for t in agenda_topics if "swot" not in t.lower()][:32]

        agenda_slide = {"type": "agenda", "content": {"bullets": agenda_topics[:32]}, "_render_exact": True}



        # Classify deck intent once for deterministic, intent-driven layout selection.

        deck_category = _classify_deck_category(deck_title, input_text or "", agenda_topics)



        # 2b) One-Idea intro slide immediately after Agenda

        def _build_one_idea_slide() -> Dict[str, Any]:

            # Headline: prefer the overall deck title; fall back to first agenda topic

            headline_src = (deck_title or "").strip() or (agenda_topics[0] if agenda_topics else "Overview")

            headline = str(headline_src).strip()

            # Soft character cap to keep it readable on one or two lines

            if len(headline) > 140:

                h_trim = headline[:140]

                headline = (h_trim.rsplit(" ", 1)[0] or h_trim).rstrip(",.;:- ")



            # Explainer: natural, topic-level sentence in simple language

            # Prefer a short subject phrase rather than repeating the full title

            base_title = (deck_title or "").strip()

            first_topic = (agenda_topics[0].strip() if agenda_topics else "")

            src = base_title or first_topic or "This topic"



            expl = ""



            if ENABLE_EXPORT_ONE_IDEA_AI:

                ai_spec: Dict[str, Any] | None = None

                try:

                    ai_spec = derive_one_idea_intro(

                        deck_title=deck_title,

                        agenda_topics=agenda_topics,

                        input_text=input_text or "",

                        deck_id=deck_id,

                        user_id=(current_user.id if getattr(current_user, "is_authenticated", False) else None),

                    ) or None

                except Exception:

                    ai_spec = None

                if isinstance(ai_spec, dict):

                    ai_headline = str(ai_spec.get("headline") or "").strip()

                    ai_expl = str(ai_spec.get("explainer") or "").strip()

                    if ai_headline:

                        headline = ai_headline

                    if ai_expl:

                        expl = ai_expl



            if not expl:

                # Pattern: "The Impact of X on Y" -> "X is changing how Y ..."

                m = re.search(r"The Impact of (.+?) on (.+)", src, flags=re.IGNORECASE)

                if m:

                    tech = m.group(1).strip()

                    area = m.group(2).strip()

                    if "startup" in area.lower():

                        expl = f"{tech} is changing how startups build, launch, and grow products, from first ideas through scale-up."

                    else:

                        expl = f"{tech} is changing how {area.lower()} works and opening new ways to create value."

                elif agenda_topics:

                    # Use the first agenda item as a simple anchor

                    simple_topic = agenda_topics[0].strip()

                    expl = f"{simple_topic} focuses on what the idea is, why it matters, and how it changes everyday work."

                else:

                    # Generic fallback when we only know the deck title

                    subj = base_title or "this topic"

                    expl = f"{subj} is about the core idea, why it matters, and the difference it can make in practice."



            # Keep explainer reasonably tight

            if len(expl) > 230:

                e_trim = expl[:230]

                expl = (e_trim.rsplit(" ", 1)[0] or e_trim).rstrip(",.;:- ") + "…"



            return {

                "type": "one_idea",

                "title": headline,

                "content": {"headline": headline, "explainer": expl},

                "_render_exact": True,

            }



        one_idea_slide = _build_one_idea_slide()



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

                    if pts and not ENABLE_EXPORT_BULLETS_AI:

                        return pts

            # Derive via ChatGPT when enabled (preferred) and fall back to any existing points

            if ENABLE_EXPORT_BULLETS_AI:

                ai_pts = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=5) or []

                ai_pts = clean_list(ai_pts)

                if ai_pts:

                    return ai_pts

            return pts if 'pts' in locals() else []



        # First pass: collect topic specs with base layout and semantic infographic intent

        topic_specs: List[Dict[str, Any]] = []

        for idx, topic in enumerate(agenda_topics, start=1):

            pts = topic_bullets(topic)

            layout = _topic_layout_for_index(idx, deck_category)

            intent = _primary_infographic_intent(topic, pts)

            topic_specs.append({

                "idx": idx,

                "topic": topic,

                "pts": pts,

                "base_layout": layout,

                "intent": intent,

            })



        ai_router_cache: Dict[str, Dict[str, Any]] = {}



        router_counts: Dict[str, int] = {}

        router_recent: List[str] = []



        def _router_caps(total_topics: int) -> Dict[str, int]:

            # Policy:

            # - Avoid repeating slide types across the deck.

            # - Allow limited repeats for double_column (with visual rotation) and infographic (many variants).

            # - Encourage at least one comparison slide when suitable.

            double_cap = 2

            info_cap = 3 if total_topics >= 10 else 2

            comp_cap = 2 if total_topics >= 10 else 1

            return {

                "cards": 1,

                "double_column": double_cap,

                "comparison": comp_cap,

                "infographic": info_cap,

                "impact_effort_matrix": 1,

                "timeline": 1,

                "roadmap": 1,

                "swot": 1,

                "kpi": 1,

                "risk_matrix": 1,

                "before_after": 1,

                "architecture": 1,

                "pricing": 1,

            }



        def _ai_route_topic_layout(

            topic: str,

            pts: List[str],

            *,

            idx: int,

            total: int,

            allowed_override: set[str] | None = None,

            prefer_type: str | None = None,

        ) -> Dict[str, Any] | None:

            try:

                from app.utils.chatgpt_client import ChatGPTError, chat_completion

            except Exception:

                raise RuntimeError("OpenAI slide router unavailable: failed to import chatgpt_client")



            points = [str(x).strip() for x in (pts or []) if str(x).strip()]

            caps = _router_caps(total)

            state_sig = {

                "counts": router_counts,

                "recent": router_recent[-3:],

                "caps": caps,

                "idx": idx,

                "total": total,

            }

            cache_key = json.dumps({"t": str(topic or "").strip(), "p": points, "state": state_sig}, ensure_ascii=False)

            if cache_key in ai_router_cache:

                return ai_router_cache[cache_key]



            allowed_types = allowed_override or {

                "cards",

                "double_column",

                "comparison",

                "infographic",

                "impact_effort_matrix",

                "timeline",

                "roadmap",

                "swot",

                "kpi",

                "risk_matrix",

                "before_after",

                "architecture",

                "pricing",

            }



            def _as_list_str(v: Any, *, max_n: int) -> List[str]:

                if not isinstance(v, list):

                    return []

                out: List[str] = []

                for x in v:

                    s = str(x or "").strip()

                    if s:

                        out.append(s)

                    if len(out) >= max_n:

                        break

                return out



            def _validate_content(t: str, content: Dict[str, Any] | None) -> None:

                if t in {"cards", "double_column", "comparison", "infographic"}:

                    return

                if t == "impact_effort_matrix":

                    return

                if not isinstance(content, dict):

                    raise ValueError(f"Missing content object for type '{t}'")

                if t in {"timeline", "roadmap"}:

                    tl = content.get("timeline")

                    if not isinstance(tl, list) or len(tl) < 4:

                        raise ValueError(f"Type '{t}' requires content.timeline with 4-6 items")

                    ok = 0

                    for it in tl[:6]:

                        if isinstance(it, dict) and str(it.get("label") or "").strip():

                            ok += 1

                    if ok < 4:

                        raise ValueError(f"Type '{t}' requires 4-6 timeline items with non-empty label")

                elif t == "swot":

                    keys = ["strengths", "weaknesses", "opportunities", "threats"]

                    nonempty = 0

                    for k in keys:

                        q = content.get(k)

                        pts2 = None

                        if isinstance(q, dict):

                            pts2 = q.get("points")

                        if isinstance(pts2, list) and any(str(x).strip() for x in pts2):

                            nonempty += 1

                    if nonempty < 2:

                        raise ValueError("Type 'swot' requires at least 2 non-empty quadrants")

                elif t == "kpi":

                    metrics = content.get("metrics")

                    if not isinstance(metrics, list) or len(metrics) < 2:

                        raise ValueError("Type 'kpi' requires content.metrics with 2-4 items")

                    ok = 0

                    for m in metrics[:4]:

                        if not isinstance(m, dict):

                            continue

                        if str(m.get("label") or "").strip() and str(m.get("value") or "").strip():

                            ok += 1

                    if ok < 2:

                        raise ValueError("Type 'kpi' requires at least 2 metrics with label and value")

                elif t == "risk_matrix":

                    keys = [

                        "low_likelihood_low_impact",

                        "low_likelihood_high_impact",

                        "high_likelihood_low_impact",

                        "high_likelihood_high_impact",

                    ]

                    nonempty = 0

                    for k in keys:

                        q = content.get(k)

                        pts2 = None

                        if isinstance(q, dict):

                            pts2 = q.get("points")

                        if isinstance(pts2, list) and any(str(x).strip() for x in pts2):

                            nonempty += 1

                    if nonempty < 2:

                        raise ValueError("Type 'risk_matrix' requires at least 2 non-empty quadrants")

                elif t == "before_after":

                    b = content.get("before")

                    a = content.get("after")

                    if not (isinstance(b, dict) and isinstance(a, dict)):

                        raise ValueError("Type 'before_after' requires content.before and content.after objects")

                    bp = _as_list_str(b.get("points"), max_n=8)

                    ap = _as_list_str(a.get("points"), max_n=8)

                    if len(bp) < 3 or len(ap) < 3:

                        raise ValueError("Type 'before_after' requires at least 3 points in each column")

                elif t == "architecture":

                    boxes = content.get("boxes")

                    conns = content.get("connections")

                    if not isinstance(boxes, list) or len(boxes) < 4:

                        raise ValueError("Type 'architecture' requires content.boxes with 4-8 items")

                    if not isinstance(conns, list) or len(conns) < 2:

                        raise ValueError("Type 'architecture' requires content.connections with at least 2 items")

                    labels: List[str] = []

                    for b in boxes[:8]:

                        if isinstance(b, dict):

                            lab = str(b.get("label") or "").strip()

                            if lab:

                                labels.append(lab)

                    if len(labels) < 4:

                        raise ValueError("Type 'architecture' requires boxes with non-empty label")

                    ok2 = 0

                    for c in conns[:12]:

                        if not isinstance(c, dict):

                            continue

                        fb = str(c.get("from_box") or "").strip()

                        tb = str(c.get("to_box") or "").strip()

                        if fb in labels and tb in labels:

                            ok2 += 1

                    if ok2 < 2:

                        raise ValueError("Type 'architecture' requires connections referencing existing box labels")

                elif t == "pricing":

                    plans = content.get("plans")

                    if not isinstance(plans, list) or len(plans) < 2:

                        raise ValueError("Type 'pricing' requires content.plans with 2-4 items")

                    ok = 0

                    for p in plans[:4]:

                        if not isinstance(p, dict):

                            continue

                        if str(p.get("name") or "").strip() and isinstance(p.get("features"), list):

                            ok += 1

                    if ok < 2:

                        raise ValueError("Type 'pricing' requires at least 2 plans with name and features")

                else:

                    raise ValueError(f"Unsupported type '{t}'")



            system = (

                "You are a presentation slide-type router. Output ONLY valid JSON. "

                "Choose the best slide layout type for a single topic slide based on meaning, not keywords. "

                "Prefer visual slide types when they genuinely fit the slide title and content."

            )

            user_payload: Dict[str, Any] = {

                "task": "Select the best slide layout type for this slide.",

                "slide": {"title": str(topic or "").strip(), "bullets": points},

                "variety": {

                    "index": idx,

                    "total": total,

                    "counts": router_counts,

                    "recent": router_recent[-3:],

                    "caps": caps,

                    "rules": [

                        "Avoid using the same type twice in a row.",

                        "Do not exceed caps[type].",

                        "Prefer underused types when multiple choices are equally relevant.",

                    ],

                },

                "preference": {"prefer_type": (prefer_type or "")},

                "types": {

                    "cards": "A process/sequence/timeline/steps slide (chevrons / process cards).",

                    "double_column": "Two-column bullet slide for explanations or lists.",

                    "comparison": "Two-column comparison table (A vs B, options, pros/cons).",

                    "infographic": "Visual summary / map / diagram style slide.",

                    "impact_effort_matrix": "A 2x2 prioritization matrix with axes impact and effort/ease.",

                    "timeline": "A timeline with 4-6 labeled stages; will render with varied timeline templates.",

                    "roadmap": "A roadmap with 4-6 phases (title/date_range/description).",

                    "swot": "SWOT analysis with strengths/weaknesses/opportunities/threats.",

                    "kpi": "KPI metrics slide with 2-4 metric cards (label/value/caption).",

                    "risk_matrix": "Risk matrix (likelihood vs impact) with 2x2 quadrants.",

                    "before_after": "Before vs After transformation comparison.",

                    "architecture": "System architecture diagram with boxes and connections.",

                    "pricing": "Pricing/plan comparison with 2-4 plans and features.",

                },

                "rules": [

                    "Return schema: {type: <one of the keys above>, confidence: 0..1, content?: {...}, quadrants?: {...}}.",

                    "If preference.prefer_type is provided and is relevant, choose it.",

                    "Only choose impact_effort_matrix if the slide is truly about prioritizing items by impact and effort/ease.",

                    "If you choose impact_effort_matrix, you MUST output a quadrants object with keys: quick_easy_wins, major_projects, fill_in, thankless_tasks. Each value is an array of strings (0-6 items).",

                    "If you choose timeline or roadmap, you MUST output content.timeline = [{label, description, date_range?}] with 4-6 items.",

                    "If you choose timeline, you may optionally set content.timeline_variant to one of: horizontal, linear, vertical, roadmap, vchevrons.",

                    "Only choose timeline when the slide is explicitly chronological or stage-based (dates/quarters/phases/milestones). If it's just a list of items or categories, choose infographic/cards/double_column instead.",

                    "If you choose swot, you MUST output content.strengths/weaknesses/opportunities/threats each with {points:[...]} and at least 2 non-empty quadrants.",

                    "If you choose kpi, you MUST output content.metrics = [{label, value, caption?}] with 2-4 items.",

                    "If you choose risk_matrix, you MUST output content.low_likelihood_low_impact/low_likelihood_high_impact/high_likelihood_low_impact/high_likelihood_high_impact each with {points:[...]} and at least 2 non-empty quadrants.",

                    "If you choose before_after, you MUST output content.before and content.after each with {heading, points:[...]} and at least 3 points per side.",

                    "If you choose architecture, you MUST output content.boxes=[{label, description?}] (4-8) and content.connections=[{from_box,to_box,label?}] referencing existing labels.",

                    "If you choose pricing, you MUST output content.plans=[{name, price?, features:[...]}] with 2-4 plans.",

                    "If you choose infographic, you may optionally set content.infographic_intent or content.intent to one of: IMPACT_MAP_SIMPLE, IMPACT_MAP_COMPLEX, PROCESS_FLOW_CHEVRON, RISK_MATRIX_ADVANCED, LAYERED_BENEFITS_STACK, CYCLIC_OPTIMIZATION_LOOP, GANTT_CHART, BUSINESS_MODEL_CANVAS. You may also set content.impact_map_variant to one of: classic, radial, pyramid, two_sided, swimlane.",

                    "Avoid selecting comparison/double_column when a more visual type above is clearly applicable.",

                ],

            }



            last_err: str | None = None

            last_raw: str | None = None

            for attempt in range(2):

                # If we hit a cap, the repair attempt should be constrained to remaining eligible types

                # to avoid hard-failing export when the model keeps repeating an overused layout.

                allowed_types_attempt = set(allowed_types)

                if attempt == 1:

                    try:

                        eligible = {

                            t for t in allowed_types

                            if router_counts.get(t, 0) < caps.get(t, 99)

                        }

                        if router_recent:

                            eligible = {t for t in eligible if t != str(router_recent[-1])}

                        if eligible:

                            allowed_types_attempt = eligible

                    except Exception:

                        allowed_types_attempt = set(allowed_types)



                avoid: List[str] = []

                try:

                    if router_recent:

                        avoid.append(str(router_recent[-1]))

                    for t2, cap in caps.items():

                        if router_counts.get(t2, 0) >= cap:

                            avoid.append(t2)

                except Exception:

                    avoid = []

                user_payload["avoid_types"] = sorted(list({str(x) for x in avoid if str(x).strip()}))

                user_payload["allowed_types"] = sorted(list(allowed_types_attempt))



                messages = [

                    {"role": "system", "content": system},

                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},

                ]

                if attempt == 1 and last_err and last_raw is not None:

                    fix_payload = {

                        "task": "Repair your previous JSON to satisfy the schema and constraints exactly.",

                        "validation_error": last_err,

                        "previous_response": last_raw,

                        "slide": {"title": str(topic or "").strip(), "bullets": points},

                        "allowed_types": sorted(list(allowed_types_attempt)),

                        "avoid_types": user_payload.get("avoid_types") or [],

                        "variety": user_payload.get("variety") or {},

                    }

                    messages.append({"role": "user", "content": json.dumps(fix_payload, ensure_ascii=False)})



                try:

                    raw = chat_completion(

                        messages,

                        max_tokens=900,

                        temperature=0.0,

                        timeout=60,

                    )

                except ChatGPTError as exc:

                    raise RuntimeError(f"OpenAI slide router failed: {exc}")

                except Exception as exc:

                    raise RuntimeError(f"OpenAI slide router failed: {exc}")



                last_raw = raw



                try:

                    obj = json.loads(raw)

                except Exception:

                    last_err = f"Invalid JSON" 

                    continue

                if not isinstance(obj, dict):

                    last_err = f"Non-object JSON" 

                    continue



                out_type = str(obj.get("type") or "").strip().lower()

                try:

                    conf_v = float(obj.get("confidence") or 0.0)

                except Exception:

                    conf_v = 0.0



                if out_type not in allowed_types:

                    last_err = f"Invalid type '{out_type}'"

                    continue



                if out_type not in allowed_types_attempt:

                    # Second attempt is best-effort: we steer the model away from overused types by narrowing

                    # allowed_types_attempt, but we should not hard-fail export if it still picks a globally

                    # allowed type (e.g., double_column). Treat as acceptable on attempt 1.

                    if attempt == 0:

                        last_err = f"Invalid type '{out_type}' (not in allowed_types for this attempt)"

                        continue



                out: Dict[str, Any] = {"type": out_type, "confidence": conf_v}



                content_obj = obj.get("content")

                if isinstance(content_obj, dict):

                    out["content"] = content_obj



                if out_type == "impact_effort_matrix":

                    quads = obj.get("quadrants")

                    if not isinstance(quads, dict) or not quads:

                        last_err = "impact_effort_matrix missing quadrants"

                        continue

                    out["quadrants"] = quads



                # Enforce deck-level variety caps and no immediate repeats.

                # First attempt: strict (forces a reroute/repair if violated).

                # Second attempt: best-effort (still steered by avoid_types/caps, but do not hard-fail export).

                try:

                    # Always enforce caps to prevent overuse (e.g., timeline should not dominate a deck).

                    if router_counts.get(out_type, 0) >= caps.get(out_type, 99):

                        # If there are eligible alternatives remaining, enforce strictly.

                        # Otherwise accept best-effort to avoid hard-failing export.

                        try:

                            has_alts = any(

                                (t != out_type) and (router_counts.get(t, 0) < caps.get(t, 99))

                                for t in allowed_types

                            )

                        except Exception:

                            has_alts = False

                        if has_alts:

                            last_err = f"Variety violation: overused type '{out_type}'"

                            continue

                except Exception:

                    pass



                try:

                    if router_recent and out_type == str(router_recent[-1]):

                        last_err = f"Variety violation: repeated type '{out_type}'"

                        continue

                except Exception:

                    pass



                try:

                    _validate_content(out_type, out.get("content") if isinstance(out.get("content"), dict) else None)

                except Exception as exc:

                    last_err = str(exc)

                    continue



                ai_router_cache[cache_key] = out

                return out



            raise RuntimeError(f"OpenAI slide router could not produce a valid result for '{topic}': {last_err or 'unknown error'}")



        if not ENABLE_EXPORT_SEMANTIC_SLIDE_ROUTER:

            showcase_mode = mode == "showcase"

            eligible_specs = [s for s in topic_specs if s.get("intent")]



            for spec in topic_specs:

                spec["final_layout"] = spec["base_layout"]

                spec["use_infographic"] = False



            if showcase_mode:

                is_operational = deck_category in {"project_planning", "technical_product"}

                max_infographics = 4 if is_operational else 2

                candidates = [s for s in topic_specs if s.get("intent")]

                candidates.sort(key=lambda s: (_INTENT_PRIORITY.get(str(s.get("intent")), 99), s["idx"]))

                for spec in candidates[:max_infographics]:

                    spec["use_infographic"] = True

                    spec["final_layout"] = "infographic"

            else:

                is_operational = deck_category in {"project_planning", "technical_product"}

                max_infographics = 3 if is_operational else 2



                for spec in topic_specs:

                    if spec["base_layout"] == "infographic" and spec.get("intent"):

                        spec["use_infographic"] = True

                        spec["final_layout"] = "infographic"



                for spec in topic_specs:

                    if spec["base_layout"] == "infographic" and not spec.get("intent"):

                        pts = spec.get("pts") or []

                        spec["final_layout"] = "double_column" if len(pts) >= 4 else "cards"



                if is_operational:

                    for spec in topic_specs:

                        if spec.get("intent") in {"GANTT", "PROCESS_FLOW"}:

                            spec["final_layout"] = "infographic"



                current = [s for s in topic_specs if s.get("final_layout") == "infographic" and s.get("intent")]

                current.sort(key=lambda s: (_INTENT_PRIORITY.get(str(s.get("intent")), 99), s["idx"]))

                keep = set((s["idx"]) for s in current[:max_infographics])

                for spec in topic_specs:

                    if spec.get("final_layout") == "infographic" and spec.get("intent") and spec["idx"] not in keep:

                        pts = spec.get("pts") or []

                        spec["final_layout"] = "double_column" if len(pts) >= 4 else "cards"

                        spec["use_infographic"] = False



                total_eligible = len(eligible_specs)

                current_infographics = sum(1 for s in topic_specs if s.get("final_layout") == "infographic" and s.get("intent"))

                if total_eligible >= 1 and current_infographics < 1:

                    candidates = [s for s in topic_specs if s.get("intent")]

                    candidates.sort(key=lambda s: (_INTENT_PRIORITY.get(str(s.get("intent")), 99), s["idx"]))

                    pick = candidates[0] if candidates else None

                    if pick:

                        pick["use_infographic"] = True

                        pick["final_layout"] = "infographic"



        if ENABLE_EXPORT_SEMANTIC_SLIDE_ROUTER:

            for spec in topic_specs:

                spec["use_infographic"] = False

                routed = _ai_route_topic_layout(

                    str(spec.get("topic") or ""),

                    list(spec.get("pts") or []),

                    idx=int(spec.get("idx") or 1),

                    total=len(topic_specs),

                )

                rtype = str(routed.get("type") or "").strip().lower()

                spec["final_layout"] = rtype

                spec["use_infographic"] = (rtype == "infographic")

                rcontent = routed.get("content")

                if isinstance(rcontent, dict) and rcontent:

                    spec["ai_content"] = rcontent

                    if rtype == "infographic":

                        try:

                            ai_intent = str(rcontent.get("infographic_intent") or rcontent.get("intent") or "").strip()

                            if ai_intent:

                                spec["intent"] = ai_intent

                        except Exception:

                            pass

                if rtype == "impact_effort_matrix":

                    quads = routed.get("quadrants")

                    if not isinstance(quads, dict) or not quads:

                        raise RuntimeError(f"OpenAI slide router selected impact_effort_matrix but did not provide quadrants for '{spec.get('topic')}'")

                    spec["ai_quadrants"] = quads



                router_counts[rtype] = router_counts.get(rtype, 0) + 1

                router_recent.append(rtype)

                if len(router_recent) > 8:

                    router_recent = router_recent[-8:]



            # Ensure at least one comparison/table slide when suitable.

            if router_counts.get("comparison", 0) <= 0:

                try:

                    def _cand_score(sp: Dict[str, Any]) -> int:

                        t = str(sp.get("topic") or "").strip().lower()

                        pts0 = sp.get("pts") or []

                        n = len([p for p in pts0 if str(p).strip()])

                        score = 0

                        if n >= 6:

                            score += 3

                        if any(k in t for k in ["analysis", "compare", "comparison", "options", "approach", "aspects", "tradeoff", "trade-off", "vs", "versus", "across"]):

                            score += 4

                        if sp.get("final_layout") in {"double_column", "cards", "infographic"}:

                            score += 1

                        return score



                    candidates = sorted(topic_specs, key=_cand_score, reverse=True)

                    pick = candidates[0] if candidates else None

                    if pick and _cand_score(pick) >= 4:

                        r2 = _ai_route_topic_layout(

                            str(pick.get("topic") or ""),

                            list(pick.get("pts") or []),

                            idx=int(pick.get("idx") or 1),

                            total=len(topic_specs),

                            allowed_override={"comparison", "double_column", "infographic"},

                            prefer_type="comparison",

                        )

                        t2 = str(r2.get("type") or "").strip().lower()

                        if t2 == "comparison":

                            old = str(pick.get("final_layout") or "").strip().lower()

                            pick["final_layout"] = "comparison"

                            pick["use_infographic"] = False

                            pick.pop("ai_content", None)

                            try:

                                if old:

                                    router_counts[old] = max(0, int(router_counts.get(old, 0)) - 1)

                                router_counts["comparison"] = int(router_counts.get("comparison", 0)) + 1

                                pos = int(pick.get("idx") or 1) - 1

                                if 0 <= pos < len(router_recent):

                                    router_recent[pos] = "comparison"

                            except Exception:

                                pass

                except Exception:

                    pass



        # Second pass: build topic slides from the resolved layouts.

        topic_slides: List[Dict[str, Any]] = []

        for spec in topic_specs:

            topic = spec["topic"]

            pts = spec["pts"]

            layout = spec["final_layout"]

            intent = spec["intent"] if spec["use_infographic"] else None



            if layout == "impact_effort_matrix":

                q = spec.get("ai_quadrants")

                if isinstance(q, dict):

                    def _arr(k: str) -> list[str]:

                        v = q.get(k) or []

                        if not isinstance(v, list):

                            return []

                        return [str(x).strip() for x in v if str(x).strip()][:6]

                    q_obj = {

                        "quick_easy_wins": _arr("quick_easy_wins"),

                        "major_projects": _arr("major_projects"),

                        "fill_in": _arr("fill_in"),

                        "thankless_tasks": _arr("thankless_tasks"),

                    }

                    topic_slides.append({

                        "type": "impact_effort_matrix",

                        "title": topic,

                        "content": q_obj,

                        "_render_exact": True,

                    })

                    continue

                raise RuntimeError(f"Missing AI quadrants for impact_effort_matrix topic '{topic}'")



            if layout in {"timeline", "roadmap", "swot", "kpi", "risk_matrix", "before_after", "architecture", "pricing"}:

                c0 = spec.get("ai_content")

                if not isinstance(c0, dict) or not c0:

                    raise RuntimeError(f"Missing AI content for {layout} topic '{topic}'")

                c = dict(c0)

                if "title" not in c:

                    c["title"] = topic

                topic_slides.append({

                    "type": layout,

                    "title": topic,

                    "content": c,

                    "_render_exact": True,

                })

                continue



            if layout == "double_column":  # Double Column

                # Ensure at least 4 per column; derive extras if needed

                pool = list(pts)



                def split_lr(src):

                    return src[::2][:8], src[1::2][:8]



                left, right = split_lr(pool)

                guard = 0

                while (len(left) < 4 or len(right) < 4) and guard < 2 and ENABLE_EXPORT_BULLETS_AI:

                    more = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=6) or []

                    for m in clean_list(more):

                        if m not in pool:

                            pool.append(m)

                    left, right = split_lr(pool)

                    guard += 1

                if len(left) < 4 or len(right) < 4:

                    topic_slides.append({

                        "type": "infographic",

                        "title": topic,

                        "content": {"bullets": clean_list(pool)[:8]},

                        "_render_exact": True,

                    })

                else:

                    topic_slides.append({

                        "type": "double_column",

                        "title": topic,

                        "content": {"left_points": left, "right_points": right},

                        "_render_exact": True,

                    })

            elif layout == "comparison":  # Table (comparison)

                # Prefer an AI-structured comparison table when enabled

                table_spec = None

                if ENABLE_EXPORT_COMPARISON_AI:

                    table_spec = derive_topic_comparison_table(deck_title=deck_title, topic=topic, input_text=input_text, bullets=pts) or None

                if table_spec:

                    left_pts = clean_list(table_spec.get("left_points") or [])[:6]

                    right_pts = clean_list(table_spec.get("right_points") or [])[:6]

                    guard = 0

                    while (len(left_pts) < 3 or len(right_pts) < 3) and guard < 1 and ENABLE_EXPORT_BULLETS_AI:

                        more = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=6) or []

                        for m in clean_list(more):

                            if m not in left_pts and len(left_pts) < 6:

                                left_pts.append(m)

                            elif m not in right_pts and len(right_pts) < 6:

                                right_pts.append(m)

                        guard += 1

                    lh = (table_spec.get("left_heading") or "").strip()

                    rh = (table_spec.get("right_heading") or "").strip()

                    if not lh or not rh:

                        lh, rh = topic_comparison_headers(topic, deck_title)

                    if len(left_pts) < 3 or len(right_pts) < 3:

                        topic_slides.append({

                            "type": "infographic",

                            "title": topic,

                            "content": {"bullets": clean_list(left_pts + right_pts)[:8]},

                            "_render_exact": True,

                        })

                    else:

                        topic_slides.append({

                            "type": "comparison",

                            "title": topic,

                            "content": {"table": {"left": {"heading": lh, "points": left_pts}, "right": {"heading": rh, "points": right_pts}}},

                            "_render_exact": True,

                        })

                    continue

                # Ensure at least 3 per column

                pool = list(pts)

                mid = max(1, len(pool) // 2)

                left_pts = pool[:mid][:6]

                right_pts = pool[mid:][:6]

                guard = 0

                while (len(left_pts) < 3 or len(right_pts) < 3) and guard < 1 and ENABLE_EXPORT_BULLETS_AI:

                    more = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=4) or []

                    for m in clean_list(more):

                        if m not in pool:

                            pool.append(m)

                    mid = max(1, len(pool) // 2)

                    left_pts = pool[:mid][:6]

                    right_pts = pool[mid:][:6]

                    guard += 1

                left_head, right_head = topic_comparison_headers(topic, deck_title)

                if len(left_pts) < 3 or len(right_pts) < 3:

                    topic_slides.append({

                        "type": "infographic",

                        "title": topic,

                        "content": {"bullets": clean_list(left_pts + right_pts)[:8]},

                        "_render_exact": True,

                    })

                else:

                    topic_slides.append({

                        "type": "comparison",

                        "title": topic,

                        "content": {"table": {"left": {"heading": left_head, "points": left_pts}, "right": {"heading": right_head, "points": right_pts}}},

                        "_render_exact": True,

                    })

            elif layout == "cards":  # Cards / process steps

                # Ensure at least 4 cards

                cards = list(clean_list(pts))

                guard = 0

                while len(cards) < 4 and guard < 2 and ENABLE_EXPORT_BULLETS_AI:

                    more = derive_topic_bullets(deck_title=deck_title, topic=topic, input_text=input_text, n=6) or []

                    for m in clean_list(more):

                        if m not in cards:

                            cards.append(m)

                        if len(cards) >= 4:

                            break

                    guard += 1

                if len(cards) < 4:

                    topic_slides.append({

                        "type": "infographic",

                        "title": topic,

                        "content": {"bullets": clean_list(cards)[:8]},

                        "_render_exact": True,

                    })

                else:

                    topic_slides.append({

                        "type": "cards",

                        "title": topic,

                        "content": {"cards": cards[:6]},

                        "_render_exact": True,

                    })

            else:  # Infographic / semantic visual summary

                # Some structured infographics (e.g., Gantt/process/matrix/canvas)

                # can use more than 5 items when available.

                if intent in {"GANTT", "BMC", "PROCESS_FLOW", "COMPARE"}:

                    bullets = list(pts or [])[:18]

                else:

                    bullets = list(pts or [])[:5]

                content: Dict[str, Any] = {"bullets": bullets}

                # Prefer OpenAI-provided intent tags when present; otherwise fall back to coarse heuristic.

                ai_c = spec.get("ai_content")

                ai_tag = ""

                if isinstance(ai_c, dict):

                    ai_tag = str(ai_c.get("infographic_intent") or ai_c.get("intent") or "").strip()

                    if ai_tag:

                        content["infographic_intent"] = ai_tag

                        content["intent"] = ai_tag

                        v = str(ai_c.get("impact_map_variant") or ai_c.get("variant") or "").strip()

                        if v:

                            content["impact_map_variant"] = v

                if intent and not ai_tag:

                    # Map coarse intent to a specific infographic layout identifier

                    if intent == "GANTT":

                        tag = "GANTT_CHART"

                    elif intent == "BMC":

                        tag = "BUSINESS_MODEL_CANVAS"

                    elif intent == "PROCESS_FLOW":

                        tag = "PROCESS_FLOW"

                    elif intent == "COMPARE":

                        tag = "COMPARISON_MATRIX"

                    elif intent == "IMPACT":

                        tag = "IMPACT_MAP_COMPLEX" if len(bullets) > 4 else "IMPACT_MAP_SIMPLE"

                    elif intent == "PROCESS":

                        tag = "PROCESS_FLOW_CHEVRON"

                    elif intent == "RISK":

                        tag = "RISK_MATRIX_ADVANCED"

                    elif intent == "OPTIMIZATION":

                        tag = "CYCLIC_OPTIMIZATION_LOOP"

                    elif intent == "BENEFITS":

                        tag = "LAYERED_BENEFITS_STACK"

                    else:

                        tag = "IMPACT_MAP_SIMPLE"

                    content["infographic_intent"] = tag

                    content["intent"] = tag

                topic_slides.append({

                    "type": "infographic",

                    "title": topic,

                    "content": content,

                    "_render_exact": True,

                })



        # 4) Thank You last

        s7 = _find_first("thank_you", "cta") or {}

        thanks_slide = {"type": "thank_you", "title": "Thank You", "cta": (s7.get("cta") or data.get("cta") or ""), "_render_exact": True}



        # Build final slides list: Title → Agenda → One-Idea Intro → Topic slides → Thank You

        slides_out: List[Dict[str, Any]] = [title_slide, agenda_slide, one_idea_slide] + topic_slides + [thanks_slide]

        t_stage = _t0()

        slides_out = finalize_slides(slides_out, stage="export_final")

        try:

            current_app.logger.info("export:stage finalize_final trace_id=%s ms=%s", trace_id, _ms(_t0() - t_stage))

        except Exception:

            pass

        slides_out = _clamp_for_preview(slides_out)



        if deck_id and deck_obj is not None:

            try:

                deck_obj["slides_out"] = slides_out

                DECK_STORE[deck_id] = deck_obj

                out_dir = os.path.join(PREVIEW_ROOT, deck_id)

                os.makedirs(out_dir, exist_ok=True)

                with open(os.path.join(out_dir, "deck.json"), "w", encoding="utf-8") as f:

                    json.dump(deck_obj, f, ensure_ascii=False)

            except Exception:

                pass



        if dry_run:

            return jsonify({

                "ok": True,

                "deck_id": deck_id,

                "deck_title": deck_title,

                "theme_name": theme_name,

                "slides": slides_out,

                "count": len(slides_out),

            })



        # Build PPTX locally

        import uuid

        folder = (deck_id or uuid.uuid4().hex[:8])

        out_dir = os.path.join(PREVIEW_ROOT, folder)

        pptx_name = f"deck_{folder}.pptx"

        theme_key = (theme_name or "SLEEK").strip().upper()

        export_dir = os.path.join(out_dir, "exports", theme_key)

        os.makedirs(export_dir, exist_ok=True)

        pptx_path = os.path.join(export_dir, pptx_name)

        try:

            current_app.logger.info(

                "export:build_pptx deck_id=%s theme=%s slides_out=%s out=%s",

                deck_id,

                theme_key,

                (len(slides_out) if isinstance(slides_out, list) else "?"),

                pptx_path,

            )

        except Exception:

            pass

        # Build PPTX from our exact-pattern slides

        t_build = _t0()

        try:

            build_pptx_from_slides(slides_out, pptx_path, theme_name=theme_name)

        except Exception as exc:  # noqa: BLE001

            raise RuntimeError(f"export_stage=build_pptx_final failed: {exc}")

        try:

            current_app.logger.info("export:stage build_pptx_final trace_id=%s ms=%s", trace_id, _ms(_t0() - t_build))

        except Exception:

            pass



        download_url = url_for("main.download_pptx", deck_id=folder, theme=theme_key, _external=True)



        # Record usage: log presentation and increment user counter when logged in

        if not no_log:

            try:

                if getattr(current_user, "is_authenticated", False) and getattr(current_user, "id", None):

                    log = PresentationLog(

                        user_id=current_user.id,

                        title=deck_title,

                        num_slides=len(slides_out),

                        units_used=1,

                    )

                    db.session.add(log)

                    try:

                        current_user.presentations_count = (getattr(current_user, "presentations_count", 0) or 0) + 1

                    except Exception:

                        pass

                    db.session.commit()

            except Exception:

                db.session.rollback()



        return jsonify({

            "ok": True,

            "download_url": download_url,

            "pptx_path": pptx_name,

            "theme": theme_key,

            "folder": folder,

            "deck_id": folder,

        })

    except Exception:  # noqa: BLE001

        raise





# Preview-related endpoints removed





@bp.get("/api/diagnostics/export_health")

def export_health() -> Any:

    token = (request.args.get("token") or "").strip()

    expected = (os.getenv("DIAGNOSTICS_TOKEN") or os.getenv("EXPORT_HEALTH_TOKEN") or "").strip()

    if not expected or token != expected:

        return jsonify({"ok": False, "error": "Forbidden"}), 403



    trace_id = None

    try:

        trace_id = getattr(g, "trace_id", None)

    except Exception:

        trace_id = None



    soffice_path = None

    try:

        soffice_path = _find_soffice()

    except Exception:

        soffice_path = None



    diag = {}

    try:

        diag = _soffice_diagnostics()

    except Exception:

        diag = {}



    tmp = None

    try:

        tmp = tempfile.gettempdir()

    except Exception:

        tmp = None



    free_disk = None

    try:

        usage = shutil.disk_usage(tmp or os.getcwd())

        free_disk = {"total": int(usage.total), "used": int(usage.used), "free": int(usage.free)}

    except Exception:

        free_disk = None



    mem = None

    try:

        import psutil  # type: ignore

        vm = psutil.virtual_memory()

        mem = {"total": int(vm.total), "available": int(vm.available), "percent": float(vm.percent)}

    except Exception:

        mem = None



    return jsonify({

        "ok": True,

        "trace_id": trace_id,

        "sys_platform": sys.platform,

        "os_name": os.name,

        "platform": platform.platform(),

        "soffice": {"path": soffice_path, "diagnostics": diag},

        "temp_dir": tmp,

        "disk": free_disk,

        "memory": mem,

    })





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





# ---------- SEO: IndexNow (Bing) ----------

_INDEXNOW_KEY_DEFAULT = "81c78f79b06249f588fcc66bf8c96806"


def _indexnow_key() -> str:

    return (os.environ.get("INDEXNOW_KEY") or _INDEXNOW_KEY_DEFAULT).strip()


def _indexnow_host() -> str:

    host = (os.environ.get("INDEXNOW_HOST") or "").strip()

    if host:

        return host

    try:

        h = (request.host or "").strip()

        return (h.split(":", 1)[0] or "pptjet.com").strip()

    except Exception:

        return "pptjet.com"


@bp.get("/<key>.txt")

def indexnow_key_file(key: str) -> Any:

    k = _indexnow_key()

    if key != k:

        abort(404)

    return Response(k, mimetype="text/plain")


@bp.post("/indexnow/submit")

def indexnow_submit() -> Any:

    submit_token = (os.environ.get("INDEXNOW_SUBMIT_TOKEN") or "").strip()

    if not submit_token:

        abort(404)

    auth = (request.headers.get("Authorization") or "").strip()

    bearer = ""

    if auth.lower().startswith("bearer "):

        bearer = auth.split(" ", 1)[1].strip()

    provided = (

        (request.headers.get("X-IndexNow-Token") or "").strip()

        or (request.args.get("token") or "").strip()

        or bearer

    )

    if not provided or not secrets.compare_digest(provided, submit_token):

        abort(403)

    data = request.get_json(silent=True) or {}

    url_list = data.get("urlList") or data.get("urls") or []

    if isinstance(url_list, str):

        url_list = [url_list]

    if not isinstance(url_list, list):

        return jsonify({"ok": False, "error": "Invalid urlList"}), 400

    url_list = [str(u).strip() for u in url_list if str(u).strip()]

    if not url_list:

        return jsonify({"ok": False, "error": "Missing urlList"}), 400

    host = _indexnow_host()

    k = _indexnow_key()

    key_location = f"https://{host}/{k}.txt"

    endpoint = (os.environ.get("INDEXNOW_ENDPOINT") or "https://api.indexnow.org/indexnow").strip()

    payload = {

        "host": host,

        "key": k,

        "keyLocation": key_location,

        "urlList": url_list,

    }

    try:

        resp = requests.post(endpoint, json=payload, timeout=12)

        return jsonify({

            "ok": resp.ok,

            "status": resp.status_code,

            "response": (resp.text or "")[:4000],

            "submitted": len(url_list),

            "host": host,

            "keyLocation": key_location,

        }), (200 if resp.ok else 502)

    except Exception as exc:

        return jsonify({"ok": False, "error": str(exc), "submitted": len(url_list)}), 502


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

        {"loc": url_for('main.ai_presentation_generator_page', _external=True), "changefreq": "weekly", "priority": "0.8"},

        {"loc": url_for('main.ai_business_presentation_generator_page', _external=True), "changefreq": "weekly", "priority": "0.8"},

        {"loc": url_for('main.create_timeline_slides_with_ai_page', _external=True), "changefreq": "weekly", "priority": "0.8"},

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

