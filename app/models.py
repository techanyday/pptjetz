from flask_login import UserMixin
from datetime import datetime
import os
from app import db
from app.core.config import settings

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    # Database columns
    id = db.Column(db.String(100), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    profile_pic = db.Column(db.String(200))
    plan = db.Column(db.String(20), default='free')
    presentations_count = db.Column(db.Integer, default=0)
    last_reset = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    # Relationship to presentation logs
    logs = db.relationship('PresentationLog', backref='user', lazy=True)

    PLANS = {
        'free': {
            'name': 'Free',
            'limit': 3,
            'price': 0
        },
        'pay_per_use': {
            'name': 'Pay per Use',
            'limit': None,
            'price': 0.20  # USD per presentation
        },
        'pro': {
            'name': 'Monthly Pro',
            'limit': 50,  # 50 presentations per month
            'price': float(os.getenv('PAYSTACK_PLAN_PRO_MONTHLY_PRICE', 49.90)),
            'plan_id': os.getenv('PAYSTACK_PLAN_PRO_MONTHLY_ID')
        },
        'creator': {
            'name': 'Monthly Creator',
            'limit': 20,  # 20 presentations per month
            'price': float(os.getenv('PAYSTACK_PLAN_CREATOR_MONTHLY_PRICE', 29.90)),
            'plan_id': os.getenv('PAYSTACK_PLAN_CREATOR_MONTHLY_ID')
        }
    }

    def __init__(self, id_, name, email, profile_pic, is_admin=False, plan='free', presentations_count=0, last_reset=None):
        self.id = id_
        self.name = name
        self.email = email
        self.profile_pic = profile_pic
        self.is_admin = is_admin
        self.plan = plan
        self.presentations_count = presentations_count
        self.last_reset = last_reset or datetime.utcnow()

    @property
    def presentations_remaining(self):
        """Calculate remaining presentations for the current month"""
        # Global switch
        if getattr(settings, 'DISABLE_LIMITS', False):
            return None
        # Admins or whitelisted emails always have unlimited presentations
        unlimited_list = set(
            e.strip().lower()
            for e in (settings.UNLIMITED_USERS.split(',') if settings.UNLIMITED_USERS else [])
            if e.strip()
        )
        if getattr(self, 'is_admin', False) or self.email.lower() in unlimited_list:
            return None  # Treat as unlimited

        plan_info = self.PLANS.get(self.plan)
        if not plan_info or not plan_info['limit']:
            return None  # Unlimited or pay-per-use
        
        # Check if we need to reset the count (new month)
        now = datetime.utcnow()
        if now.year != self.last_reset.year or now.month != self.last_reset.month:
            self.presentations_count = 0
            self.last_reset = now
            db.session.commit()
        
        return max(0, plan_info['limit'] - self.presentations_count)

    @staticmethod
    def get(user_id):
        return User.query.get(user_id)

    def to_dict(self):
        """Convert user to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "profile_pic": self.profile_pic,
            "plan": self.plan,
            "presentations_count": self.presentations_count,
            "last_reset": self.last_reset.isoformat()
        }


# Blog posts
class BlogPost(db.Model):
    __tablename__ = 'blog_posts'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(160), unique=True, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.String(300))
    content = db.Column(db.Text, nullable=False)
    featured_image = db.Column(db.String(300))  # relative to static folder, e.g., uploads/blog/filename.jpg
    published = db.Column(db.Boolean, default=False, index=True)

    author_id = db.Column(db.String(100), db.ForeignKey('users.id'), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    published_at = db.Column(db.DateTime, nullable=True, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "featured_image": self.featured_image,
            "published": self.published,
            "author_id": self.author_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }

    @staticmethod
    def slugify(text: str) -> str:
        import re as _re
        base = (text or '').strip().lower()
        base = _re.sub(r"[^a-z0-9\s-]", "", base)
        base = _re.sub(r"\s+", "-", base).strip("-")
        return base[:150]
