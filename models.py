from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='Admin')  # Admin, Marketing, Inventory
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False, index=True)  # Seeds, Fertilizer, Tools, Equipment
    crop_type = db.Column(db.String(100), index=True)  # Rice, Corn, Vegetables
    season_applicable = db.Column(db.String(100))
    price = db.Column(db.Float, nullable=False)
    original_price = db.Column(db.Float, nullable=False)
    discount_percentage = db.Column(db.Float, default=0.0)
    discounted_price = db.Column(db.Float)
    stock_quantity = db.Column(db.Integer, default=0)
    stock_threshold = db.Column(db.Integer, default=10)
    packaging_type = db.Column(db.String(100))
    description = db.Column(db.Text)
    application_instructions = db.Column(db.Text)
    safety_notes = db.Column(db.Text)
    image_path = db.Column(db.String(500))
    auto_post_enabled = db.Column(db.Boolean, default=False)
    is_active         = db.Column(db.Boolean, default=True, index=True)
    # Scheduled / recurring post fields
    post_status       = db.Column(db.String(20), default='none')  # none | scheduled | posted
    post_tone         = db.Column(db.String(20), default='friendly')
    scheduled_post_at = db.Column(db.DateTime, nullable=True)
    recurring_enabled = db.Column(db.Boolean, default=False)
    recurring_days    = db.Column(db.Integer, default=7)
    last_posted_at    = db.Column(db.DateTime, nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    facebook_posts = db.relationship('FacebookPostLog', backref='product', lazy=True)
    automation_logs = db.relationship('AutomationLog', backref='product', lazy=True)

    def calculate_discounted_price(self):
        if self.discount_percentage and self.discount_percentage > 0:
            self.discounted_price = round(self.original_price * (1 - self.discount_percentage / 100), 2)
            self.price = self.discounted_price
        else:
            self.discounted_price = self.original_price
            self.price = self.original_price

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.stock_threshold

    @property
    def is_discounted(self):
        return self.discount_percentage and self.discount_percentage > 0

    def __repr__(self):
        return f'<Product {self.name}>'


class Campaign(db.Model):
    __tablename__ = 'campaigns'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(50))  # Planting Season, Harvest Sale, Flash Sale
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    discount_percentage = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='Scheduled')  # Scheduled, Active, Ended
    auto_post = db.Column(db.Boolean, default=True)
    category_target = db.Column(db.String(50))  # Which product category to target
    crop_target = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Campaign {self.name}>'


class FacebookPostLog(db.Model):
    __tablename__ = 'facebook_post_logs'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=True)
    post_id = db.Column(db.String(200))
    status = db.Column(db.String(20))  # success, failed, pending
    caption = db.Column(db.Text)
    response_message = db.Column(db.Text)
    retry_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<FacebookPostLog {self.id} - {self.status}>'


class AutomationLog(db.Model):
    __tablename__ = 'automation_logs'
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(100))  # discount_applied, campaign_started, etc.
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('campaigns.id'), nullable=True)
    status = db.Column(db.String(20))  # success, failed, info
    message = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<AutomationLog {self.event_type} - {self.timestamp}>'
