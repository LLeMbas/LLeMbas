"""The pixel artwork of the built-in themes, drawn from geometry on a grid.

Used by `build_artwork.py`; the application never imports it.

Every drawing is a dict ``{(x, y): role}`` on a small grid. Nothing in here
knows a colour: a role is a name (``leaf``, ``line``, ``door``...) that a theme
gives a colour to as ``--art-<role>`` in tokens.css, and the PNG icons, which
cannot read CSS, get one from a palette passed in. So one drawing serves both
themes and every custom theme built on them.

Drawn rather than hand-placed because three of these exist and will be tuned:
a hand-edited grid of a thousand cells is a file nobody changes twice. The
shapes are a few lines each -- a pointed leaf is two overlapping circles, an
arch is a half circle on two posts -- and the grid is what makes them read as
pixel art at any size.

Middle-earth lives here and in the empty states, never in the functional UI.
"""

from __future__ import annotations

import math

Pixels = dict[tuple[int, int], str]


def runs(pixels: Pixels, width: int, height: int):
    """Horizontal runs of one role, as (x, y, length, role).

    One `<rect>` per run instead of per cell keeps the Doors of Durin at a few
    hundred elements rather than a few thousand.
    """
    for y in range(height):
        x = 0
        while x < width:
            role = pixels.get((x, y))
            if role is None:
                x += 1
                continue
            start = x
            while x < width and pixels.get((x, y)) == role:
                x += 1
            yield start, y, x - start, role


def svg_rects(pixels: Pixels, width: int, height: int, *, colour=None, cls: str = "px-") -> str:
    """The cells as `<rect>`s: classed by role, or filled from `colour(role)`."""
    out = []
    for x, y, length, role in runs(pixels, width, height):
        if colour is None:
            paint = f'class="{cls}{role}"'
        else:
            fill = colour(role)
            if fill is None:
                continue
            paint = f'fill="{fill}"'
        out.append(f'<rect x="{x}" y="{y}" width="{length}" height="1" {paint}/>')
    return "".join(out)


# --- The mallorn leaf, 16x16: the mark ---------------------------------------------
LEAF_SIZE = 16


def leaf() -> Pixels:
    """A pointed leaf on the diagonal, its midrib lit, a short stem.

    The blade is the lens two circles make where they overlap -- the shape a
    mallorn leaf has, and the only one that stays a leaf at 16 cells.
    """
    a, b = (3.0, 12.5), (13.5, 2.0)  # stem end of the blade -> its tip
    ax, ay = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ax, ay)
    ux, uy = ax / length, ay / length
    px, py = -uy, ux
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    half, width = length / 2, 3.9
    radius = (half**2 + width**2) / (2 * width)  # a circle through both ends
    offset = radius - width
    c1 = (mx + px * offset, my + py * offset)
    c2 = (mx - px * offset, my - py * offset)

    cells: Pixels = {}
    for y in range(LEAF_SIZE):
        for x in range(LEAF_SIZE):
            cx, cy = x + 0.5, y + 0.5
            if math.hypot(cx - c1[0], cy - c1[1]) > radius:
                continue
            if math.hypot(cx - c2[0], cy - c2[1]) > radius:
                continue
            along = (cx - a[0]) * ux + (cy - a[1]) * uy
            across = (cx - a[0]) * px + (cy - a[1]) * py
            role = "leaf"
            if across > 0.9:
                role = "leaf-dark"
            if abs(across) < 0.75 and 0.5 < along < length - 1.2:
                role = "vein"
            if across < -2.2:
                role = "leaf-light"
            cells[(x, y)] = role
    for x, y in ((2, 13), (1, 14), (0, 15)):
        cells[(x, y)] = "leaf-dark"
    return cells


# --- Moria: the Doors of Durin, 48x56 ----------------------------------------------
DURIN_SIZE = (48, 56)


def durin() -> Pixels:
    """The west-gate in ithildin: a double arch on its posts, two trees, the
    crown and seven stars, and the star of the House of Fëanor between them."""
    width, height = DURIN_SIZE
    cells: Pixels = {}

    def put(x: float, y: float, role: str = "line") -> None:
        if 0 <= x < width and 0 <= y < height:
            cells[(int(x), int(y))] = role

    cx, top = 23.5, 22
    for radius, left, right in ((19.5, 4, 43), (16.5, 7, 40)):
        for tenth in range(0, 1801):
            t = math.radians(tenth / 10)
            put(round(cx + radius * math.cos(math.pi + t)), round(top - radius * math.sin(t)))
        for y in range(top, 52):
            put(left, y)
            put(right, y)
    for x in range(1, 47):
        put(x, 52)
    for x in range(2, 46, 2):
        put(x, 54, "faint")

    for trunk in (11, 36):
        for y in range(30, 52):
            put(trunk, y)
        for y in range(36, 52, 4):
            put(trunk - 1, y, "faint")
            put(trunk + 1, y + 2, "faint")
        for k in range(1, 6):
            role = "line" if k < 3 else "faint"
            put(trunk - k, 30 - k, role)
            put(trunk + k, 30 - k, role)
        for k in range(1, 5):
            put(trunk, 30 - k)
        for y in range(18, 31):
            for x in range(trunk - 7, trunk + 8):
                d = math.hypot((x - trunk) / 1.15, y - 23.5)
                ragged = (x * 13 + y * 7) % 5
                if d < 5.2 - (0.9 if ragged == 0 else 0) and (x + y) % 2 == 0:
                    put(x, y, "leaf")
                elif d < 4.2 and ragged in (1, 3):
                    put(x, y, "leaf-dark")

    sx, sy = 23, 35
    put(sx, sy, "star")
    for k in range(1, 5):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            put(sx + dx * k, sy + dy * k, "star" if k < 3 else "line")
    for k in range(1, 3):
        for dx, dy in ((1, 1), (-1, -1), (1, -1), (-1, 1)):
            put(sx + dx * k, sy + dy * k)
    for dx in (-2, 0, 2):
        put(sx + dx, 25)
    for dx in (-2, -1, 0, 1, 2):
        put(sx + dx, 26)
    for i in range(7):
        t = math.radians(205 + i * 21.7)
        put(round(sx + 9 * math.cos(t)), round(29 + 9 * math.sin(t)), "star")
    # The inscription: a dotted band along the inner arch.
    for degree in range(12, 169, 9):
        t = math.radians(degree)
        put(round(cx + 13.5 * math.cos(math.pi + t)), round(top - 13.5 * math.sin(t)), "faint")
    return cells


# --- Shire: a round door in a hill, 64x40 -------------------------------------------
SMIAL_SIZE = (64, 40)


def smial() -> Pixels:
    """A round green door with the knob in the middle, two round windows, a
    path, a few flowers, under a dithered sky."""
    width, height = SMIAL_SIZE
    cells: Pixels = {}

    def hill_top(x: int) -> float:
        return 15 + ((x - 32) / 32) ** 2 * 13

    for y in range(height):
        for x in range(width):
            crest = hill_top(x)
            if y >= crest:
                depth = y - crest
                if depth < 1:
                    role = "grass-light"
                elif depth < 3:
                    role = "grass-light" if (x + y) % 2 == 0 else "grass"
                elif depth > 6 and (x * 7 + y * 3) % 11 == 0:
                    role = "grass-dark"
                else:
                    role = "grass"
                cells[(x, y)] = role
            else:
                band = y / crest  # dithered, thickening towards the horizon
                if (band > 0.55 and (x + y) % 2 == 0) or band > 0.8:
                    cells[(x, y)] = "sky"

    for y in range(1, 11):
        for x in range(47, 58):
            if (x + 0.5 - 52.5) ** 2 + (y + 0.5 - 6) ** 2 <= 12.5:
                cells[(x, y)] = "sun"

    door, door_radius = (32, 29), 8.4
    for y in range(height):
        for x in range(width):
            d = math.hypot(x + 0.5 - door[0], y + 0.5 - door[1])
            if d <= door_radius and y < 38:
                if d > door_radius - 1.3:
                    cells[(x, y)] = "frame"
                else:
                    cells[(x, y)] = "door-dark" if (x - 24) % 4 == 0 else "door"
    for x, y in ((31, 28), (32, 28), (31, 29), (32, 29)):
        cells[(x, y)] = "brass"

    for wx in (16, 48):
        for y in range(height):
            for x in range(width):
                d = math.hypot(x + 0.5 - wx, y + 0.5 - 28)
                if d <= 3.6:
                    pane = x == wx or y == 28
                    cells[(x, y)] = "frame" if d > 2.5 or pane else "window"

    for y in range(38, 40):
        for x in range(27 - (y - 38) * 2, 38 + (y - 38) * 2):
            cells[(x, y)] = "path"
    for x, y, role in ((21, 35, "flower-a"), (23, 36, "flower-b"), (41, 35, "flower-b"),
                       (43, 36, "flower-a"), (19, 36, "flower-a")):
        cells[(x, y)] = role
    return cells


# Every role a drawing uses. tokens.css must give each a colour in the theme
# that shows the drawing; tests/test_artwork.py holds the two lists together.
ROLES = {
    "leaf": ("leaf", "leaf-dark", "leaf-light", "vein"),
    "durin": ("line", "faint", "star", "leaf", "leaf-dark"),
    "smial": ("grass", "grass-light", "grass-dark", "sky", "sun", "frame", "door",
              "door-dark", "brass", "window", "path", "flower-a", "flower-b"),
}
