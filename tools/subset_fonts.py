"""One-off: subset the typefaces used by the app and write woff2 files. Run with system python3 (fontTools)."""
import pathlib
from fontTools import subset

OUT = pathlib.Path(__file__).resolve().parents[1] / "app/finapp/static/fonts"
OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.woff2"):
    old.unlink()
UNI = set(range(0x20, 0x7F)) | set(range(0xA0, 0x100)) | {0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026, 0x2192, 0x2264, 0x2265, 0x00D7, 0x2212}
SRC = {"fraktur-bold": "/usr/share/fonts/opentype/mathjax/MathJax_Fraktur-Bold.otf",      # Apache-2.0
       "pixel-console": "/usr/share/fonts/xscreensaver/clacon.ttf",}  # Classic Console, MIT
for name, path in SRC.items():
    o = subset.Options(); o.flavor = "woff2"; o.layout_features = ["*"]; o.notdef_outline = True
    f = subset.load_font(path, o); s = subset.Subsetter(o); s.populate(unicodes=UNI); s.subset(f)
    subset.save_font(f, str(OUT / f"{name}.woff2"), o)
    print(f"{name}.woff2  {(OUT / f'{name}.woff2').stat().st_size // 1024} KB")
