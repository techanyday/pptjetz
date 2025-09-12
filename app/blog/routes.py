from __future__ import annotations

from typing import Any, List
from datetime import datetime

from flask import jsonify, request
from flask_login import current_user

from app import db
from app.blog import bp
from app.models import BlogPost


def _require_admin() -> tuple[dict, int] | None:
    if not getattr(current_user, "is_authenticated", False) or not getattr(current_user, "is_admin", False):
        return {"detail": "Admin privileges required"}, 403
    return None


@bp.get("")
@bp.get("/")
def list_posts() -> Any:
    """List published posts. Supports ?page=&page_size=&include_unpublished=1 for admins."""
    page = max(1, int(request.args.get("page", 1)))
    page_size = min(50, max(1, int(request.args.get("page_size", 10))))

    q = BlogPost.query
    include_unpub = request.args.get("include_unpublished") and getattr(current_user, "is_admin", False)
    if not include_unpub:
        q = q.filter(BlogPost.published.is_(True))
    q = q.order_by(BlogPost.published_at.desc().nullslast(), BlogPost.created_at.desc())

    items = q.paginate(page=page, per_page=page_size, error_out=False)
    return jsonify({
        "ok": True,
        "page": page,
        "page_size": page_size,
        "total": items.total,
        "posts": [p.to_dict() for p in items.items],
    })


@bp.get("/<slug>")
def get_post(slug: str) -> Any:
    q = BlogPost.query.filter_by(slug=slug)
    if not (getattr(current_user, "is_admin", False)):
        q = q.filter(BlogPost.published.is_(True))
    post = q.first()
    if not post:
        return jsonify({"detail": "Not found"}), 404
    return jsonify({"ok": True, "post": post.to_dict()})


@bp.post("")
@bp.post("/")
def create_post() -> Any:
    guard = _require_admin()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    summary = (data.get("summary") or "").strip() or None
    slug = (data.get("slug") or BlogPost.slugify(title)).strip()
    published = bool(data.get("published", False))

    if not title or not content or not slug:
        return jsonify({"detail": "title, content, and slug are required"}), 422
    if BlogPost.query.filter_by(slug=slug).first():
        return jsonify({"detail": "slug already exists"}), 409

    post = BlogPost(
        title=title,
        slug=slug,
        content=content,
        summary=summary,
        author_id=getattr(current_user, "id", None),
        published=published,
        published_at=datetime.utcnow() if published else None,
    )
    db.session.add(post)
    db.session.commit()
    return jsonify({"ok": True, "post": post.to_dict()}), 201


@bp.put("/<slug>")
def update_post(slug: str) -> Any:
    guard = _require_admin()
    if guard:
        return guard
    post = BlogPost.query.filter_by(slug=slug).first()
    if not post:
        return jsonify({"detail": "Not found"}), 404

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or post.title).strip()
    content = (data.get("content") or post.content).strip()
    summary = (data.get("summary") or post.summary or "").strip() or None
    new_slug = (data.get("slug") or post.slug).strip()
    published = bool(data.get("published", post.published))

    if new_slug != post.slug and BlogPost.query.filter_by(slug=new_slug).first():
        return jsonify({"detail": "slug already exists"}), 409

    post.title = title
    post.content = content
    post.summary = summary
    if new_slug:
        post.slug = new_slug
    if published and not post.published:
        post.published_at = datetime.utcnow()
    post.published = published

    db.session.commit()
    return jsonify({"ok": True, "post": post.to_dict()})


@bp.delete("/<slug>")
def delete_post(slug: str) -> Any:
    guard = _require_admin()
    if guard:
        return guard
    post = BlogPost.query.filter_by(slug=slug).first()
    if not post:
        return jsonify({"detail": "Not found"}), 404
    db.session.delete(post)
    db.session.commit()
    return jsonify({"ok": True})
