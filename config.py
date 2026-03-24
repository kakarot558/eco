import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'agri-fortress-secret-key-change-in-prod-2024'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'instance', 'agristore.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Upload settings
    UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

    # Security
    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_SECURE = False  # Set True in production with HTTPS
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # ── Facebook API ──────────────────────────────────────────────────────────
    # PAGE ACCESS TOKEN (not User Token — exchanged via /me/accounts)
    # This token expires in ~1 hour. When it expires, run fb_test.py again.
    # For a never-expiring token, exchange for a Long-Lived Page Token (see below).
    FACEBOOK_PAGE_ID    = os.environ.get('FACEBOOK_PAGE_ID') or '1022925337569355'
    FACEBOOK_ACCESS_TOKEN = os.environ.get('FACEBOOK_ACCESS_TOKEN') or \
        'EAALHWunapggBQ76PXQ0TkZBfGod2P7spWPqjRAb9mu4bXjEzKOlrVYIjjAXsNc3fkUGGeSJQPm3EA1YZAz1j4K9FU7L7uOqfl97AIOGjy0iniI8CxU5lWVWZBsduxG5Dg99GljqpqY18L2nZBpGggF3vgqJOprlt9MBD7QNCGPBynjnjcqQuYgTdtG2NvZCbh0JZAlA0hW'
         # Store Info
    STORE_NAME      = 'Agri-support supply Co.'
    STORE_WEBSITE   = 'agri-support.ph'
    WATERMARK_TEXT  = 'Agri-support'

    # Rate limiting
    RATELIMIT_DEFAULT     = "200 per day;50 per hour"
    RATELIMIT_STORAGE_URL = "memory://"

    # Image signing secret
    IMAGE_SIGN_SECRET  = os.environ.get('IMAGE_SIGN_SECRET') or 'img-sign-secret-2024'
    IMAGE_TOKEN_EXPIRY = 3600  # 1 hour