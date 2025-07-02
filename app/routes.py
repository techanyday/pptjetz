import os
import json
import requests
import secrets
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from functools import wraps
from flask import Blueprint, request, render_template, send_from_directory, jsonify, url_for, redirect, current_app, flash, session
import requests
from flask_login import login_required, login_user, logout_user, current_user
from app.utils.ppt_generator import PPTGenerator
from app.models import User
from app.presentation_log import PresentationLog
from app import db
from datetime import datetime

# Google OAuth 2.0 endpoints
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

bp = Blueprint("main", __name__)

# Template image routes
@bp.route('/static/images/templates/<template>.jpg')
def serve_template_image(template):
    image_path = os.path.join('images', 'templates', f'{template}.jpg')
    return send_from_directory(current_app.static_folder, image_path, mimetype='image/jpeg')

# Get the absolute path to the generated directory
GENERATED_FOLDER = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'generated'))
os.makedirs(GENERATED_FOLDER, exist_ok=True)

# ====================
# Download token serializer (signed, timed)
# ====================

def _get_serializer():
    """Return a URLSafeTimedSerializer using the app's secret key."""
    secret_key = current_app.config.get('SECRET_KEY')
    return URLSafeTimedSerializer(secret_key, salt='download-token')

# ------------------
# Admin decorator
# ------------------

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            flash('Admin privileges required', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return wrapper

# In-memory download_tokens dict deprecated – using signed tokens now

def get_google_provider_cfg():
    return requests.get(GOOGLE_DISCOVERY_URL).json()


@bp.route("/")
def index():
    return render_template('index.html', user=current_user)

@bp.route("/privacy")
def privacy():
    return render_template('privacy.html', user=current_user, current_date='May 13, 2025')

@bp.route("/terms")
def terms():
    return render_template('terms.html', user=current_user, current_date='May 13, 2025')

# ------------------
# Informational pages
# ------------------

@bp.route("/how-it-works")
def how_it_works():
    """Render a rich, text-heavy explanation of how PPTJet works."""
    return render_template('how_it_works.html', user=current_user)

@bp.route("/about")
def about():
    """Render the About Us page."""
    return render_template('about_us.html', user=current_user)

@bp.route("/faq")
def faq():
    """Render the Frequently Asked Questions page."""
    return render_template('faq.html', user=current_user)

@bp.route("/pricing")
def pricing():
    """Render the Pricing page with package details."""
    return render_template('pricing.html', user=current_user, plans=User.PLANS)

# ------------------
# Blog routes
# ------------------

@bp.route("/blog")
def blog_index():
    """Render the blog index page listing all blog posts."""
    from datetime import datetime
    # Build the posts metadata list (could later be database-driven)
    posts = [
        {
            'title': 'The Future of Presentations: Why AI Is Reshaping Slide Creation',
            'description': 'How AI is transforming slide creation and storytelling.',
            'url': url_for('main.blog_future'),
            'image': None
        },
        {
            'title': 'From Idea to Slide: How PPTJet Transforms Presentations with AI',
            'description': 'See how PPTJet turns raw ideas into structured slides in seconds.',
            'url': url_for('main.blog_idea_to_slide'),
            'image': None
        },
        {
            'title': 'When the Slides Write Themselves',
            'description': 'Real-world use-cases showing PPTJet saving time for everyone.',
            'url': url_for('main.blog_slides_write_themselves'),
            'image': None
        },
        {
            'title': 'Design Less, Communicate More: Why Minimalism Works',
            'description': 'Minimalist principles that make presentations clear and memorable.',
            'url': url_for('main.blog_minimalism'),
            'image': None
        },
    ]
    return render_template('blog_index.html', user=current_user, posts=posts, datetime=datetime)

@bp.route("/blog/future-of-presentations")
def blog_future():
    """Render the first blog post page."""
    from datetime import datetime
    return render_template('blog_future_ai_presentations.html', user=current_user, datetime=datetime)

@bp.route("/blog/idea-to-slide")
def blog_idea_to_slide():
    """Render the second blog post page about PPTJet transformation."""
    from datetime import datetime
    return render_template('blog_idea_to_slide.html', user=current_user, datetime=datetime)

@bp.route("/blog/when-slides-write-themselves")
def blog_slides_write_themselves():
    """Render the third blog post page about self-writing slides."""
    from datetime import datetime
    return render_template('blog_slides_write_themselves.html', user=current_user, datetime=datetime)

@bp.route("/blog/design-less-communicate-more")
def blog_minimalism():
    """Render the blog post about minimalist design and storytelling."""
    from datetime import datetime
    return render_template('blog_minimalism_presentations.html', user=current_user, datetime=datetime)

# ------------------
# SEO routes (robots.txt & sitemap.xml)
# ------------------

@bp.route('/robots.txt')
def robots_txt():
    """Serve robots.txt referencing the sitemap."""
    from flask import Response, url_for
    sitemap_url = url_for('main.sitemap_xml', _external=True)
    content = f"User-agent: *\nAllow: /\nSitemap: {sitemap_url}\n"
    return Response(content, mimetype='text/plain')

@bp.route('/sitemap.xml')
def sitemap_xml():
    """Generate a simple static-like sitemap."""
    from flask import Response, url_for
    pages = [
        url_for('main.index', _external=True),
        url_for('main.how_it_works', _external=True),
        url_for('main.about', _external=True),
        url_for('main.faq', _external=True),
        url_for('main.pricing', _external=True),
        url_for('main.blog_index', _external=True),
        url_for('main.blog_future', _external=True),
        url_for('main.blog_idea_to_slide', _external=True),
        url_for('main.blog_slides_write_themselves', _external=True),
        url_for('main.blog_minimalism', _external=True),
        url_for('main.privacy', _external=True),
        url_for('main.terms', _external=True),
    ]
    xml_lines = ["<?xml version='1.0' encoding='UTF-8'?>", "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"]
    for page in pages:
        xml_lines.append(f"  <url><loc>{page}</loc></url>")
    xml_lines.append("</urlset>")
    return Response("\n".join(xml_lines), mimetype='application/xml')

@bp.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    # Get Google's OAuth 2.0 endpoints
    google_provider_cfg = get_google_provider_cfg()
    authorization_endpoint = google_provider_cfg["authorization_endpoint"]
    
    # OAuth client
    client = current_app.config['OAUTH_CLIENT']

    # Build redirect URI dynamically based on current host
    redirect_uri = url_for('main.callback', _external=True)

    # Allow insecure transport when running on localhost
    if request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    
    print(f"Debug - Login redirect URI: {redirect_uri}")
    
    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=redirect_uri,
        scope=["openid", "email", "profile"],
    )
    return redirect(request_uri)

@bp.route("/login/callback")
def callback():
    try:
        # Get the authorization code from the request
        code = request.args.get("code")
        if not code:
            return "Error: No code provided", 400

        # Prepare token endpoint
        google_provider_cfg = get_google_provider_cfg()
        token_endpoint = google_provider_cfg["token_endpoint"]

        # Reconstruct redirect_uri based on current host
        redirect_uri = url_for('main.callback', _external=True)
        if request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        
        print(f"Debug - Callback redirect URI: {redirect_uri}")

        # Prepare the token request
        client = current_app.config['OAUTH_CLIENT']
        token_url, headers, body = client.prepare_token_request(
            token_endpoint,
            authorization_response=request.url,
            redirect_url=redirect_uri,
            code=code
        )

        # Get client credentials from the OAuth client config
        client_secrets = {
            "web": {
                "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
            }
        }

        # Build the token payload including client credentials
        import urllib.parse
        token_payload = dict(urllib.parse.parse_qsl(body))
        token_payload["client_id"] = client_secrets["web"]["client_id"]
        token_payload["client_secret"] = client_secrets["web"]["client_secret"]

        # --- Debug: Token Request ---
        print("\n--- GOOGLE OAUTH TOKEN REQUEST ---")
        print("URL:", token_url)
        print("Payload:", token_payload)
        print("----------------------------------")

        token_response = requests.post(
            token_url,
            headers=headers,
            data=token_payload,
        )

        # Check if the token request was successful
        if not token_response.ok:
            error_data = token_response.json()
            print(f"Token Error Response: {error_data}")
            return f"Error getting token: {error_data.get('error_description', 'Unknown error')}", 400

        # Parse the tokens
        token_data = token_response.json()
        if 'error' in token_data:
            print(f"Token Error: {token_data}")
            return f"Error in token response: {token_data.get('error_description', 'Unknown error')}", 400

        client.parse_request_body_response(json.dumps(token_data))

        # Get user info from Google
        print("Debug - Getting user info from Google...")
        userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
        uri, headers, body = client.add_token(userinfo_endpoint)
        print(f"Debug - User info request: URI={uri}, Headers={headers}")
        
        userinfo_response = requests.get(uri, headers=headers, data=body)
        print(f"Debug - User info response status: {userinfo_response.status_code}")
        print(f"Debug - User info response: {userinfo_response.json()}")

        if userinfo_response.json().get("email_verified"):
            unique_id = userinfo_response.json()["sub"]
            users_email = userinfo_response.json()["email"]
            users_name = userinfo_response.json()["given_name"]
            picture = userinfo_response.json()["picture"]
            # Determine if the logged-in user is an admin based on configured admin emails
            is_admin_user = users_email.lower() in current_app.config.get('ADMIN_EMAILS', [])
            print(f"Debug - Admin status for {users_email}: {is_admin_user}")

            # Create or update user
            user = User.query.get(unique_id)
            if not user:
                user = User(id_=unique_id, email=users_email, name=users_name, profile_pic=picture, is_admin=is_admin_user)
                db.session.add(user)
            else:
                user.email = users_email
                user.name = users_name
                user.profile_pic = picture
                user.is_admin = is_admin_user
            
            db.session.commit()
            login_user(user)
            return redirect(url_for('main.index'))
        else:
            return "User email not verified by Google.", 400
            
    except Exception as e:
        print(f"Error in callback: {str(e)}")
        return f"Error processing callback: {str(e)}", 400
    
        # Build the token payload including client credentials
        import urllib.parse
        token_payload = dict(urllib.parse.parse_qsl(body))
        token_payload["client_id"] = client_secrets["web"]["client_id"]
        token_payload["client_secret"] = client_secrets["web"]["client_secret"]

        # --- Debug: Token Request ---
        print("\n--- GOOGLE OAUTH TOKEN REQUEST ---")
        print("URL:", token_url)
        print("Payload:", token_payload)
        print("----------------------------------")

        token_response = requests.post(
            token_url,
            headers=headers,
            data=token_payload,
        )

        # Check if the token request was successful
        if not token_response.ok:
            error_data = token_response.json()
            print(f"Token Error Response: {error_data}")
            return f"Error getting token: {error_data.get('error_description', 'Unknown error')}", 400

        # Parse the tokens
        token_data = token_response.json()
        if 'error' in token_data:
            print(f"Token Error: {token_data}")
            return f"Error in token response: {token_data.get('error_description', 'Unknown error')}", 400

        client.parse_request_body_response(json.dumps(token_data))

    # Get user info from Google
    userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
    uri, headers, body = client.add_token(userinfo_endpoint)
    userinfo_response = requests.get(uri, headers=headers, data=body)

    if userinfo_response.json().get("email_verified"):
        unique_id = userinfo_response.json()["sub"]
        users_email = userinfo_response.json()["email"]
        users_name = userinfo_response.json()["given_name"]
        picture = userinfo_response.json()["picture"]

        # Create or update user in our db with the information provided by Google
        # Get or create user
        user = User.query.get(unique_id)
        if not user:
            is_admin_user = users_email in ADMIN_EMAILS
            user = User(
                id_=unique_id,
                name=users_name,
                email=users_email,
                profile_pic=picture,
                is_admin=is_admin_user
            )
            db.session.add(user)
            db.session.commit()

        # Begin user session by logging the user in
        login_user(user)

        # Send user back to homepage
        return redirect(url_for('main.index'))
    else:
        return "User email not available or not verified by Google.", 400

@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))


@bp.route("/generate", methods=["GET", "POST"])
@login_required
def generate():
    if request.method == "GET":
        # Check if we need to reset the monthly count
        if current_user.last_reset and (datetime.now() - current_user.last_reset).days >= 30:
            current_user.presentations_count = 0
            current_user.last_reset = datetime.utcnow()
            db.session.commit()
        
        # Calculate remaining presentations
        plan_info = User.PLANS.get(current_user.plan)
        remaining = None
        
        if current_user.plan == 'pay_per_use':
            remaining = 'Pay per use'
        elif plan_info:
            if plan_info['limit'] is None:
                remaining = 'Unlimited'
            else:
                remaining = max(0, plan_info['limit'] - current_user.presentations_count)
        else:
            remaining = 'Unknown'
        
        return render_template(
            'generate.html',
            user=current_user,
            remaining_presentations=remaining,
            current_plan=current_user.plan,
            plan_info=plan_info,
            plans=User.PLANS
        )

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        # Extract and validate required fields
        title = data.get("title")
        presenter = data.get("presenter")
        prompt = data.get("prompt")
        
        if not all([title, presenter, prompt]):
            return jsonify({"error": "Missing required fields: title, presenter, or prompt"}), 400

        # Extract optional fields with defaults
        num_slides = int(data.get("num_slides", 5))
        template_style = data.get("template_style", "Professional")
        include_images = bool(data.get("include_images", False))

        # Initialize PPT generator
        try:
            # For pay-per-use, check payment first
            if current_user.plan == 'pay_per_use':
                if not session.get('payment_verified'):
                    return jsonify({
                        'error': 'Payment required',
                        'payment_required': True
                    }), 402
                # Clear payment verification after use
                session.pop('payment_verified', None)
            else:
                # For other plans, check presentation limits
                # Skip limit check for admins
                if not current_user.is_admin:
                    plan_limit = User.PLANS[current_user.plan]['limit']
                    if plan_limit and current_user.presentations_count >= plan_limit:
                        return jsonify({
                            'error': f'You have reached your {User.PLANS[current_user.plan]["name"]} plan limit. Please upgrade to continue.'
                        }), 403

            # Initialize PPT generator
            try:
                ppt_generator = PPTGenerator()
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except Exception as e:
                return jsonify({"error": f"Error initializing presentation generator: {str(e)}"}), 500

            # Generate slide content
            try:
                slides_content = ppt_generator.generate_slide_content(prompt, num_slides)
            except Exception as e:
                return jsonify({"error": f"Error generating slide content: {str(e)}"}), 500
            
            # Create presentation
            try:
                # Use the prompt as both the title and the first slide title
                filepath = ppt_generator.create_presentation(
                    title=prompt,  # Use prompt as title instead of the generic title
                    presenter=presenter,
                    slides_content=slides_content,
                    template_style=template_style,
                    include_images=include_images
                )
            except Exception as e:
                return jsonify({"error": f"Error creating presentation: {str(e)}"}), 500
            
            # Increment presentation count and log usage
            current_user.presentations_count += 1
            # Create presentation log entry
            log_entry = PresentationLog(
                user_id=current_user.id,
                title=prompt,
                num_slides=num_slides,
                units_used=1
            )
            db.session.add(log_entry)
            db.session.commit()

            # Get filename from path
            filename = os.path.basename(filepath)
            
            return jsonify({
                'success': True,
                'filename': filename,
                'download_url': url_for('main.download_page', filename=filename)
            })
            
        except Exception as e:
            return jsonify({"error": f"Error generating presentation: {str(e)}"}), 500

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@bp.route("/download/page/<filename>")
@login_required
def download_page(filename):
    # Calculate remaining presentations
    plan_limit = User.PLANS[current_user.plan]['limit']
    remaining = plan_limit - current_user.presentations_count if plan_limit else 'Pay per use'
    
    # Generate a signed download token (valid for 1 hour)
    serializer = _get_serializer()
    token = serializer.dumps(filename)
    
    return render_template('download.html',
                           user=current_user,
                           download_url=url_for('main.download_file', token=token),
                           remaining_presentations=remaining)

@bp.route("/download/file/<token>")
@login_required
def download_file(token):
    # Verify token and get filename using signed token
    try:
        filename = _get_serializer().loads(token, max_age=3600)  # Expires in 1 hour
    except SignatureExpired:
        error_msg = "Download link expired"
        filename = None
    except BadSignature:
        error_msg = "Invalid download link"
        filename = None

    if not filename:
        # Refund the presentation unit since the link is invalid/expired
        try:
            if current_user.is_authenticated and current_user.presentations_count > 0:
                current_user.presentations_count -= 1
                db.session.commit()
        except Exception as e:
            # Log any issues but don't block the response
            print(f"Error refunding presentation unit: {str(e)}")
        return error_msg, 400
    

    # Set cache control headers to prevent caching
    response = send_from_directory(GENERATED_FOLDER, filename, as_attachment=True)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@bp.route('/switch_plan/<plan_type>', methods=['POST'])
@login_required
def switch_plan(plan_type):
    try:
        print(f"Debug - Switch plan request received for plan: {plan_type}")
        
        # Validate plan type (including pay_per_use)
        valid_plans = list(User.PLANS.keys()) + ['pay_per_use']
        if plan_type not in valid_plans:
            print(f"Debug - Invalid plan type: {plan_type}")
            return jsonify({
                'success': False,
                'error': 'Invalid plan type'
            }), 400

        # For paid plans (pro and creator), initialize payment
        if plan_type in ['pro', 'creator']:
            print(f"Debug - Initializing payment for paid plan: {plan_type}")
            
            # Get plan details
            plan = User.PLANS[plan_type]
            amount = plan['price']
            plan_id = plan.get('plan_id')
            
            print(f"Debug - Plan details: amount={amount}, plan_id={plan_id}")

            # Initialize Paystack payment
            url = "https://api.paystack.co/transaction/initialize"
            headers = {
                "Authorization": f"Bearer {os.getenv('PAYSTACK_SECRET_KEY')}",
                "Content-Type": "application/json"
            }
            
            callback_url = url_for('main.payment_callback', _external=True, _scheme='http')
            print(f"Debug - Callback URL: {callback_url}")
            
            data = {
                "email": current_user.email,
                "amount": int(amount * 100),  # Convert to pesewas
                "currency": "GHS",
                "callback_url": callback_url,
                "metadata": {
                    "user_id": current_user.id,
                    "plan": plan_type,
                    "plan_id": plan_id
                }
            }
            
            print(f"Debug - Payment request data: {data}")
            response = requests.post(url, headers=headers, json=data)
            print(f"Debug - Paystack response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"Debug - Paystack response: {result}")
                
                if result.get('status'):
                    # Store payment reference in session
                    session['payment_reference'] = result['data']['reference']
                    payment_url = result['data']['authorization_url']
                    print(f"Debug - Payment initialized, redirecting to: {payment_url}")
                    
                    return jsonify({
                        'success': False,  # Set to false to ensure frontend redirects
                        'payment_url': payment_url
                    })
                else:
                    print(f"Debug - Payment initialization failed: {result}")
            else:
                print(f"Debug - Payment request failed: {response.text}")

            return jsonify({
                'success': False,
                'error': 'Failed to initialize payment'
            }), 400

        # For free plan or pay-per-use, switch immediately
        print(f"Debug - Switching to {plan_type} plan immediately")
        if plan_type in ['free', 'pay_per_use']:
            # Store the old plan and count in case we need to revert
            old_plan = current_user.plan
            old_count = current_user.presentations_count
            old_reset = current_user.last_reset
            
            current_user.plan = plan_type
            current_user.presentations_count = 0  # Reset count on plan change
            current_user.last_reset = datetime.utcnow()  # Reset the monthly counter
            db.session.commit()
            
            # Store the old plan info in session in case payment fails
            session['previous_plan'] = old_plan
            session['previous_count'] = old_count
            session['previous_reset'] = old_reset.isoformat() if old_reset else None
        
        return jsonify({
            'success': True,
            'message': f'Successfully switched to {plan_type} plan'
        })
        
    except Exception as e:
        print(f"Debug - Error in switch_plan: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@bp.route('/initialize_payment', methods=['POST'])
@login_required
def initialize_payment():
    try:
        # Calculate amount in pesewas (Paystack uses smallest currency unit)
        usd_amount = 0.20  # Price in USD
        usd_to_ghs_rate = 12  # Exchange rate
        ghs_amount = round(usd_amount * usd_to_ghs_rate, 2)  # Convert to GHS
        pesewas_amount = int(ghs_amount * 100)  # Convert to pesewas
        
        # Initialize Paystack payment
        url = "https://api.paystack.co/transaction/initialize"
        headers = {
            "Authorization": f"Bearer {os.getenv('PAYSTACK_SECRET_KEY')}",
            "Content-Type": "application/json"
        }
        data = {
            "email": current_user.email,
            "amount": pesewas_amount,  # Amount in pesewas
            "currency": "GHS",  # Specify Ghana Cedis
            "callback_url": url_for('main.payment_callback', _external=True),
            "metadata": {
                "user_id": current_user.id,
                "plan": "pay_per_use",
                "usd_amount": usd_amount,
                "exchange_rate": usd_to_ghs_rate
            }
        }
        
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            if result['status']:
                # Store payment reference in session
                session['payment_reference'] = result['data']['reference']
                return jsonify({
                    'success': True,
                    'authorization_url': result['data']['authorization_url']
                })
        
        return jsonify({
            'success': False,
            'error': 'Failed to initialize payment'
        }), 400
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ------------------
# User Dashboard
# ------------------
@bp.route('/dashboard')
@login_required
def dashboard():
    plan_info = User.PLANS.get(current_user.plan)
    used = current_user.presentations_count
    remaining = 'Unlimited' if current_user.is_admin or not plan_info or plan_info['limit'] is None else max(0, plan_info['limit'] - used)
    logs = PresentationLog.query.filter_by(user_id=current_user.id).order_by(PresentationLog.created_at.desc()).limit(20).all()
    return render_template('dashboard.html', user=current_user, used=used, remaining=remaining, logs=logs)

# ------------------
# Admin Dashboard Page
# ------------------
@bp.route('/admin')
@admin_required
def admin_dashboard_page():
    # HTML template fetches usage via JS
    return render_template('admin/dashboard.html', user=current_user)

# ------------------
# Admin endpoints
# ------------------
@bp.route('/admin/usage')
@admin_required
def admin_usage():
    # Aggregate usage by user
    from sqlalchemy import func
    usage = (
        db.session.query(
            User.id,
            User.email,
            func.count(PresentationLog.id).label('presentations'),
            func.max(PresentationLog.created_at).label('last_date')
        )
        .join(PresentationLog, PresentationLog.user_id == User.id)
        .group_by(User.id)
        .all()
    )

    usage_data = [
        {
            "user_id": u.id,
            "email": u.email,
            "presentations": u.presentations,
            "last_date": u.last_date.strftime('%Y-%m-%d') if u.last_date else 'N/A'
        }
        for u in usage
    ]
    return jsonify({"usage": usage_data})

@bp.route('/admin/award_units', methods=['POST'])
@admin_required
def admin_award_units():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    units = int(data.get('units', 0))
    if not user_id or units <= 0:
        return jsonify({'success': False, 'error': 'Invalid parameters'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    user.presentations_count = max(0, user.presentations_count - units)
    db.session.commit()
    return jsonify({'success': True, 'presentations_count': user.presentations_count})

@bp.route('/payment/callback')
@login_required
def payment_callback():
    reference = request.args.get('reference')
    print(f"Debug - Payment callback received with reference: {reference}")
    print(f"Debug - Session reference: {session.get('payment_reference')}")
    
    if not reference or reference != session.get('payment_reference'):
        flash('Invalid payment reference', 'error')
        return redirect(url_for('main.generate'))
    
    try:
        # Verify payment with Paystack
        url = f"https://api.paystack.co/transaction/verify/{reference}"
        headers = {
            "Authorization": f"Bearer {os.getenv('PAYSTACK_SECRET_KEY')}"
        }
        print(f"Debug - Verifying payment with Paystack")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            result = response.json()
            print(f"Debug - Paystack response: {result}")
            
            if result['status'] and result['data']['status'] == 'success':
                # Get metadata from payment
                metadata = result['data'].get('metadata', {})
                plan_type = metadata.get('plan')
                print(f"Debug - Plan type from metadata: {plan_type}")

                if plan_type in ['pro', 'creator']:
                    # Update user's subscription plan
                    print(f"Debug - Updating user plan from {current_user.plan} to {plan_type}")
                    current_user.plan = plan_type
                    current_user.presentations_count = 0  # Reset count on plan change
                    current_user.last_reset = datetime.utcnow()  # Reset the monthly counter
                    db.session.commit()
                    print(f"Debug - Plan updated in database and counters reset")
                    flash(f'Payment successful! Your plan has been upgraded to {plan_type}.', 'success')
                    
                    # Clear the previous plan info from session
                    session.pop('previous_plan', None)
                    session.pop('previous_count', None)
                    session.pop('previous_reset', None)
                else:
                    # For pay-per-use, mark payment as verified
                    session['payment_verified'] = True
                    flash('Payment successful! You can now generate your presentation.', 'success')

                # Clear the payment reference from session
                session.pop('payment_reference', None)
                return redirect(url_for('main.generate'))
            else:
                print(f"Debug - Payment not successful: {result}")
        else:
            print(f"Debug - Paystack verification failed with status code: {response.status_code}")
        
        # Revert to previous plan if payment failed
        if 'previous_plan' in session:
            current_user.plan = session['previous_plan']
            current_user.presentations_count = session['previous_count']
            if session['previous_reset']:
                current_user.last_reset = datetime.fromisoformat(session['previous_reset'])
            db.session.commit()
            
            # Clear the previous plan info from session
            session.pop('previous_plan', None)
            session.pop('previous_count', None)
            session.pop('previous_reset', None)
        
        flash('Payment verification failed - your previous plan has been restored', 'error')
        return redirect(url_for('main.generate'))
        
    except Exception as e:
        print(f"Debug - Error in payment callback: {str(e)}")
        flash('Error verifying payment', 'error')
        return redirect(url_for('main.generate'))

