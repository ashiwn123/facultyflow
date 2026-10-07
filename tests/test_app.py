from datetime import date, timedelta
import hashlib
import hmac
import pytest
from app import create_app, now


@pytest.fixture
def app(monkeypatch):
    outbox = []
    monkeypatch.setattr('app.send_email', lambda recipient, subject, body, html=None: outbox.append({'recipient': recipient, 'subject': subject, 'body': body, 'html': html}))
    application = create_app({'TESTING': True, 'DEMO_MODE': True, 'SECRET_KEY': 'test-secret'})
    application.extensions['outbox'] = outbox
    return application


def login(client, role):
    client.get('/login')
    return post(client, '/login', email=f'{role}@college.edu', password='College@123')


def post(client, path, **data):
    with client.session_transaction() as s:
        csrf = s['csrf']
    return client.post(path, data=dict(csrf=csrf, **data), follow_redirects=True)


@pytest.mark.parametrize('role', ['admin', 'principal', 'dean', 'hod', 'teacher', 'student', 'staff'])
def test_role_pages(app, role):
    client = app.test_client()
    assert login(client, role).status_code == 200
    for path in ['/', '/leaves', '/balance', '/timetable', '/calendar', '/notifications', '/profile', '/people', '/queue']:
        assert client.get(path).status_code == 200, (role, path)
    assert client.get('/admin').status_code == (200 if role == 'admin' else 403)
    assert client.get('/substitutes').status_code == (200 if role in ['admin', 'hod'] else 403)


def test_approval_chain_and_no_self_approval(app):
    client = app.test_client()
    db = app.extensions['db']
    login(client, 'teacher')
    assert post(client, '/leaves/demo-0/decision', action='approve').status_code == 403
    for role, expected in [('hod', 'dean'), ('dean', 'principal'), ('principal', 'approved')]:
        login(client, role)
        assert post(client, '/leaves/demo-0/decision', action='approve').status_code == 200
        assert db.leaves.find_one({'_id': 'demo-0'})['status'] == expected
    assert len(db.leaves.find_one({'_id': 'demo-0'})['history']) == 3


def test_student_scope_and_teacher_approval(app):
    client = app.test_client()
    login(client, 'student')
    assert b'Dr. Ananya' not in client.get('/people').data
    assert b'LV-1020' not in client.get('/leaves?view=approvals').data
    assert post(client, '/leaves/demo-0/cancel').status_code == 400
    login(client, 'teacher')
    assert post(client, '/leaves/demo-2/decision', action='approve').status_code == 200
    assert app.extensions['db'].leaves.find_one({'_id': 'demo-2'})['status'] == 'approved'


def test_department_boundary(app):
    db = app.extensions['db']
    db.users.update_one({'_id': 'teacher'}, {'$set': {'department': 'Mechanical'}})
    db.leaves.update_one({'_id': 'demo-0'}, {'$set': {'department': 'Mechanical'}})
    client = app.test_client()
    login(client, 'hod')
    assert b'Dr. Ananya' not in client.get('/people').data
    assert post(client, '/leaves/demo-0/decision', action='approve').status_code == 403


def test_submit_overlap_and_balance(app):
    client = app.test_client()
    login(client, 'student')
    start = date.today() + timedelta(days=10)
    data = dict(start=start.isoformat(), end=start.isoformat(), type='CL', reason='Family appointment')
    assert post(client, '/leaves/apply', **data).status_code == 200
    response = post(client, '/leaves/apply', **data)
    assert b'already have a request' in response.data
    data.update(start=(start+timedelta(days=1)).isoformat(), end=(start+timedelta(days=20)).isoformat())
    assert b'exceeds your remaining' in post(client, '/leaves/apply', **data).data


def test_queue_promotes_when_slot_released(app, monkeypatch):
    monkeypatch.setenv('STAFF_LEAVE_SLOTS', '1')
    client = app.test_client()
    login(client, 'staff')
    target = app.extensions['db'].leaves.find_one({'_id': 'demo-0'})['start']
    post(client, '/leaves/apply', start=target, end=target, type='CL', reason='Personal appointment')
    db = app.extensions['db']
    waiting = db.leaves.find_one({'user_id': 'staff', 'status': 'waiting'})
    assert waiting
    login(client, 'hod')
    post(client, '/leaves/demo-0/decision', action='reject', note='Cannot arrange coverage')
    assert db.leaves.find_one({'_id': waiting['_id']})['status'] == 'hod'


def test_csrf_and_rate_limit(app):
    client = app.test_client()
    client.get('/login')
    assert client.post('/login', data={'email': 'student@college.edu'}).status_code == 400
    for _ in range(10):
        post(client, '/login', email='student@college.edu', password='wrong')
    assert login(client, 'student').status_code == 429


def test_password_reset_consumed_and_sessions_revoked(app):
    client = app.test_client()
    existing = app.test_client()
    login(existing, 'teacher')
    client.get('/login')
    with client.session_transaction() as s:
        s['reset_email'] = 'teacher@college.edu'
    key = hashlib.sha256(b'teacher@college.edu').hexdigest()
    app.extensions['db'].otps.insert_one({'_id': key, 'user_id': 'teacher', 'digest': hmac.new(b'test-secret', b'123456', hashlib.sha256).hexdigest(), 'expires': now()+timedelta(minutes=5), 'attempts': 0})
    response = post(client, '/reset-password', code='123456', password='Changed@12345')
    assert b'Password updated' in response.data
    assert app.extensions['db'].otps.find_one({'_id': key}) is None
    assert existing.get('/').status_code == 302
    assert b'Password updated' not in post(client, '/reset-password', code='123456', password='Changed@67890').data


def test_timetable_conflicts_and_permissions(app):
    client = app.test_client()
    login(client, 'admin')
    data = dict(teacher_id='teacher', day='0', start_time='09:30', end_time='10:30', subject='Python', class_name='3rd Sem B', room='501')
    assert b'conflicts with' in post(client, '/timetable', **data).data
    data.update(start_time='14:00', end_time='15:00')
    assert b'Timetable entry added' in post(client, '/timetable', **data).data
    login(client, 'student')
    assert post(client, '/timetable', **data).status_code == 403


def test_create_account_and_first_password_change(app):
    client = app.test_client()
    login(client, 'admin')
    post(client, '/admin', name='New Student', email='new@college.edu', role='student', department='Computer Science', class_name='5th Sem A', advisor_id='teacher', password='Temporary@123')
    post(client, '/logout')
    response = post(client, '/login', email='new@college.edu', password='Temporary@123')
    assert b'Set your own password' in response.data
    assert b'Password changed' in post(client, '/profile', current_password='Temporary@123', password='NewPassword@123').data


def test_substitute_assignment(app):
    client = app.test_client()
    login(client, 'hod')
    assert b'Substitute assigned' in post(client, '/substitutes', leave_id='demo-0', teacher_id='teacher2').data
    assert app.extensions['db'].leaves.find_one({'_id': 'demo-0'})['substitute_assigned'] == 'teacher2'


def test_password_minimum_six_characters(app):
    client = app.test_client()
    login(client, 'teacher')
    response = post(client, '/profile', current_password='College@123', password='Abc12')
    assert b'at least 6 characters' in response.data
    response = post(client, '/profile', current_password='College@123', password='Abc123')
    assert b'Password changed' in response.data
    post(client, '/logout')
    response = post(client, '/login', email='teacher@college.edu', password='Abc123')
    assert b'Good morning' in response.data


@pytest.mark.parametrize('new_password', ['123456789', 'Example@321'])
def test_password_change_distinguishes_current_password(app, new_password):
    client = app.test_client()
    login(client, 'teacher')
    response = post(client, '/profile', current_password='wrong-current', password=new_password)
    assert b'Current password is incorrect' in response.data
    response = post(client, '/profile', current_password='College@123', password=new_password)
    assert b'Password changed' in response.data


def test_account_welcome_email_and_duplicate(app, monkeypatch):
    client = app.test_client()
    login(client, 'admin')
    data = dict(name='New <Teacher>', email='new@example.com', role='teacher', department='Computer Science', password='Temp123')
    response = post(client, '/admin', **data)
    assert b'Login details were sent by email' in response.data
    mail = app.extensions['outbox'][0]
    assert mail['recipient'] == data['email']
    assert 'Login:' not in mail['body'] and 'http' not in mail['body'] + mail['html']
    assert 'Email address: new@example.com' in mail['body']
    assert 'Temporary password: Temp123' in mail['body']
    assert 'Temp123' in mail['html'] and 'New &lt;Teacher&gt;' in mail['html'] and '<Teacher>' not in mail['html']
    user = app.extensions['db'].users.find_one({'email': data['email']})
    assert user['must_change'] and user['welcome_email_status'] == 'sent'
    assert user['password'] != data['password']
    post(client, '/admin', **data)
    assert len(app.extensions['outbox']) == 1


def test_welcome_email_failure_keeps_account(app, monkeypatch):
    import smtplib
    def fail(*args, **kwargs):
        raise smtplib.SMTPException('Unavailable')
    monkeypatch.setattr('app.send_email', fail)
    client = app.test_client()
    login(client, 'admin')
    response = post(client, '/admin', name='Teacher', email='failed@example.com', role='teacher', department='Computer Science', password='Temp123')
    assert b'Account created, but the welcome email could not be sent' in response.data
    user = app.extensions['db'].users.find_one({'email': 'failed@example.com'})
    assert user['welcome_email_status'] == 'failed' and user['must_change']


def test_admin_delete_user_removes_data(app):
    client = app.test_client()
    db = app.extensions['db']
    login(client, 'admin')
    post(client, '/admin', name='Temp Staff', email='temp@example.com', role='staff', department='Computer Science', password='Temp123')
    uid = db.users.find_one({'email': 'temp@example.com'})['_id']
    db.leaves.insert_one({'_id': 'tmp-leave', 'user_id': uid, 'status': 'hod'})
    db.notifications.insert_one({'user_id': uid, 'message': 'x', 'read': False, 'created': now()})
    assert b'Delete' in client.get('/admin').data
    response = post(client, f'/admin/users/{uid}/delete')
    assert b"Temp Staff&#39;s account was deleted" in response.data
    assert db.users.find_one({'_id': uid}) is None
    assert db.leaves.count_documents({'user_id': uid}) == 0 and db.notifications.count_documents({'user_id': uid}) == 0


def test_delete_user_guards(app):
    client = app.test_client()
    db = app.extensions['db']
    login(client, 'admin')
    admin_id = db.users.find_one({'email': 'admin@college.edu'})['_id']
    assert b'cannot delete your own' in post(client, f'/admin/users/{admin_id}/delete').data
    assert db.users.find_one({'_id': admin_id})
    teacher = db.users.find_one({'email': 'teacher@college.edu'})
    if db.users.count_documents({'advisor_id': teacher['_id'], 'role': 'student'}):
        assert b'Delete or reassign those students first' in post(client, f"/admin/users/{teacher['_id']}/delete").data
        assert db.users.find_one({'_id': teacher['_id']})
    login(client, 'hod')
    assert post(client, f'/admin/users/{admin_id}/delete').status_code == 403
