"""A review of the spacing between every pair of characters, in the fonts as they are built.

    python tools/kerning_review.py measure     # every pair in every style, about ten minutes
    python tools/kerning_review.py             # the report, into build/kerning/report.md

`measure` shapes every pair of CHARS with HarfBuzz in each style (so kerning and contextual forms are
those a reader gets), and takes three numbers per pair, in thousandths of the em:

    gap      the narrowest horizontal distance between the two letters
    near     the narrowest distance in any direction (a tip over a shoulder, a quote above a letter)
    white    the average white between them over the height they share, counting no deeper than DEPTH
             into either letter: what the eye reads as the space between two letters

It measures the release and, beside it, a reference: the same source built with every design
operation off, so only scaled to the width. The report lists where the two differ:

    what the recipe changed   split into what belongs to a letter's side (it was redrawn) and what is
                              left over for the pair (its kerning moved)
    closer than regular       pairs that lose much of their share of the spacing at a narrow or wide width
    italic and roman          the italic sets as wide as the roman, so its pairs should match
    closest pairs             under 0.3 of a stem now and not in the reference

Italic outlines are sheared upright before measuring, so distances are taken along the slant.
Results are kept in build/kerning/ (one file per style); the report and the workbench's Proofs tab
read them from there.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import uharfbuzz as hb
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "kerning"
RELEASE = ROOT / "releases" / "Raveo-2.0" / "desktop" / "variable"

UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "ÁČĎÉĚÍŇÓŘŠŤÚŮÝŽÄÖÜĽĹŔÔĄĆĘŁŃŚŹŻÀÂÇÈÊËÎÏÙÛŒÆÑØÅŐŰȘĞİŞ"
LOWER = "abcdefghijklmnopqrstuvwxyz" "áčďéěíňóřšťúůýžäöüßľĺŕôąćęłńśźżàâçèêëîïùûœæñøåőűșğış"
DIGIT = "0123456789"
STOPS = ".,:;!?…"
QUOTE = "‘’“”‚„'\"«»‹›"
OTHER = "()[]{}-–—/\\*@&%+=·•°€$#_|"
CHARS = UPPER + LOWER + DIGIT + STOPS + QUOTE + OTHER
KIND = {**{c: "U" for c in UPPER}, **{c: "l" for c in LOWER}, **{c: "0" for c in DIGIT}, **{c: "." for c in STOPS},
        **{c: "q" for c in QUOTE}, **{c: "x" for c in OTHER}}

BAND, Y0, Y1 = 8, -320, 1120          # height of a band and the range covered, in thousandths of the em
BANDS = (Y1 - Y0) // BAND
REACH = 12                            # `near` looks this many bands up and down
DEPTH = 0.2                           # `white` counts no deeper into a letter than this share of the x-height
WEIGHTS = (300, 400, 520, 900)
WIDTHS, SIZES = (60, 100, 125), (14, 32)


class Dense:
    """Collects a glyph's outline as closely spaced points."""

    def __init__(self, step):
        self.step, self.xs, self.ys, self.at, self.start = step, [], [], None, None

    def _add(self, xs, ys):
        self.xs.append(xs)
        self.ys.append(ys)

    def moveTo(self, p):
        self.at = self.start = p

    def lineTo(self, p):
        a, n = self.at, max(1, int(math.hypot(p[0] - self.at[0], p[1] - self.at[1]) / self.step))
        t = np.linspace(0.0, 1.0, n + 1)
        self._add(a[0] + (p[0] - a[0]) * t, a[1] + (p[1] - a[1]) * t)
        self.at = p

    def qCurveTo(self, *points):
        points, a = list(points), self.at
        while len(points) > 2:                              # consecutive off-curve points imply one between them
            c, nxt = points.pop(0), points[0]
            mid = ((c[0] + nxt[0]) / 2, (c[1] + nxt[1]) / 2)
            self._quad(a, c, mid)
            a = mid
        self._quad(a, points[0], points[1])
        self.at = points[1]

    def _quad(self, a, c, b):
        n = max(4, int((math.hypot(c[0] - a[0], c[1] - a[1]) + math.hypot(b[0] - c[0], b[1] - c[1])) / self.step))
        t = np.linspace(0.0, 1.0, n + 1)
        self._add((1 - t) ** 2 * a[0] + 2 * t * (1 - t) * c[0] + t * t * b[0], (1 - t) ** 2 * a[1] + 2 * t * (1 - t) * c[1] + t * t * b[1])

    def curveTo(self, c1, c2, b):
        a = self.at
        n = max(4, int((math.hypot(c1[0] - a[0], c1[1] - a[1]) + math.hypot(c2[0] - c1[0], c2[1] - c1[1])
                        + math.hypot(b[0] - c2[0], b[1] - c2[1])) / self.step))
        t = np.linspace(0.0, 1.0, n + 1)
        self._add(*((1 - t) ** 3 * a[k] + 3 * t * (1 - t) ** 2 * c1[k] + 3 * t * t * (1 - t) * c2[k] + t ** 3 * b[k] for k in (0, 1)))
        self.at = b

    def closePath(self):
        if self.at != self.start:
            self.lineTo(self.start)

    def endPath(self):
        pass


def measure(path, location):
    """Every pair of CHARS in one style of one font: a dict of (n, n) arrays and the style's own measures."""
    path = Path(path)
    face = hb.Face(path.read_bytes())
    font = hb.Font(face)
    font.set_variations({k: v for k, v in location.items() if k in {a.tag for a in face.axis_infos}})
    unit = 1000 / face.upem
    lean = math.tan(math.radians(-TTFont(path, lazy=True)["post"].italicAngle))

    n = len(CHARS)
    first, second, pen = np.zeros((n, n), np.int32), np.zeros((n, n), np.int32), np.full((n, n), np.nan, np.float32)
    for i, a in enumerate(CHARS):
        for j, b in enumerate(CHARS):
            buf = hb.Buffer()
            buf.add_str(a + b)
            buf.direction, buf.script, buf.language = "LTR", "Latn", "en"
            hb.shape(font, buf)
            if len(buf.glyph_infos) == 2:                   # not a ligature
                (g1, g2), (p1, p2) = buf.glyph_infos, buf.glyph_positions
                first[i, j], second[i, j] = g1.codepoint, g2.codepoint
                pen[i, j] = (p1.x_advance + p2.x_offset - p1.x_offset) * unit

    gids = sorted(set(first.ravel()) | set(second.ravel()))
    slot = {gid: k for k, gid in enumerate(gids)}
    left = np.full((len(gids), BANDS), np.inf, np.float32)          # leftmost ink per band
    right = np.full((len(gids), BANDS), -np.inf, np.float32)        # rightmost ink per band
    advance = np.zeros(len(gids), np.float32)
    for gid, k in slot.items():
        advance[k] = font.get_glyph_h_advance(gid) * unit
        dense = Dense(2 / unit)
        font.draw_glyph_with_pen(gid, dense)
        if not dense.xs:
            continue
        y = np.concatenate(dense.ys) * unit
        x = np.concatenate(dense.xs) * unit - y * lean              # an italic is measured along its slant
        band = np.floor((y - Y0) / BAND).astype(int)
        keep = (band >= 0) & (band < BANDS)
        np.minimum.at(left[k], band[keep], x[keep])
        np.maximum.at(right[k], band[keep], x[keep])
    first, second = np.vectorize(slot.get)(first), np.vectorize(slot.get)(second)
    inked = np.isfinite(left)
    rows = np.arange(BANDS)
    bottom = np.where(inked.any(axis=1), np.argmax(inked, axis=1), BANDS)
    top = np.where(inked.any(axis=1), BANDS - 1 - np.argmax(inked[:, ::-1], axis=1), -1)

    def one(ch):
        return slot[font.get_nominal_glyph(ord(ch))]
    base = (0 - Y0) // BAND
    x_top, tall_top, low = int(top[one("x")]), int(top[one("l")]), int(bottom[one("p")])
    x_height = (x_top + 1) * BAND + Y0
    stem = float((right[one("l")] - left[one("l")])[(base + x_top) // 2])
    deep, high = bottom < base - 4, top > x_top + 6                 # reaches below the baseline, above the x-height

    # `white`: per glyph and zone, the white on each side, counted DEPTH deep at most.
    depth = DEPTH * x_height
    to_right = np.zeros((len(gids), 4), np.float32)
    to_left = np.zeros((len(gids), 4), np.float32)
    for zone in range(4):                                           # bit 0: down to the descender, bit 1: up to the ascender
        span = slice(low if zone & 1 else base, (tall_top if zone & 2 else x_top) + 1)
        r, l = right[:, span], left[:, span]
        edge_r = np.where(np.isfinite(r).any(axis=1), np.where(np.isfinite(r), r, -np.inf).max(axis=1), right.max(axis=1))
        edge_l = np.where(np.isfinite(l).any(axis=1), np.where(np.isfinite(l), l, np.inf).min(axis=1), left.min(axis=1))
        to_right[:, zone] = advance - edge_r + np.minimum(edge_r[:, None] - r, depth).mean(axis=1)
        to_left[:, zone] = edge_l + np.minimum(l - edge_l[:, None], depth).mean(axis=1)
    zone = (deep[first] | deep[second]) * 1 + (high[first] | high[second]) * 2
    kern = pen - advance[first]
    white = to_right[first, zone] + to_left[second, zone] + kern

    # `gap` and `near`: from the side profiles of the two glyphs, band against band.
    after = (advance[:, None] - right)[first]                       # (n, n, bands): from the left glyph's ink to its advance
    before = left[second]
    gap = (after + before).min(axis=2) + kern
    near = np.full((n, n), np.inf, np.float32)
    for shift in range(-REACH, REACH + 1):
        a = after[:, :, max(0, -shift): BANDS - max(0, shift)]
        b = before[:, :, max(0, shift): BANDS - max(0, -shift)]
        across = a + b + kern[:, :, None]
        if shift:
            across = np.hypot(np.clip(across, 0, None), (abs(shift) - 1) * BAND)
        near = np.minimum(near, np.nan_to_num(across, nan=np.inf, posinf=np.inf).min(axis=2))
    gap[~np.isfinite(gap)] = np.nan
    near[~np.isfinite(near)] = np.nan
    return {"white": white.astype(np.float32), "gap": gap.astype(np.float32), "near": near.astype(np.float32),
            "kern": kern.astype(np.float32), "stem": stem, "x_height": float(x_height)}


def key(family, italic, size, weight, width):
    return f"{family}-{'italic' if italic else 'roman'}-{size}-{weight}-{width}"


def reference():
    """The family built from the source with every design operation off (width scaling only): what 2.0 is
    measured against. Built once into build/kerning/plain*/."""
    sys.path.insert(0, str(ROOT / "tools"))
    from pipeline import PLAIN, Pipeline, build, load_recipe
    paths = {}
    for italic in (False, True):
        folder = OUT / ("plain-italic" if italic else "plain")
        paths[italic] = folder / ("Raveo-Italic-VF.ttf" if italic else "Raveo-VF.ttf")
        if not paths[italic].exists():
            build(Pipeline(italic=italic), {**load_recipe(), **PLAIN}, folder)
    return paths


def measure_all(only=None):
    OUT.mkdir(parents=True, exist_ok=True)
    plain, jobs = reference(), []
    for italic in (False, True):
        for size in SIZES:
            for weight in WEIGHTS:
                for width in WIDTHS:
                    location = {"opsz": size, "wght": weight, "wdth": width}
                    jobs.append((key("raveo", italic, size, weight, width), RELEASE / ("Raveo-Italic-VF.ttf" if italic else "Raveo-VF.ttf"), location))
                    jobs.append((key("plain", italic, size, weight, width), plain[italic], location))
    jobs = [job for job in jobs if only is None or job[0].startswith(only)]
    for k, (name, path, location) in enumerate(jobs):
        result = measure(path, location)
        np.savez_compressed(OUT / f"{name}.npz", **result)
        print(f"{k + 1:>3}/{len(jobs)}  {name}  stem {result['stem']:.0f}  n-n {result['gap'][CHARS.index('n'), CHARS.index('n')]:.0f}", flush=True)


def load(*parts):
    data = np.load(OUT / f"{key(*parts)}.npz")
    return {name: data[name] for name in data.files}


def polish(table):
    """Splits a table of differences into what belongs to each first letter, each second letter, and the pair.

    A letter redrawn wider on its right moves every pair it starts by the same amount: that is its
    side, not its kerning. What is left over after both sides are taken out is the pair's own.
    """
    row, col = np.zeros(table.shape[0]), np.zeros(table.shape[1])
    with np.errstate(all="ignore"):
        for _ in range(4):
            row = np.nan_to_num(np.nanmedian(table - col[None, :], axis=1))
            col = np.nan_to_num(np.nanmedian(table - row[:, None], axis=0))
    return row, col, table - row[:, None] - col[None, :]


CORE = [k for k, c in enumerate(CHARS) if c.isascii() or c in STOPS + QUOTE + OTHER]


def compare(pairs, what="white", scale=False):
    """Differences between styles, weight by weight: `pairs` is [(style a, style b)], both loaded.

    Returns the median over the weights of each letter's side as first and as second letter, and of
    each pair's remainder, with the two styles' own numbers for the lists.
    """
    rows, cols, rest = [], [], []
    for a, b in pairs:
        ta, tb = a[what][np.ix_(CORE, CORE)], b[what][np.ix_(CORE, CORE)]
        if scale:                                       # as shares of the style's own n-n
            n = CORE.index(CHARS.index("n"))
            ta, tb = 100 * ta / ta[n, n], 100 * tb / tb[n, n]
        row, col, left_over = polish(ta - tb)
        rows.append(row), cols.append(col), rest.append(left_over)
    with np.errstate(all="ignore"):
        return np.median(rows, axis=0), np.median(cols, axis=0), np.nanmedian(rest, axis=0)


def grouped(a, b, rest, least, most=28):
    """Pairs whose own difference is `least` or more, gathered by first letter and size of the difference."""
    groups = {}
    for i, j in zip(*np.where(np.abs(np.nan_to_num(rest)) >= least)):
        ci, cj = CORE[i], CORE[j]
        step = lambda value: int(round(float(value) / 4)) * 4
        groups.setdefault((CHARS[ci], step(rest[i, j]), step(a["kern"][ci, cj] - b["kern"][ci, cj])), []).append(
            (CHARS[cj], a["near"][ci, cj], b["near"][ci, cj]))
    lines = []
    for (first, own, kern), seconds in sorted(groups.items(), key=lambda item: -abs(item[0][1]) * len(item[1]))[:most]:
        now, was = (np.nanmedian([x[k] for x in seconds]) for k in (1, 2))
        lines.append(f"  {first} + {''.join(x[0] for x in seconds):<36} white {own:+4d}  kern {kern:+4d}  nearest {was:4.0f} -> {now:4.0f}")
    return lines + [f"  ({len(groups)} groups, {sum(len(v) for v in groups.values())} pairs)"]


def section(title, pairs, side=5.0, own=8.0):
    row, col, rest = compare(pairs)
    a, b = pairs[len(pairs) // 2]                       # a middle weight, for the numbers shown beside
    order = np.argsort(-(np.abs(row) + np.abs(col)))
    sides = "  ".join(f"{CHARS[CORE[k]]} {row[k]:+.0f}/{col[k]:+.0f}" for k in order if abs(row[k]) > side or abs(col[k]) > side)
    return [f"\n### {title}\n", "Sides (white after the letter / before it): " + (sides or "none over the limit"), "",
            "Pairs, beyond what their letters' sides explain:"] + grouped(a, b, rest, own)


def closeness(style, wide, regular):
    """Pairs that stand much closer at one width than at the regular one, both taken against n-n."""
    n = CHARS.index("n")
    here, there = wide["near"] / wide["gap"][n, n], regular["near"] / regular["gap"][n, n]
    with np.errstate(all="ignore"):
        ratio = here / there
    out = []
    for i, j in zip(*np.where((ratio < 0.6) & (here < 0.7) & (wide["near"] > 0))):
        if i in CORE and j in CORE:
            out.append((float(ratio[i, j]), CHARS[i] + CHARS[j], float(wide["near"][i, j]), float(regular["near"][i, j])))
    return [f"  {pair} {now:.0f} (regular {was:.0f}, {100 * ratio:.0f} % of its share)" for ratio, pair, now, was in sorted(out)[:24]] + \
           [f"  ({len(out)} pairs in {style})"]


def findings(italic, size, weight, width, most=72):
    """For the workbench: in the measured style nearest to the one asked for, the pairs the recipe moved
    most and the letter pairs that stand closest. None before anything has been measured."""
    style = (bool(italic), 14 if size == 14 else 32, min(WEIGHTS, key=lambda w: abs(w - weight)), min(WIDTHS, key=lambda x: abs(x - width)))
    try:
        raveo, plain = load("raveo", *style), load("plain", *style)
    except FileNotFoundError:
        return None
    number = lambda value: None if not np.isfinite(value) else int(round(float(value)))
    entry = lambda i, j, **more: {"pair": CHARS[i] + CHARS[j], "kern": [number(raveo["kern"][i, j]), number(plain["kern"][i, j])],
                                  "nearest": [number(raveo["near"][i, j]), number(plain["near"][i, j])], **more}
    _, _, rest = polish((raveo["white"] - plain["white"])[np.ix_(CORE, CORE)])
    moved = []
    for flat in np.argsort(-np.abs(np.nan_to_num(rest)), axis=None)[:most]:
        i, j = divmod(int(flat), len(CORE))
        if abs(rest[i, j]) >= 8:
            moved.append(entry(CORE[i], CORE[j], white=number(rest[i, j])))
    text = [c for c in CORE if KIND[CHARS[c]] in "Ul0"]
    share = raveo["near"][np.ix_(text, text)] / raveo["stem"]
    closest = []
    for flat in np.argsort(np.nan_to_num(share, nan=99.0), axis=None)[:most]:
        i, j = divmod(int(flat), len(text))
        closest.append(entry(text[i], text[j], stems=round(float(share[i, j]), 2)))
    return {"style": {"italic": style[0], "opsz": style[1], "wght": style[2], "wdth": style[3]}, "stem": number(raveo["stem"]),
            "moved": moved, "closest": closest}


def report():
    out = ["# Kerning review", "",
           "Raveo 2.0 as released, against the same source built with every design operation off.",
           "Numbers are thousandths of the em. \"White\" is the average white between two letters,",
           "\"nearest\" the narrowest distance in any direction.", ""]
    for italic in (False, True):
        for size in SIZES:
            out.append(f"\n## {'Italic' if italic else 'Roman'}, {'text' if size == 14 else 'Display'}")
            raveo = {(w, x): load("raveo", italic, size, w, x) for w in WEIGHTS for x in WIDTHS}
            plain = {(w, x): load("plain", italic, size, w, x) for w in WEIGHTS for x in WIDTHS}
            for width in WIDTHS:
                out += section(f"What the recipe changed, width {width}", [(raveo[w, width], plain[w, width]) for w in WEIGHTS])
            for width in (60, 125):
                out.append(f"\n### Closer at width {width} than at the regular width\n")
                out += closeness(f"{WEIGHTS[1]}/{width}", raveo[WEIGHTS[1], width], raveo[WEIGHTS[1], 100])
            if italic:
                roman = {w: load("raveo", False, size, w, 100) for w in WEIGHTS}
                out += section("Italic against the roman, regular width", [(raveo[w, 100], roman[w]) for w in WEIGHTS])
            out.append("\n### Closest pairs: under 0.3 of a stem now, not so in the reference\n")
            fresh = {}
            for (w, x), style in raveo.items():
                near, was, floor = style["near"], plain[w, x]["near"], 0.3 * style["stem"]
                for i, j in zip(*np.where((near < floor) & ~(was < floor) & ~(near > was - 2))):
                    if i in CORE and j in CORE:
                        pair = CHARS[i] + CHARS[j]
                        share = float(near[i, j] / style["stem"])
                        if pair not in fresh or share < fresh[pair][0]:
                            fresh[pair] = (share, w, x, float(was[i, j] / style["stem"]))
            out.append("  ".join(f"{pair} {share:.2f} (was {was:.2f}) at {w}/{x}" for pair, (share, w, x, was) in sorted(fresh.items(), key=lambda kv: kv[1])[:60])
                       or "none")
            out.append(f"  ({len(fresh)} pairs)")
    (OUT / "report.md").write_text("\n".join(out) + "\n")
    print(f"{OUT / 'report.md'}: {len(out)} lines")


if __name__ == "__main__":
    if sys.argv[1:2] == ["measure"]:
        measure_all(*sys.argv[2:3])
    else:
        report()
