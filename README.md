# Jerry Agu's Dashboard

An internal communication board for small teams — department feeds, direct messages, @mentions, and an admin panel — built with **FastAPI** on the backend and **vanilla JavaScript** on the frontend (no build step, no framework).

Started as a functional comms tool, then hardened into a genuinely production-minded auth system: mandatory MFA, server-side session revocation, rate limiting, security headers, and login-anomaly detection.

---

## Features

**Core**
- Department-based feeds — posts are scoped to a department, or posted generally to everyone
- Direct messages, including "send this as a DM instead" when a post @mentions exactly one person
- Threaded replies/comments on posts
- Notifications with unread badge count
- File/image/video attachments on posts
- Admin panel: add teammates, manage members, view the access log
- Profile pictures

**Security**
- **Mandatory MFA** (TOTP, e.g. Google Authenticator) with one-time recovery codes, enforced on every account by default
- **Account lockout** after repeated failed logins, independent of IP-based rate limiting
- **Server-side sessions** — logout, and "sign out of all other devices," actually revoke tokens server-side instead of just clearing `localStorage`. Each session tracks IP, user-agent, and last-active time, viewable from your profile.
- **Rate limiting** (via `slowapi`) on login, MFA verification/enrollment, and file uploads — per-IP for auth endpoints, per-user for uploads
- **Security headers** on every response — CSP, HSTS (on HTTPS), `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and `Cache-Control: no-store` on all API responses
- **Login-anomaly detection** — signing in from a new IP or device pops a dedicated "Was this you?" security alert (separate from the regular notification feed), with a one-click "sign out of all other devices" response
- Password policy enforcement (length, character variety) on account creation

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, SQLAlchemy (SQLite) |
| Auth | JWT (`python-jose`), bcrypt password hashing, TOTP MFA (`pyotp`) |
| Rate limiting | `slowapi` |
| Frontend | Vanilla JS, HTML, CSS — no framework, no bundler |

---

## Getting started

### 1. Clone and set up a virtual environment

```bash
git clone https://github.com/jayy-agu/api.git
cd api
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
SECRET_KEY=replace-with-a-real-random-value
ACCESS_TOKEN_EXPIRE_MINUTES=120
PRE_AUTH_TOKEN_EXPIRE_MINUTES=5
MAX_FAILED_LOGIN_ATTEMPTS=3
LOCKOUT_MINUTES=30
MFA_MANDATORY=true
```

Generate a real `SECRET_KEY` with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> `.env` is git-ignored on purpose — never commit real secrets here.

### 4. Run the app

```bash
uvicorn main:app --reload
```

Visit **http://127.0.0.1:8000**. The very first account you create becomes the super admin.

### 5. (Existing installs only) Run the migration

If you're pulling this update onto a database that already has data, run the additive migration once so old rows aren't touched:

```bash
python migrate.py
```

Safe to run more than once — it only adds columns/tables that don't already exist.

---

## Project structure

```
api/
├── main.py              # App entrypoint, middleware, router registration
├── models.py             # SQLAlchemy models
├── schemas.py             # Pydantic request/response schemas
├── security.py            # Password hashing, JWT creation/verification
├── deps.py               # Auth dependency (get_current_user, role checks)
├── ratelimit.py            # slowapi limiter config
├── migrate.py             # Additive SQLite migration script
├── audit.py               # Access-log helper
├── mfa.py                # TOTP secret generation/verification, recovery codes
├── routers/
│   ├── auth.py            # Login, MFA, sessions, logout
│   ├── posts.py            # Feed, comments, uploads
│   ├── notifications.py       # Activity feed + security alerts
│   ├── users.py            # Roster, profile
│   └── admin.py            # Member management, access log
└── static/
    ├── index.html
    ├── styles.css
    └── app.js             # Entire frontend — no build step
```

---

## A note on security scope

MFA, session revocation, rate limiting, security headers, and login-anomaly alerts are all implemented and tested manually against their acceptance criteria. A couple of things worth knowing if you extend this:

- Login-anomaly detection compares IP address and user-agent against your last session — it flags *any* change (new wifi, new phone), not specifically "impossible travel." True impossible-travel detection (distance/speed between two logins) would need a GeoIP lookup and isn't implemented here.
- This is a single-SQLite-file app — fine for a small internal team, not built for concurrent write-heavy scale.

---

## License

Add a license of your choice here (e.g. MIT) if you want others to reuse this code.
