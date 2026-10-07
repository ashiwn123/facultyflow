"""FacultyFlow: role-scoped college leave and academic scheduling."""
import os
import json
from pathlib import Path
import secrets
import hashlib
import hmac
import smtplib
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

import click
from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
ROLES = ['student', 'teacher', 'staff', 'hod', 'dean', 'principal', 'admin']
LEAVE_TYPES = {'CL': ('Casual leave', 12), 'EL': ('Earned leave', 15), 'DH': ('Duty holiday', 5), 'SPL': ('Special leave', 5), 'LWP': ('Leave without pay', 365)}
ACTIVE = ['hod', 'dean', 'principal', 'teacher', 'approved']

def now():
    return datetime.now(timezone.utc)

def send_email(recipient, subject, body, html=None):
    """Send via the configured SMTP account; never log credentials or message bodies."""
    import ssl
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = recipient
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype='html')
    with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '587')), timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
        refused = smtp.send_message(msg)
        if refused:
            raise smtplib.SMTPException('Recipient rejected')

def welcome_email(name, email, password, role, department):
    """Return (plain_text, html) for a new account's login details."""
    from html import escape
    text = (
        f"Hello {name} 👋,\n\n"
        "🎉 Your FacultyFlow account has been created.\n\n"
        "🔐 Your login details\n"
        f"Email address: {email}\n"
        f"Temporary password: {password}\n\n"
        f"🎓 Role: {role.title()}\n"
        f"🏫 Department: {department}\n\n"
        "✅ Next steps\n"
        "1. Sign in with the email and temporary password above.\n"
        "2. Enter the temporary password as your current password.\n"
        "3. Choose a new password of at least 6 characters.\n\n"
        "Need help? Contact your college administrator.\n\n"
        "— FacultyFlow Administration\n"
    )
    n, e, p, r, d = (escape(x) for x in (name, email, password, role.title(), department))
    font = "font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    row = lambda icon, label, value, mono=False: (
        f'<tr><td style="padding:14px 18px;border-bottom:1px solid #e6e9f5;{font}">'
        f'<div style="font-size:12px;color:#7a819c;text-transform:uppercase;letter-spacing:1px;">{icon}&nbsp; {label}</div>'
        f'<div style="font-size:17px;color:#14213d;font-weight:600;margin-top:4px;'
        f'{"font-family:Consolas,Menlo,monospace;letter-spacing:1px;" if mono else ""}">{value}</div></td></tr>')
    step = lambda num, txt: (
        f'<tr><td width="34" valign="top" style="padding:6px 0;"><div style="width:26px;height:26px;line-height:26px;border-radius:13px;'
        f'background:#4f5bd5;color:#fff;text-align:center;font-size:13px;font-weight:700;{font}">{num}</div></td>'
        f'<td style="padding:6px 0;font-size:15px;color:#3b4262;{font}">{txt}</td></tr>')
    html = f"""<!doctype html><html><body style="margin:0;padding:0;background:#eef1fb;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1fb;padding:32px 12px;"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:18px;overflow:hidden;box-shadow:0 8px 30px rgba(20,33,61,.12);">
<tr><td bgcolor="#14213d" style="background:#14213d;background-image:linear-gradient(135deg,#14213d 0%,#2b3a8c 55%,#4f5bd5 100%);padding:34px 32px 30px;" align="center">
  <table role="presentation" cellpadding="0" cellspacing="0"><tr>
    <td style="width:46px;height:46px;background:#ffffff;border-radius:12px;text-align:center;vertical-align:middle;font-size:26px;color:#14213d;line-height:46px;">🏛️</td>
    <td style="padding-left:12px;font-size:26px;font-weight:800;color:#ffffff;{font}">FacultyFlow<span style="color:#8f9bff;">.</span></td>
  </tr></table>
  <div style="margin-top:22px;font-size:40px;line-height:1;">🎉</div>
  <h1 style="margin:12px 0 6px;font-size:24px;color:#ffffff;{font}">Welcome aboard, {n}!</h1>
  <p style="margin:0;font-size:15px;color:#c9cff5;{font}">Your college workspace account is ready.</p>
</td></tr>
<tr><td style="padding:30px 32px 8px;{font}">
  <p style="margin:0 0 18px;font-size:15px;color:#3b4262;line-height:1.6;">Hello {n} 👋,<br>Your <b>FacultyFlow</b> account has been created. Here are your login details:</p>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f7fd;border:1px solid #e6e9f5;border-radius:12px;">
    {row('📧', 'Email address', e)}
    {row('🔑', 'Temporary password', p, mono=True)}
    <tr><td style="padding:0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="50%" style="padding:14px 18px;{font}"><div style="font-size:12px;color:#7a819c;text-transform:uppercase;letter-spacing:1px;">🎓&nbsp; Role</div><div style="font-size:16px;color:#14213d;font-weight:600;margin-top:4px;">{r}</div></td>
      <td width="50%" style="padding:14px 18px;{font}"><div style="font-size:12px;color:#7a819c;text-transform:uppercase;letter-spacing:1px;">🏫&nbsp; Department</div><div style="font-size:16px;color:#14213d;font-weight:600;margin-top:4px;">{d}</div></td>
    </tr></table></td></tr>
  </table>
</td></tr>
<tr><td style="padding:22px 32px 6px;{font}">
  <h2 style="margin:0 0 10px;font-size:16px;color:#14213d;">✅ Next steps</h2>
  <table role="presentation" cellpadding="0" cellspacing="0">
    {step(1, 'Sign in with the email and temporary password above.')}
    {step(2, 'Enter the temporary password as your <b>current password</b>.')}
    {step(3, 'Choose a new password of at least 6 characters. 🔒')}
  </table>
</td></tr>
<tr><td style="padding:18px 32px 28px;{font}">
  <div style="background:#fff7e6;border-left:4px solid #f5a623;border-radius:8px;padding:12px 14px;font-size:13px;color:#7a5a12;line-height:1.5;">⚠️ For your security, please don’t share this email. You’ll be asked to change your password the first time you sign in.</div>
</td></tr>
<tr><td bgcolor="#f6f7fd" style="background:#f6f7fd;padding:20px 32px;text-align:center;border-top:1px solid #e6e9f5;{font}">
  <p style="margin:0;font-size:13px;color:#7a819c;">Need help? Contact your college administrator. 💬</p>
  <p style="margin:6px 0 0;font-size:12px;color:#a2a8c3;">— FacultyFlow Administration · Leave management &amp; academic scheduling</p>
</td></tr>
</table></td></tr></table></body></html>"""
    return text, html

def process_avatar(file_storage):
    """Validate, orient, resize, and convert uploaded avatar to an optimized base64 data URL.
    Limits upload processing to 30 MB.
    """
    import io
    import base64
    from PIL import Image, ImageOps

    data = file_storage.read()
    if not data:
        raise ValueError('No image selected.')
    if len(data) > 30 * 1024 * 1024:
        raise ValueError('Image exceeds limit. Image should be less then 30 mb.')

    try:
        Image.MAX_IMAGE_PIXELS = 100_000_000
        img = Image.open(io.BytesIO(data))
        if img.format not in ('JPEG', 'PNG', 'WEBP', 'GIF', 'BMP', 'TIFF', 'MPO'):
            raise ValueError('Please upload a standard image file (JPG, PNG, WebP, GIF).')

        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        w, h = img.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        img = img.crop((left, top, left + min_dim, top + min_dim))
        img = img.resize((300, 300), Image.Resampling.LANCZOS)

        if img.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        out = io.BytesIO()
        img.save(out, format='JPEG', quality=88, optimize=True)
        encoded = base64.b64encode(out.getvalue()).decode('utf-8')
        return f'data:image/jpeg;base64,{encoded}'
    except ValueError:
        raise
    except Exception:
        raise ValueError('Invalid or corrupted image file.')

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY'), DEMO_MODE=os.getenv('DEMO_MODE', 'false').lower() == 'true',
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=30 * 1024 * 1024,
                      TEMPLATES_AUTO_RELOAD=True)
    if test_config:
        app.config.update(test_config)
    if not app.config.get('SECRET_KEY') or app.config['SECRET_KEY'] == 'replace-with-a-long-random-secret':
        app.config['SECRET_KEY'] = secrets.token_hex(32)

    client = None
    if not app.config.get('DEMO_MODE') and os.getenv('MONGODB_URI'):
        try:
            uri = os.getenv('MONGODB_URI')
            client = MongoClient(uri, serverSelectionTimeoutMS=4000, tz_aware=True)
            client.admin.command('ping')
        except Exception as e:
            app.logger.warning(f'MongoDB connection failed ({e}). Falling back to in-memory mode.')
            client = None
            app.config['DEMO_MODE'] = True

    if client is None:
        import mongomock
        client = mongomock.MongoClient(tz_aware=True)
        app.config['DEMO_MODE'] = True

    db = client[os.getenv('MONGODB_DB', 'facultyflow')]
    app.extensions['db'] = db
    try:
        db.users.create_index('email', unique=True)
        db.otps.create_index('expires', expireAfterSeconds=0)
        db.throttles.create_index('expires', expireAfterSeconds=0)
        db.leaves.create_index([('user_id', 1), ('start', 1)])
        db.notifications.create_index([('user_id', 1), ('created', -1)])
    except Exception:
        pass

    if app.config['DEMO_MODE']:
        if db.users.count_documents({}) == 0:
            seed(db)

    @app.before_request
    def before():
        session.setdefault('csrf', secrets.token_hex(24))
        g.user = db.users.find_one({'_id': session.get('uid'), 'active': True}) if session.get('uid') else None
        if g.user and session.get('version') != g.user.get('version', 0):
            session.clear()
            g.user = None
        if request.method == 'POST' and not hmac.compare_digest(str(session.get('csrf', '')), request.form.get('csrf', 'missing')):
            abort(400, 'This form expired. Refresh the page and try again.')

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user:
                return redirect(url_for('login'))
            if g.user.get('must_change') and request.endpoint not in ['profile', 'logout']:
                flash('Set your own password before continuing.', 'info')
                return redirect(url_for('profile'))
            return fn(*args, **kwargs)
        return wrapped

    def scoped_users():
        role = g.user['role']
        if role in ['admin', 'principal', 'dean']:
            query = {}
        elif role == 'hod':
            query = {'department': g.user['department']}
        elif role == 'teacher':
            query = {'$or': [{'advisor_id': g.user['_id']}, {'_id': g.user['_id']}]}
        else:
            query = {'_id': g.user['_id']}
        return list(db.users.find(query, {'password': 0}))

    def scoped_leaves():
        return list(db.leaves.find({'user_id': {'$in': [u['_id'] for u in scoped_users()]}}).sort('created', -1))

    def can_approve(leave):
        if leave['user_id'] == g.user['_id'] or leave['status'] != g.user['role']:
            return False
        if g.user['role'] == 'hod':
            return leave['department'] == g.user['department']
        if g.user['role'] == 'teacher':
            owner = db.users.find_one({'_id': leave['user_id']})
            return owner and owner.get('advisor_id') == g.user['_id']
        return g.user['role'] in ['dean', 'principal']

    def notify(uid, message):
        db.notifications.insert_one({'user_id': uid, 'message': message, 'read': False, 'created': now()})

    def notify_reviewers(leave):
        q = {'role': leave['status'], 'active': True}
        if leave['status'] == 'hod':
            q['department'] = leave['department']
        if leave['status'] == 'teacher':
            owner = db.users.find_one({'_id': leave['user_id']})
            q['_id'] = owner.get('advisor_id')
        for reviewer in db.users.find(q):
            notify(reviewer['_id'], f"{leave['name']}'s {leave['type']} request needs your approval.")

    @contextmanager
    def workflow_lock():
        # Unique MongoDB document serializes short mutations across workers.
        try:
            db.locks.insert_one({'_id': 'leave-workflow'})
        except DuplicateKeyError:
            abort(409, 'Another leave update is in progress. Please retry.')
        try:
            yield
        finally:
            db.locks.delete_one({'_id': 'leave-workflow'})

    def has_slot(leave):
        cap = max(1, int(os.getenv('STAFF_LEAVE_SLOTS', '3')))
        current = date.fromisoformat(leave['start'])
        while current <= date.fromisoformat(leave['end']):
            if db.leaves.count_documents({'department': leave['department'], 'role': {'$ne': 'student'},
                    'status': {'$in': ACTIVE}, 'start': {'$lte': current.isoformat()}, 'end': {'$gte': current.isoformat()}}) >= cap:
                return False
            current += timedelta(days=1)
        return True

    def promote():
        for leave in db.leaves.find({'status': 'waiting'}).sort('created', 1):
            if has_slot(leave):
                leave['status'] = leave['stages'][0]
                db.leaves.update_one({'_id': leave['_id']}, {'$set': {'status': leave['status']}})
                notify(leave['user_id'], 'A leave slot is available. Your request has entered approval.')
                notify_reviewers(leave)

    @app.context_processor
    def context():
        return dict(user=g.user, roles=ROLES, leave_types=LEAVE_TYPES, demo=app.config['DEMO_MODE'], today=date.today().isoformat(),
                    unread=db.notifications.count_documents({'user_id': g.user['_id'], 'read': False}) if g.user else 0,
                    can_approve=can_approve)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            key = 'login:' + hashlib.sha256((email + (request.remote_addr or '')).encode()).hexdigest()
            limiter = db.throttles.find_one({'_id': key})
            if limiter and limiter['expires'] > now() and limiter['count'] >= 10:
                flash('Too many attempts. Try again in 15 minutes.', 'error')
                return render_template('auth.html', mode='login'), 429
            u = db.users.find_one({'$or': [{'email': email}, {'_id': email}, {'username': email}], 'active': True})
            if u and check_password_hash(u['password'], request.form.get('password', '')):
                session.clear()
                session.update(uid=u['_id'], version=u.get('version', 0), csrf=secrets.token_hex(24))
                session.permanent = True
                db.throttles.delete_one({'_id': key})
                return redirect(url_for('dashboard'))
            if not limiter or limiter['expires'] <= now():
                db.throttles.replace_one({'_id': key}, {'_id': key, 'count': 1, 'expires': now() + timedelta(minutes=15)}, upsert=True)
            else:
                db.throttles.update_one({'_id': key}, {'$inc': {'count': 1}})
            flash('Invalid email or username. Please verify your credentials or contact administrator.', 'error')
        return render_template('auth.html', mode='login')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.route('/forgot-password', methods=['GET', 'POST'])
    def forgot():
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            key = hashlib.sha256(email.encode()).hexdigest()
            # Limit both requests and verification attempts, including unknown accounts.
            throttle_id = 'otp:' + hashlib.sha256((request.remote_addr or '').encode()).hexdigest()
            limit = db.throttles.find_one({'_id': throttle_id})
            if limit and limit['expires'] > now() and limit['count'] >= 5:
                flash('Recovery request limit reached. Try again in 15 minutes.', 'error')
                return render_template('auth.html', mode='forgot'), 429
            if not limit or limit['expires'] <= now():
                db.throttles.replace_one({'_id': throttle_id}, {'_id': throttle_id, 'count': 1, 'expires': now() + timedelta(minutes=15)}, upsert=True)
            else:
                db.throttles.update_one({'_id': throttle_id}, {'$inc': {'count': 1}})
            old = db.otps.find_one({'_id': key})
            if old and old['sent'] > now() - timedelta(seconds=60):
                flash('Please wait 60 seconds before requesting another code.', 'error')
                return redirect(url_for('reset'))
            u = db.users.find_one({'email': email, 'active': True})
            if u:
                code = f'{secrets.randbelow(1000000):06d}'
                digest = hmac.new(app.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()
                db.otps.replace_one({'_id': key}, {'_id': key, 'user_id': u['_id'], 'digest': digest, 'attempts': 0,
                                    'sent': now(), 'expires': now() + timedelta(minutes=5)}, upsert=True)
                try:
                    if app.config['DEMO_MODE'] and os.getenv('OTP_CONSOLE', 'false').lower() == 'true':
                        print(f'DEVELOPMENT recovery OTP for {email}: {code}', flush=True)
                    else:
                        msg = EmailMessage()
                        msg['Subject'] = 'FacultyFlow password recovery code'
                        msg['From'] = os.environ['SMTP_FROM']
                        msg['To'] = email
                        msg.set_content(f'Your verification code is {code}. It expires in 5 minutes. If you did not request this, ignore this email.')
                        import ssl
                        with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT', '587')), timeout=15) as smtp:
                            smtp.starttls(context=ssl.create_default_context())
                            smtp.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
                            smtp.send_message(msg)
                except (OSError, smtplib.SMTPException, KeyError):
                    db.otps.delete_one({'_id': key})
                    app.logger.error('OTP delivery failed. Check SMTP configuration.')
            session['reset_email'] = email
            flash('If this account exists, a code has been sent. It expires in 5 minutes.', 'info')
            return redirect(url_for('reset'))
        return render_template('auth.html', mode='forgot')

    @app.route('/reset-password', methods=['GET', 'POST'])
    def reset():
        if request.method == 'POST':
            key = hashlib.sha256(session.get('reset_email', '').encode()).hexdigest()
            otp = db.otps.find_one_and_update({'_id': key, 'expires': {'$gt': now()}, 'attempts': {'$lt': 5}}, {'$inc': {'attempts': 1}}, return_document=ReturnDocument.AFTER)
            digest = hmac.new(app.secret_key.encode(), request.form.get('code', '').encode(), hashlib.sha256).hexdigest()
            password = request.form.get('password', '')
            if otp and hmac.compare_digest(digest, otp['digest']) and len(password) >= 6:
                consumed = db.otps.delete_one({'_id': key, 'digest': digest})
                if consumed.deleted_count:
                    db.users.update_one({'_id': otp['user_id']}, {'$set': {'password': generate_password_hash(password), 'must_change': False}, '$inc': {'version': 1}})
                    session.clear()
                    flash('Password updated. Sign in with your new password.', 'success')
                    return redirect(url_for('login'))
            flash('Invalid or expired code, or password shorter than 6 characters. Request a new code after five attempts.', 'error')
        return render_template('auth.html', mode='reset')


    @app.get('/')
    @login_required
    def dashboard():
        role = g.user['role']
        users = scoped_users()
        leaves = scoped_leaves()
        pending = [x for x in leaves if x['status'] not in ['approved', 'rejected', 'cancelled']]
        today_str = date.today().isoformat()
        current_year = str(date.today().year)

        stats = [
            len(pending),
            sum(x['status'] == 'approved' for x in leaves),
            sum(x['status'] == 'rejected' for x in leaves),
            sum(u['role'] == 'student' for u in users)
        ]
        departments = sorted(set(u.get('department') for u in users if u.get('department')))

        cap = max(1, int(os.getenv('STAFF_LEAVE_SLOTS', '3')))
        dept_occupied = db.leaves.count_documents({
            'department': g.user.get('department'),
            'role': {'': 'student'},
            'status': {'': ACTIVE},
            'start': {'': today_str},
            'end': {'': today_str}
        })
        slot_info = {
            'capacity': cap,
            'occupied': dept_occupied,
            'available': max(0, cap - dept_occupied),
            'percent': min(100, int((dept_occupied / cap) * 100))
        }

        current_request = db.leaves.find_one({
            'user_id': g.user['_id'],
            'status': {'': ['rejected', 'cancelled']}
        }, sort=[('created', -1)])
        queue_pos = None
        if current_request and current_request.get('status') == 'waiting':
            waiting_list = list(db.leaves.find({
                'department': current_request['department'],
                'status': 'waiting'
            }).sort('created', 1))
            for idx, w in enumerate(waiting_list):
                if w['_id'] == current_request['_id']:
                    queue_pos = idx + 1
                    break

        principal_data = {}
        teacher_data = {}
        student_data = {}
        hod_data = {}

        if role == 'principal':
            staff_list = list(db.users.find({'role': {'': ['teacher', 'staff', 'hod', 'dean']}, 'active': True}).sort([('department', 1), ('name', 1)]))
            for s in staff_list:
                s_leaves = list(db.leaves.find({'user_id': s['_id'], 'start': {'': f'{current_year}-01-01', '': f'{current_year}-12-31'}, 'status': {'': ['rejected', 'cancelled']}}))
                cl_used = sum(l['days'] for l in s_leaves if l.get('type') == 'CL' and l.get('status') == 'approved')
                el_used = sum(l['days'] for l in s_leaves if l.get('type') == 'EL' and l.get('status') == 'approved')
                tot_used = sum(l['days'] for l in s_leaves if l.get('status') == 'approved')
                s['leave_summary'] = {
                    'cl_remaining': max(0, LEAVE_TYPES['CL'][1] - cl_used),
                    'el_remaining': max(0, LEAVE_TYPES['EL'][1] - el_used),
                    'total_taken': tot_used,
                    'pending_count': sum(1 for l in s_leaves if l.get('status') not in ['approved', 'rejected', 'cancelled'])
                }

            all_approved = list(db.leaves.find({'status': 'approved'}))
            tot_count = len(all_approved) or 1
            dist = []
            colors = {'CL': '#3b82f6', 'EL': '#10b981', 'DH': '#f59e0b', 'SPL': '#8b5cf6', 'LWP': '#ef4444'}
            for code, (label, max_days) in LEAVE_TYPES.items():
                cnt = sum(1 for l in all_approved if l.get('type') == code)
                pct = round((cnt / tot_count) * 100) if tot_count else 0
                dist.append({'code': code, 'label': label, 'count': cnt, 'percent': pct, 'color': colors.get(code, '#64748b')})

            faculty_on_leave = list(db.leaves.find({
                'role': {'': 'student'},
                'status': 'approved',
                'start': {'': today_str},
                'end': {'': today_str}
            }))

            final_approvals = list(db.leaves.find({'status': 'principal'}).sort('created', -1))
            college_timetable = list(db.timetable.find({}).sort([('day', 1), ('start_time', 1)]))[:8]

            principal_data = {
                'staff_list': staff_list,
                'distribution': dist,
                'faculty_on_leave': faculty_on_leave,
                'final_approvals': final_approvals,
                'timetable': college_timetable,
                'approved_today': sum(1 for l in all_approved if any(h.get('action') == 'approve' and str(h.get('at', ''))[:10] == today_str for h in l.get('history', []))),
                'rejected_today': sum(1 for l in db.leaves.find({'status': 'rejected'}) if any(h.get('action') == 'reject' and str(h.get('at', ''))[:10] == today_str for h in l.get('history', [])))
            }

        elif role == 'teacher':
            my_students = list(db.users.find({'advisor_id': g.user['_id'], 'role': 'student', 'active': True}))
            student_ids = [s['_id'] for s in my_students]
            student_leaves = list(db.leaves.find({'user_id': {'': student_ids}}).sort('created', -1))
            pending_student_leaves = [l for l in student_leaves if l.get('status') == 'teacher']

            classes_affected = 0
            if current_request:
                my_sessions = list(db.timetable.find({'teacher_id': g.user['_id']}))
                classes_affected = len(my_sessions)

            teacher_data = {
                'my_students': my_students,
                'student_leaves': student_leaves,
                'pending_student_leaves': pending_student_leaves,
                'classes_affected': classes_affected
            }

        elif role == 'student':
            advisor = None
            if g.user.get('advisor_id'):
                advisor = db.users.find_one({'_id': g.user['advisor_id']})
            my_leaves = list(db.leaves.find({'user_id': g.user['_id']}).sort('created', -1))
            student_data = {
                'advisor': advisor,
                'my_leaves': my_leaves,
                'days_taken': sum(l['days'] for l in my_leaves if l.get('status') == 'approved'),
                'my_class': g.user.get('class_name', 'General')
            }

        elif role == 'hod':
            dept_pending = list(db.leaves.find({'department': g.user.get('department'), 'status': 'hod'}).sort('created', -1))
            dept_staff_on_leave = list(db.leaves.find({
                'department': g.user.get('department'),
                'role': {'': 'student'},
                'status': 'approved',
                'start': {'': today_str},
                'end': {'': today_str}
            }))
            hod_data = {
                'dept_pending': dept_pending,
                'dept_staff_on_leave': dept_staff_on_leave,
                'dept_teachers': list(db.users.find({'department': g.user.get('department'), 'role': 'teacher', 'active': True}))
            }

        return render_template(
            'dashboard.html',
            title='Dashboard',
            stats=stats,
            leaves=leaves[:6],
            departments=departments,
            people=users,
            sessions=get_timetable()[:5],
            pending=pending,
            slot_info=slot_info,
            current_request=current_request,
            queue_pos=queue_pos,
            principal_data=principal_data,
            teacher_data=teacher_data,
            student_data=student_data,
            hod_data=hod_data
        )

    @app.route('/leaves/apply', methods=['GET', 'POST'])
    @login_required
    def apply_leave():
        if g.user['role'] in ['admin', 'principal']:
            abort(403)
        teachers = list(db.users.find({'role': 'teacher', 'department': g.user['department'], 'active': True, '_id': {'$ne': g.user['_id']}}))
        if request.method == 'POST':
            try:
                start, end = date.fromisoformat(request.form['start']), date.fromisoformat(request.form['end'])
                kind, reason = request.form['type'], request.form['reason'].strip()
                if start < date.today() or end < start or start.year != end.year or (end-start).days > 60 or kind not in LEAVE_TYPES or not 5 <= len(reason) <= 1000:
                    raise ValueError()
                substitute = request.form.get('substitute', '')
                if substitute and substitute not in [u['_id'] for u in teachers]:
                    raise ValueError()
            except (KeyError, ValueError):
                flash('Use valid dates in one calendar year (maximum 61 days), a leave type, and a reason of 5–1000 characters.', 'error')
                return render_template('apply.html', title='Apply leave', teachers=teachers), 400
            days = (end-start).days + 1
            with workflow_lock():
                existing = list(db.leaves.find({'user_id': g.user['_id'], 'status': {'$nin': ['rejected', 'cancelled']}}))
                if any(x['start'] <= end.isoformat() and x['end'] >= start.isoformat() for x in existing):
                    flash('You already have a request covering these dates.', 'error')
                    return redirect(url_for('apply_leave'))
                used = sum(x['days'] for x in existing if x['type'] == kind and x['start'][:4] == str(start.year))
                if used + days > LEAVE_TYPES[kind][1]:
                    flash('This exceeds your remaining leave allowance, including pending requests.', 'error')
                    return redirect(url_for('apply_leave'))
                stages = {'student': ['teacher'], 'teacher': ['hod', 'dean', 'principal'], 'staff': ['hod', 'dean', 'principal'], 'hod': ['dean', 'principal'], 'dean': ['principal']}[g.user['role']]
                leave = {'_id': secrets.token_hex(8), 'reference': 'LV-' + secrets.token_hex(3).upper(), 'user_id': g.user['_id'], 'name': g.user['name'],
                         'role': g.user['role'], 'department': g.user['department'], 'type': kind, 'start': start.isoformat(), 'end': end.isoformat(),
                         'days': days, 'reason': reason, 'substitute': substitute, 'stages': stages, 'status': stages[0], 'created': now(), 'history': []}
                if g.user['role'] != 'student' and not has_slot(leave):
                    leave['status'] = 'waiting'
                db.leaves.insert_one(leave)
                notify(g.user['_id'], f"Request {leave['reference']} submitted successfully.")
                if leave['status'] != 'waiting':
                    notify_reviewers(leave)
            flash('Leave application submitted.', 'success')
            return redirect(url_for('leaves'))
        return render_template('apply.html', title='Apply leave', teachers=teachers)

    @app.get('/leaves')
    @login_required
    def leaves():
        records = scoped_leaves() if request.args.get('view') == 'approvals' else list(db.leaves.find({'user_id': g.user['_id']}).sort('created', -1))
        if request.args.get('view') == 'approvals':
            records = [x for x in records if can_approve(x)]
        status = request.args.get('status', 'all')
        if status != 'all':
            records = [x for x in records if x['status'] == status or status == 'pending' and x['status'] in ACTIVE[:-1] + ['waiting']]
        return render_template('leaves.html', title='Pending approvals' if request.args.get('view') == 'approvals' else 'My leave requests', leaves=records, status=status)

    @app.post('/leaves/<lid>/decision')
    @login_required
    def decision(lid):
        with workflow_lock():
            leave = db.leaves.find_one({'_id': lid})
            if not leave or not can_approve(leave):
                abort(403)
            action = request.form.get('action')
            if action not in ['approve', 'reject']:
                abort(400)
            note = request.form.get('note', '').strip()[:500]
            if action == 'reject' and not note:
                flash('Add a short reason before rejecting the request.', 'error')
                return redirect(url_for('leaves', view='approvals'))
            idx = leave['stages'].index(leave['status'])
            status = 'rejected' if action == 'reject' else leave['stages'][idx+1] if idx+1 < len(leave['stages']) else 'approved'
            db.leaves.update_one({'_id': lid, 'status': leave['status']}, {'$set': {'status': status}, '$push': {'history': {'by': g.user['name'], 'role': g.user['role'], 'action': action, 'note': note, 'at': now()}}})
            notify(leave['user_id'], f"{leave['reference']}: {g.user['role'].title()} {action}d your request. {note}")
            leave['status'] = status
            if status in ACTIVE[:-1]:
                notify_reviewers(leave)
            promote()
        flash('Decision recorded.', 'success')
        return redirect(url_for('leaves', view='approvals'))

    @app.post('/leaves/<lid>/cancel')
    @login_required
    def cancel(lid):
        with workflow_lock():
            leave = db.leaves.find_one({'_id': lid, 'user_id': g.user['_id']})
            if not leave or leave['start'] < date.today().isoformat() or leave['status'] in ['cancelled', 'rejected']:
                abort(400)
            db.leaves.update_one({'_id': lid}, {'$set': {'status': 'cancelled'}, '$push': {'history': {'by': g.user['name'], 'role': g.user['role'], 'action': 'cancel', 'note': '', 'at': now()}}})
            promote()
        flash('Request cancelled and slot released.', 'success')
        return redirect(url_for('leaves'))

    @app.get('/queue')
    @login_required
    def queue():
        # Staff see department queue names and dates, never other users' private reasons.
        q = {'role': {'$ne': 'student'}, 'status': {'$in': ACTIVE[:-1] + ['waiting']}}
        if g.user['role'] not in ['admin', 'dean', 'principal']:
            q['department'] = g.user['department']
        if g.user['role'] == 'student':
            q['user_id'] = g.user['_id']
        return render_template('queue.html', title='Leave slot management', leaves=list(db.leaves.find(q).sort('created', 1)), capacity=os.getenv('STAFF_LEAVE_SLOTS', '3'))

    def get_timetable():
        q = {}
        if g.user['role'] == 'teacher':
            q = {'teacher_id': g.user['_id']}
        elif g.user['role'] == 'student':
            q = {'class_name': g.user.get('class_name'), 'department': g.user['department']}
        elif g.user['role'] in ['hod', 'staff']:
            q = {'department': g.user['department']}
        return list(db.timetable.find(q).sort([('day', 1), ('start_time', 1)]))

    @app.route('/timetable', methods=['GET', 'POST'])
    @login_required
    def timetable():
        if request.method == 'POST':
            if g.user['role'] not in ['admin', 'hod']:
                abort(403)
            if request.form.get('delete'):
                q = {'_id': request.form['delete']}
                if g.user['role'] == 'hod':
                    q['department'] = g.user['department']
                db.timetable.delete_one(q)
            else:
                teacher = db.users.find_one({'_id': request.form.get('teacher_id'), 'role': 'teacher', 'active': True})
                try:
                    day = int(request.form['day'])
                    start = datetime.strptime(request.form['start_time'], '%H:%M').strftime('%H:%M')
                    end = datetime.strptime(request.form['end_time'], '%H:%M').strftime('%H:%M')
                    if not teacher or day not in range(5) or start >= end or g.user['role'] == 'hod' and teacher['department'] != g.user['department']:
                        raise ValueError()
                    fields = {k: request.form.get(k, '').strip()[:100] for k in ['subject', 'class_name', 'room']}
                    if not all(fields.values()):
                        raise ValueError()
                except (ValueError, KeyError):
                    abort(400, 'Invalid timetable entry.')
                with workflow_lock():
                    conflict = db.timetable.find_one({'day': day, 'start_time': {'$lt': end}, 'end_time': {'$gt': start}, '$or': [
                        {'teacher_id': teacher['_id']}, {'room': fields['room']}, {'class_name': fields['class_name'], 'department': teacher['department']}]})
                    if conflict:
                        flash('This time conflicts with the teacher, classroom, or class timetable.', 'error')
                    else:
                        db.timetable.insert_one(dict(_id=secrets.token_hex(8), day=day, start_time=start, end_time=end, teacher_id=teacher['_id'], teacher=teacher['name'], department=teacher['department'], **fields))
                        flash('Timetable entry added.', 'success')
            return redirect(url_for('timetable'))
        teachers = list(db.users.find({'role': 'teacher', 'active': True, **({'department': g.user['department']} if g.user['role'] == 'hod' else {})}))
        return render_template('timetable.html', title='Academic timetable', sessions=get_timetable(), teachers=teachers)

    @app.get('/people')
    @login_required
    def people():
        return render_template('people.html', title='College directory', people=scoped_users(), leaves=scoped_leaves())

    @app.route('/admin', methods=['GET', 'POST'])
    @login_required
    def admin():
        if g.user['role'] != 'admin':
            abort(403)
        if request.method == 'POST':
            fields = {k: request.form.get(k, '').strip() for k in ['name', 'email', 'role', 'department', 'class_name', 'advisor_id']}
            fields['email'] = fields['email'].lower()
            password = request.form.get('password', '')
            if fields['role'] not in ROLES or '@' not in fields['email'] or not fields['name'] or not fields['department'] or len(password) < 6:
                flash('Complete all required fields. Temporary passwords need at least 6 characters.', 'error')
            elif fields['role'] == 'student' and not db.users.find_one({'_id': fields['advisor_id'], 'role': 'teacher', 'department': fields['department'], 'active': True}):
                flash('Assign a teacher from the student’s department.', 'error')
            else:
                try:
                    db.users.insert_one(dict(_id=secrets.token_hex(8), password=generate_password_hash(password), active=True, must_change=True, version=0, **fields))
                except DuplicateKeyError:
                    flash('An account with this email already exists.', 'error')
                else:
                    body, html = welcome_email(fields['name'], fields['email'], password, fields['role'], fields['department'])
                    try:
                        send_email(fields['email'], '🎉 Welcome to FacultyFlow — your login details', body, html=html)
                    except (OSError, smtplib.SMTPException, KeyError, ValueError):
                        app.logger.error('Account welcome email delivery failed. Check SMTP configuration.')
                        db.users.update_one({'email': fields['email']}, {'$set': {'welcome_email_status': 'failed'}})
                        flash('Account created, but the welcome email could not be sent. Do not create the account again. Share the temporary password directly, or ask the user to use Forgot password once email delivery is working.', 'error')
                    else:
                        db.users.update_one({'email': fields['email']}, {'$set': {'welcome_email_status': 'sent'}})
                        flash('Account created. Login details were sent by email. The user must change their temporary password at first login.', 'success')
        delete_target = None
        if request.args.get('confirm_delete'):
            delete_target = db.users.find_one({'_id': request.args.get('confirm_delete')})
        return render_template('admin.html', title='Account management', people=list(db.users.find({}, {'password': 0})), delete_target=delete_target)

    @app.post('/admin/users/<uid>/delete')
    @login_required
    def delete_user(uid):
        if g.user['role'] != 'admin':
            abort(403)
        target = db.users.find_one({'_id': uid})
        if not target:
            flash('That account no longer exists.', 'error')
        elif target['_id'] == g.user['_id']:
            flash('You cannot delete your own administrator account.', 'error')
        elif db.users.count_documents({'advisor_id': uid, 'role': 'student'}):
            count = db.users.count_documents({'advisor_id': uid, 'role': 'student'})
            flash(f"{target['name']} is class teacher for {count} student{'s' if count != 1 else ''}. Delete or reassign those students first.", 'error')
        else:
            with workflow_lock():
                db.leaves.delete_many({'user_id': uid})
                db.leaves.update_many({'substitute': uid}, {'$set': {'substitute': ''}})
                db.timetable.delete_many({'teacher_id': uid})
                db.notifications.delete_many({'user_id': uid})
                db.otps.delete_many({'user_id': uid})
                db.users.delete_one({'_id': uid})
                promote()
            flash(f"{target['name']}'s account was deleted.", 'success')
        return redirect(url_for('admin'))

    @app.get('/balance')
    @login_required
    def balance():
        records = list(db.leaves.find({'user_id': g.user['_id'], 'start': {'$gte': f'{date.today().year}-01-01', '$lte': f'{date.today().year}-12-31'}, 'status': {'$nin': ['rejected', 'cancelled']}}))
        balances = [{'code': k, 'name': v[0], 'total': v[1], 'used': sum(x['days'] for x in records if x['type'] == k and x['status'] == 'approved'), 'reserved': sum(x['days'] for x in records if x['type'] == k and x['status'] != 'approved')} for k, v in LEAVE_TYPES.items()]
        return render_template('balance.html', title='Leave balance', balances=balances)

    @app.route('/notifications', methods=['GET', 'POST'])
    @login_required
    def notifications():
        if request.method == 'POST':
            db.notifications.update_many({'user_id': g.user['_id']}, {'$set': {'read': True}})
            return redirect(url_for('notifications'))
        return render_template('notifications.html', title='Notifications', notices=list(db.notifications.find({'user_id': g.user['_id']}).sort('created', -1)))

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'photo' or 'photo' in request.files or 'remove_photo' in request.form:
                if 'remove_photo' in request.form:
                    db.users.update_one({'_id': g.user['_id']}, {'$unset': {'photo': ''}})
                    g.user.pop('photo', None)
                    flash('Profile photo removed.', 'info')
                    return redirect(url_for('profile'))

                photo_file = request.files.get('photo')
                if not photo_file or not photo_file.filename:
                    flash('Please select an image file to upload.', 'error')
                    return redirect(url_for('profile'))

                try:
                    data_url = process_avatar(photo_file)
                    db.users.update_one({'_id': g.user['_id']}, {'$set': {'photo': data_url}})
                    g.user['photo'] = data_url
                    flash('Profile photo updated successfully.', 'success')
                except ValueError as ve:
                    flash(str(ve), 'error')
                except Exception:
                    flash('Failed to process image. Image should be less then 30 mb.', 'error')
                return redirect(url_for('profile'))

            else:
                password = request.form.get('password', '')
                if not check_password_hash(g.user['password'], request.form.get('current_password', '')):
                    flash('Current password is incorrect. Enter the password you used to sign in, or use Forgot password from the login page.', 'error')
                elif len(password) < 6:
                    flash('Your new password must contain at least 6 characters.', 'error')
                else:
                    db.users.update_one({'_id': g.user['_id']}, {'$set': {'password': generate_password_hash(password), 'must_change': False}, '$inc': {'version': 1}})
                    session['version'] = g.user.get('version', 0) + 1
                    flash('Password changed.', 'success')
                    return redirect(url_for('dashboard'))
        return render_template('profile.html', title='Profile & security')

    @app.route('/substitutes', methods=['GET', 'POST'])
    @login_required
    def substitutes():
        if g.user['role'] not in ['hod', 'admin']:
            abort(403)
        q = {'role': 'teacher', 'status': {'$nin': ['rejected', 'cancelled']}, 'end': {'$gte': date.today().isoformat()}}
        if g.user['role'] == 'hod':
            q['department'] = g.user['department']
        if request.method == 'POST':
            with workflow_lock():
                leave = db.leaves.find_one(dict(q, _id=request.form.get('leave_id')))
                teacher = db.users.find_one({'_id': request.form.get('teacher_id'), 'role': 'teacher', 'active': True})
                if not leave or not teacher or teacher['_id'] == leave['user_id'] or teacher['department'] != leave['department']:
                    abort(400, 'Choose another teacher from the same department.')
                unavailable = db.leaves.find_one({'user_id': teacher['_id'], 'status': {'$nin': ['rejected', 'cancelled']}, 'start': {'$lte': leave['end']}, 'end': {'$gte': leave['start']}})
                if unavailable:
                    flash('This teacher has a leave request during these dates.', 'error')
                else:
                    affected = list(db.timetable.find({'teacher_id': leave['user_id']}))
                    days = {(date.fromisoformat(leave['start']) + timedelta(days=n)).weekday() for n in range(leave['days'])}
                    conflict = any(db.timetable.find_one({'teacher_id': teacher['_id'], 'day': s['day'], 'start_time': {'$lt': s['end_time']}, 'end_time': {'$gt': s['start_time']}}) for s in affected if s['day'] in days)
                    other = db.leaves.find_one({'_id': {'$ne': leave['_id']}, 'substitute_assigned': teacher['_id'], 'status': {'$nin': ['cancelled', 'rejected']}, 'start': {'$lte': leave['end']}, 'end': {'$gte': leave['start']}})
                    if conflict or other:
                        flash('This teacher has a timetable or substitute assignment conflict.', 'error')
                    else:
                        db.leaves.update_one({'_id': leave['_id']}, {'$set': {'substitute_assigned': teacher['_id'], 'substitute_name': teacher['name']}})
                        notify(teacher['_id'], f"You are assigned to cover {leave['name']} from {leave['start']} to {leave['end']} if the leave is approved.")
                        flash('Substitute assigned.', 'success')
            return redirect(url_for('substitutes'))
        return render_template('substitutes.html', title='Substitute management', leaves=list(db.leaves.find(q)), teachers=list(db.users.find({'role': 'teacher', 'active': True})))

    @app.get('/calendar')
    @login_required
    def calendar():
        import calendar as cal
        try:
            raw_month = request.args.get('month', date.today().strftime('%Y-%m'))
            month = date.fromisoformat(raw_month + '-01' if len(raw_month) == 7 else raw_month).replace(day=1)
            if not 1901 <= month.year <= 2099:
                abort(400)
        except ValueError:
            abort(400)
        records = [x for x in scoped_leaves() if x['status'] == 'approved']
        weeks = cal.Calendar(firstweekday=6).monthdatescalendar(month.year, month.month)
        events = {d.isoformat(): [x for x in records if x['start'] <= d.isoformat() <= x['end']] for week in weeks for d in week}
        holiday_file = Path(__file__).parent / 'data' / f'karnataka_holidays_{month.year}.json'
        holiday_data = json.loads(holiday_file.read_text(encoding='utf-8')) if holiday_file.exists() else None
        holidays = [h for h in holiday_data['holidays'] if h['date'].startswith(month.strftime('%Y-%m'))] if holiday_data else []
        holiday_map = {d.isoformat(): [h for h in holidays if h['date'] == d.isoformat()] for week in weeks for d in week}
        try:
            selected = date.fromisoformat(request.args.get('day', date.today().isoformat() if month == date.today().replace(day=1) else month.isoformat()))
            if selected.replace(day=1) != month:
                selected = month
        except ValueError:
            abort(400)
        return render_template('calendar.html', title='College calendar', month=month, weeks=weeks, events=events,
                               holidays=holidays, holiday_map=holiday_map, holiday_data=holiday_data, selected=selected,
                               gazetted=sum(h['type'] == 'gazetted' for h in holidays),
                               restricted=sum(h['type'] == 'restricted' for h in holidays),
                               prev=(month-timedelta(days=1)).replace(day=1), next=(month+timedelta(days=32)).replace(day=1))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def error(e):
        return render_template('error.html', title='Unable to complete request', error=e), e.code

    @app.errorhandler(413)
    def file_too_large(e):
        flash('Image exceeds limit. Image should be less then 30 mb.', 'error')
        return redirect(url_for('profile'))

    @app.cli.command('create-admin')
    @click.option('--email', prompt=True)
    @click.option('--name', prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(email, name, password):
        if len(password) < 6:
            raise click.ClickException('Password must be at least 6 characters.')
        try:
            db.users.insert_one({'_id': secrets.token_hex(8), 'email': email.lower().strip(), 'name': name, 'role': 'admin', 'department': 'Administration', 'password': generate_password_hash(password), 'active': True, 'version': 0})
        except DuplicateKeyError:
            raise click.ClickException('Email already exists.')
        click.echo('Administrator created.')

    @app.cli.command('unlock-workflow')
    def unlock_workflow():
        """Use only with ALL web workers stopped after a process crash."""
        db.locks.delete_one({'_id': 'leave-workflow'})
        click.echo('Workflow lock removed. Restart web workers.')
    return app

def seed(db):
    names = {'admin': 'Aditi Sharma', 'principal': 'Dr. Reddy', 'dean': 'Dr. Kumar', 'hod': 'Dr. Rahul', 'teacher': 'Dr. Ananya', 'staff': 'Meera Nair', 'student': 'Avani Shah'}
    password = generate_password_hash('College@123')
    for role, name in names.items():
        db.users.insert_one({'_id': role, 'name': name, 'email': f'{role}@college.edu', 'role': role, 'department': 'Computer Science', 'class_name': '5th Sem A', 'advisor_id': 'teacher', 'password': password, 'active': True, 'version': 0})
    db.users.insert_one({'_id': 'teacher2', 'name': 'Prof. Arjun Rao', 'email': 'arjun@college.edu', 'role': 'teacher', 'department': 'Computer Science', 'password': password, 'active': True, 'version': 0})
    for day in range(5):
        for i, subject in enumerate(['Computer Networks', 'Theory of Computation', 'Unix Programming']):
            db.timetable.insert_one({'_id': f'slot-{day}-{i}', 'day': day, 'start_time': f'{9+i:02}:00', 'end_time': f'{10+i:02}:00', 'subject': subject, 'class_name': '5th Sem A', 'room': '204', 'teacher_id': 'teacher', 'teacher': 'Dr. Ananya', 'department': 'Computer Science'})
    for i, (uid, status) in enumerate([('teacher', 'hod'), ('staff', 'approved'), ('student', 'teacher')]):
        start = (date.today()+timedelta(days=i+2)).isoformat()
        u = db.users.find_one({'_id': uid})
        db.leaves.insert_one({'_id': f'demo-{i}', 'reference': f'LV-102{i}', 'user_id': uid, 'name': u['name'], 'role': uid, 'department': u['department'], 'type': 'CL', 'start': start, 'end': start, 'days': 1, 'reason': 'Family commitment', 'stages': ['teacher'] if uid == 'student' else ['hod', 'dean', 'principal'], 'status': status, 'history': [], 'created': now()})
    for role in ROLES:
        db.notifications.insert_one({'user_id': role, 'message': 'Welcome to FacultyFlow. Your college workspace is ready.', 'read': False, 'created': now()})

app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    app.run(host='127.0.0.1', port=port, debug=True)
