#!/usr/bin/env python3
"""Generate every LLeMbas brand asset from one source of truth.

Why a generator rather than hand-written files: the pixel mallorn leaf is the
mark in the favicon, the logo, the lockup and every PWA icon, and the Doors of
Durin are both the empty chat's picture and the README banner's. Keeping the
drawings in one place (scripts/pixel_art.py) and the palette in one place (the
constants below, copied from the Moria and Shire blocks of tokens.css) is the
only way those stay identical as the look is tuned.

The look is the application's own: "Middle-earth on a terminal". Dark
stone, JetBrains Mono, uppercase tracked labels in brackets, a dotted rule, a
prompt caret, ithildin cyan and mallorn green, square corners and pixel art on
a grid. Every pixel drawing is emitted with `shape-rendering="crispEdges"` and,
where the size is known, on whole device pixels.

Why the type is outlines and not <text>: a README banner on GitHub or Gitea
cannot load a webfont, so <text> would render in whatever monospace the viewer
happens to have. Outlines look the same everywhere. Letterforms come from the
vendored JetBrains Mono (src/lembas/web/static/vendor, SIL OFL 1.1) -- a
variable font, instanced here at the weight each line needs; only the glyphs
actually used are drawn, as static paths, and no font binary is redistributed.

This is a design-time tool. The application never imports it, and the generated
files are committed. Re-run it only when the artwork itself changes:

    pip install fonttools brotli cairosvg
    python scripts/build_artwork.py
    python scripts/build_artwork.py --cli-banner PATH   # the LLeMbas CLI's banner

brotli is what lets fontTools read a woff2. cairosvg is needed only for the PWA
icons, which have to be PNG: an installed web app's icon is drawn by the
operating system's launcher, and neither Android's adaptive-icon masking nor
iOS's home screen will take an SVG. The rasterisation happens here, once, and
the PNGs are committed like everything else -- the running application still
has no build step and no rasteriser.

`--cli-banner` writes the matching banner for the LLeMbas CLI (a separate
repository) to the path given and nothing else: it is generated here so the two
projects share one wordmark and one palette, and committed over there.
"""

from __future__ import annotations

import argparse
import sys
from functools import cache
from pathlib import Path

try:
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
except ImportError:  # pragma: no cover - design-time tool
    sys.exit("fontTools is required for this script: pip install fonttools brotli")

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
STATIC_IMG = ROOT / "src" / "lembas" / "web" / "static" / "img"
TEMPLATES = ROOT / "src" / "lembas" / "web" / "templates"
FONT = ROOT / "src" / "lembas" / "web" / "static" / "vendor" / "jetbrains-mono-latin.woff2"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pixel_art  # noqa: E402 - beside this script, not an installed module

# assets/ holds the design masters; the application serves its own copies from
# static/. These are the few the running app actually needs.
SERVED_BY_APP = (
    "favicon.svg",
    "logo-mark.svg",
    "banner.svg",
    "icon-192.png",
    "icon-512.png",
    "icon-maskable-512.png",
    "apple-touch-icon-180.png",
    "badge-72.png",
)

WORDMARK = "LLeMbas"
TAGLINE = "Waybread for the long road of thought"
# The capitals of LLeMbas spell LLM. Those three glyphs carry the brand colour.
ACCENT_GLYPHS = frozenset({0, 1, 3})

# --- Palette -----------------------------------------------------------------
# Copied from tokens.css, Moria (the default, dark) -- the exact values, so the
# README and the application are one colour. Change them there first.
BG = "#0B0D10"           # --bg
BG_SUNKEN = "#07090B"    # --bg-sunken
SURFACE = "#11151A"      # --surface
SURFACE_RAISED = "#171C22"  # --surface-raised
BORDER = "#26303A"       # --border
BORDER_STRONG = "#5C6976"  # --border-strong
INK = "#E2E8EC"          # --ink
INK_MUTED = "#A6B2BC"    # --ink-muted
INK_FAINT = "#808D98"    # --ink-faint
ACCENT = "#8FD8E8"       # --accent, ithildin
LEAF = "#7EE081"         # --leaf, mallorn: the brand
# The pixel artwork's roles (--art-* in Moria).
ART = {
    "leaf": "#7EE081", "leaf-dark": "#4FAE5A", "leaf-light": "#B6F2A6",
    "vein": "#D8FFD0", "line": "#8FD8E8", "faint": "#4F8794", "star": "#EAFCFF",
}
ART_GLOW = "#8FD8E8"     # --art-glow is this at 0.55

# Shire (light): only what standalone type needs to stay legible when a
# document is opened on a light page.
SHIRE_INK = "#1F2418"    # --ink
SHIRE_LEAF = "#3D6B1C"   # --leaf

# The edge the bare leaf carries, so it reads on a light browser tab as well as
# a dark one: the stone the application draws it on, as a one-cell outline.
OUTLINE = BG

HEADER = '<svg xmlns="http://www.w3.org/2000/svg"'


# --- Type --------------------------------------------------------------------
@cache
def _font(weight: int) -> TTFont:
    """JetBrains Mono at one weight: the variable font, instanced."""
    if not FONT.exists():
        sys.exit(f"font not found: {FONT} (run scripts/fetch_vendor.py)")
    try:
        font = TTFont(FONT)
    except ImportError:  # pragma: no cover - design-time tool
        sys.exit("brotli is required to read a woff2: pip install brotli")
    return instancer.instantiateVariableFont(font, {"wght": weight})


class TextRun:
    """A string converted to SVG outlines, positioned with the baseline at y=0.

    `size` is the em in pixels (a CSS font-size). `tracking` is extra space
    after every glyph, in ems -- the letter-spacing of the uppercase labels.
    """

    def __init__(self, text: str, size: float, *, weight: int = 400, tracking: float = 0.0):
        font = _font(weight)
        cmap = font.getBestCmap()
        glyph_set = font.getGlyphSet()
        hmtx = font["hmtx"]
        scale = size / font["head"].unitsPerEm
        self.cap = font["OS/2"].sCapHeight * scale
        self.size = size

        self.glyphs: list[dict] = []
        pen_x = 0.0
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")

        for index, char in enumerate(text):
            name = cmap.get(ord(char))
            if name is None:
                sys.exit(f"{FONT.name} has no glyph for {char!r}")
            # Font coordinates run upwards; SVG's run downwards, hence -scale.
            transform = (scale, 0, 0, -scale, pen_x, 0)
            svg_pen = SVGPathPen(glyph_set, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
            glyph_set[name].draw(TransformPen(svg_pen, transform))
            bounds_pen = BoundsPen(glyph_set)
            glyph_set[name].draw(TransformPen(bounds_pen, transform))

            if bounds_pen.bounds:
                gx0, gy0, gx1, gy1 = bounds_pen.bounds
                x0, y0 = min(x0, gx0), min(y0, gy0)
                x1, y1 = max(x1, gx1), max(y1, gy1)
                self.glyphs.append({"char": char, "index": index, "path": svg_pen.getCommands()})
            pen_x += hmtx[name][0] * scale + (tracking * size if index < len(text) - 1 else 0)

        self.advance = pen_x  # the pen's travel: the line box, not the ink
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.width = x1 - x0
        self.height = y1 - y0

    def paths(self, accent_indices=frozenset(), *, indent="  ", base_class="base",
              accent_class="accent") -> str:
        """Render the glyphs, in pen coordinates (the origin is the pen's start).

        Class names are caller-supplied because <style> inside an SVG is scoped
        to the whole document, not to the group it sits in. Two runs in one file
        sharing a class name means the second rule silently recolours the first.
        """
        out = []
        for glyph in self.glyphs:
            cls = accent_class if glyph["index"] in accent_indices else base_class
            out.append(f'{indent}<path class="{cls}" data-char="{glyph["char"]}" '
                       f'd="{glyph["path"]}"/>')
        return "\n".join(out)

    def fill(self, colour: str, *, indent="    ") -> str:
        """The run as one path in one colour -- for lines that are not themed."""
        return f'{indent}<path fill="{colour}" d="{"".join(g["path"] for g in self.glyphs)}"/>'


def type_style(indent: str = "  ") -> str:
    """Colour rules for themed type: the Shire's ink by default, Moria's on a dark page.

    The CSS variables let the application theme the type when the SVG is
    inlined into a page. The media query matters just as much: opened as a
    standalone file or loaded through <img>, no page CSS reaches the document,
    and it is the only thing keeping the wordmark legible on a dark background.
    Light is the default because a renderer that ignores the query (an editor,
    a converter) nearly always draws on white.
    """
    return f"""{indent}<style>
{indent}  .base {{ fill: var(--lembas-ink, {SHIRE_INK}); }}
{indent}  .accent {{ fill: var(--lembas-leaf, {SHIRE_LEAF}); }}
{indent}  @media (prefers-color-scheme: dark) {{
{indent}    .base {{ fill: var(--lembas-ink, {INK}); }}
{indent}    .accent {{ fill: var(--lembas-leaf, {LEAF}); }}
{indent}  }}
{indent}</style>"""


# --- The pixel mark ----------------------------------------------------------
# The mallorn leaf of scripts/pixel_art.py, which the application draws in its
# sidebar. Its blade fills a 14x14 box inside the 16x16 grid -- columns 0-13,
# rows 2-15 -- so moved one cell right and one up it sits centred with a cell
# to spare on every side, and that cell is exactly what the outline needs.
MARK_SHIFT = (1, -1)
MARK_CELLS = 16


def mark_cells(*, outline: bool) -> pixel_art.Pixels:
    """The leaf centred in its 16-cell grid, optionally with a dark edge.

    The edge is the four-neighbour ring around the blade: one cell, never a
    diagonal, so the stem's staircase stays a staircase. It is what lets the
    bare leaf read on a white browser tab, where the pale greens alone are
    under 2:1; on a dark tab it disappears into the tab, which is the point.
    """
    dx, dy = MARK_SHIFT
    cells = {(x + dx, y + dy): role for (x, y), role in pixel_art.leaf().items()}
    if outline:
        for (x, y) in list(cells):
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) not in cells and 0 <= nx < MARK_CELLS and 0 <= ny < MARK_CELLS:
                    cells[(nx, ny)] = "outline"
    return cells


def _art_colour(role: str) -> str:
    return OUTLINE if role == "outline" else ART[role]


def pixel_group(cells, width: int, height: int, *, x: float = 0, y: float = 0,
                scale: float = 1, colour=_art_colour, extra: str = "") -> str:
    rects = pixel_art.svg_rects(cells, width, height, colour=colour)
    return (f'<g transform="translate({x:g} {y:g}) scale({scale:g})" '
            f'shape-rendering="crispEdges"{extra}>{rects}</g>')


def build_art_macros() -> str:
    """templates/_art.html: the three drawings as Jinja macros.

    Classed, never coloured: `.px-<role>` in app.css fills each cell from the
    theme's `--art-<role>`, so the same markup is ithildin on Moria and moss on
    the Shire, and a custom theme recolours it by setting the tokens.
    """
    def macro(name: str, cells, width: int, height: int, *, labelled: bool) -> str:
        rects = pixel_art.svg_rects(cells, width, height)
        a11y = ('{% if label %}role="img" aria-label="{{ label }}"{% else %}'
                'aria-hidden="true"{% endif %}') if labelled else 'aria-hidden="true"'
        args = 'cls="", label=""' if labelled else 'cls=""'
        return (f"{{% macro {name}({args}) -%}}\n"
                f'<svg class="art art--{name} {{{{ cls }}}}" viewBox="0 0 {width} {height}" '
                f'shape-rendering="crispEdges" focusable="false" {a11y}>{rects}</svg>\n'
                "{%- endmacro %}\n")

    w, h = pixel_art.DURIN_SIZE
    w2, h2 = pixel_art.SMIAL_SIZE
    return ("{#- Generated by scripts/build_artwork.py from scripts/pixel_art.py.\n"
            "    Do not edit: the next run replaces this file. -#}\n"
            + macro("leaf", pixel_art.leaf(), pixel_art.LEAF_SIZE, pixel_art.LEAF_SIZE,
                    labelled=True)
            + macro("durin", pixel_art.durin(), w, h, labelled=False)
            + macro("smial", pixel_art.smial(), w2, h2, labelled=False))


# --- Marks and type ----------------------------------------------------------
def _bare_mark(desc: str) -> str:
    """The leaf alone on nothing, one viewBox unit a cell.

    No tile: a favicon sits on the browser's own tab, light or dark, and a
    square of stone around the leaf is a second, unasked-for shape. At 16px
    every cell is one device pixel, at 32 two -- a pixel drawing stays sharp
    only on whole pixels.
    """
    return f"""{HEADER} viewBox="0 0 16 16" width="64" height="64"
     shape-rendering="crispEdges" role="img" aria-label="LLeMbas">
  <title>LLeMbas</title>
  <desc>{desc}</desc>
  {pixel_group(mark_cells(outline=True), MARK_CELLS, MARK_CELLS)}
</svg>
"""


def build_logo_mark() -> str:
    return _bare_mark("A pixel mallorn leaf with a dark one-pixel edge.")


def build_favicon() -> str:
    return _bare_mark("The LLeMbas favicon: a pixel mallorn leaf, transparent around it.")


def build_wordmark() -> str:
    """Standalone type. Follows the reader's colour scheme."""
    run = TextRun(WORDMARK, 128, weight=700)
    pad = 2
    w, h = run.width + 2 * pad, run.height + 2 * pad
    return f"""{HEADER} viewBox="0 0 {w:.2f} {h:.2f}"
     width="{w:.2f}" height="{h:.2f}" role="img" aria-label="LLeMbas">
  <title>LLeMbas</title>
  <!-- JetBrains Mono Bold (SIL OFL 1.1) outlines. The capitals L, L and M spell
       out LLM and take the brand colour; see scripts/build_artwork.py. -->
{type_style()}
  <g transform="translate({pad - run.x0:.2f} {pad - run.y0:.2f})">
{run.paths(ACCENT_GLYPHS, indent="    ")}
  </g>
</svg>
"""


def build_lockup() -> str:
    """Horizontal mark + wordmark, for a header or a page that names the app.

    The leaf is drawn four units a cell -- 64 -- and the wordmark's cap height
    is set to the blade's own height, so the two read as one line.
    """
    cell = 4
    mark = MARK_CELLS * cell
    cap = 12 * cell  # the blade is 12 cells tall between its outline rows
    run = TextRun(WORDMARK, cap / 0.73, weight=700)
    gap = 18
    height = mark
    baseline = (height + run.cap) / 2
    width = mark + gap + run.width + 2
    return f"""{HEADER} viewBox="0 0 {width:.2f} {height}"
     width="{width:.2f}" height="{height}" role="img" aria-label="LLeMbas">
  <title>LLeMbas</title>
{type_style()}
  {pixel_group(mark_cells(outline=True), MARK_CELLS, MARK_CELLS, scale=cell)}
  <g transform="translate({mark + gap - run.x0:.2f} {baseline:.2f})">
{run.paths(ACCENT_GLYPHS, indent="    ")}
  </g>
</svg>
"""


# --- PWA icons ---------------------------------------------------------------
# Same drawing, rasterised because a launcher icon has to be a bitmap -- and
# drawn straight in device pixels, with a whole number of pixels a cell, so no
# edge of the leaf lands between two pixels and smears. Unlike the favicon these
# are opaque: a launcher puts an icon on its own tile and a transparent one
# becomes whatever colour the launcher picks (iOS: black; Android: white).
#
#   "any"      -- the stone tile to the edges, square like everything else.
#   "maskable" -- Android crops it to a circle, squircle or rounded square of
#                 the launcher's choosing, so the leaf must sit inside the
#                 central safe circle (radius 40%). The leaf runs corner to
#                 corner, so its box's diagonal is what has to fit.
#   apple      -- iOS rounds the corners itself.
def _icon(size: int, cell: int, *, grid: bool = True) -> str:
    leaf = mark_cells(outline=False)
    span = 14 * cell  # the blade's own box, without the outline's spare cells
    offset = (size - span) // 2 - cell  # the grid is 16 cells, the blade 14
    # A faint ithildin grid on the stone, as the empty chat has: one line every
    # two cells, aligned to the leaf's own pixels.
    lines = ""
    if grid:
        step = cell * 2
        start = offset % step
        coords = range(start, size, step)
        lines = (f'  <g fill="{ACCENT}" fill-opacity="0.06" shape-rendering="crispEdges">'
                 + "".join(f'<rect x="{c}" y="0" width="{max(1, cell // 8)}" height="{size}"/>'
                           f'<rect x="0" y="{c}" width="{size}" height="{max(1, cell // 8)}"/>'
                           for c in coords)
                 + "</g>\n")
    return f"""{HEADER} viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <rect width="{size}" height="{size}" fill="{BG}"/>
{lines}  {pixel_group(leaf, MARK_CELLS, MARK_CELLS, x=offset, y=offset, scale=cell)}
</svg>
"""


def _rasterise(svg: str, size: int) -> bytes:
    try:
        import cairosvg
    except ImportError:  # pragma: no cover - design-time tool
        sys.exit("cairosvg is required for the PWA icons: pip install cairosvg")
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=size,
                            output_height=size)


def build_icon_192() -> bytes:
    return _rasterise(_icon(192, 9), 192)  # 126px of leaf: 66%


def build_icon_512() -> bytes:
    return _rasterise(_icon(512, 24), 512)  # 336px: 66%


def build_icon_maskable() -> bytes:
    # 14 cells of 20px = 280px; its diagonal, 396px, inside the 410px safe circle.
    return _rasterise(_icon(512, 20), 512)


def build_apple_touch_icon() -> bytes:
    return _rasterise(_icon(180, 9), 180)  # 126px: 70%


def build_badge() -> bytes:
    """The small mark beside a notification in the Android status bar.

    A badge is used as a *mask*: the device keeps the alpha channel and throws
    every colour away. So this is the leaf as a white silhouette on nothing --
    no tile and no outline, neither of which would survive the mask -- with the
    midrib left open, since a cut is the only detail a mask can carry. The
    application once used `icon-192.png` here, which is opaque to its edges, so
    what Android drew was a grey square.

    72px because that is the size Android asks for; four pixels a cell.
    """
    leaf = mark_cells(outline=False)
    return _rasterise(
        f"""{HEADER} viewBox="0 0 72 72" width="72" height="72">
  {pixel_group(leaf, MARK_CELLS, MARK_CELLS, x=4, y=4, scale=4,
               colour=lambda role: None if role == "vein" else "#FFFFFF")}
</svg>
""",
        72,
    )


# --- Banners -----------------------------------------------------------------
# Both banners are one layout: a pixel drawing on the left, and on the right a
# terminal block -- a bracketed label, the wordmark, a dotted rule, a prompt
# line with a block caret. Fixed colours rather than themed: a banner carries
# its own dark screen, because a README is rendered on a light page as often as
# a dark one.
BANNER_W, BANNER_H = 1280, 400


def _at(x: float, y: float, body: str) -> str:
    return f'  <g transform="translate({x:.2f} {y:.2f})">\n{body}\n  </g>'


def _label(text: str, size: float, *, x: float, baseline: float) -> str:
    """`[ TEXT ]` as the application sets it: brackets in mallorn, the words
    uppercase, tracked, in faint ink."""
    tracking = 0.14
    left = TextRun("[", size, weight=600)
    words = TextRun(text.upper(), size, weight=600, tracking=tracking)
    right = TextRun("]", size, weight=600)
    space = size * (0.6 + tracking)
    xw = x + left.advance + space
    xr = xw + words.advance + space
    return "\n".join((_at(x, baseline, left.fill(LEAF)),
                      _at(xw, baseline, words.fill(INK_FAINT)),
                      _at(xr, baseline, right.fill(LEAF))))


def _corners(inset: int, arm: int, weight: int) -> str:
    """The four corners of the frame, picked out the way a terminal draws a box."""
    far_x, far_y = BANNER_W - inset - weight, BANNER_H - inset - weight
    rects = []
    for cx, dx in ((inset, 0), (far_x, weight - arm)):
        for cy, dy in ((inset, 0), (far_y, weight - arm)):
            rects.append(f'<rect x="{cx + dx}" y="{cy}" width="{arm}" height="{weight}"/>')
            rects.append(f'<rect x="{cx}" y="{cy + dy}" width="{weight}" height="{arm}"/>')
    return (f'  <g fill="{BORDER_STRONG}" shape-rendering="crispEdges">'
            + "".join(rects) + "</g>")


def _screen(prefix: str) -> str:
    """The background of the application's empty screens: stone, a grid that
    fades out from the middle, lit faintly from the top (--texture-screen) --
    in a square hairline frame with its corners picked out."""
    return f"""  <defs>
    <pattern id="{prefix}-grid" width="24" height="24" patternUnits="userSpaceOnUse">
      <rect width="24" height="1" fill="{ACCENT}" fill-opacity="0.07"/>
      <rect width="1" height="24" fill="{ACCENT}" fill-opacity="0.07"/>
    </pattern>
    <radialGradient id="{prefix}-fade" cx="0.5" cy="0.45" r="0.62">
      <stop offset="0.35" stop-color="{BG}" stop-opacity="0"/>
      <stop offset="1" stop-color="{BG}" stop-opacity="1"/>
    </radialGradient>
    <radialGradient id="{prefix}-light" cx="0.28" cy="0.1" r="0.55">
      <stop offset="0" stop-color="{ACCENT}" stop-opacity="0.10"/>
      <stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/>
    </radialGradient>
    <filter id="{prefix}-glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <!-- glow-title: the wordmark's own light, kept low: light, not fog. -->
    <filter id="{prefix}-type" x="-10%" y="-30%" width="120%" height="160%">
      <feGaussianBlur stdDeviation="9" result="b"/>
      <feColorMatrix in="b" type="matrix"
        values="0 0 0 0 0.56  0 0 0 0 0.85  0 0 0 0 0.91  0 0 0 0.22 0"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <style>.wm {{ fill: {INK}; }} .wm-a {{ fill: {LEAF}; }} .wm-c {{ fill: {ACCENT}; }}</style>
  <rect width="{BANNER_W}" height="{BANNER_H}" fill="{BG}"/>
  <rect width="{BANNER_W}" height="{BANNER_H}" fill="url(#{prefix}-grid)"/>
  <rect width="{BANNER_W}" height="{BANNER_H}" fill="url(#{prefix}-fade)"/>
  <rect width="{BANNER_W}" height="{BANNER_H}" fill="url(#{prefix}-light)"/>
  <rect x="16.5" y="16.5" width="{BANNER_W - 33}" height="{BANNER_H - 33}" fill="none"
        stroke="{BORDER}"/>
{_corners(16, 14, 2)}"""


def _terminal_block(prefix: str, *, x: float, top: float, label: str, word: TextRun,
                    classes: dict[int, str], prompt: str, prompt_text: str,
                    footnote: str) -> str:
    """The right-hand block: label, wordmark, dotted rule, prompt, footnote.
    The rule is measured from the wordmark, so it ends where the word does."""
    label_size = 17
    out = [_label(label, label_size, x=x, baseline=top + label_size * 0.73)]

    word_base = top + label_size * 0.73 + 30 + word.cap
    glyphs = "\n".join(
        f'    <path class="{classes.get(g["index"], "wm")}" data-char="{g["char"]}" '
        f'd="{g["path"]}"/>' for g in word.glyphs)
    out.append(f'  <g transform="translate({x - word.x0:.2f} {word_base:.2f})" '
               f'filter="url(#{prefix}-type)">\n{glyphs}\n  </g>')

    rule_y = round(word_base + 30) + 0.5
    out.append(f'  <line x1="{x:.2f}" y1="{rule_y}" x2="{x + word.width:.2f}" y2="{rule_y}" '
               f'stroke="{BORDER_STRONG}" stroke-width="2" stroke-dasharray="2 4"/>')

    size = 23
    p = TextRun(prompt, size, weight=700)
    t = TextRun(prompt_text, size, weight=400)
    line_base = rule_y + 22 + size * 0.73
    step = size * 0.6  # one monospace cell
    tx = x + p.advance + step
    out.append(_at(x, line_base, p.fill(ACCENT)))
    out.append(_at(tx, line_base, t.fill(INK_MUTED)))
    # The block caret, one cell wide, standing on the baseline like a terminal's.
    cx = tx + t.advance + step * 0.4
    out.append(f'  <rect x="{cx:.2f}" y="{line_base - size * 0.78:.2f}" width="{step:.2f}" '
               f'height="{size * 0.98:.2f}" fill="{ACCENT}" fill-opacity="0.85"/>')
    note = TextRun(footnote, 14, weight=500, tracking=0.04)
    out.append(_at(x, line_base + 40, note.fill(INK_FAINT)))
    return "\n".join(out)


def build_banner() -> str:
    """README hero: the Doors of Durin, and the wordmark as a terminal block."""
    w, h = pixel_art.DURIN_SIZE
    cell = 5
    door_w, door_h = w * cell, h * cell
    word = TextRun(WORDMARK, 132, weight=800)
    gap = 84
    left = round((BANNER_W - (door_w + gap + word.width)) / 2)
    door_y = (BANNER_H - door_h) // 2
    door = pixel_group(pixel_art.durin(), w, h, x=left, y=door_y, scale=cell,
                       extra=' filter="url(#b-glow)"')
    block = _terminal_block("b", x=left + door_w + gap, top=96, label="Self-hosted LLM web UI",
                            word=word, classes=dict.fromkeys(ACCENT_GLYPHS, "wm-a"),
                            prompt=">", prompt_text=TAGLINE,
                            footnote="server-rendered python  ·  no build step  ·  no CDN")

    return f"""{HEADER} viewBox="0 0 {BANNER_W} {BANNER_H}"
     width="{BANNER_W}" height="{BANNER_H}" role="img"
     aria-label="LLeMbas - {TAGLINE}">
  <title>LLeMbas</title>
  <desc>{TAGLINE}. The Doors of Durin in pixel ithildin beside the LLeMbas
    wordmark, set as a terminal.</desc>
{_screen("b")}
  <!-- The west-gate, as the empty chat draws it: five pixels a cell, in its
       own light, as art-glow gives it there. -->
  {door}
{block}
</svg>
"""


def build_cli_banner() -> str:
    """The LLeMbas CLI's README hero: the same screen and type, the leaf in a
    terminal window in place of the door, and `$ lembas` at the prompt."""
    cell = 13
    span = 14 * cell  # the blade's box; the 16-cell grid has a spare cell each side
    name = "LLeMbas CLI"
    word = TextRun(name, 112, weight=800)
    gap = 84
    left = round((BANNER_W - (span + gap + word.width)) / 2)
    # The window: a square frame, a title bar, a hard offset shadow (the
    # application's --shadow-block), the leaf centred below the bar.
    pad, bar, drop = 34, 18, 6
    fw, fh = span + 2 * pad, span + 2 * pad + bar
    fx, fy = left - pad, (BANNER_H - fh) // 2
    shadow = 'fill="#000" fill-opacity="0.55"'
    dots = "".join(f'<rect x="{fx + 10 + i * 12}" y="{fy + 7}" width="6" height="6"/>'
                   for i in range(3))
    window = "\n".join((
        f'  <rect x="{fx + fw + 1}" y="{fy + drop}" width="{drop}" height="{fh}" {shadow}/>',
        f'  <rect x="{fx + drop}" y="{fy + fh + 1}" width="{fw + 1}" height="{drop}" {shadow}/>',
        f'  <rect x="{fx + 0.5}" y="{fy + 0.5}" width="{fw}" height="{fh}" '
        f'fill="{BG_SUNKEN}" stroke="{BORDER_STRONG}"/>',
        f'  <rect x="{fx + 1}" y="{fy + 1}" width="{fw - 1}" height="{bar}" '
        f'fill="{SURFACE_RAISED}"/>',
        f'  <g fill="{BORDER_STRONG}" shape-rendering="crispEdges">{dots}</g>',
    ))
    leaf = pixel_group(mark_cells(outline=False), MARK_CELLS, MARK_CELLS,
                       x=left - cell, y=fy + bar + pad - cell, scale=cell,
                       extra=' filter="url(#c-glow)"')
    # LLM in mallorn as on the app's own banner; "CLI" in ithildin, the part of
    # the name that is the terminal.
    classes = dict.fromkeys(ACCENT_GLYPHS, "wm-a") | dict.fromkeys((8, 9, 10), "wm-c")
    block = _terminal_block("c", x=left + span + gap, top=106, label="Terminal coding agent",
                            word=word, classes=classes, prompt="$", prompt_text="lembas",
                            footnote="one binary  ·  any LLM API  ·  git-native")

    return f"""{HEADER} viewBox="0 0 {BANNER_W} {BANNER_H}"
     width="{BANNER_W}" height="{BANNER_H}" role="img"
     aria-label="LLeMbas CLI - a terminal coding agent">
  <title>LLeMbas CLI</title>
  <desc>The LLeMbas CLI: a pixel mallorn leaf in a terminal window beside the
    wordmark and a lembas prompt.</desc>
{_screen("c")}
{window}
  {leaf}
{block}
</svg>
"""


# --- Entry point -------------------------------------------------------------
BUILDERS = {
    "logo-mark.svg": build_logo_mark,
    "favicon.svg": build_favicon,
    "wordmark.svg": build_wordmark,
    "logo-lockup.svg": build_lockup,
    "banner.svg": build_banner,
    "icon-192.png": build_icon_192,
    "icon-512.png": build_icon_512,
    "icon-maskable-512.png": build_icon_maskable,
    "apple-touch-icon-180.png": build_apple_touch_icon,
    "badge-72.png": build_badge,
    # Not an asset: written into the templates, see main().
    "_art.html": build_art_macros,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ASSETS)
    parser.add_argument("--only", nargs="*", choices=sorted(BUILDERS), default=None)
    parser.add_argument("--cli-banner", type=Path, metavar="PATH",
                        help="write the LLeMbas CLI's banner to PATH and nothing else")
    args = parser.parse_args()

    if args.cli_banner:
        data = build_cli_banner().encode("utf-8")
        args.cli_banner.parent.mkdir(parents=True, exist_ok=True)
        args.cli_banner.write_bytes(data)
        print(f"wrote {args.cli_banner} ({len(data):,} bytes)")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    STATIC_IMG.mkdir(parents=True, exist_ok=True)

    for filename in args.only or BUILDERS:
        content = BUILDERS[filename]()
        # The PNG builders return bytes; everything else returns SVG source.
        data = content if isinstance(content, bytes) else content.encode("utf-8")

        path = (TEMPLATES if filename == "_art.html" else args.out) / filename
        path.write_bytes(data)
        print(f"wrote {path.relative_to(ROOT)} ({len(data):,} bytes)")

        if filename in SERVED_BY_APP:
            served = STATIC_IMG / filename
            served.write_bytes(data)
            print(f"      -> {served.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
