# FlatMatch - Tinder-style flatmate finder

Mobile-first web app with swipe matching, chat, and an admin panel.
Data is stored in **cloud Postgres (Neon, free)** when `DATABASE_URL` is set, otherwise in a local SQLite file.

## Features
- Sign up / log in, profile with budget, area, move-in date, lifestyle
- Swipe deck (drag right = like, left = pass, up = super like), compatibility %, rewind
- Match -> chat, unmatch, report & block, delete account
- **Admin panel at `/admin`**: dashboard stats, searchable user list, full user details, reports, delete user

## 1. Create a free cloud database (Neon)
1. Sign up at neon.tech (no credit card).
2. Create a project. Pick region **AWS Asia Pacific (Singapore)** - closest to India.
3. On the project dashboard select **Connect**, turn on **Connection pooling**, copy the connection string.
   It looks like `postgresql://user:pass@ep-xxxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`

Tables are created automatically the first time the app starts.

## 2. Run locally (Windows PowerShell)
```powershell
py -m pip install -r requirements.txt
$env:DATABASE_URL="paste-your-neon-connection-string"
$env:ADMIN_PASSWORD="choose-a-long-admin-password"   # 12+ characters
$env:FLATMATCH_SECURE_COOKIE="0"                     # only for http://localhost
py seed.py                                           # optional demo users (password demo12345)
py -m uvicorn app.main:app --reload
```
- App: http://localhost:8000
- Admin panel: http://localhost:8000/admin

Leave out `DATABASE_URL` to use a local SQLite file instead.
Tip: set `FLATMATCH_SECRET` to a fixed long random string so logins survive restarts.

## 3. Deploy on Render (free)
1. Push to GitHub.
2. render.com -> **New** -> **Blueprint** -> select the repo.
3. When asked, paste `DATABASE_URL` (Neon) and `ADMIN_PASSWORD`. `FLATMATCH_SECRET` is generated for you.
4. Admin panel: `https://your-app.onrender.com/admin`

## Test
```powershell
py -m pytest -q
```
27 tests (features, admin, security). To also run them against Postgres, set
`TEST_DATABASE_URL` to an **empty test database** (the tests drop and recreate tables).

## Environment variables
| Var | Purpose |
|---|---|
| DATABASE_URL | Postgres connection string (Neon/Supabase). Empty = local SQLite |
| ADMIN_PASSWORD | Enables `/admin`. Must be 12+ characters, otherwise admin is disabled |
| FLATMATCH_SECRET | Signs login cookies. Set a long random value in production |
| FLATMATCH_SECURE_COOKIE | 1 = HTTPS-only cookies + HSTS (default). Use 0 only on localhost |
| FLATMATCH_DB | SQLite file path when DATABASE_URL is empty |
| DB_POOL_SIZE | Max Postgres connections (default 5) |

## Security summary
| Area | Control |
|---|---|
| Passwords | scrypt + per-user salt; never shown to admin |
| Sessions | HMAC-signed cookies, HttpOnly, Secure, SameSite=Strict; admin session separate, 8 h expiry |
| Admin | Disabled unless strong ADMIN_PASSWORD set; 5 login attempts / 15 min; not indexed by search engines |
| CSRF | SameSite=Strict + required custom header on every write |
| SQL injection | Parameterised queries on both SQLite and Postgres |
| XSS | No innerHTML; strict CSP |
| Access control | Chats visible only to the 2 matched users; admin sees message counts, not message text |
| Abuse | Rate limits on login, messages, swipes; 3 super likes/day |
| Secrets | Never commit `.env` or connection strings (already in .gitignore) |
