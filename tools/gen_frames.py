"""Generate static/frames.css: 9-slice pixel-art borders (stepped corners) as inline SVG border-images.
Run with any python3. Edit FRAMES to change the look; app.css only decides layout and text."""
import pathlib
from urllib.parse import quote

OUT = pathlib.Path(__file__).resolve().parents[1] / "app/finapp/static/frames.css"
INK, GOLD, GOLD_LT, GOLD_DK = "#2A1B12", "#E0AC3A", "#FFE08A", "#8A5E10"
PARCH, PARCH_LT, PARCH_AGED = "#DCC48E", "#F4E8C8", "#C9AC6E"
STONE_BEZEL = "#555A6C"
SHADOW = "#1C1E27"


def build(edge, fill, hi=None, shadow=None, c=3, s=0, pressed=False):
    n = 2 * c + 2
    h = n
    th = n + s
    thr = max(1, c - 1)

    def body(x, y):
        yy = y - (s if pressed else 0)
        if not (0 <= x < n and 0 <= yy < h):
            return False
        cx, cy = min(x, n - 1 - x), min(yy, h - 1 - yy)
        return not (cx < c and cy < c and cx + cy < thr)

    def depth(x, y):
        yy = y - (s if pressed else 0)
        d, seen = 1, {(x, yy)}
        frontier = [(x, yy)]
        while True:
            nxt = []
            for px, py in frontier:
                for qx, qy in ((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)):
                    if not (0 <= qx < n and 0 <= qy < h) or not body(qx, qy + (s if pressed else 0)):
                        return d
                    if (qx, qy) not in seen:
                        seen.add((qx, qy)); nxt.append((qx, qy))
            frontier, d = nxt, d + 1
            if d > n:
                return d

    cells = {}
    for y in range(th):
        for x in range(n):
            if body(x, y):
                d = depth(x, y)
                cells[(x, y)] = edge if d == 1 else (hi if (d == 2 and hi) else fill)
            elif shadow and not pressed and s and any(body(x, y - k) for k in range(1, s + 1)):
                cells[(x, y)] = shadow
    rects = []
    for y in range(th):
        x = 0
        while x < n:
            col = cells.get((x, y))
            if not col:
                x += 1
                continue
            e = x
            while e < n and cells.get((e, y)) == col:
                e += 1
            rects.append(f'<rect x="{x}" y="{y}" width="{e - x}" height="1" fill="{col}"/>')
            x = e
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{n}" height="{th}" viewBox="0 0 {n} {th}" shape-rendering="crispEdges">'
           + "".join(rects) + "</svg>")
    return "url(\"data:image/svg+xml," + quote(svg, safe="/:=\" ") .replace('"', "%22").replace(" ", "%20") + "\")"


def rule(sel, u, c, s, center=True, **kw):
    img = build(c=c, s=s, **kw)
    top = u * c
    bot = u * (c + s)
    return (f"{sel}{{border-style:solid;border-color:transparent;border-width:{top}px {top}px {bot}px {top}px;"
            f"border-image:{img} {c} {c} {c + s} {c}{' fill' if center else ''} / {top}px {top}px {bot}px {top}px stretch}}\n")


FR = []   # (selector, U, c, s, center, kwargs)


def add(sel, u, c, s=0, center=True, **kw):
    FR.append((sel, u, c, s, center, kw))


# panels
add(".card,.panel", 3, 3, 1, False, edge=INK, hi=GOLD, fill=PARCH, shadow=SHADOW)
add(".card.aged", 3, 3, 1, False, edge=INK, hi=GOLD_DK, fill=PARCH_AGED, shadow=SHADOW)
add(".hero", 4, 3, 0, False, edge=INK, hi=GOLD, fill=STONE_BEZEL)
add(".notice", 2, 3, 1, edge="#6A4608", hi=GOLD_LT, fill="#F8E9B4", shadow="#3A2A10")
add(".error", 2, 3, 1, edge="#6E1A12", hi="#F27B6F", fill="#F9D6D1", shadow="#6E1A12")
add(".toast", 2, 2, 1, edge=INK, hi=GOLD, fill=PARCH_LT, shadow=SHADOW)
add(".speech", 2, 2, 0, edge=INK, fill="#FFFFFF")
add(".gallery figure", 2, 2, 0, edge=INK, hi=GOLD_DK, fill="#3E4357")
add("h2", 2, 2, 0, edge=INK, hi=GOLD, fill="#5B3A82")
# tiles (pouches)
add(".tile", 2, 3, 1, edge=INK, hi="#FFFFFF", fill="#F6ECCF", shadow="#6B5A40")
add(".tile:active", 2, 3, 1, edge=INK, hi="#FFFFFF", fill="#F6ECCF", pressed=True)
add('.tile[data-status="available"]', 2, 3, 1, edge="#1F5E36", hi="#F0FFF2", fill="#CFEBD4", shadow="#1F5E36")
add('.tile[data-status="available"]:active', 2, 3, 1, edge="#1F5E36", hi="#F0FFF2", fill="#CFEBD4", pressed=True)
add('.tile[data-status="overdrawn"]', 2, 3, 1, edge="#7E2018", hi="#FFF0EE", fill="#F6D2CC", shadow="#7E2018")
add('.tile[data-status="overdrawn"]:active', 2, 3, 1, edge="#7E2018", hi="#FFF0EE", fill="#F6D2CC", pressed=True)
# buttons
for sel, fillc, hic, shc in ((".btn", GOLD, GOLD_LT, "#5A3516"), (".btn.ghost,.opt", PARCH_LT, "#FFFFFF", "#5A3516"),
                             (".btn.danger", "#D8453B", "#F27B6F", "#5A1A14"), (".opt.on", "#47B26B", "#A5E8BD", "#1F5E36"),
                             (".opt.hint", "#E4F0FC", "#FFFFFF", "#2E69A8")):
    edge = {"#47B26B": "#1F5E36", "#E4F0FC": "#2E69A8"}.get(fillc, INK)
    act = ",".join(f"{p}:active" for p in sel.split(","))
    add(sel, 2, 2, 1, edge=edge, hi=hic, fill=fillc, shadow=shc)
    add(act, 2, 2, 1, edge=edge, hi=hic, fill=fillc, pressed=True)
# tabs and nav
add(".tab", 2, 2, 1, edge=INK, hi="#9A9DAE", fill="#6A6E80", shadow=SHADOW)
add(".tab.on", 2, 2, 1, edge=INK, hi=GOLD_LT, fill=PARCH_LT, shadow=SHADOW)
add(".topnav a[aria-current],.tabbar a[aria-current]", 2, 2, 1, edge=INK, hi=GOLD_LT, fill=PARCH_LT, shadow=SHADOW)
add(".pick span", 2, 2, 0, edge="#6A5236", hi="#DCCDA8", fill="#FFFDF5")
add(".pick input:checked+span", 2, 2, 1, edge="#24508A", hi="#BBD7F2", fill="#DCEBFA", shadow="#24508A")
# fields
add("input[type=text],input[type=password],input[type=number],input[type=time],input[type=month],select,textarea", 2, 2, 0,
    edge="#6A5236", hi="#E3D6B2", fill="#FFFDF5")
add("input[type=text]:focus,input[type=password]:focus,input[type=number]:focus,input[type=time]:focus,input[type=month]:focus,select:focus,textarea:focus",
    2, 2, 0, edge="#24508A", hi="#BBD7F2", fill="#FFFFFF")
add(".meter", 2, 2, 0, edge=INK, hi="#C9B98F", fill="#FFFDF5")
# chips and matrix cells
for name, edge, fillc in (("green", "#1F5E36", "#47B26B"), ("red", "#6E1A12", "#D8453B"), ("blue", "#24508A", "#4A8FE0"),
                          ("gold", "#6A4608", GOLD), ("line", "#8A7A5A", "#D9CFB5"), ("purple", "#3C2457", "#8E5CC4")):
    sel = {"green": ".chip.green,.cell.complete", "red": ".chip.red,.cell.missing", "blue": ".chip.blue,.cell.csv",
           "gold": ".chip.gold,.cell.review", "line": ".chip.line,.cell.future,.cell.na", "purple": ".cell.soon"}[name]
    add(sel, 2, 1, 0, edge=edge, fill=fillc)

import random


def uri(svg):
    return "url(\"data:image/svg+xml," + quote(svg, safe="/:=\" ").replace('"', "%22").replace(" ", "%20") + "\")"


def wall(base, var, seed, hi, mortar, W=432, bh=48):
    """A tile of ashlar stone: staggered blocks, bevelled, with a few worn specks."""
    rnd = random.Random(seed)
    cuts = [[0, 130, 290], [60, 200, 350], [30, 170, 330], [100, 240, 390]]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    out = []

    def hexc(c):
        return "#%02X%02X%02X" % tuple(max(0, min(255, v)) for v in c)
    for r, cs in enumerate(cuts):
        y0 = r * bh
        for i, x0 in enumerate(cs):
            x1 = cs[(i + 1) % 3] if i < 2 else cs[0] + W
            segs = [(x0, min(x1, W))] + ([(0, x1 - W)] if x1 > W else [])
            d = rnd.randint(-var, var)
            col = hexc([v + d for v in b])
            for a, e in segs:
                if e <= a:
                    continue
                out.append(f'<rect x="{a}" y="{y0}" width="{e - a}" height="{bh}" fill="{col}"/>')
                out.append(f'<rect x="{a}" y="{y0}" width="{e - a}" height="3" fill="{hi}"/>')
                out.append(f'<rect x="{a}" y="{y0}" width="3" height="{bh}" fill="{hi}"/>')
                out.append(f'<rect x="{a}" y="{y0 + bh - 3}" width="{e - a}" height="3" fill="{mortar}"/>')
                out.append(f'<rect x="{e - 3}" y="{y0}" width="3" height="{bh}" fill="{mortar}"/>')
                for _ in range(rnd.randint(2, 5)):
                    sx, sy = rnd.randint(a + 6, max(a + 7, e - 12)), rnd.randint(y0 + 6, y0 + bh - 12)
                    out.append(f'<rect x="{sx}" y="{sy}" width="3" height="3" fill="{rnd.choice([mortar, hi])}" fill-opacity=".35"/>')
    return uri(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{bh * 4}" viewBox="0 0 {W} {bh * 4}" shape-rendering="crispEdges">' + "".join(out) + "</svg>")


def specks(seed=7, n=46, T=132):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        x, y = rnd.randrange(0, T - 6, 3), rnd.randrange(0, T - 6, 3)
        w, h = rnd.choice([(3, 3), (3, 3), (6, 3), (3, 6)])
        col, op = rnd.choice([("#8A6A2C", ".13"), ("#6B4A18", ".10"), ("#FFFFFF", ".22"), ("#A5792E", ".12")])
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{col}" fill-opacity="{op}"/>')
    return uri(f'<svg xmlns="http://www.w3.org/2000/svg" width="{T}" height="{T}" viewBox="0 0 {T} {T}" shape-rendering="crispEdges">' + "".join(out) + "</svg>")



def carve(rows, pal, cell=3):
    """ASCII relief -> tiling SVG (each character is one 3px pixel)."""
    h, w = len(rows), len(rows[0])
    out = []
    for y, row in enumerate(rows):
        x = 0
        while x < w:
            ch = row[x]
            if ch == ".":
                x += 1
                continue
            e = x
            while e < w and row[e] == ch:
                e += 1
            out.append(f'<rect x="{x * cell}" y="{y * cell}" width="{(e - x) * cell}" height="{cell}" fill="{pal[ch]}"/>')
            x = e
    return uri(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w * cell}" height="{h * cell}" viewBox="0 0 {w * cell} {h * cell}" shape-rendering="crispEdges">' + "".join(out) + "</svg>")


CARVE_PAL = {"L": "#656B84", "M": "#4C5167", "D": "#1D1F29", "S": "#2A2D3B"}
ROYAL_PAL = {"L": "#8F6BBF", "M": "#6F4B9E", "D": "#26123F", "S": "#33194F"}
FRIEZE = ["LLLL", ".L..", "LMD.", ".D..", "DDDD"]
FRIEZE_UP = FRIEZE[::-1]
PILLAR = ["DLMMSLMD"]

TEX = f"""
body{{background-color:#555A6A;background-image:{wall("#5A5E6E", 7, 3, "#6E7384", "#33363F")}}}
.top{{background-color:#5B3A82;background-image:{carve(FRIEZE,ROYAL_PAL)},linear-gradient(#69459A,#4D2F72);background-repeat:repeat-x,no-repeat;background-position:left bottom,0 0;background-size:auto,100% 100%}}
.tabbar{{background-color:#33374A;background-image:{carve(FRIEZE_UP,CARVE_PAL)},linear-gradient(#2D3142,#3B4056);background-repeat:repeat-x,no-repeat;background-position:left top,0 0;background-size:auto,100% 100%}}
.hero{{background-color:#393E52;background-image:{carve(FRIEZE,CARVE_PAL)},radial-gradient(ellipse at 50% 70%,#4A5068 0%,#393E52 72%);background-repeat:repeat-x,no-repeat;background-position:left top,0 0;background-size:auto,100% 100%;background-clip:padding-box;background-origin:padding-box}}
@media (min-width:601px){{.hero{{background-image:{carve(FRIEZE,CARVE_PAL)},{carve(PILLAR,CARVE_PAL)},{carve(PILLAR,CARVE_PAL)},radial-gradient(ellipse at 50% 70%,#4A5068 0%,#393E52 72%);background-repeat:repeat-x,repeat-y,repeat-y,no-repeat;background-position:left top,left 15px,right 15px,0 0;background-size:auto,auto,auto,100% 100%}}}}
.card,.panel{{background-color:{PARCH};background-image:{specks()},radial-gradient(ellipse at 50% 35%,#F3E6C2 0%,#EBDAAA 55%,{PARCH} 100%);background-clip:padding-box}}
.card.aged{{background-color:{PARCH_AGED};background-image:{specks(11)},radial-gradient(ellipse at 50% 35%,#E3CD96 0%,#D8BF86 55%,{PARCH_AGED} 100%);background-clip:padding-box}}
.card.rv,.tile,.notice,.speech{{background-clip:padding-box}}
"""

CSS = "/* generated by tools/gen_frames.py: pixel-stepped 9-slice borders */\n" + "".join(rule(sel, u, c, s, center, **kw) for sel, u, c, s, center, kw in FR) + TEX
OUT.write_text(CSS)
print(len(FR), "frames,", len(CSS) // 1024, "KB")
