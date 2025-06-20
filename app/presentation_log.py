from datetime import datetime
from app import db

class PresentationLog(db.Model):
    """Tracks each presentation generation event"""

    __tablename__ = 'presentation_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100), db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200))
    num_slides = db.Column(db.Integer)
    units_used = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
