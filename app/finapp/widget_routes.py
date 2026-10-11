"""The home-screen widget: get the app, make a key for the phone, revoke keys."""
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import tokens
from .db import DB

APK_NAME = "alberich-widget.apk"
PACKAGE = "app.alberich.widget"


def register(app, cfg, get_db, session, render):
    def apk_path():
        return cfg.data_dir / "apk" / APK_NAME

    def page(request, db, new_token=None):
        base = f"https://{request.headers.get('host', '')}"
        link = None
        if new_token:
            q = f"url={quote(base, safe='')}&token={quote(new_token, safe='')}"
            link = f"intent://setup?{q}#Intent;scheme=alberich;package={PACKAGE};end"
        p = apk_path()
        return render(request, db, "widget.html", keys=tokens.listing(db), new_token=new_token, link=link, base=base,
                      apk=p.exists(), apk_kb=(p.stat().st_size // 1024) if p.exists() else 0)

    @app.get("/settings/widget", response_class=HTMLResponse)
    def widget_page(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        return page(request, db)

    @app.post("/settings/widget/new", response_class=HTMLResponse)
    async def widget_new(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        return page(request, db, tokens.create(db, (f.get("name") or "My phone").strip()))

    @app.post("/settings/widget/revoke")
    async def widget_revoke(request: Request, db: DB = Depends(get_db), _=Depends(session)):
        f = await request.form()
        tokens.revoke(db, int(f["id"]))
        return RedirectResponse("/settings/widget", 303)

    @app.get("/download/" + APK_NAME)
    def apk(_=Depends(session)):
        p = apk_path()
        if not p.exists():
            raise HTTPException(404, "The widget app has not been built yet.")
        return FileResponse(p, media_type="application/vnd.android.package-archive", filename=APK_NAME)
