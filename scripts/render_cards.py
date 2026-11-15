#!/usr/bin/env python3
"""Render the profile README cards into cards/ and regenerate README.md.

Every card is a self-contained SVG meant for an <img> tag: one inline <style> block
(CSS keyframes), SMIL <animate*> elements, and fonts subset with pyftsubset and
embedded as base64 WOFF data URIs. Nothing external is loaded.

    python scripts/render_cards.py           # animated cards
    STATIC=1 python scripts/render_cards.py  # final frame only, for screenshots

Inputs: profile.json (content), data/contributions.json (refreshed daily by
scripts/fetch_data.py), fonts/src (Bricolage Grotesque + IBM Plex Mono, OFL),
and the optional portrait named in profile.json (needs Pillow).
"""
from __future__ import annotations

import base64
import calendar
import datetime as dt
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "cards"
FONT_SRC = ROOT / "fonts" / "src"
FONT_BUILD = ROOT / "fonts" / "build"
STATIC = os.environ.get("STATIC") == "1"

CFG = json.loads((ROOT / "profile.json").read_text())
DATA = json.loads((ROOT / "data" / "contributions.json").read_text())

W = 880             # full-width cards; README shows them at width="100%"
TW, TH = 286, 214   # row cards (tiles, repos); README shows them at width="32.5%"
GAP = 14            # transparent space baked under every panel, so rows stack evenly
R = 22              # panel corner radius

# One accent (vermilion) on warm neutrals; l0..l4 are the contribution levels.
PALETTE = {
    "light": dict(panel="#F2F1EE", ink="#141414", mut="#55534E", sub="#8C8981", acc="#F0472B", onacc="#FFFFFF",
                  bar="#141414", line="rgba(20,20,20,.1)",
                  l0="#E4E2DD", l1="#FCC9BC", l2="#F78C71", l3="#F0472B", l4="#AE2A14",
                  p1="#FCC9BC", p2="#F78C71", p3="#F0472B", p4="#AE2A14"),
    "dark": dict(panel="#18181A", ink="#F2F1EE", mut="#B7B4AC", sub="#7F7C75", acc="#FF5A3C", onacc="#FFFFFF",
                 bar="#F2F1EE", line="rgba(242,241,238,.12)",
                 l0="#26262A", l1="#5C2418", l2="#9C3521", l3="#F0472B", l4="#FF8D72",
                 # portrait tones stay put so the face never turns into a negative
                 p1="#FCC9BC", p2="#F78C71", p3="#F0472B", p4="#B8301A"),
}

# key: (source file, axis location or None, css fallback stack, weight)
FONTS = {
    "d": ("Bricolage.ttf", {"wght": 700, "opsz": 96, "wdth": 100}, "system-ui,sans-serif", 700),
    "dl": ("Bricolage.ttf", {"wght": 300, "opsz": 96, "wdth": 100}, "system-ui,sans-serif", 300),
    "b": ("Bricolage.ttf", {"wght": 400, "opsz": 14, "wdth": 100}, "system-ui,sans-serif", 400),
    "bs": ("Bricolage.ttf", {"wght": 600, "opsz": 24, "wdth": 100}, "system-ui,sans-serif", 600),
    "m": ("IBMPlexMono-Regular.ttf", None, "ui-monospace,monospace", 400),
    "mm": ("IBMPlexMono-Medium.ttf", None, "ui-monospace,monospace", 500),
}

# Page timeline in seconds, top to bottom.
T = dict(hero=0.0, heatmap=0.6, activity=1.3, tiles=1.95, tile_step=0.12, title=2.4, repos=2.6, repo_step=0.12)
EASE = "cubic-bezier(.22,.8,.24,1)"


# ---------------------------------------------------------------- fonts

def instanced(key: str) -> Path:
    """Static instance of the font for this key (cached in fonts/build)."""
    src_name, axes, *_ = FONTS[key]
    src = FONT_SRC / src_name
    if axes is None:
        return src
    out = FONT_BUILD / f"{key}.ttf"
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        FONT_BUILD.mkdir(parents=True, exist_ok=True)
        font = TTFont(src)
        instancer.instantiateVariableFont(font, axes, inplace=True)
        font.save(out)
    return out


METRICS: dict[str, tuple[dict, dict, int]] = {}


def measure(text: str, key: str, size: float, spacing: float = 0) -> float:
    if key not in METRICS:
        f = TTFont(instanced(key))
        METRICS[key] = (f.getBestCmap(), f["hmtx"].metrics, f["head"].unitsPerEm)
    cmap, hmtx, upem = METRICS[key]
    return sum(hmtx[cmap.get(ord(c), ".notdef")][0] for c in text) * size / upem + spacing * len(text)


def wrap(text: str, key: str, size: float, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial, key, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


PYFTSUBSET = [shutil.which("pyftsubset")] if shutil.which("pyftsubset") else [sys.executable, "-m", "fontTools.subset"]
_subsets: dict[str, str] = {}


def woff_data_uri(key: str, chars: set[str]) -> str:
    text = "".join(sorted(chars | {" "}))
    digest = hashlib.sha1(f"{key}:{text}".encode()).hexdigest()
    if digest not in _subsets:
        with tempfile.TemporaryDirectory() as tmp:
            txt, out = Path(tmp, "chars.txt"), Path(tmp, "out.woff")
            txt.write_text(text, encoding="utf-8")
            subprocess.run([*PYFTSUBSET, str(instanced(key)), f"--text-file={txt}", f"--output-file={out}",
                            "--flavor=woff", "--layout-features=kern,liga,tnum", "--no-hinting",
                            "--desubroutinize", "--drop-tables+=meta,DSIG"], check=True)
            _subsets[digest] = base64.b64encode(out.read_bytes()).decode()
    return f"data:font/woff;base64,{_subsets[digest]}"


# ---------------------------------------------------------------- svg card

def esc(s: str) -> str:
    return html.escape(s, quote=True)


class Card:
    def __init__(self, name: str, w: float, h: float, t0: float, title: str):
        self.name, self.w, self.h, self.t0, self.title = name, w, h, t0, title
        self.parts: list[str] = []
        self.defs: list[str] = []
        self.css: list[str] = []
        self.chars: dict[str, set[str]] = {k: set() for k in FONTS}

    @property
    def ph(self) -> float:
        """Panel height (the card minus the baked-in gap below it)."""
        return self.h - GAP

    def add(self, *xml: str) -> None:
        self.parts.extend(xml)

    def anim(self, kind: str | None, delay: float = 0, dur: float | None = None, cls: str = "") -> str:
        """class/style attributes for a CSS-animated element; delay is relative to the card."""
        if STATIC or not kind:
            return f'class="{cls}"' if cls else ""
        style = f"animation-delay:{self.t0 + delay:.2f}s" + (f";animation-duration:{dur:.2f}s" if dur else "")
        return f'class="{f"{cls} an {kind}".strip()}" style="{style}"'

    def at(self, delay: float) -> str:
        return f"{self.t0 + delay:.2f}s"

    def motion(self, animated: str, still: str) -> str:
        """CSS can't stop SMIL, so reduced motion swaps the SMIL copy for a still one."""
        return still if STATIC else f'<g class="smil">{animated}</g><g class="still">{still}</g>'

    def text(self, x: float, y: float, s: str, font: str, size: float, cls: str = "ink",
             anchor: str = "start", kind: str | None = None, delay: float = 0, spacing: float = 0) -> str:
        self.chars[font].update(s)
        extra = (f' letter-spacing="{spacing}"' if spacing else "") + (f' text-anchor="{anchor}"' if anchor != "start" else "")
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}"{extra} '
                f'{self.anim(kind, delay, cls=f"{font} {cls}")}>{esc(s)}</text>')

    def tspans(self, x: float, y: float, size: float, runs: list[tuple[str, str, str]], kind=None, delay=0.0) -> str:
        """One line made of (text, font, colour class) runs."""
        out = []
        for s, font, cls in runs:
            self.chars[font].update(s)
            out.append(f'<tspan class="{font} {cls}">{esc(s)}</tspan>')
        return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" {self.anim(kind, delay)}>{"".join(out)}</text>'

    def panel(self, cls: str = "panel") -> None:
        self.add(f'<rect width="{self.w:.3f}" height="{self.ph}" rx="{R}" class="{cls}"/>')

    def kicker(self, x: float, y: float, s: str, cls: str = "sub", square: str | None = "acc", delay: float = 0) -> None:
        if square:
            self.add(f'<rect x="{x}" y="{y - 10}" width="10" height="10" rx="2" {self.anim("pop", delay, cls=square)}/>')
            x += 22
        self.add(self.text(x, y, s.upper(), "mm", 13, cls, kind="rise", delay=delay, spacing=1.5))

    def arrow(self, x: float, y: float, cls: str, delay: float) -> None:
        self.add(f'<path d="M{x} {y + 14} L{x + 14} {y} M{x + 3} {y} H{x + 14} V{y + 11}" fill="none" stroke-width="2" '
                 f'stroke-linecap="round" stroke-linejoin="round" pathLength="1" {self.anim("draw", delay, .6, cls=f"{cls} dash")}/>')

    def render(self) -> str:
        faces = [f"@font-face{{font-family:F{k};font-weight:{FONTS[k][3]};src:url({woff_data_uri(k, ch)}) format('woff')}}"
                 for k, ch in self.chars.items() if ch]
        fam = "".join(f".{k}{{font-family:F{k},{v[2]};font-weight:{v[3]}}}" for k, v in FONTS.items())
        css = "\n".join([*faces, palette_css(), fam, "" if STATIC else ANIM_CSS, *([] if STATIC else self.css)])
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w:.3f}" height="{self.h}" '
                f'viewBox="0 0 {self.w:.3f} {self.h}" role="img" aria-labelledby="t">'
                f'<title id="t">{esc(self.title)}</title><style>{css}</style>'
                f'<defs>{"".join(self.defs)}</defs>{"".join(self.parts)}</svg>\n')

    def save(self) -> Path:
        path = CARDS / f"{self.name}.svg"
        path.write_text(self.render(), encoding="utf-8")
        return path


def palette_css() -> str:
    def vars_(p: dict) -> str:
        return ";".join(f"--{k}:{v}" for k, v in p.items())
    fills = "".join(f".{k}{{fill:var(--{k})}}" for k in PALETTE["light"] if k != "line")
    strokes = "".join(f".s-{k}{{fill:none;stroke:var(--{k})}}" for k in ("acc", "ink", "onacc", "sub"))
    return (f"svg{{{vars_(PALETTE['light'])}}}"
            f"@media (prefers-color-scheme:dark){{svg{{{vars_(PALETTE['dark'])}}}}}"
            f"{fills}{strokes}.ln{{stroke:var(--line)}}.dash{{stroke-dasharray:1}}.still{{display:none}}")


ANIM_CSS = f"""
.an{{animation-duration:.8s;animation-timing-function:{EASE};animation-fill-mode:both}}
.rise{{animation-name:rise}}.fade{{animation-name:fade}}.draw{{animation-name:draw;animation-duration:1s}}
.grow{{animation-name:grow;transform-box:fill-box;transform-origin:50% 100%}}
.growx{{animation-name:growx;transform-box:fill-box;transform-origin:0 50%}}
.pop{{animation-name:pop;animation-duration:.5s;transform-box:fill-box;transform-origin:50% 50%}}
@keyframes rise{{from{{opacity:0;transform:translateY(14px)}}}}
@keyframes fade{{from{{opacity:0}}}}
@keyframes grow{{from{{transform:scaleY(0)}}}}
@keyframes growx{{from{{transform:scaleX(0)}}}}
@keyframes pop{{from{{opacity:0;transform:scale(.2)}}}}
@keyframes draw{{from{{stroke-dashoffset:1}}}}
@media (prefers-reduced-motion:reduce){{.an{{animation:none!important}}.smil{{display:none}}.still{{display:inline}}}}
"""


# ---------------------------------------------------------------- data

def days() -> list[tuple[dt.date, int]]:
    return [(dt.date.fromisoformat(d["date"]), d["count"]) for d in DATA["days"]]


def stats() -> dict:
    ds = days()
    counts = [c for _, c in ds]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    tail = counts[:-1] if counts and counts[-1] == 0 else counts  # today may still be empty
    current = 0
    for c in reversed(tail):
        if not c:
            break
        current += 1
    busiest = max(ds, key=lambda d: d[1]) if ds else None
    return dict(total=sum(counts), active=sum(1 for c in counts if c), span=len(counts),
                current=current, longest=longest, busiest=busiest if busiest and busiest[1] else None)


def months() -> list[tuple[dt.date, int]]:
    """The 12 calendar months ending with the current one."""
    ds = days()
    last = ds[-1][0] if ds else dt.date.today()
    keys, (y, m) = [], (last.year, last.month)
    for _ in range(12):
        keys.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    totals = dict.fromkeys(keys, 0)
    for d, c in ds:
        if (d.year, d.month) in totals:
            totals[(d.year, d.month)] += c
    return [(dt.date(y, m, 1), totals[(y, m)]) for y, m in reversed(keys)]


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def levels(values: list[int]):
    """Map a count to 0..4 by quartiles of the active days, so one huge day doesn't flatten the rest."""
    nz = sorted(v for v in values if v)
    cuts = [nz[int(len(nz) * p)] for p in (.25, .5, .75)] if nz else []
    return lambda v: 0 if not v else 1 + sum(v > t for t in cuts)


# ---------------------------------------------------------------- cards

def portrait_cells(path: Path, n: int) -> list[tuple[int, int, int]]:
    """The pixel-art avatar, pixel for pixel, as (col, row, level) cells of a contribution graph.

    n is the avatar's own grid (23 for the current one): each cell samples the centre of
    one source pixel. Background pixels (blue) become empty l0 cells; the figure maps to
    l1..l4 by brightness, darkest (hair) to l4.
    """
    from PIL import Image
    im = Image.open(path).convert("RGB")
    sx, sy = im.width / n, im.height / n

    def is_bg(p: tuple[int, int, int]) -> bool:
        r, g, b = p
        return b > r + 30 and b >= g

    cells = []
    for row in range(n):
        for col in range(n):
            p = im.getpixel((int((col + .5) * sx), int((row + .5) * sy)))
            if is_bg(p):
                cells.append((col, row, 0))
                continue
            lum = .299 * p[0] + .587 * p[1] + .114 * p[2]
            cells.append((col, row, 4 if lum < 70 else 3 if lum < 100 else 2 if lum < 150 else 1))
    return cells


def hero() -> Card:
    c = Card("hero", W, 420, T["hero"], f"{' '.join(CFG['name'])}. {CFG['tagline']}")
    c.panel()
    x = 48
    c.kicker(x, 66, CFG["kicker"], delay=.05)
    first, last = CFG["name"]
    c.add(c.text(x - 4, 182, first, "d", 116, kind="rise", delay=.12, spacing=-3),
          c.tspans(x - 4, 292, 116, [(last, "d", "ink"), (".", "d", "acc")], kind="rise", delay=.22))
    ty = 352
    c.add(c.text(x, ty, CFG["tagline"], "b", 26, "mut", kind="rise", delay=.4))
    # blinking cursor after the line (SMIL)
    cx = x + measure(CFG["tagline"], "b", 26) + 8
    cursor = f'<rect x="{cx:.1f}" y="{ty - 22}" width="3" height="27" class="acc"'
    blink = f'{cursor}><animate attributeName="opacity" values="1;1;0;0" keyTimes="0;.5;.5;1" dur="1.1s" repeatCount="indefinite"/></rect>'
    c.add(f'<g {c.anim("fade", .7)}>', c.motion(blink, f"{cursor}/>"), "</g>")

    portrait = CFG.get("portrait") and ROOT / CFG["portrait"]
    if portrait and portrait.exists():
        n = CFG.get("portrait_pixels", 23)
        size = 322
        step = size / n
        px, py = W - 52 - size, (c.ph - size) / 2
        cells = portrait_cells(portrait, n)
        if not STATIC:  # one delay class per diagonal instead of a style on every cell
            c.css.append("".join(f".g{k}{{animation-delay:{c.t0 + .3 + k * .03:.3f}s}}" for k in range(2 * n)))
        c.add(*(f'<rect x="{px + col * step:.1f}" y="{py + row * step:.1f}" width="{step - 2.4:.1f}" height="{step - 2.4:.1f}" '
                f'rx="2.4" class="{f"p{lv}" if lv else "l0"}{"" if STATIC else f" an pop g{col + row}"}"/>'
                for col, row, lv in cells))
    return c


def heatmap() -> Card:
    ds, s = days(), stats()
    c = Card("heatmap", W, 270, T["heatmap"], f"Contribution graph: {plural(s['total'], 'contribution')} in the last year")
    c.panel()
    c.kicker(40, 52, "A year on GitHub")
    x0, y0, step, cell = 74, 104, 14.6, 11.6
    lead = (ds[0][0].weekday() + 1) % 7 if ds else 0  # GitHub weeks start on Sunday
    cols = (lead + len(ds) + 6) // 7
    level = levels([v for _, v in ds])

    lx = W - 40 - measure("More", "m", 12) - 10 - cell - 4 * step
    c.add(c.text(lx - 10, 52, "Less", "m", 12, "sub", "end", "fade", .1),
          *(f'<rect x="{lx + k * step:.1f}" y="{52 - cell + 1:.1f}" width="{cell}" height="{cell}" rx="2.5" '
            f'{c.anim("pop", .1 + k * .05, cls=f"l{k}")}/>' for k in range(5)),
          c.text(lx + 4 * step + cell + 10, 52, "More", "m", 12, "sub", kind="fade", delay=.1))
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.add(c.text(40, y0 + row * step + cell - 2, name, "m", 11, "sub", kind="fade", delay=.1))
    last_label = -9
    for i, (d, _) in enumerate(ds):
        col = (lead + i) // 7
        if d.day <= 7 and (lead + i) % 7 == 0 and col - last_label >= 3:
            c.add(c.text(x0 + col * step, y0 - 12, calendar.month_abbr[d.month], "m", 11, "sub", kind="fade", delay=.15 + col * .02))
            last_label = col

    c.css.append("".join(f".c{k}{{animation-delay:{c.t0 + .2 + k * .02:.3f}s}}" for k in range(cols)))
    c.add(*(f'<rect x="{x0 + col * step:.1f}" y="{y0 + row * step:.1f}" width="{cell}" height="{cell}" rx="2.5" '
            f'class="l{level(v)}{"" if STATIC else f" an pop c{col}"}"><title>{d:%b} {d.day}: {v}</title></rect>'
            for i, (d, v) in enumerate(ds) for col, row in [divmod(lead + i, 7)]))

    if ds:  # today's cell pulses (SMIL)
        col, row = divmod(lead + len(ds) - 1, 7)
        ring = (f'<rect x="{x0 + col * step - 2.5:.1f}" y="{y0 + row * step - 2.5:.1f}" width="{cell + 5}" height="{cell + 5}" '
                f'rx="4" class="s-acc" stroke-width="1.6"')
        pulse = (f'{ring} opacity="0"><animate attributeName="opacity" values="0;1;.2;1" keyTimes="0;.2;.6;1" '
                 f'dur="2.4s" begin="{c.at(.3 + cols * .02)}" repeatCount="indefinite"/></rect>')
        c.add(c.motion(pulse, f"{ring}/>"))

    fy, end = c.ph - 26, ds[-1][0] if ds else dt.date.today()
    note = (f"{plural(s['total'], 'contribution')} · {plural(s['active'], 'active day')}" if s["total"]
            else "Most of my work lives in private repositories")
    after = .4 + cols * .02
    c.add(c.text(40, fy, note, "m", 12, "mut", kind="fade", delay=after),
          c.text(W - 40, fy, f"updated {end:%b} {end.day}, {end.year}", "m", 12, "sub", "end", "fade", after))
    return c


def activity() -> Card:
    s, ms = stats(), months()
    c = Card("activity", W, 380, T["activity"],
             f"{plural(s['total'], 'contribution')} in the last 12 months, {plural(s['current'], 'day')} current streak")
    c.panel()
    c.kicker(40, 52, f"{ms[0][0]:%b %Y} – {ms[-1][0]:%b %Y}")
    c.add(c.tspans(36, 118, 64, [(f"{s['total']:,} ", "d", "ink"), ("contributions", "dl", "ink")], kind="rise", delay=.08))

    # bars
    x0, x1, top, base = 40, 590, 168, c.ph - 46
    peak = max(v for _, v in ms)
    slot, bw = (x1 - x0) / len(ms), 32
    for j, (month, v) in enumerate(ms):
        cx, d = x0 + slot * (j + .5), .3 + j * .05
        current = j == len(ms) - 1
        bh = max(3.0, (base - top) * v / peak) if peak else 3.0
        cls = "acc" if v == peak and peak else ("l0" if current or not v else "bar")
        c.add(f'<rect x="{cx - bw / 2:.1f}" y="{base - bh:.1f}" width="{bw}" height="{bh:.1f}" rx="5" '
              f'{c.anim("grow", d, .9, cls=cls)}/>',
              c.text(cx, base + 26, f"{month:%b}", "m", 12, "sub", "middle", "fade", d))
        if v == peak and peak:
            c.add(c.text(cx, base - bh - 10, f"{v:,}", "mm", 13, "acc", "middle", "fade", d + .5))

    # stats column
    sx, d0 = 632, .5
    busiest = s["busiest"]
    blocks = [
        ([(f"{s['current']:,} ", "d", "ink"), ("days" if s["current"] != 1 else "day", "dl", "ink")],
         f"current streak · longest {s['longest']:,}"),
        ([(f"{s['active']:,}", "d", "ink"), (f" / {s['span']}", "dl", "sub")], "days with a contribution"),
        ([(f"{busiest[1]:,}" if busiest else "0", "d", "ink")],
         f"on the busiest day, {busiest[0]:%b} {busiest[0].day}" if busiest else "on the busiest day"),
    ]
    for k, (runs, note) in enumerate(blocks):
        y, d = 96 + k * 92, d0 + k * .12
        if k:
            c.add(f'<line x1="{sx}" y1="{y - 46}" x2="{W - 40}" y2="{y - 46}" stroke-width="1" {c.anim("growx", d, cls="ln")}/>')
        c.add(c.tspans(sx, y, 44, runs, kind="rise", delay=d),
              c.text(sx, y + 26, note, "b", 15, "mut", kind="rise", delay=d + .05))
        if k == 0 and s["current"]:
            dx = sx + measure(f"{s['current']:,} days", "d", 44) + 6
            dot = f'<circle cx="{dx + 8:.1f}" cy="{y - 30}" r="4.5" class="acc"/>'
            ring = (f'<circle cx="{dx + 8:.1f}" cy="{y - 30}" r="4.5" class="s-acc" stroke-width="1.5">'
                    f'<animate attributeName="r" values="4.5;12" dur="1.8s" begin="{c.at(1.2)}" repeatCount="indefinite"/>'
                    f'<animate attributeName="opacity" values=".9;0" dur="1.8s" begin="{c.at(1.2)}" repeatCount="indefinite"/></circle>')
            c.add(f'<g {c.anim("fade", d)}>{dot}', c.motion(ring, ""), "</g>")
    return c


def tile(i: int, t: dict) -> Card:
    c = Card(f"tile-{i}", TW, TH, T["tiles"] + (i - 1) * T["tile_step"], f"{t['label']}: {t['title']}")
    accent = i == 1
    fg, mut = ("onacc", "onacc") if accent else ("ink", "mut")
    c.add(f'<g {c.anim("rise", 0, .9)}>')
    c.panel("acc" if accent else "panel")
    c.kicker(26, 44, t["label"], "onacc" if accent else "sub", square=None)
    size = 34
    while measure(t["title"], "d", size) > TW - 52 and size > 22:
        size -= 1
    c.add(c.text(24, c.ph - 56, t["title"], "d", size, fg),
          c.text(26, c.ph - 28, t["sub"], "b", 14, mut), "</g>")
    c.arrow(TW - 44, 30, f"s-{fg}", .45)
    return c


def coding_title() -> Card:
    light, bold = CFG["coding_title"]
    c = Card("title-coding", W, 96, T["title"], f"{light} {bold}")
    c.add(c.tspans(4, 60, 48, [(f"{light} ", "dl", "ink"), (bold, "d", "acc")], kind="rise"),
          f'<rect x="4" y="{c.ph - 6}" width="{W - 8}" height="1.5" {c.anim("growx", .2, 1.2, cls="l0")}/>')
    return c


def repo(k: int, item: dict) -> Card:
    c = Card(f"repo-{item['name']}", TW, TH, T["repos"] + k * T["repo_step"], f"{item['name']}: {item['description']}")
    c.add(f'<g {c.anim("rise", 0, .9)}>')
    c.panel()
    size = 21
    while measure(item["name"], "bs", size) > TW - 72 and size > 15:
        size -= .5
    c.add(f'<rect x="26" y="{44 - 12}" width="11" height="11" rx="2" class="acc"/>',
          c.text(46, 44, item["name"], "bs", size))
    for n, line in enumerate(wrap(item["description"], "b", 15, TW - 52)[:4]):
        c.add(c.text(26, 82 + n * 22, line, "b", 15, "mut"))
    meta = "  ·  ".join(x for x in (item.get("language"), "private" if item.get("private", True) else None) if x)
    c.add(c.text(26, c.ph - 26, meta, "m", 12, "sub"), "</g>")
    return c


# ---------------------------------------------------------------- readme + preview

def readme(row1: list[Card], row2: list[Card]) -> str:
    tiles = " ".join(f'<a href="{esc(t["url"])}"><img src="./cards/{card.name}.svg" width="32.5%" alt="{esc(card.title)}" /></a>'
                     for t, card in zip(CFG["tiles"], row1))
    repos = " ".join(f'<img src="./cards/{card.name}.svg" width="32.5%" alt="{esc(card.title)}" />' for card in row2)
    links = " &nbsp;/&nbsp; ".join(f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in CFG["links"])
    name = " ".join(CFG["name"])
    return f"""<!-- Generated by scripts/render_cards.py from profile.json; refreshed daily by
     .github/workflows/profile-cards.yml. Edit profile.json or the script, not this file. -->
<div align="center">
<img src="./cards/hero.svg" width="100%" alt="{esc(name)}. {esc(CFG['tagline'])}" /><br>
<img src="./cards/heatmap.svg" width="100%" alt="Contribution graph, refreshed daily" /><br>
<img src="./cards/activity.svg" width="100%" alt="Contributions per month over the last 12 months, with streak and busiest day" /><br>
{tiles}<br>
<img src="./cards/title-coding.svg" width="100%" alt="{esc(' '.join(CFG['coding_title']))}" /><br>
{repos}
</div>

<p align="center">
{links}
</p>
"""


def preview(row1: list[Card], row2: list[Card]) -> str:
    """Local stand-in for the GitHub README column (~830px), for checking and screenshots."""
    def img(card: Card, width: str) -> str:
        return f'<img src="cards/{card.name}.svg" width="{width}">'
    row = lambda cards: " ".join(img(cd, "32.5%") for cd in cards)
    return ("<!doctype html><meta charset=utf-8><title>Profile preview</title>"
            "<style>body{margin:0;background:#fff;font:14px system-ui}@media (prefers-color-scheme:dark){body{background:#0d1117}}"
            "main{max-width:830px;margin:32px auto;padding:0 16px;text-align:center}img{vertical-align:top}</style><main>"
            f"<img src='cards/hero.svg' width='100%'><br><img src='cards/heatmap.svg' width='100%'><br>"
            f"<img src='cards/activity.svg' width='100%'><br>{row(row1)}<br>"
            f"<img src='cards/title-coding.svg' width='100%'><br>{row(row2)}</main>\n")


def main() -> None:
    CARDS.mkdir(exist_ok=True)
    for old in CARDS.glob("*.svg"):
        old.unlink()
    row1 = [tile(i, t) for i, t in enumerate(CFG["tiles"], 1)]
    row2 = [repo(k, item) for k, item in enumerate(CFG["featured"])]
    for card in [hero(), heatmap(), activity(), *row1, coding_title(), *row2]:
        path = card.save()
        print(f"cards/{path.name:<28} {path.stat().st_size / 1024:5.0f} KB")
    (ROOT / "README.md").write_text(readme(row1, row2), encoding="utf-8")
    (ROOT / "preview.html").write_text(preview(row1, row2), encoding="utf-8")
    print("README.md, preview.html" + ("  (STATIC: final frame, no animation)" if STATIC else ""))


if __name__ == "__main__":
    main()
