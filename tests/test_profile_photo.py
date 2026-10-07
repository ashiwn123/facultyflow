import io
import pytest
from PIL import Image
from app import create_app


@pytest.fixture
def app(monkeypatch):
    outbox = []
    monkeypatch.setattr('app.send_email', lambda recipient, subject, body, html=None: outbox.append({'recipient': recipient, 'subject': subject, 'body': body, 'html': html}))
    application = create_app({'TESTING': True, 'DEMO_MODE': True, 'SECRET_KEY': 'test-secret'})
    application.extensions['outbox'] = outbox
    return application


def login(client, role='admin'):
    client.get('/login')
    with client.session_transaction() as s:
        csrf = s['csrf']
    return client.post('/login', data={'csrf': csrf, 'email': f'{role}@college.edu', 'password': 'College@123'}, follow_redirects=True)


def test_profile_photo_upload_and_replacement(app):
    client = app.test_client()
    db = app.extensions['db']
    login(client, 'admin')

    # Initial state: no custom photo
    user = db.users.find_one({'email': 'admin@college.edu'})
    assert 'photo' not in user

    # Create dummy image in memory
    img = Image.new('RGB', (200, 200), color='teal')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    buf.seek(0)

    # Upload photo
    with client.session_transaction() as s:
        csrf = s['csrf']

    resp = client.post('/profile', data={
        'csrf': csrf,
        'action': 'photo',
        'photo': (buf, 'avatar.jpg')
    }, follow_redirects=True)

    assert resp.status_code == 200
    assert b'Profile photo updated successfully' in resp.data

    # Check saved in DB
    user = db.users.find_one({'email': 'admin@college.edu'})
    assert 'photo' in user
    assert user['photo'].startswith('data:image/jpeg;base64,')

    # Replacement: upload a different photo
    img2 = Image.new('RGB', (150, 150), color='purple')
    buf2 = io.BytesIO()
    img2.save(buf2, format='PNG')
    buf2.seek(0)

    with client.session_transaction() as s:
        csrf = s['csrf']

    resp2 = client.post('/profile', data={
        'csrf': csrf,
        'action': 'photo',
        'photo': (buf2, 'new_avatar.png')
    }, follow_redirects=True)

    assert resp2.status_code == 200
    user2 = db.users.find_one({'email': 'admin@college.edu'})
    assert user2['photo'] != user['photo']

    # Remove photo
    with client.session_transaction() as s:
        csrf = s['csrf']

    resp3 = client.post('/profile', data={
        'csrf': csrf,
        'action': 'photo',
        'remove_photo': '1'
    }, follow_redirects=True)

    assert resp3.status_code == 200
    assert b'Profile photo removed' in resp3.data
    user3 = db.users.find_one({'email': 'admin@college.edu'})
    assert 'photo' not in user3


def test_invalid_image_upload_rejected(app):
    client = app.test_client()
    login(client, 'admin')

    with client.session_transaction() as s:
        csrf = s['csrf']

    # Non-image file
    buf = io.BytesIO(b'not an image content')
    resp = client.post('/profile', data={
        'csrf': csrf,
        'action': 'photo',
        'photo': (buf, 'fake.jpg')
    }, follow_redirects=True)

    assert resp.status_code == 200
    assert b'Invalid or corrupted image file' in resp.data or b'Please upload a standard image file' in resp.data


def test_image_size_limit_30mb(app):
    from app import process_avatar
    from werkzeug.datastructures import FileStorage

    # Verify process_avatar strictly rejects files > 30MB
    class OversizedFile:
        def read(self):
            return b'X' * (30 * 1024 * 1024 + 1)

    with pytest.raises(ValueError) as exc:
        process_avatar(OversizedFile())
    assert 'Image exceeds limit. Image should be less then 30 mb.' in str(exc.value)

    # Verify profile page displays the exact hint text
    client = app.test_client()
    login(client, 'admin')
    resp = client.get('/profile')
    assert resp.status_code == 200
    assert b'image should be less then 30 mb' in resp.data

