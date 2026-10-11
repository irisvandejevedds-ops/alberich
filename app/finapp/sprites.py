"""Alberich, dwarf lord of the Nibelungs, as pixel art.

The base bust is hand-drawn art (25x32). Moods are small overlays on top of it (eyes, brows, mouth, effects) plus palette tints.
Overlay coordinates are base-sprite (x, y, letter); the canvas adds margin for sparkles, flames and sweat drops.
Letters: A beard/hair  B robe  C crown and fur  D skin  E shadow  F robe light  G skin shade  H red  I brow  J eye white.
"""
from io import BytesIO

BW, BH = 25, 32
OX, OY = 2, 4
W, H = BW + 2 * OX, BH + OY

BASE = [
    "..........C......C.......",
    "........C..C.CC.C..C.....",
    ".........C.CCHHCC.C......",
    ".........CACHHHHCAC......",
    "......C..CACCHHCCAC..C...",
    ".......CACCCCCCCCCCAC....",
    ".......CCDDDDDDDDDDCC....",
    "...GGG.CDDIIDDDDIIDDC.GGG",
    "...DDGGCDIFBIDDIFBIDCGGDD",
    "...DDDGGDJBBJDDJBBJDGGDDD",
    "....DDDGDDDDGDDGDDDDGDDD.",
    ".....DDAADDGDDDDGDDAADD..",
    "......AAAEEDGDDGDEEAAA...",
    ".....AAAEEAADGGDAAEEAAA..",
    ".....AEEEAAAAAAAAAAEEEA..",
    "......AEAAAAAHHAAAAAEA...",
    "......EAAAEAADDAAEAAAE...",
    ".....CCAAEEEAAAAEEEAACC..",
    ".....CCCEEEAAAAAAEEECCC..",
    "....BBBCCEAAAEEAAAECCBBB.",
    "....BBBBCCAAEEEEAACCBBBB.",
    "....BBBFFBCAAEEAACBFFBBB.",
    "...BBBBFFBBAAEEAABBFFBBBB",
    "...BBBBFCBBFCAAFFBBBFBBBB",
    "...BBGGCFBBFFBBFFBBFFGGBB",
    "...BBDDGFBBFCBBFFBBFGDDBB",
    "....BCDDFFBBFBBFBBFFDDBB.",
    "....C.BBBFFBCBBFBFFBBB...",
    ".CCCA..AAFFBBEEBBFFAA..A.",
    "CHCCAAAAABBAAEEAABBAAAAA.",
    "CCHC.AAAAAAAEEEEAAAAAAA..",
    ".CC...EEEEEEEEEEEEEEEE..."
]

EYE_L, EYE_R = (9, 8), (15, 8)           # top-left cell of each 4x2 eye
MOUTH = (13, 15)


def mirror(cells):
    return [(27 - x, y, c) for x, y, c in cells]


def both(cells):
    return list(cells) + mirror(cells)


HAPPY_EYES = both([(9, 8, "D"), (10, 8, "I"), (11, 8, "I"), (12, 8, "D"), (9, 9, "I"), (10, 9, "D"), (11, 9, "D"), (12, 9, "I")])
LIDDED_EYES = both([(9, 8, "I"), (10, 8, "I"), (11, 8, "I"), (12, 8, "I")])
ANGRY_BROWS = both([(10, 7, "D"), (11, 7, "D"), (9, 6, "E"), (10, 6, "E"), (11, 7, "E"), (12, 7, "E")])
SICK_BROWS = both([(10, 7, "D"), (11, 7, "D"), (9, 7, "I"), (10, 7, "I"), (12, 6, "I"), (11, 6, "I")])

OVERLAYS = {
    "neutral": [],
    "annoyed": LIDDED_EYES + ANGRY_BROWS + [(13, 16, "A"), (14, 16, "A")]
               + [(22, 5, "R"), (23, 5, "R"), (22, 4, "R"), (23, 3, "R"), (21, 4, "R")],
    "happy": HAPPY_EYES + [(12, 15, "H"), (13, 15, "H"), (14, 15, "H"), (15, 15, "H"), (13, 16, "H"), (14, 16, "H")]
             + [(9, 10, "q"), (10, 10, "q"), (18, 10, "q"), (19, 10, "q")]
             + [(0, 10, "Q"), (1, 9, "Q"), (1, 11, "Q"), (2, 10, "Q"), (-1, 3, "Q"), (0, 4, "Q"), (-2, 4, "Q"), (0, 5, "Q"),
                (25, 9, "Q"), (26, 8, "Q"), (26, 10, "Q"), (27, 9, "Q"), (26, 2, "Q"), (27, 3, "Q"), (25, 3, "Q"), (26, 4, "Q")],
    "sick": LIDDED_EYES + SICK_BROWS + [(12, 15, "H"), (13, 16, "H"), (14, 15, "H"), (15, 16, "H"), (13, 15, "A"), (14, 16, "A")]
            + [(1, 11, "T"), (1, 12, "T"), (2, 13, "T"), (-1, 6, "Z"), (-2, 7, "Z"), (0, 7, "Z"), (26, 7, "Z"), (27, 8, "Z"), (25, 8, "Z")],
    "evil": [(10, 8, "R"), (11, 8, "R"), (16, 8, "R"), (17, 8, "R"), (10, 9, "R"), (11, 9, "R"), (16, 9, "R"), (17, 9, "R"),
             (9, 8, "E"), (12, 8, "E"), (15, 8, "E"), (18, 8, "E")] + ANGRY_BROWS
            + [(11, 15, "H"), (12, 15, "H"), (13, 15, "H"), (14, 15, "H"), (15, 15, "H"), (16, 15, "H"), (12, 16, "J"), (15, 16, "J"),
               (13, 16, "A"), (14, 16, "A")]
            + [(0, 5, "V"), (-1, 7, "V"), (0, 9, "V"), (2, 4, "V"), (3, 7, "V"), (-1, 11, "V"), (-2, 4, "V"),
               (25, 5, "V"), (26, 7, "V"), (25, 9, "V"), (23, 4, "V"), (22, 7, "V"), (26, 11, "V"), (27, 4, "V")]
            + [(9, -1, "O"), (12, -1, "O"), (15, -1, "O"), (10, -2, "O"), (13, -2, "O"), (16, -2, "O"), (12, -3, "O"), (14, -3, "O")],
}
MOODS = ["neutral", "happy", "sick", "evil", "annoyed"]

_P = {"A": "#41293B", "B": "#6F4586", "C": "#CEDAF9", "D": "#E7C59F", "E": "#222033", "F": "#A480B8", "G": "#D0A26F", "H": "#CA5F66",
      "I": "#603B33", "J": "#FFFFFF", "R": "#FF3B3B", "q": "#F29B8B", "T": "#7CC4F0", "V": "#9B4DFF", "O": "#FF8A1F", "Q": "#FFE873", "Z": "#8FD14F"}
PALETTES = {m: dict(_P) for m in MOODS}
PALETTES["happy"].update({"D": "#F0CBA6", "G": "#DCAE7B"})
PALETTES["annoyed"].update({"D": "#E9B596", "G": "#D08F6C", "H": "#C24A55"})
PALETTES["sick"].update({"D": "#B9D29B", "G": "#97B57C", "B": "#566377", "F": "#8E9BAA", "H": "#7E9A6C", "C": "#C6D6D8", "A": "#34323F"})
PALETTES["evil"].update({"D": "#DE8A6C", "G": "#BF6048", "B": "#4B1F66", "F": "#7A3FA0", "H": "#FF3B5C", "A": "#2A1626", "C": "#E8D58A",
                         "I": "#1A1020"})


def grid(mood: str):
    cells = {}
    for y, row in enumerate(BASE):
        assert len(row) == BW, (y, len(row))
        for x, ch in enumerate(row):
            if ch != ".":
                cells[(x + OX, y + OY)] = ch
    for (x, y, c) in OVERLAYS[mood]:
        cx, cy = x + OX, y + OY
        if 0 <= cx < W and 0 <= cy < H:
            cells[(cx, cy)] = c
    return cells


def svg(mood: str, scale: int = 8, label: str = "", cls: str = "") -> str:
    cells = grid(mood)
    rects = "".join(f'<rect x="{x}" y="{y}" width="1" height="1" fill="{PALETTES[mood][c]}"/>'
                    for (y, x), c in sorted(((y, x), c) for (x, y), c in cells.items()))
    return (f'<svg class="alberich {cls}" viewBox="0 0 {W} {H}" width="{W * scale}" height="{H * scale}" '
            f'shape-rendering="crispEdges" role="img" aria-label="{label or "Alberich, " + mood}">{rects}</svg>')


def png(mood: str, px: int = 192, bg=None, mono: bool = False) -> bytes:
    from PIL import Image, ImageDraw
    cells = grid(mood)
    cell = max(1, (px * 9 // 10) // max(W, H))
    iw, ih = W * cell, H * cell
    img = Image.new("RGBA", (px, px), bg or (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox, oy = (px - iw) // 2, (px - ih) // 2
    for (x, y), c in cells.items():
        col = (255, 255, 255, 255) if mono else PALETTES[mood][c]
        d.rectangle([ox + x * cell, oy + y * cell, ox + (x + 1) * cell - 1, oy + (y + 1) * cell - 1], fill=col)
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
