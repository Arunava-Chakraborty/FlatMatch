"""FlatMatch - swipe to find your flatmate.

Database: set DATABASE_URL to a Postgres URL (e.g. Neon / Supabase) for cloud storage.
If DATABASE_URL is empty, a local SQLite file (FLATMATCH_DB) is used.
"""
import hashlib, hmac, os, secrets, sqlite3, time, threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_PATH = os.environ.get("FLATMATCH_DB", "flatmatch.db")
SECRET = os.environ.get("FLATMATCH_SECRET") or secrets.token_hex(32)
COOKIE_SECURE = os.environ.get("FLATMATCH_SECURE_COOKIE", "1") == "1"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_TTL = 7 * 24 * 3600
ADMIN_TTL = 8 * 3600
STATIC = Path(__file__).parent / "static"

# ---------------- DB layer (works on SQLite and Postgres) ----------------
class Row(dict):
    """Dict row that also supports row[0] style access."""
    def __init__(self, names, values):
        super().__init__(zip(names, values))
        self._v = tuple(values)
    def __getitem__(self, k):
        return self._v[k] if isinstance(k, int) else dict.__getitem__(self, k)

def _sqlite_row(cur, values):
    return Row([d[0] for d in cur.description], values)

def _pg_row_factory(cur):
    names = [d.name for d in cur.description] if cur.description else []
    return lambda values: Row(names, values)

if IS_PG:
    import psycopg
    from psycopg_pool import ConnectionPool
    IntegrityErrors = (sqlite3.IntegrityError, psycopg.errors.UniqueViolation)
else:
    IntegrityErrors = (sqlite3.IntegrityError,)

_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "5")),
                               kwargs={"row_factory": _pg_row_factory, "connect_timeout": 15},
                               check=ConnectionPool.check_connection, open=True)
    return _pool

def close_db():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None

class DB:
    def __init__(self, raw):
        self.raw = raw
    def execute(self, sql, params=()):
        if IS_PG:
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, tuple(params))

@contextmanager
def db():
    if IS_PG:
        with _get_pool().connection() as raw:  # commits on success, rolls back on error
            yield DB(raw)
        return
    raw = sqlite3.connect(DB_PATH, timeout=10)
    raw.row_factory = _sqlite_row
    try:
        yield DB(raw)
        raw.commit()
    finally:
        raw.close()

PK = "BIGSERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY"
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users(
  id {PK}, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
  name TEXT NOT NULL, age INTEGER NOT NULL, gender TEXT NOT NULL DEFAULT 'other',
  city TEXT NOT NULL DEFAULT '', area TEXT NOT NULL DEFAULT '',
  budget_min INTEGER NOT NULL DEFAULT 0, budget_max INTEGER NOT NULL DEFAULT 0,
  move_in TEXT NOT NULL DEFAULT '', looking TEXT NOT NULL DEFAULT 'room',
  occupation TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '',
  smoking INTEGER NOT NULL DEFAULT 0, pets INTEGER NOT NULL DEFAULT 0, drinks INTEGER NOT NULL DEFAULT 0,
  veg INTEGER NOT NULL DEFAULT 0, cleanliness INTEGER NOT NULL DEFAULT 3,
  sleep TEXT NOT NULL DEFAULT 'flexible', guests TEXT NOT NULL DEFAULT 'sometimes',
  pref_gender TEXT NOT NULL DEFAULT 'any', created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS swipes(swiper BIGINT NOT NULL, target BIGINT NOT NULL, liked INTEGER NOT NULL,
  super INTEGER NOT NULL DEFAULT 0, ts DOUBLE PRECISION NOT NULL, PRIMARY KEY(swiper,target));
CREATE TABLE IF NOT EXISTS matches(id {PK}, a BIGINT NOT NULL, b BIGINT NOT NULL,
  ts DOUBLE PRECISION NOT NULL, UNIQUE(a,b));
CREATE TABLE IF NOT EXISTS messages(id {PK}, match_id BIGINT NOT NULL, sender BIGINT NOT NULL,
  body TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS blocks(blocker BIGINT NOT NULL, blocked BIGINT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  ts DOUBLE PRECISION NOT NULL, PRIMARY KEY(blocker,blocked));
CREATE INDEX IF NOT EXISTS idx_msg ON messages(match_id, id);
CREATE INDEX IF NOT EXISTS idx_swipe_target ON swipes(target);
"""

def init_db():
    with db() as c:
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                c.execute(stmt)

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield
    close_db()

app = FastAPI(title="FlatMatch", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

# ---------------- Security helpers ----------------
def hash_pw(pw: str, salt: Optional[bytes] = None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return h.hex(), salt.hex()

def verify_pw(pw: str, h: str, salt: str) -> bool:
    calc, _ = hash_pw(pw, bytes.fromhex(salt))
    return hmac.compare_digest(calc, h)

def _sign(msg: str) -> str:
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def make_token(uid: int) -> str:
    msg = f"{uid}.{int(time.time()) + SESSION_TTL}"
    return f"{msg}.{_sign(msg)}"

def read_token(tok: str) -> Optional[int]:
    try:
        uid, exp, sig = tok.split(".")
        if not hmac.compare_digest(_sign(f"{uid}.{exp}"), sig) or int(exp) < time.time():
            return None
        return int(uid)
    except Exception:
        return None

def make_admin_token() -> str:
    msg = f"admin.{int(time.time()) + ADMIN_TTL}"
    return f"{msg}.{_sign(msg)}"

def is_admin_token(tok: str) -> bool:
    try:
        kind, exp, sig = tok.split(".")
        return (kind == "admin" and hmac.compare_digest(_sign(f"{kind}.{exp}"), sig)
                and int(exp) >= time.time())
    except Exception:
        return False

class RateLimiter:
    def __init__(self, limit, window):
        self.limit, self.window, self.hits, self.lock = limit, window, defaultdict(deque), threading.Lock()
    def check(self, key):
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(429, "Too many attempts. Try again later.")
            q.append(now)

auth_limiter = RateLimiter(10, 300)   # 10 auth attempts / 5 min / IP
admin_limiter = RateLimiter(5, 900)   # 5 admin login attempts / 15 min / IP
msg_limiter = RateLimiter(30, 60)     # 30 messages / min / user
swipe_limiter = RateLimiter(120, 60)  # 120 swipes / min / user

@app.middleware("http")
async def security_mw(request: Request, call_next):
    # CSRF defence: state-changing API calls must carry a custom header (cannot be sent cross-site without CORS)
    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get("x-requested-with") != "flatmatch":
            return JSONResponse({"detail": "Missing CSRF header"}, status_code=403)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    if COOKIE_SECURE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/") or request.url.path.startswith("/admin"):
        resp.headers["Cache-Control"] = "no-store"
    if request.url.path.startswith("/admin") or request.url.path.startswith("/api/admin"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp

def current_user(request: Request) -> int:
    uid = read_token(request.cookies.get("fm_session", ""))
    if not uid:
        raise HTTPException(401, "Not logged in")
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(401, "Not logged in")
    return uid

def require_admin(request: Request) -> bool:
    if not ADMIN_PASSWORD or not is_admin_token(request.cookies.get("fm_admin", "")):
        raise HTTPException(401, "Admin login required")
    return True

def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

# ---------------- Models ----------------
class Signup(BaseModel):
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=40)
    age: int = Field(ge=18, le=99)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1] or " " in v:
            raise ValueError("Invalid email")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Name required")
        return v

class Login(BaseModel):
    email: str = Field(max_length=120)
    password: str = Field(max_length=128)

class AdminLogin(BaseModel):
    password: str = Field(max_length=256)

class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    age: int = Field(ge=18, le=99)
    gender: Literal["male", "female", "other"] = "other"
    city: str = Field(default="", max_length=40)
    area: str = Field(default="", max_length=60)
    budget_min: int = Field(default=0, ge=0, le=1_000_000)
    budget_max: int = Field(default=0, ge=0, le=1_000_000)
    move_in: str = Field(default="", max_length=10)
    looking: Literal["room", "roommate"] = "room"
    occupation: str = Field(default="", max_length=60)
    bio: str = Field(default="", max_length=500)
    smoking: bool = False
    pets: bool = False
    drinks: bool = False
    veg: bool = False
    cleanliness: int = Field(default=3, ge=1, le=5)
    sleep: Literal["early", "late", "flexible"] = "flexible"
    guests: Literal["rarely", "sometimes", "often"] = "sometimes"
    pref_gender: Literal["any", "male", "female"] = "any"

    @field_validator("move_in")
    @classmethod
    def _date(cls, v):
        if v:
            date.fromisoformat(v)
        return v

    @field_validator("budget_max")
    @classmethod
    def _budget(cls, v, info):
        if v and info.data.get("budget_min", 0) > v:
            raise ValueError("budget_max must be >= budget_min")
        return v

class Swipe(BaseModel):
    target: int
    action: Literal["like", "pass", "super"]

class Message(BaseModel):
    body: str = Field(min_length=1, max_length=1000)

class Report(BaseModel):
    target: int
    reason: str = Field(default="", max_length=300)

PROFILE_COLUMNS = set(Profile.model_fields)
BOOL_FIELDS = ("smoking", "pets", "drinks", "veg")
PUBLIC_FIELDS = ("id name age gender city area budget_min budget_max move_in looking occupation bio "
                 "smoking pets drinks veg cleanliness sleep guests").split()

def public(row) -> dict:
    d = {k: row[k] for k in PUBLIC_FIELDS}
    for k in BOOL_FIELDS:
        d[k] = bool(d[k])
    return d

# ---------------- Compatibility ----------------
def compatibility(me, other) -> int:
    score, total = 0.0, 0.0
    def add(w, v):
        nonlocal score, total
        score += w * v; total += w
    if me["city"] and other["city"]:
        add(3, 1.0 if me["city"].lower() == other["city"].lower() else 0.0)
    if me["budget_max"] and other["budget_max"]:
        lo, hi = max(me["budget_min"], other["budget_min"]), min(me["budget_max"], other["budget_max"])
        add(3, 1.0 if hi >= lo else 0.0)
    add(2, 1 - abs(me["cleanliness"] - other["cleanliness"]) / 4)
    add(1.5, 1.0 if bool(me["smoking"]) == bool(other["smoking"]) else 0.0)
    add(1, 1.0 if bool(me["pets"]) == bool(other["pets"]) else 0.5)
    add(1, 1.0 if bool(me["veg"]) == bool(other["veg"]) else 0.5)
    add(1, 1.0 if bool(me["drinks"]) == bool(other["drinks"]) else 0.6)
    add(1.5, 1.0 if me["sleep"] == other["sleep"] or "flexible" in (me["sleep"], other["sleep"]) else 0.2)
    gmap = {"rarely": 0, "sometimes": 1, "often": 2}
    add(1, 1 - abs(gmap[me["guests"]] - gmap[other["guests"]]) / 2)
    add(1.5, 1.0 if me["looking"] != other["looking"] else 0.6)  # one has a room, other needs one
    return round(100 * score / total) if total else 50

# ---------------- User routes ----------------
def set_session(resp: Response, uid: int):
    resp.set_cookie("fm_session", make_token(uid), max_age=SESSION_TTL, httponly=True,
                    secure=COOKIE_SECURE, samesite="strict", path="/")

@app.post("/api/signup")
def signup(data: Signup, request: Request, response: Response):
    auth_limiter.check("auth:" + client_ip(request))
    h, salt = hash_pw(data.password)
    try:
        with db() as c:
            uid = c.execute("INSERT INTO users(email,pw_hash,salt,name,age,created) VALUES(?,?,?,?,?,?) RETURNING id",
                            (data.email, h, salt, data.name, data.age, time.time())).fetchone()[0]
    except IntegrityErrors:
        raise HTTPException(409, "Email already registered")
    set_session(response, uid)
    return {"ok": True, "id": uid}

@app.post("/api/login")
def login(data: Login, request: Request, response: Response):
    auth_limiter.check("auth:" + client_ip(request))
    with db() as c:
        row = c.execute("SELECT id,pw_hash,salt FROM users WHERE email=?", (data.email.strip().lower(),)).fetchone()
    if not row or not verify_pw(data.password, row["pw_hash"], row["salt"]):
        raise HTTPException(401, "Invalid email or password")
    set_session(response, row["id"])
    return {"ok": True}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("fm_session", path="/")
    return {"ok": True}

@app.get("/api/me")
def me(uid: int = Depends(current_user)):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    d = public(row); d["pref_gender"] = row["pref_gender"]; d["email"] = row["email"]
    d["complete"] = bool(row["city"] and row["budget_max"])
    return d

@app.put("/api/me")
def update_me(p: Profile, uid: int = Depends(current_user)):
    vals = p.model_dump()
    for k in ("name", "city", "area", "occupation", "bio"):
        vals[k] = vals[k].strip()
    for k in BOOL_FIELDS:
        vals[k] = int(vals[k])
    if not set(vals) <= PROFILE_COLUMNS:  # defence in depth: only allow-listed column names reach SQL
        raise HTTPException(400, "Bad fields")
    cols = ", ".join(f"{k}=?" for k in vals)
    with db() as c:
        c.execute(f"UPDATE users SET {cols} WHERE id=?", (*vals.values(), uid))  # nosec B608 - allow-listed columns
    return {"ok": True}

def _delete_user(c, uid: int):
    c.execute("DELETE FROM messages WHERE match_id IN (SELECT id FROM matches WHERE a=? OR b=?)", (uid, uid))
    c.execute("DELETE FROM matches WHERE a=? OR b=?", (uid, uid))
    c.execute("DELETE FROM swipes WHERE swiper=? OR target=?", (uid, uid))
    c.execute("DELETE FROM blocks WHERE blocker=? OR blocked=?", (uid, uid))
    c.execute("DELETE FROM users WHERE id=?", (uid,))

@app.delete("/api/me")
def delete_me(response: Response, uid: int = Depends(current_user)):
    with db() as c:
        _delete_user(c, uid)
    response.delete_cookie("fm_session", path="/")
    return {"ok": True}

@app.get("/api/deck")
def deck(city: str = "", max_budget: int = 0, limit: int = 20, uid: int = Depends(current_user)):
    limit = max(1, min(limit, 50))
    with db() as c:
        me_row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        q = """SELECT * FROM users u WHERE u.id != ?
               AND u.id NOT IN (SELECT target FROM swipes WHERE swiper=?)
               AND u.id NOT IN (SELECT blocked FROM blocks WHERE blocker=?)
               AND u.id NOT IN (SELECT blocker FROM blocks WHERE blocked=?)"""
        args = [uid, uid, uid, uid]
        if city.strip():
            q += " AND lower(u.city)=lower(CAST(? AS TEXT))"; args.append(city.strip()[:40])
        if max_budget > 0:
            q += " AND u.budget_min <= ?"; args.append(max_budget)
        if me_row["pref_gender"] != "any":
            q += " AND u.gender=?"; args.append(me_row["pref_gender"])
        q += " AND (u.pref_gender='any' OR u.pref_gender=?)"; args.append(me_row["gender"])
        rows = c.execute(q + " LIMIT 300", args).fetchall()
        superliked = {r[0] for r in c.execute("SELECT swiper FROM swipes WHERE target=? AND super=1", (uid,)).fetchall()}
    cards = []
    for r in rows:
        d = public(r); d["score"] = compatibility(me_row, r); d["superliked_you"] = r["id"] in superliked
        cards.append(d)
    cards.sort(key=lambda d: (d["superliked_you"], d["score"]), reverse=True)
    return cards[:limit]

@app.post("/api/swipe")
def swipe(s: Swipe, uid: int = Depends(current_user)):
    swipe_limiter.check(f"swipe:{uid}")
    if s.target == uid:
        raise HTTPException(400, "Cannot swipe yourself")
    liked = s.action in ("like", "super")
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (s.target,)).fetchone():
            raise HTTPException(404, "User not found")
        if s.action == "super":
            n = c.execute("SELECT COUNT(*) FROM swipes WHERE swiper=? AND super=1 AND ts>?",
                          (uid, time.time() - 86400)).fetchone()[0]
            if n >= 3:
                raise HTTPException(429, "Only 3 super likes per day")
        c.execute("""INSERT INTO swipes(swiper,target,liked,super,ts) VALUES(?,?,?,?,?)
                     ON CONFLICT(swiper,target) DO UPDATE SET liked=excluded.liked, super=excluded.super, ts=excluded.ts""",
                  (uid, s.target, int(liked), int(s.action == "super"), time.time()))
        matched, match_id = False, None
        if liked and c.execute("SELECT 1 FROM swipes WHERE swiper=? AND target=? AND liked=1", (s.target, uid)).fetchone():
            a, b = sorted((uid, s.target))
            c.execute("INSERT INTO matches(a,b,ts) VALUES(?,?,?) ON CONFLICT(a,b) DO NOTHING", (a, b, time.time()))
            match_id = c.execute("SELECT id FROM matches WHERE a=? AND b=?", (a, b)).fetchone()[0]
            matched = True
    return {"matched": matched, "match_id": match_id}

@app.post("/api/undo")
def undo(uid: int = Depends(current_user)):
    """Rewind the last pass (like Tinder's rewind)."""
    with db() as c:
        row = c.execute("SELECT target FROM swipes WHERE swiper=? AND liked=0 ORDER BY ts DESC LIMIT 1", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "Nothing to undo")
        c.execute("DELETE FROM swipes WHERE swiper=? AND target=?", (uid, row[0]))
    return {"ok": True, "target": row[0]}

def _match_for(c, match_id: int, uid: int):
    m = c.execute("SELECT * FROM matches WHERE id=? AND (a=? OR b=?)", (match_id, uid, uid)).fetchone()
    if not m:
        raise HTTPException(404, "Match not found")
    other = m["b"] if m["a"] == uid else m["a"]
    if c.execute("SELECT 1 FROM blocks WHERE (blocker=? AND blocked=?) OR (blocker=? AND blocked=?)",
                 (uid, other, other, uid)).fetchone():
        raise HTTPException(404, "Match not found")
    return m, other

@app.get("/api/matches")
def matches(uid: int = Depends(current_user)):
    out = []
    with db() as c:
        me_row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        rows = c.execute("""SELECT m.id AS mid, m.ts AS mts, u.* FROM matches m JOIN users u
                            ON u.id = CASE WHEN m.a=? THEN m.b ELSE m.a END
                            WHERE (m.a=? OR m.b=?)
                            AND u.id NOT IN (SELECT blocked FROM blocks WHERE blocker=?)
                            AND u.id NOT IN (SELECT blocker FROM blocks WHERE blocked=?)""",
                         (uid, uid, uid, uid, uid)).fetchall()
        for r in rows:
            last = c.execute("SELECT body,sender,ts FROM messages WHERE match_id=? ORDER BY id DESC LIMIT 1",
                             (r["mid"],)).fetchone()
            out.append({"match_id": r["mid"], "user": public(r), "score": compatibility(me_row, r),
                        "last": dict(last) if last else None, "ts": last["ts"] if last else r["mts"]})
    out.sort(key=lambda d: d["ts"], reverse=True)
    return out

@app.get("/api/matches/{match_id}/messages")
def get_messages(match_id: int, after: int = 0, uid: int = Depends(current_user)):
    with db() as c:
        _match_for(c, match_id, uid)
        rows = c.execute("SELECT id,sender,body,ts FROM messages WHERE match_id=? AND id>? ORDER BY id LIMIT 200",
                         (match_id, after)).fetchall()
    return [dict(r) | {"mine": r["sender"] == uid} for r in rows]

@app.post("/api/matches/{match_id}/messages")
def send_message(match_id: int, m: Message, uid: int = Depends(current_user)):
    msg_limiter.check(f"msg:{uid}")
    body = m.body.strip()
    if not body:
        raise HTTPException(422, "Empty message")
    with db() as c:
        _match_for(c, match_id, uid)
        mid = c.execute("INSERT INTO messages(match_id,sender,body,ts) VALUES(?,?,?,?) RETURNING id",
                        (match_id, uid, body, time.time())).fetchone()[0]
    return {"ok": True, "id": mid}

@app.delete("/api/matches/{match_id}")
def unmatch(match_id: int, uid: int = Depends(current_user)):
    with db() as c:
        _, other = _match_for(c, match_id, uid)
        c.execute("DELETE FROM messages WHERE match_id=?", (match_id,))
        c.execute("DELETE FROM matches WHERE id=?", (match_id,))
        c.execute("UPDATE swipes SET liked=0, super=0 WHERE swiper=? AND target=?", (uid, other))
    return {"ok": True}

@app.post("/api/report")
def report(r: Report, uid: int = Depends(current_user)):
    if r.target == uid:
        raise HTTPException(400, "Cannot report yourself")
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (r.target,)).fetchone():
            raise HTTPException(404, "User not found")
        c.execute("""INSERT INTO blocks(blocker,blocked,reason,ts) VALUES(?,?,?,?)
                     ON CONFLICT(blocker,blocked) DO UPDATE SET reason=excluded.reason, ts=excluded.ts""",
                  (uid, r.target, r.reason.strip(), time.time()))
    return {"ok": True}

# ---------------- Admin routes ----------------
@app.post("/api/admin/login")
def admin_login(data: AdminLogin, request: Request, response: Response):
    if len(ADMIN_PASSWORD) < 12:
        raise HTTPException(404, "Admin panel is disabled. Set ADMIN_PASSWORD (12+ characters).")
    admin_limiter.check("admin:" + client_ip(request))
    if not hmac.compare_digest(data.password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "Wrong admin password")
    response.set_cookie("fm_admin", make_admin_token(), max_age=ADMIN_TTL, httponly=True,
                        secure=COOKIE_SECURE, samesite="strict", path="/")
    return {"ok": True}

@app.post("/api/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie("fm_admin", path="/")
    return {"ok": True}

@app.get("/api/admin/stats")
def admin_stats(_: bool = Depends(require_admin)):
    week = time.time() - 7 * 86400
    with db() as c:
        one = lambda sql, p=(): c.execute(sql, p).fetchone()[0]
        stats = {
            "users": one("SELECT COUNT(*) FROM users"),
            "new_users_7d": one("SELECT COUNT(*) FROM users WHERE created>?", (week,)),
            "profiles_complete": one("SELECT COUNT(*) FROM users WHERE city<>'' AND budget_max>0"),
            "swipes": one("SELECT COUNT(*) FROM swipes"),
            "likes": one("SELECT COUNT(*) FROM swipes WHERE liked=1"),
            "matches": one("SELECT COUNT(*) FROM matches"),
            "messages": one("SELECT COUNT(*) FROM messages"),
            "reports": one("SELECT COUNT(*) FROM blocks WHERE reason<>''"),
            "blocks": one("SELECT COUNT(*) FROM blocks"),
        }
        stats["top_cities"] = [dict(r) for r in c.execute(
            "SELECT city, COUNT(*) AS n FROM users WHERE city<>'' GROUP BY city ORDER BY n DESC LIMIT 5").fetchall()]
    stats["database"] = "postgres" if IS_PG else "sqlite"
    return stats

@app.get("/api/admin/users")
def admin_users(q: str = "", page: int = 1, _: bool = Depends(require_admin)):
    page, size = max(1, page), 25
    sql = """SELECT u.id,u.name,u.email,u.age,u.gender,u.city,u.area,u.budget_min,u.budget_max,u.looking,u.created,
               (SELECT COUNT(*) FROM matches m WHERE m.a=u.id OR m.b=u.id) AS matches,
               (SELECT COUNT(*) FROM blocks b WHERE b.blocked=u.id) AS reported
             FROM users u"""
    args = []
    if q.strip():
        like = "%" + q.strip().lower()[:60] + "%"
        sql += " WHERE lower(u.name) LIKE ? OR lower(u.email) LIKE ? OR lower(u.city) LIKE ?"
        args = [like, like, like]
    with db() as c:
        total = c.execute("SELECT COUNT(*) FROM (" + sql + ") t", args).fetchone()[0]
        rows = c.execute(sql + " ORDER BY u.id DESC LIMIT ? OFFSET ?", (*args, size, (page - 1) * size)).fetchall()
    return {"total": total, "page": page, "pages": max(1, -(-total // size)), "users": [dict(r) for r in rows]}

@app.get("/api/admin/users/{user_id}")
def admin_user(user_id: int, _: bool = Depends(require_admin)):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        d = public(row)
        d.update(email=row["email"], pref_gender=row["pref_gender"], created=row["created"])
        one = lambda sql, p: c.execute(sql, p).fetchone()[0]
        d["activity"] = {
            "swipes_made": one("SELECT COUNT(*) FROM swipes WHERE swiper=?", (user_id,)),
            "likes_given": one("SELECT COUNT(*) FROM swipes WHERE swiper=? AND liked=1", (user_id,)),
            "likes_received": one("SELECT COUNT(*) FROM swipes WHERE target=? AND liked=1", (user_id,)),
            "messages_sent": one("SELECT COUNT(*) FROM messages WHERE sender=?", (user_id,)),
        }
        d["matches"] = [dict(r) for r in c.execute(
            """SELECT u.id, u.name, m.ts FROM matches m JOIN users u ON u.id = CASE WHEN m.a=? THEN m.b ELSE m.a END
               WHERE m.a=? OR m.b=? ORDER BY m.ts DESC""", (user_id, user_id, user_id)).fetchall()]
        d["reports_against"] = [dict(r) for r in c.execute(
            """SELECT u.id, u.name, b.reason, b.ts FROM blocks b JOIN users u ON u.id=b.blocker
               WHERE b.blocked=? ORDER BY b.ts DESC""", (user_id,)).fetchall()]
    return d

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, _: bool = Depends(require_admin)):
    with db() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(404, "User not found")
        _delete_user(c, user_id)
    return {"ok": True}

@app.get("/api/admin/reports")
def admin_reports(_: bool = Depends(require_admin)):
    with db() as c:
        rows = c.execute("""SELECT b.ts, b.reason, r.id AS reporter_id, r.name AS reporter,
                              t.id AS reported_id, t.name AS reported, t.email AS reported_email
                            FROM blocks b JOIN users r ON r.id=b.blocker JOIN users t ON t.id=b.blocked
                            ORDER BY b.ts DESC LIMIT 200""").fetchall()
    return [dict(r) for r in rows]

# ---------------- Pages ----------------
@app.get("/healthz")
def health():
    with db() as c:
        c.execute("SELECT 1").fetchone()
    return {"ok": True}

app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")

@app.get("/admin")
def admin_page():
    return FileResponse(STATIC / "admin.html")

@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")
