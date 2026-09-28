
# Jerry Agu's Dashboard

An internal communication board for small teams: department feeds, direct messages, @mentions and an admin panel. The backend is **FastAPI** and the frontend is plain **JavaScript** with no framework and no build step.

# Copy this file to .env and fill in the values. Never commit the real .env.

# REQUIRED. The app will not start without these two.
# Generate with:  python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=
# Generate with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MFA_ENCRYPTION_KEY=

# Production only. Leave unset locally to use a SQLite file.
# Use the Supabase "Transaction pooler" string (port 6543) and URL-encode special characters in the password.
# DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<pooler-host>:6543/postgres

# Optional (defaults shown)
ACCESS_TOKEN_EXPIRE_MINUTES=120
PRE_AUTH_TOKEN_EXPIRE_MINUTES=5
MAX_FAILED_LOGIN_ATTEMPTS=3
LOCKOUT_MINUTES=30
MFA_MANDATORY=true


I started it as a simple message board and then spent a good part of the project on security: mandatory MFA, server-side session revocation, rate limiting, security headers and new-device alerts. There's a longer write-up of the design decisions and the weak spots in the section [Security notes](#security-notes) below.

---

## Features

**Communication**

- Department feeds (Legal, Branding, Operations, Sales, Marketing, Technical Team), or post to everyone
- Direct messages, with an offer to "send as a DM instead" when a post mentions exactly one person
- Threaded comments and @mentions
- Notifications with an unread badge
- Image, video and document attachments
- Profile pictures
- Posts older than 7 days move to an archive

**Administration**

- Four roles: `USER`, `STAFF`, `ADMIN`, `SUPER_ADMIN`
- The first account created becomes the super admin
- Add and remove people, change roles and departments, reset passwords, reset someone's MFA
- Access log of logins, lockouts and admin actions

**Security**

- Mandatory TOTP MFA (Google Authenticator, Authy and similar) with eight one-time recovery codes
- Account lockout after repeated failed logins
- Rate limiting on login, MFA and uploads
- Server-side sessions: logout and "sign out of all other devices" revoke tokens on the server
- New-device sign-in alerts
- Security headers on every response (CSP, HSTS over HTTPS, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `no-store` on the API)
- Password policy on account creation

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Uvicorn, SQLAlchemy |
| Database | SQLite locally, PostgreSQL (Supabase) in production |
| Auth | JWT (`python-jose`), bcrypt, TOTP (`pyotp`), Fernet encryption for MFA secrets |
| Rate limiting | `slowapi` |
| Frontend | Vanilla JavaScript, HTML, CSS |
| Hosting | Vercel (serverless), Supabase (database) |

---

## Getting started (local)

### 1. Clone and create a virtual environment

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

### 3. Create your `.env`

Copy the example file and fill in the two required keys:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Generate a signing key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate an MFA encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste them into `.env` as `SECRET_KEY` and `MFA_ENCRYPTION_KEY`. **The app will not start without both.** `.env` is git-ignored, so keep real values out of the repo.

### 4. Run it

```bash
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. The first account you create becomes the super admin. With no `DATABASE_URL` set, the app creates a local SQLite file.

### 5. Existing SQLite databases only

If you're updating a SQLite database that already has data, run the additive migration once:

```bash
python migrate.py
```

It only adds missing columns and tables, so it's safe to run more than once. It isn't needed for a fresh install, or for PostgreSQL, where the tables are created on first start.

---

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | yes | none | Signs all tokens |
| `MFA_ENCRYPTION_KEY` | yes | none | Encrypts MFA secrets at rest |
| `DATABASE_URL` | production | SQLite file | PostgreSQL connection string |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | 120 | Session token lifetime |
| `PRE_AUTH_TOKEN_EXPIRE_MINUTES` | no | 5 | Time allowed to enter an MFA code |
| `MAX_FAILED_LOGIN_ATTEMPTS` | no | 3 | Failures before lockout |
| `LOCKOUT_MINUTES` | no | 30 | Lockout length |
| `MFA_MANDATORY` | no | true | Require MFA on every account (set `false` only for local development) |

---

## Deploying to Vercel with Supabase

Serverless functions have no persistent disk, so a SQLite file won't work there. Use PostgreSQL.

1. **Create a Supabase project** and note the database password you set. Reset it in *Project Settings > Database* if you've lost it.
2. **Copy the pooler connection string** from *Connect* in the Supabase dashboard. Use the *Transaction pooler* (port `6543`), not the direct connection, because Vercel's network is IPv4 and the direct connection is IPv6 only.
3. **Replace `[YOUR-PASSWORD]`** with your real password. If it contains special characters such as `@`, `#` or `/`, URL-encode them (`@` becomes `%40`), or the URL will be parsed wrongly.
4. **Import the GitHub repo into Vercel.** It detects the FastAPI app from `main.py`.
5. **Add environment variables** in *Project Settings > Environment Variables* for the Production environment: `DATABASE_URL`, `SECRET_KEY` and `MFA_ENCRYPTION_KEY`, plus any others from the table above.
6. **Redeploy.** Environment variable changes only apply to new deployments.
7. **Create your admin account straight away.** Until the first user exists, anyone who reaches the signup page becomes the super admin.

Both `psycopg2-binary` and `psycopg[binary]` are in `requirements.txt`. If a deploy crashes with `No module named 'psycopg'`, one of them is missing from the build.

---

## API overview

All routes are under `/api`. Everything except the routes marked *public* needs a bearer token.

| Area | Routes |
|---|---|
| Auth | `GET /auth/status` (public), `POST /auth/bootstrap` (public, first account only), `POST /auth/login` (public), `POST /auth/mfa/enroll`, `/mfa/confirm`, `/mfa/verify`, `GET /auth/sessions`, `POST /auth/sessions/{id}/revoke`, `POST /auth/sessions/revoke-all`, `POST /auth/logout`, `GET /auth/me` |
| Posts | `GET /posts/`, `GET /posts/dms`, `GET /posts/dept-counts`, `POST /posts/`, `POST /posts/dm`, `DELETE /posts/{id}`, `POST /posts/upload`, `POST /posts/{id}/comments` |
| Notifications | `GET /notifications/`, `GET /notifications/unread-count`, `POST /notifications/read-all`, `POST /notifications/read-visible` |
| Users | `GET /users/public` (public), `GET /users/`, `POST /users/`, `POST /users/{id}/profile-image` |
| Admin | `GET /admin/stats`, `GET /admin/access-log`, `GET /admin/users`, `DELETE /admin/users/{id}`, `PUT /admin/users/{id}/role`, `PUT /admin/users/{id}/department`, `PUT /admin/users/{id}/reset-password`, `POST /admin/users/{id}/mfa-reset` |

FastAPI also serves interactive docs at `/docs` when the app is running.

---

## Project structure

```
api/
├── main.py              # App entrypoint, security-header middleware, error handler
├── database.py          # Engine and session (SQLite locally, Postgres via DATABASE_URL)
├── models.py            # SQLAlchemy models
├── schemas.py           # Pydantic request and response schemas
├── security.py          # Password hashing, JWT creation, password policy
├── deps.py              # get_current_user and role checks
├── mfa.py               # TOTP secrets, QR codes, recovery codes, encryption
├── ratelimit.py         # slowapi limiter setup
├── audit.py             # Access-log helper
├── constants.py         # Departments and archive window
├── migrate.py           # Additive migration for existing SQLite databases
├── requirements.txt
├── .env.example
├── routers/
│   ├── auth.py          # Login, MFA, sessions, logout
│   ├── posts.py         # Feed, DMs, comments, uploads
│   ├── notifications.py
│   ├── users.py         # Roster, profile images
│   └── admin.py         # Member management, access log
└── static/
    ├── index.html
    ├── styles.css
    └── app.js           # The whole frontend
```

---

## Security notes

The controls above are implemented and I tested each one by hand. There are no automated tests yet. These are the known gaps:

- **`GET /users/public` needs no login.** It returns every user's name, handle, department and admin flag to power the "Who's this?" picker. That weakens the protection against finding valid usernames, and I plan to remove it.
- **Rate-limit counters are in memory.** On a serverless host each instance has its own counters, so per-IP limits are weaker than they look. The account lockout is stored in the database and is not affected. A shared store such as Redis would fix it.
- **Uploads are basic.** They're checked by file extension only, with no size limit, saved to local disk (which doesn't persist on Vercel) and served from a public path under random names. Object storage with signed links is the proper fix.
- **A locked account gets a different message** from a wrong password, which lets someone confirm that a handle exists.
- **Session tokens are stored in `localStorage`,** so a successful XSS attack could read them. The CSP and output escaping make that difficult, but HttpOnly cookies would be stronger.
- **Message text is not encrypted by the app.** It relies on the database provider's encryption at rest.
- **New-device alerts flag any change** in IP or browser, so a new wifi network will trigger one. There is no "impossible travel" detection.

## Roadmap

- Remove the public roster endpoint
- Redis-backed rate limiting
- Object storage for attachments
- Automated tests for auth, MFA and sessions
- HttpOnly cookies with CSRF protection
