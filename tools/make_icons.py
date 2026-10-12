"""Render PWA, notification and Android launcher icons. The app icon is the wordmark's Fraktur "A" in gold on royal purple, on the phone and in the browser alike.
Run with the app venv (needs Pillow)."""
import pathlib, sys
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
from finapp import sprites

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "app/finapp/static/icons"
AND = ROOT / "android/res"
FONT = "/usr/share/fonts/opentype/mathjax/MathJax_Fraktur-Bold.otf"
PURPLE, GOLD, SHADOW = (91, 58, 130, 255), (255, 224, 138, 255), (38, 18, 63, 255)
GOLD_BG, GOLD_SHADOW = (224, 172, 58, 255), (122, 80, 12, 255)
S = 1024


def letter(size_frac, shadow=True, color=GOLD, canvas=S, shadow_color=SHADOW):
    """The A on a transparent canvas, optically centred."""
    f = ImageFont.truetype(FONT, int(canvas * size_frac))
    bb = f.getbbox("A")
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    x, y = (canvas - w) // 2 - bb[0], (canvas - h) // 2 - bb[1]
    im = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shadow:
        off = max(2, canvas // 64)
        d.text((x + off, y + off), "A", font=f, fill=shadow_color)
    d.text((x, y), "A", font=f, fill=color)
    return im


def circle_icon():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([0, 0, S - 1, S - 1], fill=PURPLE)
    im.alpha_composite(letter(0.80))
    return im


def maskable_icon():
    im = Image.new("RGBA", (S, S), PURPLE)
    im.alpha_composite(letter(0.58))
    return im


def save(im, path, px):
    im.resize((px, px), Image.LANCZOS).save(path, "PNG")


OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.png"):
    old.unlink()
for m in sprites.MOODS:
    (OUT / f"mood-{m}.png").write_bytes(sprites.png(m, 192))
c, mk = circle_icon(), maskable_icon()
save(c, OUT / "icon-192.png", 192)
save(c, OUT / "icon-512.png", 512)
save(mk, OUT / "icon-maskable-512.png", 512)
badge = letter(0.9, shadow=False, color=(255, 255, 255, 255))
save(badge, OUT / "badge.png", 96)

# Android adaptive launcher icon: purple background colour + transparent foreground with the gold A in the safe zone
fg = letter(0.50, canvas=432)
(AND / "drawable-xxxhdpi").mkdir(parents=True, exist_ok=True)
fg.save(AND / "drawable-xxxhdpi/ic_launcher_fg.png", "PNG")
(AND / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
(AND / "mipmap-anydpi-v26/ic_launcher.xml").write_text('''<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_bg"/>
    <foreground android:drawable="@drawable/ic_launcher_fg"/>
    <monochrome android:drawable="@drawable/ic_launcher_fg"/>
</adaptive-icon>
''')
(AND / "values/colors.xml").write_text('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_bg">#5B3A82</color>\n</resources>\n')
save(c, AND / "mipmap-xxxhdpi/ic_launcher.png", 192)
print(sorted(p.name for p in OUT.iterdir()))
