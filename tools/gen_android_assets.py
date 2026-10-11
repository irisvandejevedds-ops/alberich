"""Export the web app's pixel icons, fonts and launcher icon into the Android project."""
import json, pathlib, shutil, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from finapp import pxicons, sprites
A = ROOT / "android"
(A / "assets").mkdir(parents=True, exist_ok=True)
(A / "assets/icons.json").write_text(json.dumps({"pal": pxicons.PAL, "icons": {k: [r.ljust(16, ".")[:16] for r in v] for k, v in pxicons.ICONS.items()}}))
(A / "assets/alberich.json").write_text(json.dumps({"base": sprites.BASE, "overlays": sprites.OVERLAYS, "palettes": sprites.PALETTES}))
shutil.copy("/usr/share/fonts/xscreensaver/clacon.ttf", A / "assets/pixel.ttf")
shutil.copy("/usr/share/fonts/opentype/mathjax/MathJax_Fraktur-Bold.otf", A / "assets/script.otf")
from PIL import Image, ImageDraw
(A / "res/drawable-nodpi").mkdir(parents=True, exist_ok=True)
RING = [(4, 0), (7, 1), (8, 4), (7, 7), (4, 8), (1, 7), (0, 4), (1, 1)]
SHADES = ["#FFE08A", "#E0AC3A", "#9A7A2C", "#5F5025"]
for f in range(8):
    im = Image.new("RGBA", (80, 80), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    for i, (x, y) in enumerate(RING):
        age = (f - i) % 8
        d.rectangle([x * 8, y * 8, x * 8 + 15, y * 8 + 15], fill=SHADES[min(age, 3)])
    im.save(A / f"res/drawable-nodpi/spin{f}.png")
(A / "res/drawable/spin_anim.xml").write_text('<?xml version="1.0" encoding="utf-8"?>\n<animation-list xmlns:android="http://schemas.android.com/apk/res/android" android:oneshot="false">\n' + "".join(f'    <item android:drawable="@drawable/spin{i}" android:duration="95"/>\n' for i in range(8)) + '</animation-list>\n')
print("assets ok", sorted(p.name for p in (A / "assets").iterdir()))
