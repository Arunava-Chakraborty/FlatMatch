"""Run with SQLite (default). To also run against Postgres: set TEST_DATABASE_URL to an EMPTY test database."""
import os, tempfile, time, importlib
import pytest
from fastapi.testclient import TestClient

H = {"X-Requested-With": "flatmatch"}
ADMIN_PW = "super-secret-admin-pw"
BACKENDS = ["sqlite"] + (["postgres"] if os.environ.get("TEST_DATABASE_URL") else [])

@pytest.fixture(params=BACKENDS)
def mod(request, monkeypatch):
    path = None
    if request.param == "postgres":
        monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    else:
        fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        monkeypatch.setenv("DATABASE_URL", "")
        monkeypatch.setenv("FLATMATCH_DB", path)
    monkeypatch.setenv("FLATMATCH_SECURE_COOKIE", "0")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PW)
    import app.main as m
    importlib.reload(m)
    if m.IS_PG:
        with m.db() as c:
            c.execute("DROP TABLE IF EXISTS users, swipes, matches, messages, blocks")
    m.init_db()
    yield m
    m.close_db()
    if path:
        os.remove(path)

def client(m):
    return TestClient(m.app)

PROFILE = dict(name="A", age=25, gender="male", city="Pune", area="Baner", budget_min=8000, budget_max=15000,
               move_in="2026-12-01", looking="room", occupation="Engineer", bio="hi", smoking=False, pets=False,
               drinks=False, veg=True, cleanliness=4, sleep="early", guests="rarely", pref_gender="any")

def make_user(m, email, name="U", **over):
    c = client(m)
    r = c.post("/api/signup", json={"email": email, "password": "password123", "name": name, "age": 24}, headers=H)
    assert r.status_code == 200, r.text
    assert c.put("/api/me", json=dict(PROFILE, name=name, **over), headers=H).status_code == 200
    return c, r.json()["id"]

def admin(m):
    c = client(m)
    assert c.post("/api/admin/login", json={"password": ADMIN_PW}, headers=H).status_code == 200
    return c

def match(a, ida, b, idb):
    a.post("/api/swipe", json={"target": idb, "action": "like"}, headers=H)
    return b.post("/api/swipe", json={"target": ida, "action": "like"}, headers=H).json()["match_id"]

# ---------- functional ----------
def test_signup_login_me(mod):
    c, uid = make_user(mod, "a@x.com", "Asha")
    me = c.get("/api/me").json()
    assert me["name"] == "Asha" and me["complete"] and me["veg"] is True
    assert "pw_hash" not in me and "salt" not in me
    c2 = client(mod)
    assert c2.post("/api/login", json={"email": "A@X.com ", "password": "password123"}, headers=H).status_code == 200
    assert c2.post("/api/login", json={"email": "a@x.com", "password": "wrongpass1"}, headers=H).status_code == 401

def test_duplicate_email(mod):
    make_user(mod, "a@x.com")
    r = client(mod).post("/api/signup", json={"email": "a@x.com", "password": "password123", "name": "B", "age": 30}, headers=H)
    assert r.status_code == 409

def test_validation(mod):
    c = client(mod)
    assert c.post("/api/signup", json={"email": "bad", "password": "password123", "name": "B", "age": 30}, headers=H).status_code == 422
    assert c.post("/api/signup", json={"email": "k@x.com", "password": "short", "name": "B", "age": 30}, headers=H).status_code == 422
    assert c.post("/api/signup", json={"email": "k@x.com", "password": "password123", "name": "B", "age": 16}, headers=H).status_code == 422
    c, _ = make_user(mod, "v@x.com")
    for bad in (dict(budget_min=20000, budget_max=1000), dict(bio="x" * 501), dict(move_in="nope"), dict(cleanliness=9)):
        assert c.put("/api/me", json=dict(PROFILE, **bad), headers=H).status_code == 422

def test_deck_swipe_match_chat(mod):
    a, ida = make_user(mod, "a@x.com", "Asha")
    b, idb = make_user(mod, "b@x.com", "Bala", looking="roommate")
    deck = a.get("/api/deck").json()
    assert [d["id"] for d in deck] == [idb] and 0 <= deck[0]["score"] <= 100
    assert a.post("/api/swipe", json={"target": idb, "action": "like"}, headers=H).json()["matched"] is False
    r = b.post("/api/swipe", json={"target": ida, "action": "like"}, headers=H).json()
    assert r["matched"] is True
    mid = r["match_id"]
    assert a.get("/api/deck").json() == []
    assert a.get("/api/matches").json()[0]["user"]["name"] == "Bala"
    assert a.post(f"/api/matches/{mid}/messages", json={"body": "Hi! Room still free?"}, headers=H).status_code == 200
    msgs = b.get(f"/api/matches/{mid}/messages").json()
    assert msgs[0]["body"] == "Hi! Room still free?" and msgs[0]["mine"] is False
    assert b.get(f"/api/matches/{mid}/messages?after={msgs[0]['id']}").json() == []
    assert a.get("/api/matches").json()[0]["last"]["body"] == "Hi! Room still free?"

def test_pass_and_undo(mod):
    a, _ = make_user(mod, "a@x.com")
    _, idb = make_user(mod, "b@x.com")
    a.post("/api/swipe", json={"target": idb, "action": "pass"}, headers=H)
    assert a.get("/api/deck").json() == []
    assert a.post("/api/undo", headers=H).json()["target"] == idb
    assert len(a.get("/api/deck").json()) == 1

def test_super_like_priority_and_limit(mod):
    a, _ = make_user(mod, "a@x.com")
    ids = [make_user(mod, f"u{i}@x.com", f"U{i}")[1] for i in range(4)]
    for i in ids[:3]:
        assert a.post("/api/swipe", json={"target": i, "action": "super"}, headers=H).status_code == 200
    assert a.post("/api/swipe", json={"target": ids[3], "action": "super"}, headers=H).status_code == 429
    c = client(mod); c.post("/api/login", json={"email": "u0@x.com", "password": "password123"}, headers=H)
    assert c.get("/api/deck").json()[0]["superliked_you"] is True

def test_filters_and_gender_pref(mod):
    a, _ = make_user(mod, "a@x.com", gender="female", pref_gender="female")
    make_user(mod, "m@x.com", "Man", gender="male")
    make_user(mod, "f@x.com", "Fem", gender="female", city="Mumbai")
    assert [d["name"] for d in a.get("/api/deck").json()] == ["Fem"]
    assert a.get("/api/deck?city=pune").json() == []
    assert len(a.get("/api/deck?city=MUMBAI").json()) == 1
    assert a.get("/api/deck?max_budget=5000").json() == []

def test_compatibility(mod):
    base = dict(PROFILE)
    assert mod.compatibility(base, dict(base, looking="roommate")) == 100
    worse = dict(base, city="Delhi", budget_min=50000, budget_max=60000, smoking=True, cleanliness=1, sleep="late")
    assert mod.compatibility(base, worse) < 40

def test_unmatch_and_report_block(mod):
    a, ida = make_user(mod, "a@x.com"); b, idb = make_user(mod, "b@x.com")
    mid = match(a, ida, b, idb)
    assert b.post("/api/report", json={"target": ida, "reason": "spam"}, headers=H).status_code == 200
    assert b.post("/api/report", json={"target": ida, "reason": "spam again"}, headers=H).status_code == 200  # upsert
    assert a.get("/api/matches").json() == [] and b.get("/api/matches").json() == []
    assert a.post(f"/api/matches/{mid}/messages", json={"body": "hey"}, headers=H).status_code == 404
    c, idc = make_user(mod, "c@x.com"); d, idd = make_user(mod, "d@x.com")
    mid2 = match(c, idc, d, idd)
    assert c.delete(f"/api/matches/{mid2}", headers=H).status_code == 200
    assert d.get(f"/api/matches/{mid2}/messages").status_code == 404

def test_delete_account(mod):
    a, ida = make_user(mod, "a@x.com"); b, idb = make_user(mod, "b@x.com")
    mid = match(a, ida, b, idb)
    a.post(f"/api/matches/{mid}/messages", json={"body": "x"}, headers=H)
    assert a.delete("/api/me", headers=H).status_code == 200
    assert a.get("/api/me").status_code == 401
    assert b.get("/api/deck").json() == [] and b.get("/api/matches").json() == []
    with mod.db() as c:
        assert c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0

# ---------- admin ----------
def test_admin_requires_login(mod):
    c = client(mod)
    for path in ("/api/admin/stats", "/api/admin/users", "/api/admin/users/1", "/api/admin/reports"):
        assert c.get(path).status_code == 401, path
    assert c.delete("/api/admin/users/1", headers=H).status_code == 401
    assert c.post("/api/admin/login", json={"password": "wrong-password-x"}, headers=H).status_code == 401
    # a normal user session is NOT an admin session
    u, _ = make_user(mod, "a@x.com")
    assert u.get("/api/admin/stats").status_code == 401
    u.cookies.set("fm_admin", u.cookies.get("fm_session"))
    assert u.get("/api/admin/stats").status_code == 401

def test_admin_disabled_without_strong_password(mod, monkeypatch):
    monkeypatch.setattr(mod, "ADMIN_PASSWORD", "short")
    assert client(mod).post("/api/admin/login", json={"password": "short"}, headers=H).status_code == 404
    monkeypatch.setattr(mod, "ADMIN_PASSWORD", "")
    c = client(mod); c.cookies.set("fm_admin", mod.make_admin_token())
    assert c.get("/api/admin/stats").status_code == 401

def test_admin_forged_and_expired_token(mod, monkeypatch):
    c = client(mod)
    tok = mod.make_admin_token(); kind, exp, sig = tok.split(".")
    c.cookies.set("fm_admin", f"admin.{int(exp)+999}.{sig}")
    assert c.get("/api/admin/stats").status_code == 401
    old = f"admin.{int(time.time())-5}"
    c.cookies.set("fm_admin", f"{old}.{mod._sign(old)}")
    assert c.get("/api/admin/stats").status_code == 401

def test_admin_rate_limit(mod):
    c = client(mod)
    codes = [c.post("/api/admin/login", json={"password": "wrong-password-x"}, headers=H).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429

def test_admin_stats_users_detail_reports_delete(mod):
    a, ida = make_user(mod, "asha@x.com", "Asha"); b, idb = make_user(mod, "bala@x.com", "Bala", city="Hyderabad")
    mid = match(a, ida, b, idb)
    a.post(f"/api/matches/{mid}/messages", json={"body": "hello"}, headers=H)
    b.post("/api/report", json={"target": ida, "reason": "rude"}, headers=H)
    ad = admin(mod)
    s = ad.get("/api/admin/stats").json()
    assert (s["users"], s["matches"], s["messages"], s["reports"], s["likes"]) == (2, 1, 1, 1, 2)
    assert {c["city"] for c in s["top_cities"]} == {"Pune", "Hyderabad"}
    users = ad.get("/api/admin/users").json()
    assert users["total"] == 2 and users["users"][0]["name"] == "Bala"
    assert "pw_hash" not in users["users"][0] and "salt" not in users["users"][0]
    assert [u["name"] for u in ad.get("/api/admin/users", params={"q": "HYDER"}).json()["users"]] == ["Bala"]
    assert ad.get("/api/admin/users", params={"q": "' OR 1=1 --"}).json()["total"] == 0
    det = ad.get(f"/api/admin/users/{ida}").json()
    assert det["email"] == "asha@x.com" and det["activity"]["messages_sent"] == 1
    assert det["matches"][0]["name"] == "Bala" and det["reports_against"][0]["reason"] == "rude"
    assert "pw_hash" not in det
    assert ad.get("/api/admin/reports").json()[0]["reported"] == "Asha"
    assert ad.get("/api/admin/users/99999").status_code == 404
    assert ad.delete(f"/api/admin/users/{ida}", headers=H).status_code == 200
    assert a.get("/api/me").status_code == 401
    assert ad.get("/api/admin/stats").json()["users"] == 1
    assert ad.post("/api/admin/logout", headers=H).status_code == 200

def test_admin_pagination(mod):
    for i in range(27):
        mod.auth_limiter.hits.clear()  # bypass signup rate limit for bulk test data
        make_user(mod, f"p{i}@x.com", f"P{i}")
    ad = admin(mod)
    p1, p2 = ad.get("/api/admin/users?page=1").json(), ad.get("/api/admin/users?page=2").json()
    assert p1["pages"] == 2 and len(p1["users"]) == 25 and len(p2["users"]) == 2

# ---------- security ----------
def test_auth_required_everywhere(mod):
    c = client(mod)
    for path in ("/api/me", "/api/deck", "/api/matches", "/api/matches/1/messages"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/undo", headers=H).status_code == 401
    assert c.post("/api/swipe", json={"target": 1, "action": "like"}, headers=H).status_code == 401

def test_forged_and_expired_tokens(mod):
    _, ida = make_user(mod, "a@x.com")
    c = client(mod)
    uid, exp, sig = mod.make_token(ida).split(".")
    for bad in (f"{ida+1}.{exp}.{sig}", f"{uid}.{int(time.time())-10}.{sig}", "garbage"):
        c.cookies.set("fm_session", bad)
        assert c.get("/api/me").status_code == 401

def test_cannot_read_others_chat(mod):
    a, ida = make_user(mod, "a@x.com"); b, idb = make_user(mod, "b@x.com"); e, _ = make_user(mod, "e@x.com")
    mid = match(a, ida, b, idb)
    a.post(f"/api/matches/{mid}/messages", json={"body": "secret"}, headers=H)
    assert e.get(f"/api/matches/{mid}/messages").status_code == 404
    assert e.post(f"/api/matches/{mid}/messages", json={"body": "x"}, headers=H).status_code == 404
    assert e.delete(f"/api/matches/{mid}", headers=H).status_code == 404

def test_csrf_header_required(mod):
    c, _ = make_user(mod, "a@x.com")
    assert c.post("/api/swipe", json={"target": 99, "action": "like"}).status_code == 403
    assert c.put("/api/me", json=PROFILE).status_code == 403
    assert c.delete("/api/me").status_code == 403
    assert client(mod).post("/api/admin/login", json={"password": ADMIN_PW}).status_code == 403

def test_sql_injection_harmless(mod):
    c = client(mod)
    assert c.post("/api/login", json={"email": "' OR '1'='1", "password": "' OR '1'='1"}, headers=H).status_code == 401
    a, _ = make_user(mod, "a@x.com")
    assert a.get("/api/deck", params={"city": "x' OR 1=1 --"}).json() == []
    a.put("/api/me", json=dict(PROFILE, bio="'); DROP TABLE users;-- ? %s"), headers=H)
    assert a.get("/api/me").json()["bio"] == "'); DROP TABLE users;-- ? %s"

def test_xss_and_headers(mod):
    a, _ = make_user(mod, "a@x.com", name="<img src=x onerror=alert(1)>")
    assert a.get("/api/me").json()["name"] == "<img src=x onerror=alert(1)>"
    for f in ("app/static/app.js", "app/static/admin.js"):
        js = open(f).read()
        assert ".innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js
    for page in ("/", "/admin"):
        r = a.get(page)
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp
        assert r.headers["x-frame-options"] == "DENY"
    assert "noindex" in a.get("/admin").headers["x-robots-tag"]

def test_cookie_flags(mod):
    r = client(mod).post("/api/signup", json={"email": "z@x.com", "password": "password123", "name": "Z", "age": 22}, headers=H)
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    r = client(mod).post("/api/admin/login", json={"password": ADMIN_PW}, headers=H)
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()

def test_password_hashed(mod):
    make_user(mod, "a@x.com")
    with mod.db() as c:
        h = c.execute("SELECT pw_hash FROM users").fetchone()[0]
    assert "password123" not in h and len(h) == 64

def test_auth_rate_limit(mod):
    c = client(mod)
    codes = [c.post("/api/login", json={"email": "n@x.com", "password": "password1"}, headers=H).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[-1] == 429

def test_no_self_swipe_or_report(mod):
    a, ida = make_user(mod, "a@x.com")
    assert a.post("/api/swipe", json={"target": ida, "action": "like"}, headers=H).status_code == 400
    assert a.post("/api/report", json={"target": ida}, headers=H).status_code == 400
    assert a.post("/api/swipe", json={"target": 999, "action": "like"}, headers=H).status_code == 404

def test_api_docs_disabled_and_health(mod):
    c = client(mod)
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    assert c.get("/healthz").json() == {"ok": True}
