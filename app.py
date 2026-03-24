import os
import threading
import time
import hmac
import hashlib
import requests
import random
from datetime import datetime, timedelta, timezone
from functools import wraps
from io import BytesIO

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, abort, session, Response)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from config import Config
from models import db, User, Product, Campaign, FacebookPostLog, AutomationLog

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
csrf = CSRFProtect(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'

# Image token serializer
serializer = URLSafeTimedSerializer(app.config['IMAGE_SIGN_SECRET'])

# ─── Security Headers ────────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self';"
    )
    return response

# ─── Login Manager ───────────────────────────────────────────────────────────
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ─── Helpers ─────────────────────────────────────────────────────────────────
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def generate_image_token(image_path):
    return serializer.dumps(image_path)

def verify_image_token(token, max_age=3600):
    try:
        return serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def log_automation(event_type, status, message, product_id=None, campaign_id=None):
    log = AutomationLog(
        event_type=event_type,
        product_id=product_id,
        campaign_id=campaign_id,
        status=status,
        message=message
    )
    db.session.add(log)
    db.session.commit()

def add_watermark(image_path):
    """Legacy stub — watermark now applied at serve-time."""
    pass


def serve_watermarked_image(full_path):
    """Serve image with centre + corner watermark, never modifies original."""
    import io as _io
    if not PIL_AVAILABLE:
        return send_file(full_path)
    try:
        img  = Image.open(full_path).convert('RGBA')
        w, h = img.size
        wm   = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(wm)
        brand = app.config.get('WATERMARK_TEXT', 'AgriFortress')
        text  = f'© {brand}'
        fp    = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
        try:    font = ImageFont.truetype(fp, max(16, w // 22))
        except: font = ImageFont.load_default()

        def stamp(layer, cx, cy, fnt, lbl, bg_a, txt_a, r=10, pad=(18, 10)):
            d  = ImageDraw.Draw(layer)
            b  = d.textbbox((0, 0), lbl, font=fnt)
            lw = b[2]-b[0]; lh = b[3]-b[1]
            px, py = pad
            d.rounded_rectangle([cx-lw//2-px, cy-lh//2-py,
                                  cx+lw//2+px, cy+lh//2+py],
                                 radius=r, fill=(0, 0, 0, bg_a))
            d.text((cx-lw//2+1, cy-lh//2+1), lbl, font=fnt, fill=(0, 0, 0, 80))
            d.text((cx-lw//2,   cy-lh//2),   lbl, font=fnt, fill=(255, 255, 255, txt_a))

        try:    cfont_lg = ImageFont.truetype(fp, max(22, w // 14))
        except: cfont_lg = font
        stamp(wm, w//2, h//2, cfont_lg, text, bg_a=80,  txt_a=160, r=14, pad=(24, 12))

        try:    cfont_sm = ImageFont.truetype(fp, max(14, w // 28))
        except: cfont_sm = font
        cb  = draw.textbbox((0, 0), text, font=cfont_sm)
        ctw = cb[2]-cb[0]; cth = cb[3]-cb[1]
        stamp(wm, w-ctw//2-14-16, h-cth//2-8-14, cfont_sm, text,
              bg_a=170, txt_a=245, r=8, pad=(14, 8))

        result = Image.alpha_composite(img, wm).convert('RGB')
        if result.width > 900:
            ratio  = 900 / result.width
            result = result.resize((900, int(result.height * ratio)), Image.LANCZOS)

        buf = _io.BytesIO()
        result.save(buf, format='JPEG', quality=82, optimize=True)
        buf.seek(0)
        response = Response(buf, mimetype='image/jpeg')
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma']        = 'no-cache'
        return response
    except Exception as e:
        print(f'[WATERMARK] {e}')
        return send_file(full_path)

def generate_ai_caption(product, tone='friendly'):
    tones = {
        'urgent': f"""🚨 LIMITED TIME OFFER! 🚨

⏰ Don't miss out on {product.name}!
Was: ₱{product.original_price:,.2f}
NOW: ₱{product.discounted_price:,.2f} ({product.discount_percentage:.0f}% OFF!)

Perfect for {product.crop_type or 'all'} farming.
⚠️ Only {product.stock_quantity} pcs left in stock!

📞 Order NOW before it's gone!
#{product.category.replace(' ', '')} #LimitedStock #AgriFortress""",

        'friendly': f"""🌾 Hello, Mahal na Magsasaka! 🌾

Great news! {product.name} is now on SALE!
📦 Original Price: ₱{product.original_price:,.2f}
💚 Sale Price: ₱{product.discounted_price:,.2f}

✅ Perfect for {product.crop_type or 'your farm'}
✅ Season: {product.season_applicable or 'All Year'}
✅ Packaging: {product.packaging_type or 'Standard'}

Invest in quality for a better harvest! 🌱
#FarmLife #HighYield #AgriBusiness #AgriFortress""",

        'professional': f"""📣 PRODUCT ANNOUNCEMENT

{product.name}
Category: {product.category}
Crop Application: {product.crop_type or 'Multi-crop'}

💰 Special Discount Price: ₱{product.discounted_price:,.2f}
   (Regular Price: ₱{product.original_price:,.2f})
   Savings: {product.discount_percentage:.0f}% OFF

For bulk orders and inquiries, contact us directly.

#{product.category.replace(' ', '')} #AgriSupply #AgriFortress #Philippines""",

        'seasonal': f"""🌱 PLANTING SEASON SALE IS HERE! 🌱

Prepare your farm with the best!
✨ {product.name} — NOW ON SALE!

🏷️ Price: ₱{product.discounted_price:,.2f} (was ₱{product.original_price:,.2f})
💯 {product.discount_percentage:.0f}% DISCOUNT!

🌾 Best for: {product.crop_type or 'All crops'}
📅 Season: {product.season_applicable or 'Year-round'}

Make this season your most productive yet!
#PlantingSeason #HarvestReady #AgriFortress #Magsasaka""",

        'lowstock': f"""⚠️ ALMOST GONE! Last Few Stocks! ⚠️

{product.name} is selling FAST!
🔥 NOW: ₱{product.discounted_price:,.2f} (Save {product.discount_percentage:.0f}%!)

Only {product.stock_quantity} units remaining!
Once it's gone, it's gone! 😱

Order now at AgriFortress Supply Co.
📍 Visit us or message us today!

#LowStock #LastChance #AgriFortress #FarmSupply"""
    }
    return tones.get(tone, tones['friendly'])


# ──────────────────────────────────────────────────────────────────────────────
#  FACEBOOK ENGINE v3 — fixed
#  - v21.0 API  |  photo post first  |  text fallback
#  - Non-blocking threads (no time.sleep freeze)
#  - [FACEBOOK] prefix on every terminal log line
#  - /api/fb-post-now/<id> for one-click browser testing
# ──────────────────────────────────────────────────────────────────────────────

FB_API_VERSION = "v21.0"

def _fb(msg):
    print(f"[FACEBOOK] {msg}", flush=True)

def _fb_error(rdata):
    err = rdata.get('error', {})
    code = err.get('code', 0)
    msg  = err.get('message', 'Unknown error')
    hints = {
        190: "TOKEN EXPIRED/INVALID → run fb_test.py to refresh",
        100: "INVALID PAGE_ID → check config.py FACEBOOK_PAGE_ID",
        200: "PERMISSION DENIED → token needs pages_manage_posts",
        10:  "APP IN DEVELOPMENT MODE → switch to Live at developers.facebook.com",
        368: "PAGE RESTRICTED → check Meta Business Suite → Page Quality",
        4:   "RATE LIMIT → wait a few minutes",
    }
    return f"Error {code}: {msg}" + (f"  →  {hints[code]}" if code in hints else "")

def _fb_save_log(product_id, campaign_id, post_id, status, caption, msg, retry):
    """Thread-safe DB write — always opens a fresh app context."""
    try:
        with app.app_context():
            db.session.add(FacebookPostLog(
                product_id=product_id,
                campaign_id=campaign_id,
                post_id=post_id,
                status=status,
                caption=caption,
                response_message=(msg or '')[:2000],
                retry_count=retry,
                created_at=datetime.now(timezone.utc)
            ))
            db.session.commit()
    except Exception as e:
        _fb(f"DB log error: {e}")

def _fb_post_photo(page_id, token, caption, image_path):
    """POST /photos with image attached. Returns (post_id, error_str)."""
    ext  = image_path.rsplit('.', 1)[-1].lower()
    mime = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png',
            'gif':'image/gif','webp':'image/webp'}.get(ext, 'image/jpeg')
    try:
        with open(image_path, 'rb') as f:
            resp = requests.post(
                f"https://graph.facebook.com/{FB_API_VERSION}/{page_id}/photos",
                data={'caption': caption, 'published': 'true', 'access_token': token},
                files={'source': (os.path.basename(image_path), f, mime)},
                timeout=30
            )
        rdata = resp.json()
        _fb(f"Photo response: {rdata}")
        pid = rdata.get('post_id') or rdata.get('id')
        return (pid, None) if pid else (None, _fb_error(rdata))
    except Exception as e:
        return None, f"Photo exception: {e}"

def _fb_post_text(page_id, token, caption):
    """POST /feed text only. Returns (post_id, error_str)."""
    try:
        resp  = requests.post(
            f"https://graph.facebook.com/{FB_API_VERSION}/{page_id}/feed",
            data={'message': caption, 'access_token': token},
            timeout=15
        )
        rdata = resp.json()
        _fb(f"Text response: {rdata}")
        pid = rdata.get('id')
        return (pid, None) if pid else (None, _fb_error(rdata))
    except Exception as e:
        return None, f"Text exception: {e}"

def post_to_facebook(product, caption=None, campaign_id=None, retry=0):
    """Synchronous post. Always call via fb_post_async() from Flask routes."""
    token   = (app.config.get('FACEBOOK_ACCESS_TOKEN') or '').strip()
    page_id = (app.config.get('FACEBOOK_PAGE_ID') or '').strip()

    if not token or token == 'your_access_token':
        _fb("SKIPPED — FACEBOOK_ACCESS_TOKEN not set in config.py")
        return False, "Token not configured"
    if not page_id:
        _fb("SKIPPED — FACEBOOK_PAGE_ID not set in config.py")
        return False, "Page ID not configured"

    _fb(f"Posting '{product.name}' | page={page_id} | token=...{token[-12:]}")

    if not caption:
        caption = generate_ai_caption(product, 'lowstock' if product.is_low_stock else 'friendly')

    post_id  = None
    last_err = "No attempt made"

    # Try 1: photo post
    if product.image_path:
        img_path = os.path.join(app.root_path, 'static', 'uploads',
                                os.path.basename(product.image_path))
        if os.path.exists(img_path):
            _fb(f"Trying photo post | {img_path}")
            post_id, last_err = _fb_post_photo(page_id, token, caption, img_path)
            if post_id:
                _fb(f"✅ Photo posted! post_id={post_id}")
                _fb_save_log(product.id, campaign_id, post_id, 'success', caption, "photo OK", retry)
                log_automation('facebook_post_success', 'success',
                               f"Photo posted: {product.name} → {post_id}", product.id, campaign_id)
                return True, post_id
            _fb(f"Photo failed: {last_err} → trying text-only")

    # Try 2: text-only
    _fb("Trying text-only post")
    post_id, last_err = _fb_post_text(page_id, token, caption)
    if post_id:
        _fb(f"✅ Text posted! post_id={post_id}")
        _fb_save_log(product.id, campaign_id, post_id, 'success', caption, "text OK", retry)
        log_automation('facebook_post_success', 'success',
                       f"Text posted: {product.name} → {post_id}", product.id, campaign_id)
        return True, post_id

    # Both failed
    _fb(f"❌ Attempt {retry+1}/3 failed: {last_err}")

    if retry < 2:
        delay = 5 * (retry + 1)
        _fb(f"Retrying in {delay}s ...")
        _fb_save_log(product.id, campaign_id, None, 'pending_retry',
                     caption, f"retry {retry+1}: {last_err}", retry)
        def _do_retry():
            with app.app_context():
                p = Product.query.get(product.id)
                if p:
                    post_to_facebook(p, caption, campaign_id, retry + 1)
        t = threading.Timer(delay, _do_retry)
        t.daemon = True
        t.start()
        return False, f"Retry in {delay}s"

    _fb(f"❌ FINAL FAIL after 3 attempts: {last_err}")
    _fb_save_log(product.id, campaign_id, None, 'failed',
                 caption, f"3 attempts failed: {last_err}", retry)
    log_automation('facebook_post_failed', 'failed',
                   f"3 attempts failed for '{product.name}': {last_err}", product.id, campaign_id)
    return False, last_err


def fb_post_async(product_id, caption=None, campaign_id=None):
    """
    Non-blocking fire-and-forget. ALWAYS use this from Flask routes.
    Runs post_to_facebook in a daemon thread so Flask never freezes.
    """
    _fb(f"Queueing async post for product_id={product_id}")
    def _run():
        with app.app_context():
            p = Product.query.get(product_id)
            if p:
                post_to_facebook(p, caption, campaign_id)
            else:
                _fb(f"product_id={product_id} not found in DB")
    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ─── Scheduler Jobs ──────────────────────────────────────────────────────────
def check_campaigns():
    """Runs every 5 minutes — activates/ends campaigns and fires FB posts."""
    with app.app_context():
        now = datetime.now(timezone.utc)

        # Activate campaigns whose time has come
        for campaign in Campaign.query.filter(Campaign.status == 'Scheduled').all():
            s = campaign.start_date
            e = campaign.end_date
            if s.tzinfo is None: s = s.replace(tzinfo=timezone.utc)
            if e.tzinfo is None: e = e.replace(tzinfo=timezone.utc)
            if s <= now <= e:
                campaign.status = 'Active'
                q = Product.query.filter(Product.is_active == True)
                if campaign.category_target:
                    q = q.filter(Product.category == campaign.category_target)
                if campaign.crop_target:
                    q = q.filter(Product.crop_type == campaign.crop_target)
                products = q.all()
                post_ids = []
                for product in products:
                    product.discount_percentage = campaign.discount_percentage
                    product.calculate_discounted_price()
                    if campaign.auto_post and product.auto_post_enabled and product.stock_quantity > 0:
                        post_ids.append(product.id)
                db.session.commit()
                log_automation('campaign_activated', 'success',
                               f'Campaign "{campaign.name}" activated — {len(products)} products',
                               campaign_id=campaign.id)
                for pid in post_ids:
                    fb_post_async(pid, campaign_id=campaign.id)  # NON-BLOCKING

        # End expired campaigns
        for campaign in Campaign.query.filter(Campaign.status == 'Active').all():
            e = campaign.end_date
            if e.tzinfo is None: e = e.replace(tzinfo=timezone.utc)
            if now > e:
                campaign.status = 'Ended'
                q = Product.query.filter(Product.is_active == True)
                if campaign.category_target:
                    q = q.filter(Product.category == campaign.category_target)
                for product in q.all():
                    product.discount_percentage = 0
                    product.discounted_price = product.original_price
                    product.price = product.original_price
                db.session.commit()
                log_automation('campaign_ended', 'info',
                               f'Campaign "{campaign.name}" ended, prices restored',
                               campaign_id=campaign.id)

scheduler = BackgroundScheduler(timezone='UTC')
scheduler.add_job(func=check_campaigns, trigger='interval', minutes=5,
                  id='campaign_checker', max_instances=1, coalesce=True)
scheduler.start()

# ─── PUBLIC ROUTES ───────────────────────────────────────────────────────────
@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    category = request.args.get('category', '')
    crop = request.args.get('crop', '')
    season = request.args.get('season', '')
    discounted = request.args.get('discounted', '')
    in_stock = request.args.get('in_stock', '')
    search = request.args.get('search', '')

    query = Product.query.filter(Product.is_active == True)
    if category:
        query = query.filter(Product.category == category)
    if crop:
        query = query.filter(Product.crop_type == crop)
    if season:
        query = query.filter(Product.season_applicable.contains(season))
    if discounted:
        query = query.filter(Product.discount_percentage > 0)
    if in_stock:
        query = query.filter(Product.stock_quantity > 0)
    if search:
        query = query.filter(Product.name.contains(search) | Product.description.contains(search))

    products = query.order_by(Product.created_at.desc()).paginate(page=page, per_page=12, error_out=False)
    categories = db.session.query(Product.category).distinct().all()
    crops = db.session.query(Product.crop_type).filter(Product.crop_type != None).distinct().all()
    seasons = db.session.query(Product.season_applicable).filter(Product.season_applicable != None).distinct().all()
    featured = Product.query.filter(
        Product.is_active == True,
        Product.discount_percentage > 0
    ).order_by(Product.discount_percentage.desc()).limit(3).all()

    return render_template('index.html',
                           products=products, categories=categories,
                           crops=crops, seasons=seasons, featured=featured,
                           current_filters=dict(category=category, crop=crop,
                                                season=season, discounted=discounted,
                                                in_stock=in_stock, search=search))


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    if not product.is_active:
        abort(404)
    related = Product.query.filter(
        Product.category == product.category,
        Product.id != product.id,
        Product.is_active == True
    ).limit(4).all()
    return render_template('product_detail.html', product=product, related=related)


@app.route('/secure-image/<token>')
def secure_image(token):
    """Serve images securely with signed tokens"""
    referer = request.headers.get('Referer', '')
    host = request.host_url.rstrip('/')
    if referer and not referer.startswith(host) and not referer.startswith('http://localhost'):
        abort(403)

    image_path = verify_image_token(token, max_age=app.config['IMAGE_TOKEN_EXPIRY'])
    if not image_path:
        abort(403)

    full_path = os.path.join(app.root_path, 'static', 'uploads', os.path.basename(image_path))
    if not os.path.exists(full_path):
        # Return placeholder
        return redirect(url_for('static', filename='images/no-image.svg'))

    return serve_watermarked_image(full_path)


# ─── AUTH ROUTES ─────────────────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=False)
            session.permanent = True
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin_dashboard'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@app.route('/admin/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# ─── ADMIN ROUTES ─────────────────────────────────────────────────────────────
@app.route('/admin')
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    total_products = Product.query.count()
    active_products = Product.query.filter_by(is_active=True).count()
    discounted = Product.query.filter(Product.discount_percentage > 0).count()
    low_stock = Product.query.filter(Product.stock_quantity <= Product.stock_threshold).count()
    active_campaigns = Campaign.query.filter_by(status='Active').count()
    fb_success = FacebookPostLog.query.filter_by(status='success').count()
    fb_failed = FacebookPostLog.query.filter_by(status='failed').count()

    # Category breakdown
    from sqlalchemy import func
    cat_data = db.session.query(Product.category, func.count(Product.id)).group_by(Product.category).all()
    recent_logs = AutomationLog.query.order_by(AutomationLog.timestamp.desc()).limit(10).all()
    recent_fb = FacebookPostLog.query.order_by(FacebookPostLog.created_at.desc()).limit(5).all()
    low_stock_products = Product.query.filter(
        Product.stock_quantity <= Product.stock_threshold,
        Product.is_active == True
    ).all()

    return render_template('admin_dashboard.html',
                           total_products=total_products,
                           active_products=active_products,
                           discounted=discounted,
                           low_stock=low_stock,
                           active_campaigns=active_campaigns,
                           fb_success=fb_success,
                           fb_failed=fb_failed,
                           cat_data=cat_data,
                           recent_logs=recent_logs,
                           recent_fb=recent_fb,
                           low_stock_products=low_stock_products)


@app.route('/admin/products')
@login_required
def admin_products():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    category = request.args.get('category', '')
    query = Product.query
    if search:
        query = query.filter(Product.name.contains(search))
    if category:
        query = query.filter(Product.category == category)
    products = query.order_by(Product.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('admin_products.html', products=products, search=search, category=category)


@app.route('/admin/products/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if request.method == 'POST':
        try:
            name = request.form['name'].strip()
            original_price = float(request.form['original_price'])
            discount_pct = float(request.form.get('discount_percentage', 0))

            product = Product(
                name=name,
                category=request.form['category'],
                crop_type=request.form.get('crop_type', ''),
                season_applicable=request.form.get('season_applicable', ''),
                original_price=original_price,
                discount_percentage=discount_pct,
                stock_quantity=int(request.form.get('stock_quantity', 0)),
                stock_threshold=int(request.form.get('stock_threshold', 10)),
                packaging_type=request.form.get('packaging_type', ''),
                description=request.form.get('description', ''),
                application_instructions=request.form.get('application_instructions', ''),
                safety_notes=request.form.get('safety_notes', ''),
                auto_post_enabled=bool(request.form.get('auto_post_enabled')),
                is_active=bool(request.form.get('is_active', True))
            )
            product.calculate_discounted_price()

            # Handle image upload
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(f"{int(time.time())}_{file.filename}")
                    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    os.makedirs(os.path.dirname(upload_path), exist_ok=True)
                    file.save(upload_path)
                    add_watermark(upload_path)
                    product.image_path = filename

            db.session.add(product)
            db.session.commit()

            # Auto post if enabled and discounted — NON-BLOCKING thread
            if product.auto_post_enabled and product.discount_percentage > 0 and product.stock_quantity > 0:
                fb_post_async(product.id)

            log_automation('product_added', 'success', f'Product "{name}" added', product.id)
            flash(f'Product "{name}" added successfully!', 'success')
            return redirect(url_for('admin_products'))
        except Exception as e:
            flash(f'Error adding product: {str(e)}', 'danger')

    return render_template('admin_product_form.html', product=None, action='Add')


@app.route('/admin/products/edit/<int:product_id>', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    if request.method == 'POST':
        try:
            product.name = request.form['name'].strip()
            product.category = request.form['category']
            product.crop_type = request.form.get('crop_type', '')
            product.season_applicable = request.form.get('season_applicable', '')
            product.original_price = float(request.form['original_price'])
            product.discount_percentage = float(request.form.get('discount_percentage', 0))
            product.stock_quantity = int(request.form.get('stock_quantity', 0))
            product.stock_threshold = int(request.form.get('stock_threshold', 10))
            product.packaging_type = request.form.get('packaging_type', '')
            product.description = request.form.get('description', '')
            product.application_instructions = request.form.get('application_instructions', '')
            product.safety_notes = request.form.get('safety_notes', '')
            product.auto_post_enabled = bool(request.form.get('auto_post_enabled'))
            product.is_active = bool(request.form.get('is_active'))
            product.calculate_discounted_price()

            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename and allowed_file(file.filename):
                    filename = secure_filename(f"{int(time.time())}_{file.filename}")
                    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    os.makedirs(os.path.dirname(upload_path), exist_ok=True)
                    file.save(upload_path)
                    add_watermark(upload_path)
                    product.image_path = filename

            product.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()

            # Auto-post if discount is set (or changed) and auto_post is enabled
            new_disc = product.discount_percentage or 0
            if product.auto_post_enabled and new_disc > 0 and product.stock_quantity > 0:
                fb_post_async(product.id)

            log_automation('product_updated', 'success', f'Product "{product.name}" updated', product.id)
            flash(f'Product updated!', 'success')
            return redirect(url_for('admin_products'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')

    return render_template('admin_product_form.html', product=product, action='Edit')


@app.route('/admin/products/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    name = product.name
    product.is_active = False
    db.session.commit()
    flash(f'Product "{name}" deactivated.', 'warning')
    return redirect(url_for('admin_products'))


@app.route('/admin/campaigns')
@login_required
def admin_campaigns():
    campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
    return render_template('admin_campaigns.html', campaigns=campaigns)


@app.route('/admin/campaigns/add', methods=['GET', 'POST'])
@login_required
def add_campaign():
    if request.method == 'POST':
        try:
            start     = datetime.strptime(request.form['start_date'], '%Y-%m-%dT%H:%M')
            end       = datetime.strptime(request.form['end_date'],   '%Y-%m-%dT%H:%M')
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            # If the campaign window includes right now, make it Active immediately
            status    = 'Active' if start <= now_naive <= end else 'Scheduled'

            campaign = Campaign(
                name=request.form['name'],
                type=request.form['type'],
                start_date=start,
                end_date=end,
                discount_percentage=float(request.form['discount_percentage']),
                status=status,
                auto_post=bool(request.form.get('auto_post')),
                category_target=request.form.get('category_target', ''),
                crop_target=request.form.get('crop_target', '')
            )
            db.session.add(campaign)
            db.session.commit()

            post_count = 0
            if status == 'Active':
                # Apply discounts + fire FB posts right now
                q = Product.query.filter(Product.is_active == True)
                if campaign.category_target:
                    q = q.filter(Product.category == campaign.category_target)
                if campaign.crop_target:
                    q = q.filter(Product.crop_type == campaign.crop_target)
                products = q.all()
                for p in products:
                    p.discount_percentage = campaign.discount_percentage
                    p.calculate_discounted_price()
                    if campaign.auto_post and p.auto_post_enabled and p.stock_quantity > 0:
                        post_count += 1
                db.session.commit()
                # Post AFTER commit so product data is saved
                pids = [p.id for p in products
                        if campaign.auto_post and p.auto_post_enabled and p.stock_quantity > 0]
                for pid in pids:
                    fb_post_async(pid, campaign_id=campaign.id)
                flash(f'Campaign "{campaign.name}" is ACTIVE! Posting {post_count} product(s) to Facebook...', 'success')
            else:
                flash(f'Campaign "{campaign.name}" scheduled for {start.strftime("%b %d %H:%M")}.', 'success')

            log_automation('campaign_created', 'success',
                           f'Campaign "{campaign.name}" created (status={status}, posts={post_count})',
                           campaign_id=campaign.id)
            return redirect(url_for('admin_campaigns'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'danger')
    return render_template('admin_campaign_form.html', campaign=None)


@app.route('/admin/campaigns/activate/<int:campaign_id>', methods=['POST'])
@login_required
def activate_campaign(campaign_id):
    """Manually activate any campaign and immediately post all matching products."""
    campaign = Campaign.query.get_or_404(campaign_id)
    campaign.status = 'Active'
    q = Product.query.filter(Product.is_active == True)
    if campaign.category_target:
        q = q.filter(Product.category == campaign.category_target)
    if campaign.crop_target:
        q = q.filter(Product.crop_type == campaign.crop_target)
    products = q.all()
    pids = []
    for p in products:
        p.discount_percentage = campaign.discount_percentage
        p.calculate_discounted_price()
        if campaign.auto_post and p.auto_post_enabled and p.stock_quantity > 0:
            pids.append(p.id)
    db.session.commit()
    for pid in pids:
        fb_post_async(pid, campaign_id=campaign.id)
    log_automation('campaign_activated', 'success',
                   f'Campaign "{campaign.name}" manually activated — {len(pids)} posts queued',
                   campaign_id=campaign.id)
    flash(f'Campaign "{campaign.name}" activated! Posting {len(pids)} product(s) to Facebook...', 'success')
    return redirect(url_for('admin_campaigns'))


@app.route('/admin/campaigns/delete/<int:campaign_id>', methods=['POST'])
@login_required
def delete_campaign(campaign_id):
    campaign = Campaign.query.get_or_404(campaign_id)
    db.session.delete(campaign)
    db.session.commit()
    flash('Campaign deleted.', 'warning')
    return redirect(url_for('admin_campaigns'))


@app.route('/admin/logs')
@login_required
def admin_logs():
    page = request.args.get('page', 1, type=int)
    auto_logs = AutomationLog.query.order_by(AutomationLog.timestamp.desc()).paginate(page=page, per_page=30)
    fb_logs = FacebookPostLog.query.order_by(FacebookPostLog.created_at.desc()).limit(20).all()
    return render_template('admin_logs.html', auto_logs=auto_logs, fb_logs=fb_logs)


# ─── API ROUTES ───────────────────────────────────────────────────────────────
@app.route('/api/caption/<int:product_id>')
@login_required
def api_caption(product_id):
    product = Product.query.get_or_404(product_id)
    tone = request.args.get('tone', 'friendly')
    caption = generate_ai_caption(product, tone)
    return jsonify({'caption': caption, 'tone': tone})


@app.route('/api/post-facebook/<int:product_id>', methods=['POST'])
@login_required
def api_post_facebook(product_id):
    product = Product.query.get_or_404(product_id)
    body    = request.get_json(silent=True) or {}
    caption = body.get('caption') or generate_ai_caption(product, body.get('tone', 'friendly'))
    success, result = post_to_facebook(product, caption)
    return jsonify({'success': success, 'result': result, 'caption': caption})


@app.route('/api/fb-post-now/<int:product_id>')
@login_required
def fb_post_now(product_id):
    """
    One-click manual test — open in browser while logged in:
    http://localhost:5000/api/fb-post-now/1
    Watch your terminal for [FACEBOOK] lines.
    """
    product = Product.query.get_or_404(product_id)
    tone    = request.args.get('tone', 'friendly')
    caption = generate_ai_caption(product, tone)
    _fb(f"Manual browser trigger: '{product.name}' (id={product_id})")
    success, result = post_to_facebook(product, caption)
    return jsonify({
        'success': success,
        'product': product.name,
        'result':  result,
        'message': '✅ POST IS LIVE on your Facebook Page!' if success else f'❌ FAILED: {result}',
        'caption': caption,
        'tip':     'Check your terminal for [FACEBOOK] lines for full details.'
    })


@app.route('/api/stats')
@login_required
def api_stats():
    from sqlalchemy import func
    cat_data = db.session.query(Product.category, func.count(Product.id)).group_by(Product.category).all()
    stock_data = db.session.query(Product.name, Product.stock_quantity).filter(
        Product.is_active == True
    ).order_by(Product.stock_quantity.asc()).limit(10).all()
    return jsonify({
        'categories': [{'label': c[0], 'value': c[1]} for c in cat_data],
        'stock': [{'label': s[0], 'value': s[1]} for s in stock_data]
    })


@app.route('/api/image-token/<int:product_id>')
def get_image_token(product_id):
    product = Product.query.get_or_404(product_id)
    if not product.image_path:
        return jsonify({'token': None})
    token = generate_image_token(product.image_path)
    return jsonify({'token': token, 'url': url_for('secure_image', token=token), 'expires': 90})



@app.route('/set-lang/<lang>')
def set_lang(lang):
    if lang in ('en', 'fil'):
        session['lang'] = lang
    return redirect(request.referrer or url_for('index'))


@app.route('/manifest.json')
def pwa_manifest():
    return jsonify({
        "name": "AgriFortress Admin",
        "short_name": "AgriFortress",
        "start_url": "/admin",
        "display": "standalone",
        "background_color": "#111c12",
        "theme_color": "#16a34a",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    })


@app.route('/sw.js')
def service_worker():
    sw = """
self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('fetch',   e => {
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""
    return Response(sw, mimetype='application/javascript')




# ─── ADMIN — SCHEDULED POSTS ──────────────────────────────────────────────────
@app.route('/admin/scheduled')
@login_required
def admin_scheduled():
    from datetime import timedelta
    pht_offset = timedelta(hours=8)
    now        = datetime.now()

    from sqlalchemy import text as _sqlt
    ids = [r[0] for r in db.session.execute(_sqlt(
        "SELECT id FROM products "
        "WHERE post_status IN ('scheduled','posted') "
        "AND is_active=1 ORDER BY scheduled_post_at ASC"
    ))]
    scheduled = [p for p in [Product.query.get(i) for i in ids] if p]

    all_products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    return render_template('admin_scheduled.html',
                           scheduled=scheduled,
                           all_products=all_products,
                           pht_offset=pht_offset,
                           now=now)


@app.route('/admin/scheduled/set/<int:product_id>', methods=['POST'])
@login_required
def set_scheduled_post(product_id):
    from sqlalchemy import text as _text
    product = Product.query.get_or_404(product_id)

    scheduled_at_str  = request.form.get('scheduled_post_at', '').strip()
    tone              = request.form.get('post_tone', 'friendly')
    recurring_enabled = bool(request.form.get('recurring_enabled'))
    recurring_days    = int(request.form.get('recurring_days', 7) or 7)

    try:
        scheduled_dt = datetime.strptime(scheduled_at_str, '%Y-%m-%dT%H:%M')
    except (ValueError, TypeError):
        flash('Invalid date/time format.', 'danger')
        return redirect(url_for('admin_scheduled'))

    # Update via raw SQL to bypass any column cache issues
    try:
        db.session.execute(_text(
            "UPDATE products SET "
            "post_status='scheduled', "
            "scheduled_post_at=:sat, "
            "post_tone=:tone, "
            "recurring_enabled=:rec, "
            "recurring_days=:days "
            "WHERE id=:id"
        ), {
            'sat':   scheduled_dt,
            'tone':  tone,
            'rec':   1 if recurring_enabled else 0,
            'days':  recurring_days,
            'id':    product.id
        })
        db.session.commit()
        log_automation('post_scheduled', 'success',
                       f'Post scheduled for "{product.name}" at {scheduled_dt.strftime("%b %d %H:%M")} PHT',
                       product.id)
        flash(f'✅ Post for "{product.name}" scheduled at {scheduled_dt.strftime("%b %d, %Y %I:%M %p")} PHT', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')

    return redirect(url_for('admin_scheduled'))


@app.route('/admin/scheduled/trigger/<int:product_id>', methods=['POST'])
@login_required
def trigger_scheduled_post(product_id):
    product = Product.query.get_or_404(product_id)
    tone    = getattr(product, 'post_tone', None) or 'friendly'
    caption = generate_ai_caption(product, tone)
    fb_post_async(product_id, caption=caption)
    log_automation('manual_post_triggered', 'success',
                   f'Manual trigger: "{product.name}"', product.id)
    flash(f'📤 Post queued for "{product.name}"! Check Facebook in a moment.', 'success')
    return redirect(url_for('admin_scheduled'))


@app.route('/admin/scheduled/cancel/<int:product_id>', methods=['POST'])
@login_required
def cancel_scheduled_post(product_id):
    from sqlalchemy import text as _text
    product = Product.query.get_or_404(product_id)
    try:
        db.session.execute(_text(
            "UPDATE products SET post_status='none', "
            "scheduled_post_at=NULL, recurring_enabled=0 WHERE id=:id"
        ), {'id': product.id})
        db.session.commit()
        flash(f'Scheduled post for "{product.name}" cancelled.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('admin_scheduled'))

# ─── Error Handlers ───────────────────────────────────────────────────────────
@app.errorhandler(403)
def forbidden(e):
    return render_template('secure_error.html', code=403,
                           message='Access Forbidden', detail='You do not have permission to access this resource.'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('secure_error.html', code=404,
                           message='Page Not Found', detail='The page you are looking for does not exist.'), 404

@app.errorhandler(429)
def too_many_requests(e):
    return render_template('secure_error.html', code=429,
                           message='Too Many Requests', detail='Please slow down. You are making too many requests.'), 429


# ─── Init DB ──────────────────────────────────────────────────────────────────
def init_db():
    with app.app_context():
        db.create_all()
        # Create default admin
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='Admin')
            admin.set_password('Admin@AgriF0rtress!')
            db.session.add(admin)
            db.session.commit()
            print("✅ Default admin created: admin / Admin@AgriF0rtress!")

        # Add sample products
        if Product.query.count() == 0:
            samples = [
                Product(name='Premium Hybrid Rice Seed OB-918', category='Seeds', crop_type='Rice',
                        season_applicable='Wet Season', original_price=580.0, discount_percentage=15,
                        stock_quantity=250, stock_threshold=20, packaging_type='5kg bag',
                        description='High-yielding hybrid rice seed with excellent lodging resistance.',
                        application_instructions='Soak seeds for 24 hrs before planting.',
                        safety_notes='Keep in cool dry place.', auto_post_enabled=True, is_active=True),
                Product(name='Urea Fertilizer 46-0-0', category='Fertilizer', crop_type='Corn',
                        season_applicable='All Season', original_price=1250.0, discount_percentage=10,
                        stock_quantity=180, stock_threshold=15, packaging_type='50kg sack',
                        description='High-nitrogen urea fertilizer for maximum vegetative growth.',
                        application_instructions='Apply 2 bags per hectare at 30 days after planting.',
                        safety_notes='Wear gloves and mask during application.', auto_post_enabled=True, is_active=True),
                Product(name='Complete Fertilizer 14-14-14', category='Fertilizer', crop_type='Vegetables',
                        season_applicable='All Season', original_price=1450.0, discount_percentage=0,
                        stock_quantity=95, stock_threshold=10, packaging_type='50kg sack',
                        description='Balanced NPK fertilizer for vegetables and root crops.',
                        application_instructions='Broadcast or side-dress at planting.',
                        safety_notes='Avoid contact with eyes.', auto_post_enabled=False, is_active=True),
                Product(name='Hand Tractor 7HP Diesel', category='Equipment', crop_type='Rice',
                        season_applicable='All Season', original_price=45000.0, discount_percentage=5,
                        stock_quantity=8, stock_threshold=2, packaging_type='Unit',
                        description='Powerful 7HP diesel hand tractor for land preparation.',
                        application_instructions='Use recommended diesel fuel. Change oil every 50 hours.',
                        safety_notes='Read manual before operation.', auto_post_enabled=True, is_active=True),
                Product(name='Corn Hybrid Seed DK-9133', category='Seeds', crop_type='Corn',
                        season_applicable='Dry Season', original_price=890.0, discount_percentage=20,
                        stock_quantity=7, stock_threshold=10, packaging_type='1kg bag',
                        description='Premium corn hybrid seed with 80-85 day maturity.',
                        application_instructions='Plant 2-3 seeds per hill, 75x25cm spacing.',
                        safety_notes='Treat seeds with fungicide before planting.', auto_post_enabled=True, is_active=True),
                Product(name='Knapsack Sprayer 16L', category='Tools', crop_type='Vegetables',
                        season_applicable='All Season', original_price=1800.0, discount_percentage=0,
                        stock_quantity=42, stock_threshold=5, packaging_type='Unit',
                        description='16-liter manual knapsack sprayer with adjustable nozzle.',
                        application_instructions='Fill tank 3/4 full for optimal pressure.',
                        safety_notes='Wear PPE when spraying pesticides.', auto_post_enabled=False, is_active=True),
            ]
            for p in samples:
                p.calculate_discounted_price()
                db.session.add(p)
            db.session.commit()
            print("✅ Sample products added.")

        # Add sample campaign
        if Campaign.query.count() == 0:
            camp = Campaign(
                name='Planting Season Kickoff 2024',
                type='Planting Season',
                start_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
                end_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
                discount_percentage=15,
                status='Active',
                auto_post=True,
                category_target='Seeds'
            )
            db.session.add(camp)
            db.session.commit()
            print("✅ Sample campaign added.")


if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs('instance', exist_ok=True)
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)