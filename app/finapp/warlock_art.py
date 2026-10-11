"""The Warlock, a hooded arcane accountant with a glowing staff, as pixel art. Built from a few shapes so it stays symmetric."""
W, H = 30, 31
CX, STAFF_X = 12, 25
PAL = {"idle": {"K": "#14151C", "H": "#2B2F73", "R": "#4047A8", "r": "#6670D0", "F": "#0B0B14", "E": "#7CF5E6", "G": "#E0AC3A", "g": "#FFE08A",
                "S": "#7A4A24", "s": "#A9703C", "O": "#6FD6D0", "o": "#E8FFFD", "Q": "#FFE873"}}
PAL["casting"] = dict(PAL["idle"], E="#FFFFFF", O="#FF9BEA", o="#FFFFFF", r="#8A94E6")


def _cells(mood):
    c = {}

    def put(x, y, ch):
        if 0 <= x < W and 0 <= y < H:
            c[(x, y)] = ch

    def centred(y, pattern):
        n = len(pattern)
        for i, ch in enumerate(pattern):
            if ch != ".":
                put(CX - n // 2 + i, y, ch)
    hood = ["KK", "KHHK", "KHHHHK", "KHHHHHHK", "KHHHHHHHHK", "KHHKKKKKKHHK", "KHKFFFFFFKHK", "KHKFEFFEFKHK", "KHKFFFFFFKHK", "KHHKFFFFKHHK", "KHHHKKKKKKHHHK"]
    for i, row in enumerate(hood):
        centred(2 + i, row)
    robe = ["KRRRRGGGGRRRRK", "KRRRRRRGGRRRRRRK", "KRrRRRRGGRRRRrRK", "KRRrRRRRGGRRRRrRRK", "KRRRRRRRGGRRRRRRRK", "KRRrRRRRRGGRRRRRrRRK",
            "K" + "G" * 18 + "K", "KRRRrRRRRGGRRRRRrRRRRK", "KRRRRRRRRGGRRRRRRRRRRK", "KRrRRRRRRGGRRRRRRRrRRK", "KRRRRRRrRRGGRRRRRRRRRK",
            "KRRRRRRRRRGGRRRRRrRRRK", "KRRrRRRRRRGGRRRRRRRRRK", "KHHHHHHHHHHHHHHHHHHHHK", "KHHKHHKHHHHHHHKHHKHHK", ".KKK..KKKKKKKK..KKK.."]
    for i, row in enumerate(robe):
        centred(13 + i, row)
    for y in range(4, H - 1):                                  # the staff, held at the right
        put(STAFF_X, y, "S" if y % 5 else "s")
        put(STAFF_X - 1, y, "K")
        put(STAFF_X + 1, y, "K")
    for dx, dy in ((0, 0), (-1, 1), (0, 1), (1, 1), (-1, 2), (0, 2), (1, 2), (0, 3)):   # the orb
        put(STAFF_X + dx, dy + 0, "O")
    put(STAFF_X, 1, "o"); put(STAFF_X - 1, 1, "O")
    for x in range(STAFF_X - 2, STAFF_X + 3):
        put(x, 4, "G")
    for x in range(CX + 7, STAFF_X - 1):                       # the sleeve reaching the staff
        put(x, 19, "R"); put(x, 18, "K"); put(x, 20, "K")
    put(STAFF_X - 1, 19, "F")
    if mood == "casting":
        for x, y in ((STAFF_X - 4, 1), (STAFF_X + 4, 2), (STAFF_X - 3, 5), (STAFF_X + 3, 0), (CX - 7, 6), (CX + 7, 5), (CX - 8, 11)):
            put(x, y, "Q")
        put(CX - 2, 9, "E"); put(CX + 1, 9, "E")
    return c


def svg(mood="idle", scale=5, cls=""):
    pal = PAL[mood]
    rects = "".join(f'<rect x="{x}" y="{y}" width="1" height="1" fill="{pal[ch]}"/>' for (x, y), ch in sorted(_cells(mood).items(), key=lambda kv: (kv[0][1], kv[0][0])))
    return (f'<svg class="alberich {cls}" viewBox="0 0 {W} {H}" width="{W * scale}" height="{H * scale}" shape-rendering="crispEdges" role="img" aria-label="The Warlock">{rects}</svg>')
