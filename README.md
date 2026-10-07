# FacultyFlow — College Leave & Academic Scheduling

A Python Flask application with MongoDB, inspired by the supplied navy sidebar / blue accent UI. Includes Principal, Dean, HOD, Teacher, Non-teaching Staff, Student and Administrator accounts.

## Run on this computer

A virtual environment has been created in `.venv`. In PowerShell, from this folder:

```powershell
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. No environment activation is required.

Demo login: `principal@college.edu`, `teacher@college.edu`, `student@college.edu`, `hod@college.edu`, `dean@college.edu`, `staff@college.edu`, or `admin@college.edu`. Password for all demo accounts: `College@123`.

Demo mode is explicitly enabled initially. It uses an in-memory MongoDB-compatible mock, resets on restart, and never connects to your MongoDB. Run only one worker in demo mode. Real database mode never seeds demo users.

## Connect your MongoDB

Edit `.env` (already created and excluded from Git):

```dotenv
DEMO_MODE=false
MONGODB_URI=mongodb+srv://YOUR_USER:YOUR_PASSWORD@YOUR_CLUSTER/
MONGODB_DB=facultyflow
OTP_CONSOLE=false
```

Keep the generated `SECRET_KEY`. In Atlas, create a database user and allow your computer's IP address. URL-encode special characters in the database password. Stop the demo server, then create your first real administrator:

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app create-admin
.\.venv\Scripts\python.exe app.py
```

The command prompts for name, email and a hidden password. Log in and use **Manage accounts** to create the principal, dean, HODs, teachers, staff and students. Create teachers before their students. Department names must match exactly; student class names must match timetable class names. New users receive a welcome email with their login email, temporary password, and sign-in link, and must replace their temporary password at first login. Account creation reports an email delivery failure while keeping the created account. Passwords remain hashed in MongoDB; the temporary plaintext is used only for the outgoing welcome email. Set `APP_BASE_URL` in `.env` to your accessible portal URL when deployed; the default `http://127.0.0.1:5000` works only on the computer running the app. Welcome emails are sent for new accounts created through Manage accounts, not retroactively for existing accounts or the bootstrap CLI administrator. There is no public self-registration or user-selectable privilege at login.

## Role behavior

| Role | Access and responsibilities |
| --- | --- |
| Principal | College-wide timetable, directory, leave counts and balances, final staff approval |
| Dean | College-wide view and second-level staff approval |
| HOD | Own department, staff review, timetable entries and substitutes |
| Teacher | Own timetable and leave, assigned students and student leave approvals |
| Non-teaching staff | Own leave, balance and department schedule |
| Student | Own leave and class timetable; assigned teacher approves leave |
| Admin | College directory, account creation and timetable management |

Teachers and staff: **HOD → Dean → Principal**. HOD leave: **Dean → Principal**. Dean leave: **Principal**. Students: **Assigned teacher**. Principal/admin leave submission is intentionally not available because no higher approving authority was specified. Administrators do not bypass academic approval.

## Features and project assumptions

- Role checks and department/student scoping are enforced on the server.
- Leave types from the sketch: CL, EL, DH, SPL, LWP. Demo annual allowances are 12, 15, 5, 5, 365 days; change `LEAVE_TYPES` in `app.py` to match college policy. DH is provisionally named Duty Holiday; confirm your college's terminology.
- Calendar-day counting includes weekends. Requests cannot cross calendar years or exceed 61 days. Pending/queued requests reserve allowance. Rejected/cancelled requests release it.
- `STAFF_LEAVE_SLOTS=3` limits concurrent staff requests per department on each requested date. Student leave does not consume staff slots. Waiting requests are reconsidered in submission order after cancellation/rejection, skipping requests whose dates still lack capacity.
- Staff slot occupancy includes pending approval and approved requests; it is not an attendance tracker.
- Personal request history, decision notes, notification inbox, leave balances and monthly calendar.
- Timetable checks prevent teacher, room and class collisions. Timetable is a recurring Monday–Friday week.
- Teachers may suggest substitutes. HOD/admin assignments check overlapping leave, regular classes and other substitute assignments. The notification is conditional until leave approval; the weekly timetable remains the normal teaching schedule.
- Passwords use Werkzeug scrypt hashing. Sessions are HTTP-only and invalidate after password reset. POST forms require CSRF tokens. Login and recovery are rate-limited.
- The reference design's colors, navigation, cards and page structure are recreated responsively. The login illustration is CSS architecture, not the college photograph. Supporting document uploads, SMS OTP, Google sign-in and attendance are not implemented.
- Fonts load from Google Fonts when online; system fonts work offline.

## OTP: Gmail or Firebase?

For this Python college project, **email OTP through Gmail SMTP** is the simplest starting choice. Firebase is Google's authentication service; Google Sign-In is a separate login method, not an email OTP sender. This project implements six-digit email OTP for password recovery (not a second factor on every login).

1. Enable 2-Step Verification on the sending Google account.
2. Create an **App Password**, if your account supports it. Do not use your normal Gmail password. Some organizational accounts restrict App Passwords; use an approved SMTP provider in that case.
3. Configure `.env`:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-sender@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=your-sender@gmail.com
OTP_CONSOLE=false
```

The app uses STARTTLS, hashes codes in MongoDB, expires them after five minutes, limits verification to five attempts, consumes codes once, and throttles sends. In demo mode only, `OTP_CONSOLE=true` prints codes in the server terminal instead of sending mail. Real mode never prints OTPs. SMTP delivery needs your credentials and has not been verified without them.

If your college specifically requires **mobile-number SMS OTP**, Firebase Phone Authentication is the appropriate option, but it requires an additional integration, reCAPTCHA/domain configuration and applicable SMS billing. It is not configured in this project.

Official references: [Google App Passwords](https://support.google.com/accounts/answer/185833), [Firebase phone authentication](https://firebase.google.com/docs/auth/web/phone-auth), [Firebase authentication limits](https://firebase.google.com/docs/auth/limits).

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use the isolated in-memory database, not your configured MongoDB. Coverage includes role pages, forbidden access, approval stages, student scope, department isolation, duplicate leave, balance limits, slot promotion, CSRF, login throttling, reset replay/session revocation, timetable collisions, first-login password change and substitute assignment.

## Install on another computer

Install Python 3.11 or newer, then:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set a strong random `SECRET_KEY` before using real mode. The included `.env` on this computer already has one. Do not copy `.venv` between computers.

For a hosted deployment, use HTTPS with `COOKIE_SECURE=true` and a production WSGI server, for example `waitress-serve --host=127.0.0.1 --port=5000 --call app:create_app` behind a reverse proxy. Keep the proxy/rate-limit configuration appropriate to the host. Workflow mutations use a MongoDB lock document; if a process crashes during an update, stop all workers and run `python -m flask --app app:create_app unlock-workflow` before restarting. Multi-document changes are serialized but not transactional; production deployment should add MongoDB transactions, operational monitoring and backups. The current build is intended for a college project and local demonstration.

## Structure

`app.py` — routes, permissions, data validation and MongoDB operations. `templates/` — Jinja screens. `static/style.css` — responsive UI. `tests/` — integration tests. `.env` — private configuration. `.env.example` — shareable configuration template.

MongoDB collections: `users`, `leaves`, `timetable`, `notifications`, `otps`, `throttles`, `locks`. String IDs link accounts to leave applications, class advisors and schedules. Unique email and TTL indexes are created at startup.
