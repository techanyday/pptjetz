from flask_login import UserMixin
from datetime import datetime
import os
from app import db

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
            'price': float(os.getenv('PAYSTACK_PLAN_PRO_MONTHLY_PRICE', 63.50)),
            'plan_id': os.getenv('PAYSTACK_PLAN_PRO_MONTHLY_ID')
        },
        'creator': {
            'name': 'Monthly Creator',
            'limit': 20,  # 20 presentations per month
            'price': float(os.getenv('PAYSTACK_PLAN_CREATOR_MONTHLY_PRICE', 35.88)),
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
        # Admins always have unlimited presentations
        if getattr(self, 'is_admin', False):
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
