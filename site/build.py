"""Builds the static site into site/dist:
   index.html + demo/ (sample data) + app/ (real, empty, stored in the browser) + downloads/ (the phone app and the desktop package).
    python site/build.py            (build the phone app first: android/publish.sh)"""
import hashlib
import pathlib
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE, DIST, WEB = ROOT / "site", ROOT / "site/dist", ROOT / "web"
APK = ROOT / "release/alberich.apk"      # the signed build, kept in the repo by android/publish.sh
WEB_FILES = ["index.html", "app.js", "app.css", "frames.css", "assets.js", "core.js", "store.js", "manifest.webmanifest", "icon-192.png", "icon-512.png", "icon-maskable-512.png",
             "mood-annoyed.png", "mood-evil.png", "mood-happy.png", "mood-neutral.png", "mood-sick.png", "badge.png"]
FONTS = ["fraktur-bold.woff2", "pixel-console.woff2"]


def copy_web(dest, config):
    dest.mkdir(parents=True, exist_ok=True)
    for f in WEB_FILES:
        shutil.copyfile(WEB / f, dest / f)
    (dest / "fonts").mkdir(exist_ok=True)
    for f in FONTS:
        shutil.copyfile(WEB / "fonts" / f, dest / "fonts" / f)
    (dest / "config.js").write_text(config)


def size(p):
    return f"{p.stat().st_size / 1024:.0f} KB"


def main():
    if not APK.exists():
        raise SystemExit("release/alberich.apk is missing: run android/publish.sh to build and sign it.")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    copy_web(DIST / "demo", "window.ALBERICH_CONFIG = { demo: true, downloadUrl: '../' };\n")
    copy_web(DIST / "app", "window.ALBERICH_CONFIG = { downloadUrl: '../' };\n")
    (DIST / "downloads").mkdir()
    shutil.copyfile(APK, DIST / "downloads/alberich.apk")
    pkg = DIST / "downloads/alberich-desktop.zip"
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ("start.py", "requirements.txt", "README.txt"):
            z.write(ROOT / "desktop" / f, f"alberich-desktop/{f}")
        for p in sorted((ROOT / "app/finapp").rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                z.write(p, f"alberich-desktop/finapp/{p.relative_to(ROOT / 'app/finapp')}")
        z.write(APK, "alberich-desktop/data/apk/alberich-widget.apk")
    html = (SITE / "src/index.html").read_text().replace("__APK_INFO__", f"Android 8 or newer, {size(APK)}").replace("__DESKTOP_INFO__", f"Python 3.10+, {size(pkg)}")
    (DIST / "index.html").write_text(html)
    for f in ("site.css", "site.js"):
        shutil.copyfile(SITE / "src" / f, DIST / f)
    for f in ("app.css", "frames.css", "assets.js", "icon-192.png", "icon-512.png"):
        shutil.copyfile(WEB / f, DIST / f)
    (DIST / "fonts").mkdir()
    for f in FONTS:
        shutil.copyfile(WEB / "fonts" / f, DIST / "fonts" / f)
    csp = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    (DIST / "_headers").write_text(
        f"/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  Content-Security-Policy: {csp}\n"
        "/downloads/*.apk\n  Content-Type: application/vnd.android.package-archive\n  Content-Disposition: attachment\n"
        "/downloads/*.zip\n  Content-Disposition: attachment\n")
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted((DIST / "downloads").iterdir()))
    (DIST / "downloads/SHA256SUMS").write_text(sums)
    print("site built:", DIST)
    for p in sorted(DIST.rglob("*")):
        if p.is_file():
            print(f"  {str(p.relative_to(DIST)):42s}{size(p):>9}")


if __name__ == "__main__":
    main()
