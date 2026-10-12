import json
import pathlib
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from fastapi.templating import Jinja2Templates

from markupsafe import Markup

from . import auth, clock, credits, ledger, ledger_routes, pairing, pxicons, notify, pet, push, required, receipt_routes, review_routes, sort_routes, spending, sprites, sync, tokens, treasury_routes, warlock_routes, widget_routes
from .config import get_config
from .db import DB, open_db

HERE = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
PUBLIC_PREFIXES = ("/static/", "/healthz", "/login", "/setup", "/sw.js", "/manifest.webmanifest", "/offline")
NAV = [("Hall", "/", "castle"), ("Purse", "/activities", "pouch"), ("Ledgers", "/ledgers", "ledger"), ("Receipts", "/receipts", "scroll"),
       ("Review", "/review", "check"), ("Treasury", "/treasury", "chest"), ("Warlock", "/warlock", "hat"), ("Ravens", "/settings/notifications", "raven")]
STARTERS = [("Ordered in", "takeout", "order in", 4000, 3), ("Drinks out", "drinks", "go out for drinks", 6000, 3),
            ("Toy", "toy", "buy a toy", 5000, 2), ("Shopping", "shopping", "go shopping", 12000, 2),
            ("Tickets", "ticket", "get tickets to something", 9000, 2),
            ("Medium grocery shop", "cart", "do a medium grocery shop", 9000, 3),
            ("Big grocery shop", "basket", "do a big grocery shop", 20000, 3)]
MOOD_TEXT = {"neutral": ("Brooding", "You have barely spent. The hoard sits untouched, and so does he."),
             "annoyed": ("Cross", "You have been away. He keeps count."),
             "happy": ("Pleased", "Spending within reason. The golden mean agrees with him."),
             "sick": ("Ailing", "You have overspent. The coin-sickness has him."),
             "evil": ("Cursed", "Far past the plan. The Ring's curse is awake.")}
SAYINGS = {"neutral": ["The hoard sits still. As do you. Hmph.", "Nothing spent, nothing gained. Dull."],
           "annoyed": ["I count the days like coins. {d} of them.", "You dare ignore a lord of the Nibelungs?"],
           "happy": ["Moderation! Even I approve.", "The golden mean. Hmph. Acceptable."],
           "sick": ["Ugh... the coin-sickness...", "Too much. Far too much."],
           "evil": ["THE CURSE IS UPON YOU!", "Overspend again and I shall haunt your pockets."]}
ZONE_TEXT = {"idle": "barely spending", "moderate": "spending within reason", "over": "a little over", "excess": "far over"}


class NeedLogin(Exception):
    pass


class NeedSetup(Exception):
    pass


def money(cents):
    return f"${(cents or 0) / 100:,.2f}"


def to_cents(s):
    try:
        return int((Decimal(str(s).replace("$", "").replace(",", "").strip() or "0") * 100).quantize(Decimal(1), ROUND_HALF_UP))
    except InvalidOperation:
        return 0


def fmt_when(dt, tz, now=None):
    d = dt.astimezone(tz)
    n = (now or clock.now()).astimezone(tz)
    day = "today" if d.date() == n.date() else "tomorrow" if d.date() == (n + timedelta(days=1)).date() else d.strftime("%a %b %-d")
    return f"{day} {d.strftime('%-I:%M %p').lower()}"


templates.env.filters["money"] = money
templates.env.globals["pxicon"] = lambda n, size=24, cls="": Markup(pxicons.svg(n, size, cls))
templates.env.globals["picker_icons"] = pxicons.PICKER


def seed_defaults(db: DB):
    if db.get_setting("starter_seeded") or db.val("SELECT COUNT(*) FROM activities", default=0):
        return
    now = clock.iso(clock.now())
    for i, (n, ic, ph, est, w) in enumerate(STARTERS):
        db.ex("""INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,created_at)
                 VALUES(?,?,?,?,?,0,'{}',?,?)""", (n, ic, ph, est, w, i, now))
    db.set_setting("starter_seeded", "1")


def create_app():
    cfg = get_config()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    boot = open_db(cfg.db_path)
    seed_defaults(boot)
    pxicons.migrate_db(boot)
    boot.close()

    def get_db():
        d = DB(cfg.db_path)
        try:
            yield d
        finally:
            d.close()

    def session(request: Request, db: DB = Depends(get_db)):
        if not auth.has_passphrase(db):
            raise NeedSetup()
        s = auth.lookup_session(db, request.cookies.get(auth.COOKIE))
        if not s:
            bearer = request.headers.get("authorization", "")
            if bearer[:7].lower() == "bearer " and request.url.path in tokens.ALLOWED:
                if tokens.too_many_failures():
                    raise HTTPException(429, "too many attempts")
                if tokens.verify(db, bearer[7:].strip(), request.url.path):
                    return {"token": True}
                tokens.note_failure()
            raise NeedLogin()
        if request.method == "GET" and not request.url.path.startswith("/api/"):
            notify.touch(db)                              # opening a page counts as showing up
        return s

    def wants_json(request):
        return request.url.path.startswith("/api/")

    @app.exception_handler(NeedLogin)
    async def _need_login(request, exc):
        if wants_json(request):
            return JSONResponse({"error": "login required"}, status_code=401)
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/login?next=" + nxt, status_code=303)

    @app.exception_handler(NeedSetup)
    async def _need_setup(request, exc):
        return JSONResponse({"error": "setup required"}, status_code=401) if wants_json(request) else RedirectResponse("/setup", 303)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if cfg.sync_only and path not in ("/healthz", "/api/sync"):
            return PlainTextResponse("Not found", status_code=404)
        if path != "/healthz" and cfg.require_ts_header and not cfg.sync_only:
            who = request.headers.get("tailscale-user-login", "").lower()
            if who not in cfg.ts_logins:
                return PlainTextResponse("This app is only available on your tailnet.", status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            site = request.headers.get("sec-fetch-site")
            origin = request.headers.get("origin")
            if site and site not in ("same-origin", "none"):
                return PlainTextResponse("Cross-site request blocked.", status_code=403)
            if origin and site not in ("same-origin", "none") and urlparse(origin).netloc != request.headers.get("host", ""):
                return PlainTextResponse("Cross-site request blocked.", status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if not path.startswith("/static/") and path != "/sw.js":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    def ctx(request, db, **kw):
        return {"request": request, "nav": NAV, "path": request.url.path, "pet_name": "Alberich",
                "money": money, "tz": cfg.tz, **kw}

    def render(request, db, name, **kw):
        return templates.TemplateResponse(request, name, ctx(request, db, **kw))

    @app.exception_handler(StarletteHTTPException)
    async def _friendly_errors(request, exc):
        """Forms get a readable page with a way back; the JSON API and static files keep plain answers."""
        if wants_json(request) or request.url.path.startswith("/static/") or request.method == "HEAD":
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        back = "/"
        ref = request.headers.get("referer", "")
        if ref:
            u = urlparse(ref)
            if u.netloc == request.headers.get("host", "") and u.path.startswith("/"):
                back = u.path + (("?" + u.query) if u.query else "")
        msg = exc.detail if isinstance(exc.detail, str) else "Something went wrong."
        title = {404: "Nothing here", 429: "Slow down"}.get(exc.status_code, "Hold on")
        return templates.TemplateResponse(request, "error.html", ctx(request, None, title=title, message=msg, back=back, code=exc.status_code),
                                          status_code=exc.status_code)

    # ---------------- tiles ----------------
    def tile_view(s, now):
        a, tz = s["activity"], cfg.tz
        if s["allotted"] <= 0:
            status, label = "unset", "No credits this month"
        elif s["overdrawn_now"]:
            status, label = "overdrawn", f"Over by {s['used'] - s['allotted']}"
        elif s["remaining"] == 0:
            status, label = "done", "All used"
        elif s["available"] > 0:
            status, label = "available", f"{s['available']} ready now"
        else:
            status = "waiting"
            label = f"Next {fmt_when(s['next_at'], tz, now)}" if s["next_at"] else "Nothing more unlocks"
        return {"id": a["id"], "name": a["name"], "icon": a["icon"], "allotted": s["allotted"], "used": s["used"],
                "remaining": s["allotted"] - s["used"], "available": s["available"], "status": status, "label": label}

    def home_data(db, now):
        month = credits.month_of(now, cfg.tz)
        credits.ensure_periods(db, month, cfg.tz)
        states = credits.all_states(db, month, cfg.tz, now)
        plan = credits.get_plan(db, month)
        if notify.last_interaction(db) is None:
            notify.touch(db, now)
        st, _ = pet.current(db, states, plan["buffer_cents"], now, idle_days=notify.idle_days(db, now))
        return month, states, plan, st

    def mood_key(st):
        return "annoyed" if st["annoyed"] else st["mood"]

    def saying(st, now):
        opts = SAYINGS[mood_key(st)]
        return opts[now.astimezone(cfg.tz).date().toordinal() % len(opts)].format(d=int(st["idle_days"]))

    # ---------------- auth pages ----------------
    @app.get("/healthz")
    def healthz():
        return PlainTextResponse("ok")

    def safe_next(n):
        return n if n and n.startswith("/") and not n.startswith("//") else "/"

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request, db: DB = Depends(get_db)):
        if auth.has_passphrase(db):
            return RedirectResponse("/login", 303)
        return render(request, db, "login.html", mode="setup", error=None, next="/", svg=sprites.svg("neutral", 6, cls="bob"), bare=True)

    @app.post("/setup")
    def setup_submit(request: Request, passphrase: str = Form(...), confirm: str = Form(...), db: DB = Depends(get_db)):
        if auth.has_passphrase(db):
            return RedirectResponse("/login", 303)
        if len(passphrase) < auth.MIN_PASSPHRASE or passphrase != confirm:
            return render(request, db, "login.html", mode="setup", next="/", svg=sprites.svg("neutral", 6, cls="bob"), bare=True,
                          error=f"Use at least {auth.MIN_PASSPHRASE} characters, typed the same twice.")
        auth.set_passphrase(db, passphrase)
        resp = RedirectResponse("/", 303)
        resp.set_cookie(auth.COOKIE, auth.create_session(db, request.headers.get("user-agent", "")),
                        max_age=auth.SESSION_DAYS * 86400, httponly=True, secure=cfg.cookie_secure, samesite="strict")
        return resp

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/", db: DB = Depends(get_db)):
        if not auth.has_passphrase(db):
            return RedirectResponse("/setup", 303)
        return render(request, db, "login.html", mode="login", error=None, next=safe_next(next), svg=sprites.svg("annoyed", 6, cls="bob"), bare=True)

    @app.post("/login")
    def login_submit(request: Request, passphrase: str = Form(...), next: str = Form("/"), db: DB = Depends(get_db)):
        if auth.locked():
            return render(request, db, "login.html", mode="login", next=safe_next(next), svg=sprites.svg("annoyed", 6, cls="bob"), bare=True,
                          error="Too many attempts. Wait ten minutes and try again.")
        if not auth.check_passphrase(db, passphrase):
            auth.record_fail()
            return render(request, db, "login.html", mode="login", next=safe_next(next), svg=sprites.svg("annoyed", 6, cls="bob"), bare=True, error="That passphrase is not right.")
        auth.reset_fails()
        resp = RedirectResponse(safe_next(next), 303)
        resp.set_cookie(auth.COOKIE, auth.create_session(db, request.headers.get("user-agent", "")),
                        max_age=auth.SESSION_DAYS * 86400, httponly=True, secure=cfg.cookie_secure, samesite="strict")
        return resp

    @app.post("/logout")
    def logout(request: Request, db: DB = Depends(get_db)):
        auth.destroy_session(db, request.cookies.get(auth.COOKIE))
        resp = RedirectResponse("/login", 303)
        resp.delete_cookie(auth.COOKIE)
        return resp

    # ---------------- pwa assets ----------------
    @app.get("/sw.js")
    def sw():
        return FileResponse(HERE / "static" / "sw.js", media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest(db: DB = Depends(get_db)):
        icon = [{"src": "/static/icons/icon-192.png?v=2", "sizes": "192x192", "type": "image/png"}]
        shortcuts = [{"name": "Log spending", "short_name": "Log", "url": "/", "icons": icon}]
        return JSONResponse({
            "name": "Alberich", "short_name": "Alberich", "description": "A dwarf lord who keeps your money honest.",
            "id": "/", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#E9C28A", "theme_color": "#B07A45",
            "icons": [{"src": "/static/icons/icon-192.png?v=2", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                      {"src": "/static/icons/icon-512.png?v=2", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                      {"src": "/static/icons/icon-maskable-512.png?v=2", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
            "shortcuts": shortcuts,
            "share_target": {"action": "/share", "method": "POST", "enctype": "multipart/form-data",
                             "params": {"files": [{"name": "files", "accept": ["application/pdf", "text/csv", "image/*", ".csv", ".ofx", ".qfx", ".pdf"]}]}}},
            media_type="application/manifest+json")

    @app.get("/offline", response_class=HTMLResponse)
    def offline(request: Request):
        return templates.TemplateResponse(request, "offline.html", {"request": request})

    # ---------------- home ----------------
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        now = clock.now()
        month, states, plan, st = home_data(db, now)
        all_tiles = [tile_view(s, now) for s in states]
        tiles = [t for t in all_tiles if t["status"] != "unset"] or all_tiles
        idle_n = len(all_tiles) - len(tiles)
        ready = sum(t["available"] for t in tiles if t["status"] == "available")
        nxt = sorted((s["next_at"], s["activity"]["name"]) for s in states if s["next_at"] and s["allotted"] > 0)
        summary = (f"{ready} credit{'s' if ready != 1 else ''} ready now." if ready else "Nothing unlocked right now.")
        if nxt:
            summary += f" Next: {nxt[0][1]} unlocks {fmt_when(nxt[0][0], cfg.tz, now)}."
        k = mood_key(st)
        title, sub = MOOD_TEXT[k]
        _c, mrows = required.matrix(db, now.astimezone(cfg.tz).date(), 12)
        owed = required.summary(mrows)
        return render(request, db, "home.html", owed=owed, tiles=tiles, pet=st, svg=sprites.svg(k, 4, cls="bob"), mood_key=k,
                      mood_title=title, mood_sub=sub, saying=saying(st, now), summary=summary,
                      unset=all(t["allotted"] == 0 for t in all_tiles), idle_n=idle_n, month=month)

    @app.get("/api/state")
    def api_state(db: DB = Depends(get_db), _=Depends(session)):
        now = clock.now()
        month, states, plan, st = home_data(db, now)
        return {"tiles": [tile_view(s, now) for s in states], "mood": mood_key(st), "w": st["w"]}

    # ---------------- logging ----------------
    @app.post("/api/log")
    async def api_log(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        body = await request.json()
        aid, key = int(body.get("activity_id", 0)), str(body.get("key") or "")[:80]
        act = db.one("SELECT * FROM activities WHERE id=? AND archived=0", (aid,))
        if not act:
            raise HTTPException(404, "unknown activity")
        now = clock.now()
        ts = now
        if body.get("ts"):
            try:
                ts = clock.parse_iso(body["ts"])
            except ValueError:
                raise HTTPException(400, "bad timestamp")
            if ts > now + timedelta(minutes=5) or ts < now - timedelta(days=45):
                raise HTTPException(400, "timestamp out of range")
        existing = db.one("SELECT id FROM activity_logs WHERE idempotency_key=?", (key,)) if key else None
        if existing:
            log_id = existing["id"]
        else:
            log_id = db.ex("INSERT INTO activity_logs(activity_id,ts,source,idempotency_key,note) VALUES(?,?,?,?,?)",
                           (aid, clock.iso(ts), "live", key or None, (body.get("note") or "")[:200])).lastrowid
            notify.on_log(db, cfg, aid, log_id, now)
        month = credits.month_of(now, cfg.tz)
        s = credits.activity_state(db, act, month, cfg.tz, now)
        status = s["statuses"][s["log_ids"].index(log_id)] if log_id in s["log_ids"] else "on_pace"
        _, _, plan, stt = home_data(db, now)
        return {"ok": True, "log_id": log_id, "status": status, "tile": tile_view(s, now), "mood": mood_key(stt)}

    @app.post("/api/undo")
    async def api_undo(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        body = await request.json()
        row = db.one("SELECT * FROM activity_logs WHERE id=?", (int(body.get("log_id", 0)),))
        if not row:
            raise HTTPException(404, "unknown log")
        db.ex("UPDATE activity_logs SET undone_at=? WHERE id=? AND undone_at IS NULL", (clock.iso(clock.now()), row["id"]))
        now = clock.now()
        act = db.one("SELECT * FROM activities WHERE id=?", (row["activity_id"],))
        s = credits.activity_state(db, act, credits.month_of(now, cfg.tz), cfg.tz, now)
        _, _, plan, stt = home_data(db, now)
        return {"ok": True, "tile": tile_view(s, now), "mood": mood_key(stt)}

    # ---------------- activities (configurable in-app) ----------------
    def act_forms(db):
        out = []
        for a in db.q("SELECT * FROM activities ORDER BY archived, sort, id"):
            c = credits.cadence_of(a)
            out.append({"row": a, "cad": c, "estimate": f"{a['estimate_cents'] / 100:.2f}"})
        return out

    @app.get("/activities", response_class=HTMLResponse)
    def activities_page(request: Request, tab: str = "plan", db: DB = Depends(get_db), _=Depends(session)):
        now = clock.now()
        month = credits.month_of(now, cfg.tz)
        rows = act_forms(db)
        ids = {str(r["row"]["id"]) for r in rows}
        tab = tab if (tab in ids or tab in ("plan", "new")) else "plan"
        only = int(tab) if tab.isdigit() else None
        uses = []
        for l in db.q("""SELECT l.id, l.ts, l.undone_at, l.note, a.name, a.icon FROM activity_logs l JOIN activities a ON a.id=l.activity_id
                         WHERE l.ts>=? AND (? IS NULL OR l.activity_id=?) ORDER BY l.ts DESC, l.id DESC LIMIT 40""",
                      (clock.iso(now - timedelta(days=45)), only, only)):
            when = clock.parse_iso(l["ts"]).astimezone(cfg.tz)
            uses.append({**dict(l), "when": when.strftime("%a %b %d, %-I:%M %p")})
        derived, states, guess, over = {}, {}, {}, {}
        if tab == "plan":
            by_name = {r["name"]: r["id"] for r in db.q("SELECT id, name FROM categories")}
            for r in rows:
                g = ledger.guess_category(r["row"]["name"]) if not r["row"]["category_id"] else None
                guess[r["row"]["id"]] = by_name.get(g) if g else None
            derived = spending.derive(db)
            for r in rows:
                if not r["row"]["archived"]:
                    over[r["row"]["id"]] = credits.overdraw_history(db, r["row"], month, cfg.tz, now)
            states = {s["activity"]["id"]: s for s in credits.all_states(db, month, cfg.tz, now)}
        return render(request, db, "activities.html", uses=uses, tab=tab, cats=db.q("SELECT id, name FROM categories ORDER BY sort, name"), rows=rows, month=month,
                      derived=derived, states=states, guess=guess, over=over, days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])

    @app.post("/activities/log-undo")
    async def activity_log_undo(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        row = db.one("SELECT * FROM activity_logs WHERE id=?", (int(f.get("id") or 0),))
        if not row:
            raise HTTPException(404, "unknown log")
        db.ex("UPDATE activity_logs SET undone_at=? WHERE id=?", (None if f.get("restore") == "1" else clock.iso(clock.now()), row["id"]))
        return RedirectResponse(f.get("back") if str(f.get("back", "")).startswith("/activities") else "/activities", 303)

    @app.post("/activities/categories")
    async def activity_categories(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        """Give pouches that have no category one, so their credits can follow the spending in it."""
        f = await request.form()
        for k, v in f.items():
            if k.startswith("cat_") and k[4:].isdigit() and (v or "").isdigit() and db.one("SELECT 1 FROM categories WHERE id=?", (int(v),)):
                db.ex("UPDATE activities SET category_id=?, auto_credits=1 WHERE id=? AND category_id IS NULL", (int(v), int(k[4:])))
        spending.sync_purse(db, cfg.tz, clock.now())
        return RedirectResponse("/activities?tab=plan", 303)

    @app.post("/activities/save")
    async def activity_save(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        name = (f.get("name") or "").strip()[:60]
        if not name:
            raise HTTPException(400, "name required")
        cad = {"mode": f.get("mode") if f.get("mode") in ("even", "weekdays") else "even",
               "time": (f.get("time") or "09:00")[:5], "dows": sorted(int(x) for x in f.getlist("dow") if x.isdigit() and 0 <= int(x) <= 6),
               "notify": f.get("notify") == "on"}
        vals = (name, pxicons.normalise(f.get("icon")), (f.get("phrase") or "").strip()[:80], to_cents(f.get("estimate")),
                max(1, min(5, int(f.get("weight") or 3))), max(0, min(60, int(f.get("credits") or 0))), json.dumps(cad))
        aid = f.get("id")
        if aid:
            db.ex("""UPDATE activities SET name=?,icon=?,phrase=?,estimate_cents=?,weight=?,credits_per_month=?,cadence=?
                     WHERE id=?""", (*vals, int(aid)))
        else:
            aid = db.ex("""INSERT INTO activities(name,icon,phrase,estimate_cents,weight,credits_per_month,cadence,sort,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (*vals, db.val("SELECT COALESCE(MAX(sort),0)+1 FROM activities"), clock.iso(clock.now()))).lastrowid
        cid = f.get("category_id")
        cid = int(cid) if cid and cid.isdigit() else None
        db.ex("UPDATE activities SET category_id=?, auto_credits=? WHERE id=?", (cid, 1 if (cid and f.get("auto") == "on") else 0, int(aid)))
        act = db.one("SELECT * FROM activities WHERE id=?", (int(aid),))
        credits.build_period(db, credits.month_of(clock.now(), cfg.tz), act, cfg.tz, replace=True)
        spending.sync_purse(db, cfg.tz, clock.now())
        return RedirectResponse(f"/activities?tab={aid}", 303)

    @app.post("/activities/archive")
    async def activity_archive(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        db.ex("UPDATE activities SET archived=? WHERE id=?", (1 if f.get("archive") == "1" else 0, int(f.get("id"))))
        return RedirectResponse(f"/activities?tab={int(f.get('id'))}", 303)

    # ---------------- pet ----------------
    @app.get("/pet", response_class=HTMLResponse)
    def pet_page(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        now = clock.now()
        month, states, plan, st = home_data(db, now)
        k = mood_key(st)
        title, sub = MOOD_TEXT[k]
        S, E, OD, od_n = pet.spend_inputs(states)
        st["spent_n"], st["expected_n"], st["overdrawn_n"] = sum(s["used"] for s in states), sum(min(s["unlocked"], s["allotted"]) for s in states), od_n
        gallery = [(m, MOOD_TEXT[m][0], sprites.svg(m, 6)) for m in sprites.MOODS]
        return render(request, db, "pet.html", pet=st, svg=sprites.svg(k, 8, cls="bob"), mood_key=k, mood_title=title,
                      mood_sub=sub, zone_text=ZONE_TEXT[st["zone"]], gallery=gallery,
                      history=db.q("SELECT * FROM pet_state_history ORDER BY id DESC LIMIT 8"))

    @app.get("/api/sprite/{mood}")
    def sprite_svg(mood: str, _=Depends(session)):
        if mood not in sprites.MOODS:
            raise HTTPException(404)
        return PlainTextResponse(sprites.svg(mood, 4, cls="bob"), media_type="image/svg+xml")

    # ---------------- notifications ----------------
    @app.get("/settings/notifications", response_class=HTMLResponse)
    def notif_page(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        prefs = notify.get_prefs(db)
        subs = db.q("SELECT * FROM push_subscriptions ORDER BY id DESC")
        recent = db.q("SELECT * FROM notifications_sent WHERE rule_key NOT LIKE 'test%' ORDER BY id DESC LIMIT 8")
        return render(request, db, "notifications.html", prefs=prefs, types=notify.TYPES, subs=subs, recent=recent,
                      alive=any(s["active"] for s in subs))

    @app.post("/settings/notifications")
    async def notif_save(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        prefs = notify.get_prefs(db)
        prefs["types"] = {k: f.get(f"t_{k}") == "on" for k in notify.TYPES}
        prefs["quiet"] = {"start": (f.get("quiet_start") or "22:00")[:5], "end": (f.get("quiet_end") or "08:00")[:5]}
        prefs["daily_cap"] = max(0, min(20, int(f.get("daily_cap") or 2)))
        prefs["tone"] = "plain" if f.get("tone") == "plain" else "pet"
        prefs["nag_ceiling"] = max(1, min(5, int(f.get("nag_ceiling") or 5)))
        days = int(f.get("snooze_days") or 0)
        prefs["snooze_until"] = clock.iso(clock.now() + timedelta(days=days)) if days else ""
        notify.save_prefs(db, prefs)
        return RedirectResponse("/settings/notifications", 303)

    @app.get("/api/push/key")
    def push_key(_=Depends(session)):
        return {"key": push.public_key_b64url(cfg.vapid_pem)}

    @app.post("/api/push/subscribe")
    async def push_subscribe(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        b = await request.json()
        k = b.get("keys") or {}
        if not (b.get("endpoint") and k.get("p256dh") and k.get("auth")) or not str(b["endpoint"]).startswith("https://"):
            raise HTTPException(400, "bad subscription")
        db.ex("""INSERT INTO push_subscriptions(endpoint,p256dh,auth,user_agent,created_at) VALUES(?,?,?,?,?)
                 ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, active=1,
                 fail_count=0, last_error=NULL""",
              (b["endpoint"], k["p256dh"], k["auth"], request.headers.get("user-agent", "")[:200], clock.iso(clock.now())))
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    async def push_unsub(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        b = await request.json()
        db.ex("UPDATE push_subscriptions SET active=0 WHERE endpoint=?", (b.get("endpoint"),))
        return {"ok": True}

    @app.post("/api/push/test")
    def push_test(db: DB = Depends(get_db), _=Depends(session)):
        name = "Alberich"
        mood = notify.last_mood(db)
        pl = notify.payload(name, "A test raven. If you can read this, the road is open.", mood=mood, tag="test", url="/settings/notifications")
        n = push.dispatch(db, cfg, pl)
        notify.record(db, "test", clock.iso(clock.now()), n > 0, name, pl["body"], clock.now())
        return {"ok": n > 0, "delivered": n}

    ledger_routes.register(app, cfg, get_db, session, render)
    review_routes.register(app, cfg, get_db, session, render)
    sort_routes.register(app, cfg, get_db, session, render)
    receipt_routes.register(app, cfg, get_db, session, render)
    treasury_routes.register(app, cfg, get_db, session, render)
    warlock_routes.register(app, cfg, get_db, session, render)
    # ---------------- phones ----------------
    @app.post("/api/sync")
    async def api_sync(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        if int(request.headers.get("content-length") or 0) > sync.MAX_BODY:
            raise HTTPException(413, "too much data in one sync")
        raw = await request.body()
        if len(raw) > sync.MAX_BODY:
            raise HTTPException(413, "too much data in one sync")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("body is not an object")
            return sync.handle(db, cfg, body)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e) or "bad sync request")

    def phone_page(request, db, new_token=None):
        urls = pairing.endpoints(cfg, request.headers.get("host", ""))
        link = pairing.link(cfg, urls, new_token, name=pairing.computer_name()) if new_token else None
        apk = cfg.data_dir / "apk" / widget_routes.APK_NAME
        return render(request, db, "phone.html", phones=tokens.listing(db, "sync"), new_token=new_token, link=link, urls=urls, qr=pairing.qr_svg(link) if link else None,
                      tls_ready=pairing.pin(cfg) is not None, apk=apk.exists(), apk_kb=(apk.stat().st_size // 1024) if apk.exists() else 0)

    @app.get("/settings/phone", response_class=HTMLResponse)
    def phone_get(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        return phone_page(request, db)

    @app.post("/settings/phone/new", response_class=HTMLResponse)
    async def phone_new(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        return phone_page(request, db, tokens.create(db, (f.get("name") or "My phone").strip(), "sync"))

    @app.post("/settings/phone/revoke")
    async def phone_revoke(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        if (f.get("id") or "").isdigit():
            tokens.revoke(db, int(f["id"]))
        return RedirectResponse("/settings/phone", 303)

    widget_routes.register(app, cfg, get_db, session, render)
    return app


def get_app():
    return create_app()
