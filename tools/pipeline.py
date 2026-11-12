"""Raveo pipeline: Raveo source + recipe -> optical size x weight x width variable font.

    python tools/pipeline.py                         # build with recipe.json
    python tools/pipeline.py --set terminal_angle=30 --set squareness.condensed=0.5
    python tools/pipeline.py --report                # curve-quality numbers, no build

Everything is derived from Raveo's own masters (weights 100/400/900, drawn at optical size 32 for
headlines and at 14 for text) by arithmetic on the source points, so kerning, anchors, composites
and OpenType features carry over. A recipe that names one optical size builds from the headline
drawings only; with two, the text drawings get the same design operations, with the settings under
"text" laid over the others.

Width (Tim Ahrens' compensated scaling): to scale a glyph horizontally by `s` without the vertical
stems changing, x coordinates are taken from a different weight of the same design, picked so that
its stems land where we want after scaling; y coordinates stay at the target weight.

    stem(w_x) * s = stem(w) * s**stem_adapt   ->   w_x = stem_inverse(stem(w) * s**(stem_adapt - 1))

The curve operations applied afterwards live in curves.py.
"""
import argparse
import copy
import json
import math
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
from fontTools import subset
from fontTools.ttLib import TTFont
from glyphsLib import GSFont
from glyphsLib.classes import (GSAnchor, GSAxis, GSComponent, GSCustomParameter, GSInstance,
                               GSLayer, GSNode, GSPath)
from glyphsLib.types import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagonals  # noqa: E402
import bowl  # noqa: E402
import leg  # noqa: E402
import wordmark  # noqa: E402
from spacing import Clearance, floor_for, pair_shortfall, profile, reach_over, shortfalls  # noqa: E402
from curves import (GlyphData, curve_report, cut_terminals, fix_smooth, harmonize, lerp3,  # noqa: E402
                    logo_r, shift_middle, slant_compensate, square_arcs)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "sources" / "raveo.glyphspackage"
ITALIC_SOURCE = ROOT / "sources" / "raveo-italic.glyphs"
# Glyphs that hold a round dot of their own (i, j, the colon and their kin are built from these).
DOTTED = {"period", "exclam", "exclamdown", "exclamdown.case", "question", "divide", "dieresiscomb", "dotaccentcomb",
          "periodcentered", "bullet", "i.sups", "j.sups", "i.numr"}
CURSIVE = ".cursive"                    # in the italic source: Inter's written form of a letter, beside Raveo's upright one       # made by tools/make_italic.py
BUILD = ROOT / "build"
FONTC = Path(sys.executable).parent / "fontc"

SRC_MASTERS = ("m011", "m007", "m009")      # Raveo's display masters: Thin, Regular, Black
TEXT_MASTERS = ("B1F27B51-9973-4381-9301-4FE46FE1CA59", "C698F293-3EC0-4A5A-A3A0-0FDB1F5CF265",
                "5C20EF92-B63D-42A8-8878-93C2863E0093")      # the same three weights, drawn for text
DISPLAY, TEXT = 32, 14                      # the optical sizes those two sets are drawn for
# Raveo maps public weight values to internal ones (its named weights are darker than Inter's).
USER_TO_INTERNAL = ((100, 100), (400, 400), (500, 490), (600, 580), (700, 670), (800, 780), (900, 900))
STYLE_NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 520: "Medium", 630: "SemiBold",
               730: "Bold", 850: "ExtraBold", 900: "Black"}        # the weights Raveo 1.1 names

# Glyphs the kerning guard looks at: English, Czech, Slovak and German letters, figures, punctuation.
GUARD_CHARS = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
               "ÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýž"
               "ÄÖÜäöüßĽľĹĺŔŕÔô"
               "ĄąĆćĘęŁłŃńŚśŹźŻż" "ĀāĒēĢģĪīĶķĻļŅņŪūĖėĮįŲų" "ŐőŰűĂăÂâÎîȘșȚțĞğİıŞşÇç"
               "ÅåÆæØøÐðÞþÀàÈèÊêËëÏïŒœÙùÛûŸÿ" "ÑñÃãÕõÌìÒò¿¡"
               ".,:;!?…-–—()[]/&@%+=€$#*„“”‚‘’\"'«»‹›")

# European Latin: Western, Nordic, Central (Czech, Slovak, Polish, Hungarian), Baltic, Romanian, Turkish.
PUNCTUATION = [(0x2010, 0x2044), (0x20AC, 0x20AC), (0x2116, 0x2116), (0x2122, 0x2122), (0x2190, 0x2199),
               (0x2212, 0x2212), (0x2248, 0x2248), (0x2260, 0x2265), (0x202F, 0x202F)]
SUBSETS = {
    "european": [(0x20, 0x7E), (0xA0, 0x17F), (0x218, 0x21B), (0x1E9E, 0x1E9E)] + PUNCTUATION,
    "latin": [(0x20, 0x7E), (0xA0, 0xFF), (0x152, 0x153), (0x2C6, 0x2C6), (0x2DA, 0x2DC)] + PUNCTUATION,
}
SUBSETS["czech"] = SUBSETS["european"]           # older recipes used this name
SUBSETS["full"] = None                           # every character Raveo exports
SUBSETS["latin-ext"] = [r for r in SUBSETS["european"] if r not in SUBSETS["latin"]] + [(0x100, 0x151), (0x154, 0x17F)]

# Settings given separately for the narrowest width, the normal width (Raveo itself) and the widest.
POLE_KEYS = ("stem_adapt", "contrast", "contrast_follow", "terminal_angle", "aperture", "terminal_soften", "squareness",
             "smoothing", "spacing", "logo_r", "logo_a")
ROUND_TICK = "commaaccentright.cn.round"                    # the d-caron tick drawn like the round comma
CARON_GLYPHS = ("dcaron", "tcaron", "lcaron", "Lcaron")     # letters with the upright tick: ď ť ľ Ľ
RESHAPED = ("r", "a", "R")                                  # letters whose advance the design operations change
TICK_ABOVE = 40                                             # the tick is what these letters have this far above the x-height
QUOTES = ("quoteright", "quoteleft", "quotereversed", "quotesinglbase",
          "quotedblright", "quotedblleft", "quotedblreversed", "quotedblbase")


def poles(condensed, normal, wide):
    return {"condensed": condensed, "normal": normal, "wide": wide}


DEFAULT_RECIPE = {
    "family": "Raveo Headline",
    "weights": [300, 400, 800],
    "widths": [75, 100, 125],                 # narrowest, normal, widest
    "optical_sizes": [DISPLAY],               # [32] = headline drawings only; [14, 32] adds the text size
    # The styles that get a name: fonts list them in their menus and each is exported as a static file.
    # [[position on the axis, name], ...]; null = Raveo's nine weights, and the two ends of the width axis.
    "named_weights": None,
    "named_widths": None,
    "text": {},                               # settings that differ at the text size, e.g. {"spacing": ...}
    "stem_adapt": poles(0.25, 0.25, 0.25),    # 0 = stems identical at every width, 1 = stems scale with width
    "contrast": poles(0, 0, 0),               # weight offset for horizontal strokes; negative = thinner
    "contrast_follow": poles(0, 0, 0),        # 0 = horizontals keep the weight's own thickness at every width,
                                              # 1 = they thin and thicken with the stems, as in a regular width of the same stem
    "weight_spread": False,                   # true = a narrow width that cannot reach the heaviest weight spreads what it
                                              # can reach over the whole axis; false = it only compresses the part above Regular
    "terminal_angle": poles(25, 25, 25),      # degrees between the cut of a curved stroke end and level (or upright)
    "aperture": poles(0, 0, 0),               # units the cut moves back along the stroke; positive opens c e s a
    "terminal_soften": poles(0.5, 0.5, 0.5),  # 0 = raw slanted cut, 1 = the stroke turns to meet the cut squarely
    "squareness": poles(0.35, 0.0, 0.0),      # tension of rounds: positive squarer, negative rounder
    "smoothing": poles(1.0, 0.0, 1.0),        # 0..1 curvature smoothing at smooth points
    "spacing": poles(0, 0, 0),                # units added on each side of every glyph
    "light_spacing": 1.0,                     # share of the spacing change the Thin keeps; grows to all of it at Regular
    "spacing_by_side": False,                 # true = a letter takes the spacing change in proportion to the room each side has
    "punctuation_spacing": 0.4,               # share of the spacing change that punctuation and symbols take
    "kerning_floor": {"letters": 0.1, "punctuation": 0.24},   # smallest gap between neighbours, in stems
    "max_source_weight": 930,                 # how far past Raveo Black (900) narrow bold styles may reach
    "caron_gap": 0.32,                        # distance of the tick in ď ť ľ Ľ from its letter, in stems; null = as drawn
    "caron_tuck": 0.6,                        # share of the tick's width a following small letter slides under
    "caron_floor": None,                      # smallest distance from the tick to whatever follows, in stems of the Regular
                                              # (the same at every weight); null = as other letters
    "diagonals": 1.0,                         # 0..1: diagonal strokes keep their weight relative to the stems
    "diagonal_taper": 0.05,                   # how much lighter than that diagonals get at the narrowest width
    "logo_r": poles(0.4, 0.75, 0.75),         # 0 = Raveo's r, 1 = the wordmark's r, with a longer and flatter arm
    "logo_r_over": 0,                         # share of its extra length by which the wordmark r's arm reaches over a round
                                              # letter after it (o, e, a); 0 = the arm's tip stays as far away as Raveo's did
    "logo_a": poles(0.4, 0.0, 0.8),           # 0 = Raveo's a, 1 = the wordmark's a, with a more open bowl
    "leg": False,                             # true = the leg of the R is laid out again at every width (tools/leg.py)
    "round_commas": True,                     # round quotes, commas and the tick of ď ť ľ Ľ; false = square
    # The italic only. "upright" = Raveo's roman letter leaned over, "cursive" = the letter as Inter's italic writes it:
    # a with one storey, e with a curved bar, f with a tail below the line. Everything built on them follows.
    "italic_forms": {"a": "upright", "e": "upright", "f": "upright"},
    "italic_round_dots": True,                # round dots stay round in the italic instead of leaning into ovals
    "slant_compensation": 0.66,               # leaned-over letters get the stroke correction of a drawn italic: share of what
                                              # the slant adds to or takes from a curved stroke that is taken back (Inter: 0.66)
    "square_dots": False,                     # false = round dots, matching the round commas
    # Letters whose curved stroke ends are re-cut. f, j, t and y are left out on purpose: their hooks keep Raveo's upright cut.
    "terminal_glyphs": ["a", "c", "e", "g", "r", "s", "C", "G", "S",
                        "two", "three", "five", "six", "nine", "question", "ae"],
    "terminal_skip": {"a": [0]},              # the tail of Raveo's a keeps its upright cut
    # Per-letter adjustments: middle (bar height), tension, smoothing, and for stroke ends
    # angle (degrees), arm (units longer) and corners (0..1).
    "glyphs": {},
    "subset": "european",
    "version": "1.000",
    "copyright": "",                          # empty = keep the notice from the Raveo source
    "designer": "",                           # empty = keep the credit from the Raveo source
}
PLAIN = {"square_dots": False, "round_commas": False, "diagonals": 0, "glyphs": {}, "caron_gap": None, "kerning_floor": None,
         "slant_compensation": 0,
         "text": {},
         **{k: poles(0, 0, 0) for k in POLE_KEYS if k != "stem_adapt"}}


def normalize(recipe):
    """Accept older recipes where a per-width setting was a single number."""
    for key in POLE_KEYS:
        if not isinstance(recipe[key], dict):
            recipe[key] = poles(recipe[key], recipe[key], recipe[key])
    return recipe


def load_recipe(path=None, overrides=None):
    recipe = copy.deepcopy(DEFAULT_RECIPE)
    path = Path(path) if path else ROOT / "recipe.json"
    if path.exists():
        recipe.update(json.loads(path.read_text()))
    normalize(recipe)
    for key, value in (overrides or {}).items():
        if "." in key:
            outer, inner = key.split(".", 1)
            recipe[outer][inner] = value
        else:
            recipe[key] = value
    return normalize(recipe)


def at_size(recipe, opsz):
    """The recipe as it applies at one optical size: at the text size, "text" is laid over the rest."""
    if opsz != TEXT or not recipe.get("text"):
        return recipe
    out = {**recipe, **{k: v for k, v in recipe["text"].items() if k != "glyphs"}}
    letters = {name: dict(values) for name, values in recipe.get("glyphs", {}).items()}
    for name, values in recipe["text"].get("glyphs", {}).items():
        letters.setdefault(name, {}).update(values)
    out["glyphs"] = letters
    return normalize(out)


def style_weights(recipe):
    """{weight: name} for the named weights inside the recipe's weight range, lightest first."""
    lo, hi = recipe["weights"][0], recipe["weights"][-1]
    if recipe.get("named_weights"):
        names = {int(w): str(n) for w, n in recipe["named_weights"] if lo <= w <= hi and n}
        names.setdefault(400, "Regular")      # the default style always has a name
        return dict(sorted(names.items()))
    names = {w: n for w, n in STYLE_NAMES.items() if lo <= w <= hi}
    if hi < 850:                              # a family that stops short of Black ends on ExtraBold
        names[hi] = "ExtraBold"
    return names


def style_widths(recipe):
    """{width: name} for the named widths inside the recipe's width range, narrowest first; 100 has no name."""
    lo, hi = recipe["widths"][0], recipe["widths"][-1]
    names = {int(w): str(n) for w, n in recipe.get("named_widths") or ((lo, "Narrow"), (hi, "Wide")) if lo <= w <= hi and n and w != 100}
    return dict(sorted({**names, 100: ""}.items()))


def to_internal(user_weight):
    pts = USER_TO_INTERNAL
    for (u0, i0), (u1, i1) in zip(pts, pts[1:]):
        if user_weight <= u1 or (u1, i1) == pts[-1]:
            return i0 + (user_weight - u0) * (i1 - i0) / (u1 - u0)


def by_width(setting, width, widths):
    """A per-width setting given at the condensed / normal / wide poles, interpolated between them."""
    narrow, _, wide = widths
    if width <= 100:
        return setting["normal"] + (100 - width) / (100 - narrow) * (setting["condensed"] - setting["normal"])
    return setting["normal"] + (width - 100) / (wide - 100) * (setting["wide"] - setting["normal"])


def style_name(weight, width, display=False, names=STYLE_NAMES, widths=None, italic=False):
    label = widths[width] if widths and width in widths else "Narrow" if width < 100 else "Wide" if width > 100 else ""
    if italic:                                # "Italic", "Bold Italic", "Narrow Italic": the Regular gives up its name
        weight_name = names.get(weight, str(weight))
        parts = ["Display" if display else "", label, "" if weight_name == "Regular" else weight_name, "Italic"]
        return " ".join(part for part in parts if part)
    return f"{'Display' if display else ''} {label} {names.get(weight, str(weight))}".replace("  ", " ").strip()


def _same_structure(a, b):
    return a.x.shape == b.x.shape and a.anchor_names == b.anchor_names and len(a.shapes) == len(b.shapes) and all(
        s[0] == t[0] and (s[0] != "path" or [k[0] for k in s[2]] == [k[0] for k in t[2]]) for s, t in zip(a.shapes, b.shapes))


def _leaves(data, name, m, tx=0.0, ty=0.0, ax=1, ay=1):
    """The drawn glyphs a composite is made of at master `m`: (name, x, y, x direction, y direction).

    Directions are 1 or -1: Raveo's quotes are only ever shifted, mirrored or turned half round.
    """
    glyph = data[name]
    if glyph.n_nodes:
        return [(name, tx, ty, ax, ay)]
    out = []
    for (i, child), part in zip(glyph.comp_idx, (s[1] for s in glyph.shapes if s[0] == "comp")):
        turned = -1 if round(part.rotation) % 360 == 180 else 1
        cx, cy = (-1 if part.scale[0] < 0 else 1) * turned, (-1 if part.scale[1] < 0 else 1) * turned
        out += _leaves(data, child, m, tx + ax * float(glyph.x[m][i]), ty + ay * float(glyph.y[m][i]), ax * cx, ay * cy)
    return out


def _ink(data, leaf, m):
    """Left and right edge of one placed drawing."""
    name, tx, _, ax, _ = leaf
    xs = data[name].x[m][data[name].node_idx]
    return (tx + xs.min(), tx + xs.max()) if ax > 0 else (tx - xs.max(), tx - xs.min())


def fit_quote(data, name):
    """The round quote for `name`, set in the room Raveo gives its own quote.

    Raveo sets its quotes tighter than Inter does, but only the square ones it uses; the round set
    still has Inter's side bearings. This keeps the round drawing and takes the square quote's side
    bearings and, for double quotes, the gap between the two marks.
    """
    xs, ys, widths, parts = [], [], [], None
    for m in range(3):
        square = sorted((_ink(data, leaf, m) for leaf in _leaves(data, name, m)))
        leaves = sorted(_leaves(data, name + ".ss03", m), key=lambda leaf: _ink(data, leaf, m))
        if len(square) != len(leaves):
            return None
        row_x, row_y, pen = [], [], square[0][0]
        for k, leaf in enumerate(leaves):
            left, right = _ink(data, leaf, m)
            if k:
                pen += square[k][0] - square[k - 1][1]          # the gap Raveo leaves between the marks
            row_x.append(leaf[1] + pen - left)
            row_y.append(leaf[2])
            pen += right - left
        xs.append(row_x)
        ys.append(row_y)
        widths.append(pen + data[name].w[m] - square[-1][1])
        if parts is None:
            parts = []
            for leaf_name, _, _, ax, ay in leaves:
                part = GSComponent(leaf_name, (0, 0), (ax * ay, 1) if ax != ay else (1, 1))
                part.rotation = 180 if ay < 0 else 0
                parts.append(part)
    return GlyphData.placed(parts, xs, ys, widths)


def _reach(part):
    """How a component's own sideways shift carries into the glyph it sits in: 1 as it is, -1 turned half round."""
    return part.scale[0] * math.cos(math.radians(part.rotation))


class Source:
    """Raveo's three weights at one optical size: drawings, stems, kerning and vertical metrics.

    An italic source is held upright: `slope` (the tangent of its angle) and the masters' `shifts`
    (where each slant pivots) are taken out on loading, so every operation meets the same upright
    letters it meets in the roman, with Inter's italic corrections still in them. Pipeline._layer puts
    the slant back.
    """

    def __init__(self, font, master_ids, like=None, slope=0.0, shifts=(0.0, 0.0, 0.0)):
        self.data, self.slope, self.shifts = {}, slope, np.array(shifts, dtype=float)
        for glyph in font.glyphs:
            data = GlyphData(glyph, master_ids)
            if slope:
                for m in range(3):
                    data.x[m] -= data.y[m] * slope + self.shifts[m]
                    for (index, _), part in zip(data.comp_idx, (s[1] for s in data.shapes if s[0] == "comp")):
                        data.x[m][index] += self.shifts[m] * _reach(part)
            if like and not (data.compatible and _same_structure(data, like.drawn[glyph.name])):
                data = like.drawn[glyph.name]           # not interpolable with the other size: borrow its drawing
            self.data[glyph.name] = data
        self.drawn = dict(self.data)                    # as in the source, before the rebuilt glyphs below replace some
        self.data[ROUND_TICK] = GlyphData.fitted(self.data["quoteright.ss03"], self.data["commaaccentright.cn"])
        # The ogonek of e grows out of the end of its lower stroke. That end is re-cut on e itself, so
        # e-ogonek carries its own copy of e and the ogonek as one drawing, which is left as Raveo drew it.
        for name, data in list(self.data.items()):
            parts = [self.data.get(child) for _, child in data.comp_idx]
            if len(parts) == 2 and data.comp_idx[1][1] == "ogonek.e" and all(part and part.n_nodes and not part.comp_idx for part in parts) \
                    and all(tuple(shape[1].scale) == (1, 1) and not shape[1].rotation for shape in data.shapes):
                self.data[name] = GlyphData.merged(data, parts, ("top", "bottom"))
        for name in QUOTES:
            fitted = fit_quote(self.data, name)
            if fitted:
                self.data[name + ".fit"] = fitted
        self.masters = {mid: next(m for m in font.masters if m.id == mid) for mid in master_ids}
        self.kerning = [font.kerning.get(mid, {}) for mid in master_ids]
        stems = self.data["idotless"]
        self.stems = np.array([float(stems.x[i].max() - stems.x[i].min()) for i in range(3)])
        regular = self.masters[master_ids[1]]
        self.metrics = {"ascender": regular.ascender, "cap": regular.capHeight, "x": regular.xHeight,
                        "descender": regular.descender}
        self.logo_a = None


class Pipeline:
    def __init__(self, source=None, italic=False):
        started = time.time()
        self.italic = italic
        self.font = GSFont(str(source or (ITALIC_SOURCE if italic else SOURCE)))
        self.glyph_order = [g.name for g in self.font.glyphs]
        ids = {size: tuple(next(m.id for m in self.font.masters if list(m.axes) == [size, weight]) for weight in (100, 400, 900))
               for size in (DISPLAY, TEXT)}
        slope = math.tan(math.radians(self.font.masters[0].italicAngle or 0)) if italic else 0.0
        pivots = (self.font.userData["cc.raveo.slant"] or {}).get("shift", {}) if italic else {}
        slant = {size: dict(slope=slope, shifts=[pivots.get(mid, 0.0) for mid in ids[size]]) for size in ids}
        # Italic: paths that are only leaned over (glyph -> path numbers, none = all of them), and the glyphs that
        # have Inter's written form beside them as <name>.cursive (glyph -> the letters it is built on).
        noted = (self.font.userData["cc.raveo.slant"] or {}) if italic else {}
        self.plain = {name: list(paths) or None for name, paths in dict(noted.get("plain", {})).items()}
        self.forms = {name: str(letters) for name, letters in dict(noted.get("forms", {})).items()}
        display = Source(self.font, ids[DISPLAY], **slant[DISPLAY])
        self.sources = {DISPLAY: display, TEXT: Source(self.font, ids[TEXT], like=display, **slant[TEXT])}
        self.data = display.data                # for questions about structure, which both sizes share
        # Raveo switched off some European letters that are still drawn in its source (ő ű ğ æ ø ð œ ă « » ...).
        # Switch them back on so the family covers European Latin.
        european = {cp for lo, hi in SUBSETS["european"] for cp in range(lo, hi + 1)}
        self.revived = []
        for g in self.font.glyphs:
            if g.unicode and not g.export and int(g.unicode, 16) in european and self.data[g.name].compatible:
                g.export = True
                self.revived.append(g.name)
        self.cmap = {int(g.unicode, 16): g.name for g in self.font.glyphs if g.export and g.unicode}
        self.groups = {g.name: (g.rightKerningGroup, g.leftKerningGroup) for g in self.font.glyphs}
        self.stems, self.metrics = display.stems, display.metrics
        self._cache = {"key": None, "layers": {}, "needed": None}
        self.exported = {g.name for g in self.font.glyphs if g.export}
        self.is_text = {name: self._is_text(name) for name in self.glyph_order}
        self.spaces = {g.name for g in self.font.glyphs if g.unicode and unicodedata.category(chr(int(g.unicode, 16))) == "Zs"}
        self.guarded = [self.cmap[ord(ch)] for ch in GUARD_CHARS if ord(ch) in self.cmap]
        self.joiners = {n for n in self.guarded if n in ("f", "t", "tcaron")}      # crossbars meant to meet
        self.r_letters = [n for n in self.guarded if n in ("r", "rcaron", "racute")]
        self._kern_src = {}
        self.load_seconds = time.time() - started

    def logo_a(self, opsz):
        """The wordmark's a as moves from Raveo's a, measured once per optical size at the wordmark's own style."""
        source = self.sources[opsz]
        if source.logo_a is None:
            recipe = {**copy.deepcopy(DEFAULT_RECIPE), **PLAIN}
            _, x, y, _ = self.shape(self.plan(recipe, opsz), "a", self.location(recipe, *wordmark.LOGO_STYLE, opsz))
            source.logo_a = wordmark.LogoA(x, y, source.metrics["x"])
        return source.logo_a

    def _is_text(self, name):
        """Letters, figures and marks take the full spacing change; punctuation and symbols a share."""
        glyphs = self.font.glyphs
        code = glyphs[name].unicode or (glyphs[name.split(".")[0]].unicode if name.split(".")[0] in self.data else None)
        return True if not code else unicodedata.category(chr(int(code, 16)))[0] in "LNM"

    # --- where a master sits ------------------------------------------------------------------
    def weight_for_stem(self, target, opsz=DISPLAY):
        s0, s1, s2 = self.sources[opsz].stems
        if target <= s1:
            return 100 + (target - s0) * 300 / (s1 - s0)
        return 400 + (target - s1) * 500 / (s2 - s1)

    def location(self, recipe, weight, width, opsz=DISPLAY):
        recipe = at_size(recipe, opsz)
        stems = self.sources[opsz].stems
        s = width / 100
        adapt = recipe["stem_adapt"]["condensed" if width < 100 else "wide"]
        nominal = weight
        weight = self.reachable(recipe, weight, width, opsz)
        internal = to_internal(weight)
        stem = float(lerp3(stems, internal))
        w_x = internal if width == 100 else self.weight_for_stem(stem * s ** (adapt - 1), opsz)
        loc = {key: by_width(recipe[key], width, recipe["widths"]) for key in POLE_KEYS if key != "stem_adapt"}
        keep = recipe["light_spacing"]                      # hairlines cannot be set as tight as the Regular
        loc["spacing"] *= keep + (1 - keep) * max(0.0, min(1.0, (internal - 100) / 300))
        level = internal + loc["contrast_follow"] * (self.weight_for_stem(stem * s ** adapt, opsz) - internal)
        loc["cursive"] = "".join(sorted(k for k, v in (recipe.get("italic_forms") or {}).items() if v == "cursive")) if self.forms else ""
        loc.update({"weight": nominal, "drawn_weight": round(weight), "internal": internal, "width": width, "s": s, "w_x": w_x,
                    "w_y": level + loc["contrast"], "stem": stem * s ** adapt, "opsz": opsz,
                    "regular_stem": float(stems[1]) * s ** adapt,
                    "slant": (self.sources[opsz].slope, float(lerp3(self.sources[opsz].shifts, internal)))})
        return loc

    def heaviest(self, recipe, width, opsz=DISPLAY):
        """The heaviest weight a width can carry before its shapes would need more than Raveo Black."""
        if width >= 100:
            return recipe["weights"][-1]
        s, adapt = width / 100, recipe["stem_adapt"]["condensed"]
        stem_at_cap = float(lerp3(self.sources[opsz].stems, recipe["max_source_weight"])) / s ** (adapt - 1)
        internal = self.weight_for_stem(stem_at_cap, opsz)
        pts = USER_TO_INTERNAL
        for (u0, i0), (u1, i1) in zip(pts, pts[1:]):
            if internal <= i1 or (u1, i1) == pts[-1]:
                return min(recipe["weights"][-1], u0 + (internal - i0) * (u1 - u0) / (i1 - i0))

    def reachable(self, recipe, weight, width, opsz=DISPLAY):
        """Weight actually drawn: a narrow width compresses the range to what it can carry."""
        top, mid = recipe["weights"][-1], recipe["weights"][0] if recipe.get("weight_spread") else 400
        limit = self.heaviest(recipe, width, opsz)
        if weight <= mid or limit >= top:
            return weight
        return mid + (weight - mid) * (limit - mid) / (top - mid)

    def plan(self, recipe, opsz=DISPLAY):
        recipe = at_size(recipe, opsz)
        alias = {}
        if recipe["square_dots"]:
            alias = {name: name + ".ss07" for name in self.glyph_order if name + ".ss07" in self.data}
        if recipe.get("round_commas"):          # Inter's round set, completed with the tick of ď ť ľ Ľ
            alias.update({name: name + ".ss03" for name in self.glyph_order if name + ".ss03" in self.data})
            alias["commaaccentright.cn"] = ROUND_TICK
            alias.update({name: name + ".fit" for name in QUOTES if name + ".fit" in self.data})
        cursive = {k for k, v in (recipe.get("italic_forms") or {}).items() if v == "cursive"}
        for name in self.glyph_order if cursive and self.forms else ():
            target = alias.get(name, name)
            if target in self.forms and set(self.forms[target]) <= cursive:
                alias[name] = target + CURSIVE
        return {"recipe": recipe, "alias": alias, "terminals": set(recipe["terminal_glyphs"]),
                "letters": recipe.get("glyphs", {})}

    # --- one glyph at one location -----------------------------------------------------------
    def shape(self, plan, name, loc):
        """A glyph as it goes into a master: drawn, spaced, and with the tick of d-caron and its kin fitted."""
        recipe = plan["recipe"]
        data, x, y, adv = self._drawn(plan, name, loc)
        if loc["spacing"]:
            left, right = self._room(plan, name, loc)
            if data.n_nodes:
                x[data.node_idx] += left
            if data.anchor_names:
                x[-len(data.anchor_names):] += left
            # Keep parts aligned when they move by a different amount. A part turned half round (the b inside
            # d and q) carries its own move the other way, so that is taken back with the opposite sign.
            for (index, child), part in zip(data.comp_idx, (s[1] for s in data.shapes if s[0] == "comp")):
                x[index] += left - _reach(part) * self._room(plan, child, loc)[0]
            if adv > 0:
                adv += left + right
        if name in CARON_GLYPHS and recipe.get("caron_gap") is not None and len(data.comp_idx) == 2:
            adv = self._fit_caron(plan, name, loc, data, x, adv)
        return data, x, y, adv

    def _room(self, plan, name, loc):
        """Units the spacing setting adds on the left and on the right of a glyph.

        Punctuation and symbols take a share of it. With "spacing_by_side", letters take it in
        proportion to the room they already have on that side, measured against the average small
        letter: a round side, which sits closer to its neighbour than a straight one, gives up less.
        Tabular glyphs all take the same, so they keep one width.
        """
        recipe, value = plan["recipe"], loc["spacing"]
        if name in self.spaces:                 # a word space tightens and loosens with the letters around it
            return value, value
        if not self.is_text.get(name, True):
            return (value * recipe["punctuation_spacing"],) * 2
        if not recipe.get("spacing_by_side") or ".tf" in name:
            return value, value
        sides = self._sides(plan, name, loc)
        if sides is None:
            return value, value
        key = (loc["opsz"], loc["weight"], loc["width"])
        usual = plan.setdefault("usual", {})
        if key not in usual:
            found = [side for letter in "abcdefghijklmnopqrstuvwxyz" for side in (self._sides(plan, letter, loc) or ())]
            usual[key] = max(1.0, sum(found) / max(1, len(found)))
        return tuple(value * max(0.0, min(1.5, side / usual[key])) for side in sides)

    def _sides(self, plan, name, loc):
        """Left and right side bearing of a glyph as drawn, measured on its body.

        The body is what lies between the baseline and the x-height, so an accent wider than its
        letter does not change what the letter gives up, and neither does a tail below the line.
        """
        key = (loc["opsz"], loc["weight"], loc["width"], name)
        known = plan.setdefault("sides", {})
        if key not in known:
            contours, adv = self.contours(plan, name, loc, spaced=False)
            points = [(px, py) for contour in contours for px, py, kind in contour if kind != "offcurve"]
            top = self.sources[loc["opsz"]].metrics["x"]
            body = [px for px, py in points if -5 <= py <= top + 5] or [px for px, _ in points]
            known[key] = (min(body), adv - max(body)) if body and adv > 0 else None
        return known[key]

    def _drawn(self, plan, name, loc):
        """A glyph's outline at a location, with every design operation applied but before spacing."""
        recipe = plan["recipe"]
        source = self.sources[loc["opsz"]]
        drawn_as = plan["alias"].get(name, name)
        data = source.data[drawn_as]
        x, y, adv = data.at(loc["w_x"], loc["w_y"])
        x = x * loc["s"]
        adv = adv * loc["s"]
        raveo_a = name == "a" and not drawn_as.endswith(CURSIVE)   # Inter's one-storey a takes none of what is done to Raveo's
        if data.n_nodes:
            x0, y0 = (x.copy(), y.copy()) if name in ("r", "a") else (None, None)
            own = 1 - loc["logo_a"] if raveo_a else 1            # the wordmark's a brings its own stroke end
            letter = {key: by_width(value, loc["width"], recipe["widths"]) if isinstance(value, dict) else value
                      for key, value in plan["letters"].get(name, {}).items()}
            if recipe.get("diagonals") and loc["s"] != 1:
                ref_x, ref_y, _ = data.at(loc["internal"], loc["internal"])
                narrowness = max(0.0, min(1.0, (1 - loc["s"]) / 0.45))
                diagonals.fix(data, x, y, ref_x, ref_y, loc["stem"], float(lerp3(source.stems, loc["internal"])),
                              recipe["diagonals"], 1 - recipe["diagonal_taper"] * narrowness, float(source.stems[1]))
            moved = shift_middle(data, x, y, letter.get("middle", 0))
            if name == "R" and recipe.get("leg"):       # before the curve operations below, which then smooth it like any outline
                ref_x, ref_y, _ = data.at(loc["internal"], loc["internal"])
                adv += leg.shape(data, x, y, ref_x, ref_y, loc["s"], loc["stem"], float(lerp3(source.stems, loc["internal"])), letter)
                moved = True
            if loc["w_x"] != loc["w_y"] or moved:
                fix_smooth(data, x, y)
            tension = loc["squareness"] + letter.get("tension", 0)
            if tension:
                square_arcs(data, x, y, max(-1.0, min(1.0, tension)))
            smoothing = letter.get("smoothing", loc["smoothing"])
            if smoothing:
                harmonize(data, x, y, smoothing)
            angle = letter.get("angle", loc["terminal_angle"]) * own
            aperture = (loc["aperture"] - letter.get("arm", 0) * loc["s"]) * own   # a longer arm is a negative aperture
            if (angle or aperture) and name in plan["terminals"]:
                cut_terminals(data, x, y, angle, aperture, float(source.stems[1]),
                              recipe["terminal_skip"].get(name, ()), letter.get("corners", loc["terminal_soften"]))
            if name == "r" and loc["logo_r"]:
                adv += logo_r(data, x, y, x0, y0, loc["logo_r"], loc["s"])
            if raveo_a and loc["logo_a"]:
                light = min(1.0, float(lerp3(source.stems, loc["internal"])) / float(source.stems[1]))
                adv += self.logo_a(loc["opsz"]).apply(x, y, x0, loc["logo_a"], light)
            if raveo_a:
                bowl.shape(x, y, letter, loc["s"])
                fix_smooth(data, x, y)                 # moving points can pull handles out of line
            if source.slope and recipe.get("slant_compensation"):
                # Letters that are only leaned over get what a drawn italic has: see curves.slant_compensate.
                share = 1.0 if drawn_as in self.plain else loc["logo_r"] if name == "r" else 0.0
                if share:
                    slant_compensate(data, x, y, share * recipe["slant_compensation"] * loc["stem"] * source.slope / 4,
                                     self.plain.get(drawn_as))
        elif data.comp_idx and data.comp_idx[0][1] in RESHAPED and name not in CARON_GLYPHS:
            # An accented letter is as wide as the letter it is built on: where that one was redrawn wider
            # or narrower (the wordmark's r and a, the leg of the R), the accented one follows.
            base = source.data[plan["alias"].get(data.comp_idx[0][1], data.comp_idx[0][1])]
            if abs(float(lerp3(base.w, loc["w_x"])) * loc["s"] - adv) < 1:
                adv = self._drawn(plan, data.comp_idx[0][1], loc)[3]
        return data, x, y, adv

    def _fit_caron(self, plan, name, loc, data, x, adv):
        """Pull the tick of ď ť ľ Ľ toward its letter and let the next letter slide under it.

        Letters tall enough to hit the tick are kept clear by the kerning guard.
        """
        recipe = plan["recipe"]
        (base_i, base), (tick_i, tick) = data.comp_idx
        base_contours, base_adv = self.contours(plan, base, loc)
        tick_contours, _ = self.contours(plan, tick, loc)
        tx = [px + float(x[tick_i]) for c in tick_contours for px, _, kind in c if kind != "offcurve"]
        ty = [py for c in tick_contours for _, py, kind in c if kind != "offcurve"]
        left, right, bottom = min(tx), max(tx), min(ty)
        edge = max(px + float(x[base_i]) for c in base_contours for px, py, kind in c
                   if kind != "offcurve" and py > bottom - 20)
        shift = (left - edge) - recipe["caron_gap"] * loc["stem"]
        x[tick_i] -= shift
        right -= shift
        return min(adv, max(base_adv + float(x[base_i]), right - recipe["caron_tuck"] * (right - left)))

    def contours(self, plan, name, loc, depth=0, spaced=True, leaned=False):
        """Decomposed outline: list of contours, each a list of (x, y, node type). With `spaced` off, as drawn,
        before the spacing setting and the fitting of the tick. With `leaned`, an italic as the font will have
        it; otherwise upright, as every measurement wants it."""
        data, x, y, adv = self.shape(plan, name, loc) if spaced else self._drawn(plan, name, loc)
        if leaned:
            recipe = plan["recipe"]
            x = self._lean(name, data, x, y, loc["slant"], bool(recipe.get("italic_round_dots")) and not recipe["square_dots"])
        out, i = [], 0
        for shape in data.shapes:
            if shape[0] == "path":
                out.append([(float(x[i + k]), float(y[i + k]), kind) for k, (kind, _) in enumerate(shape[2])])
                i += len(shape[2])
            else:
                comp = shape[1]
                if depth < 6 and comp.name in self.data:
                    sub, _ = self.contours(plan, comp.name, loc, depth + 1, spaced, leaned)
                    sx, sy = comp.scale
                    rot = math.radians(comp.rotation)
                    cos, sin = math.cos(rot), math.sin(rot)
                    for contour in sub:
                        out.append([(float(x[i]) + (px * sx) * cos - (py * sy) * sin,
                                     float(y[i]) + (px * sx) * sin + (py * sy) * cos, kind)
                                    for px, py, kind in contour])
                i += 1
        return out, float(adv)

    def sides(self, name, cursive):
        """A glyph's kerning groups (as the left glyph, as the right glyph). A letter set in Inter's written
        form kerns as that form does: the one-storey a has a round left side, Raveo's a does not."""
        if cursive and name in self.forms and set(self.forms[name]) <= set(cursive):
            return self.groups.get(name + CURSIVE, self.groups[name])
        return self.groups[name]

    def kern_matrix(self, names, loc):
        """Kerning for every ordered pair of `names` at a location (rows = left glyph)."""
        key = (loc["opsz"], tuple(names), loc["cursive"])
        if self._kern_src.get("key") != key:
            tables = np.zeros((3, len(names), len(names)))
            for m, table in enumerate(self.sources[loc["opsz"]].kerning):
                for i, left in enumerate(names):
                    lg = self.sides(left, loc["cursive"])[0]
                    rows = [table.get(k) for k in ([left] + ([f"@MMK_L_{lg}"] if lg else []))]
                    rows = [r for r in rows if r]
                    if not rows:
                        continue
                    for j, right in enumerate(names):
                        rg = self.sides(right, loc["cursive"])[1]
                        keys = [right] + ([f"@MMK_R_{rg}"] if rg else [])
                        hit = next((row[k] for row in rows for k in keys if k in row), None)
                        if hit is not None:
                            tables[m, i, j] = hit
            self._kern_src = {"key": key, "tables": tables}
        return lerp3(self._kern_src["tables"], loc["w_x"]) * loc["s"]

    def kern(self, left, right, loc):
        lg, rg = self.sides(left, loc["cursive"])[0], self.sides(right, loc["cursive"])[1]
        lefts = [left] + ([f"@MMK_L_{lg}"] if lg else [])
        rights = [right] + ([f"@MMK_R_{rg}"] if rg else [])
        vals = []
        for table in self.sources[loc["opsz"]].kerning:
            value = 0
            for l in lefts:
                row = table.get(l)
                hit = next((row[r] for r in rights if row and r in row), None)
                if hit is not None:
                    value = hit
                    break
            vals.append(float(value))
        return float(lerp3(np.array(vals), loc["w_x"])) * loc["s"]

    # --- live preview --------------------------------------------------------------------------
    def base(self, plan, name):
        """The drawn glyph behind a letter: itself, or the first component of an accented composite."""
        data = self.data[plan["alias"].get(name, name)]
        if data.n_nodes:
            return name
        first = next((sh[1].name for sh in data.shapes if sh[0] == "comp"), None)
        return self.base(plan, first) if first in self.data else name

    def outline(self, recipe, text, weight, widths, comb=False, opsz=DISPLAY):
        """SVG path data for a short text at an exact weight and several widths, for the workbench."""
        recipe = at_size(recipe, opsz)
        plan = self.plan(recipe, opsz)
        plain_recipe = {**recipe, **PLAIN}
        ghost_plan = self.plan(plain_recipe, opsz)
        rows = []
        for width in widths:
            loc, ghost_loc = self.location(recipe, weight, width, opsz), self.location(plain_recipe, weight, width, opsz)
            glyphs, pen, previous = [], 0.0, None
            floors = recipe.get("kerning_floor")
            for ch in text:
                name = self.cmap.get(ord(ch))
                if name is None:
                    continue
                contours, adv = self.contours(plan, name, loc)
                shape = profile(contours) if floors else None
                if previous:
                    kern = self.kern(previous[0], name, loc)
                    if previous[0] in self.r_letters and loc["logo_r"]:
                        old, old_adv = self.contours(plan, previous[0], {**loc, "logo_r": 0})
                        before = (old_adv - profile(old)[1] + (shape or profile(contours))[0]).min()
                        after = (previous[2] - (previous[1] or profile(self.contours(plan, previous[0], loc)[0]))[1]
                                 + (shape or profile(contours))[0]).min()
                        if before < 1e5 and after < 1e5:
                            mine = previous[1] or profile(self.contours(plan, previous[0], loc)[0])
                            kern += before - after - recipe.get("logo_r_over", 0) * float(reach_over(
                                profile(old)[1], mine[1], (shape or profile(contours))[0][None, :], self.sources[opsz].metrics["x"])[0])
                    if floors:
                        short = 0.0
                        if not (previous[0] in self.joiners and name in self.joiners):
                            floor = floor_for(self.is_text[previous[0]], self.is_text[name], loc["stem"], floors)
                            short = pair_shortfall(previous[1], previous[2], shape, kern, floor)
                        if previous[0] in CARON_GLYPHS and recipe.get("caron_floor"):       # the tick keeps its own distance
                            short = max(short, pair_shortfall(previous[1], previous[2], shape, kern, recipe["caron_floor"] * loc["regular_stem"],
                                                              self.sources[opsz].metrics["x"] + TICK_ABOVE))
                        kern += short if short > 0.04 * loc["stem"] else 0.0
                    pen += kern
                ghost, _ = self.contours(ghost_plan, name, ghost_loc)
                base = self.base(plan, name)
                slanted = bool(loc["slant"][0])               # an italic is worked on upright and shown as it will be
                drawn = self.contours(plan, name, loc, leaned=True)[0] if slanted else contours
                entry = {"name": name, "base": base, "char": ch, "x": round(pen, 1), "advance": round(adv, 1),
                         "middle": self.sources[opsz].data[plan["alias"].get(base, base)].middle_bar() is not None,
                         "d": _svg_path(drawn), "ghost": _svg_path(self.contours(ghost_plan, name, ghost_loc, leaned=True)[0] if slanted else ghost),
                         "nodes": [[round(px, 1), round(py, 1), 0 if kind == "offcurve" else 1]
                                   for contour in drawn for px, py, kind in contour],
                         "handles": _handles(drawn)}
                if comb:
                    entry["comb"] = _comb(drawn)
                glyphs.append(entry)
                pen += adv
                previous = (name, shape, adv)
            rows.append({"width": width, "glyphs": glyphs, "advance": round(pen, 1),
                         "x_from_weight": round(loc["w_x"], 1), "drawn_weight": loc["drawn_weight"],
                         "extrapolated": not 100 <= loc["w_x"] <= 900})
        return {"rows": rows, "metrics": self.sources[opsz].metrics}

    # --- quality -------------------------------------------------------------------------------
    def quality(self, recipe, names=None, locations=None, opsz=DISPLAY):
        """Count kinked joins and curvature jumps over the subset's base glyphs."""
        narrow, _, wide = recipe["widths"]
        locations = locations or ((630, narrow), (630, 100), (630, wide), (800, narrow), (300, wide))
        if names is None:
            ranges = SUBSETS[recipe["subset"]]
            wanted = set(self.cmap) if ranges is None else {cp for lo, hi in ranges for cp in range(lo, hi + 1)}
            names = [n for cp, n in sorted(self.cmap.items()) if cp in wanted and self.data[n].n_nodes]
        plan = self.plan(recipe, opsz)
        rows = []
        for weight, width in locations:
            loc = self.location(recipe, weight, width, opsz)
            joins = kinks = jumps = 0
            worst = []
            for name in names:
                data, x, y, _ = self.shape(plan, name, loc)
                for kink, jump, node in curve_report(data, x, y):
                    joins += 1
                    kinks += int(kink > 1.0)
                    jumps += int(jump > 0.35)
                    worst.append((round(float(kink), 1), round(float(jump), 2), name))
            rows.append({"weight": weight, "width": width, "joins": joins, "kinks": kinks, "jumps": jumps,
                         "worst_kinks": sorted(worst, reverse=True)[:4],
                         "worst_jumps": sorted(worst, key=lambda w: -w[1])[:4]})
        return rows

    # --- whole font ------------------------------------------------------------------------------
    def derive(self, recipe, out_path):
        font = self.font
        weights, widths, sizes = recipe["weights"], recipe["widths"], sorted(recipe["optical_sizes"])
        multi = len(sizes) > 1
        key = (tuple(sizes), tuple(weights), tuple(widths))
        if self._cache["key"] != key:
            self._cache = {"key": key, "layers": {}, "needed": None}
        needed, cached = self._cache["needed"], self._cache["layers"]
        report = {"masters": [], "glyphs_recomputed": len(needed) if needed else len(self.glyph_order)}

        masters, layers, kerning, guards, forced = [], {}, {}, {}, set()
        for opsz in sizes:
            plan = self.plan(recipe, opsz)
            sized = plan["recipe"]
            for width in widths:
                for weight in weights:
                    loc = self.location(recipe, weight, width, opsz)
                    mid = f"o{opsz}-w{weight}-wd{width}"
                    masters.append(self._master(mid, weight, width, opsz, multi))
                    old = cached.get(mid, {})
                    layers[mid] = {}
                    for name in self.glyph_order:
                        if needed is not None and name not in needed and name in old:
                            layers[mid][name] = old[name]      # never reaches the subset font; keep as is
                        else:
                            layers[mid][name] = self._layer(mid, *self.shape(plan, name, loc), slant=loc["slant"], name=name,
                                                            round_dots=bool(sized.get("italic_round_dots")) and not sized["square_dots"])
                    kerning[mid] = self._kerning(loc)
                    if sized.get("kerning_floor"):
                        clearance = Clearance(self, plan, loc, self.guarded)
                        if loc["logo_r"]:                  # the wordmark r is wider: keep the gaps Raveo's r had
                            for name in self.r_letters:
                                old, old_adv = self.contours(plan, name, {**loc, "logo_r": 0})
                                change = clearance.keep_gaps(name, profile(old), old_adv, self.sources[opsz].metrics["x"],
                                                             sized.get("logo_r_over", 0))
                                forced.update((name, right) for right, c in zip(self.guarded, change) if abs(c) > 3)
                        guards[mid] = (clearance, shortfalls(clearance, self.is_text, loc["stem"], sized["kerning_floor"],
                                                             self.joiners, CARON_GLYPHS,
                                                             (sized.get("caron_floor") or 0) * loc["regular_stem"],
                                                             self.sources[opsz].metrics["x"] + TICK_ABOVE))
                    report["masters"].append({"opsz": opsz, "weight": weight, "width": width,
                                              "x_from_weight": round(loc["w_x"], 1), "drawn_weight": loc["drawn_weight"],
                                              "extrapolated": not 100 <= loc["w_x"] <= 900,
                                              "stem": round(loc["stem"], 1)})
        pairs = {pair for _, short in guards.values() for pair in short} | forced
        index = {name: i for i, name in enumerate(self.guarded)}
        for mid, (clearance, short) in guards.items():          # the same pairs in every master
            for left, right in pairs:
                base = clearance.kern[index[left], index[right]]
                kerning[mid].setdefault(left, {})[right] = int(round(base + short.get((left, right), 0.0)))
        report["guarded_pairs"] = len(pairs)
        self._cache["layers"] = layers

        font.familyName = recipe["family"]
        font.axes = ([GSAxis(name="Optical size", tag="opsz")] if multi else []) + [
            GSAxis(name="Weight", tag="wght"), GSAxis(name="Width", tag="wdth")]
        font.masters = masters
        for glyph in font.glyphs:
            glyph._layers = {}
            for mid in layers:
                glyph.layers[mid] = layers[mid][glyph.name]
        cursive = "".join(sorted(k for k, v in (recipe.get("italic_forms") or {}).items() if v == "cursive")) if self.forms else ""
        for glyph in font.glyphs:                               # kerning groups go with the form each letter is set in
            glyph.rightKerningGroup, glyph.leftKerningGroup = self.sides(glyph.name, cursive)
        font.kerning.clear()
        font.kerning.update(kerning)
        font.instances = self._instances(recipe)
        default = f"o{sizes[0]}-w{400 if 400 in weights else weights[0]}-wd{100 if 100 in widths else widths[0]}"
        keep = [p for p in font.customParameters
                if p.name not in ("Variable Font Origin", "Axis Mappings", "Disable Masters", "Get Hints From Master")]
        mapping = {str(u): round(self._new_internal(u, weights), 2) for u, _ in USER_TO_INTERNAL
                   if weights[0] <= u <= weights[-1]}
        mapping[str(weights[0])], mapping[str(weights[-1])] = weights[0], weights[-1]
        keep.append(GSCustomParameter("Variable Font Origin", default))
        keep.append(GSCustomParameter("Axis Mappings", {**({"opsz": {str(o): o for o in sizes}} if multi else {}),
                                                        "wght": mapping, "wdth": {str(w): w for w in widths}}))
        font._customParameters = keep
        font.save(str(out_path))
        return report

    def remember_needed(self, glyph_names):
        """After the first build: only glyphs that reach the subset font need recomputing."""
        needed, queue = set(), list(glyph_names)
        while queue:
            name = queue.pop()
            if name in needed or name not in self.data:
                continue
            needed.add(name)
            for candidate in (name, name + ".ss07"):
                if candidate in self.data:
                    needed.add(candidate)
                    queue += [s[1].name for s in self.data[candidate].shapes if s[0] == "comp"]
        self._cache["needed"] = needed

    def _new_internal(self, user_weight, weights):
        """Position on the new weight axis whose interpolated drawing equals Raveo at `user_weight`."""
        target = to_internal(user_weight)
        stops = [(w, to_internal(w)) for w in weights]
        for (a, ia), (b, ib) in zip(stops, stops[1:]):
            if target <= ib or b == weights[-1]:
                return a + (target - ia) * (b - a) / (ib - ia)

    def _master(self, mid, weight, width, opsz, multi):
        source = min(zip(self.sources[opsz].masters.values(), (100, 400, 900)),
                     key=lambda m: abs(m[1] - to_internal(weight)))[0]
        master = copy.copy(source)
        master.id = mid
        master.name = style_name(weight, width, multi and opsz == DISPLAY, italic=self.italic)
        master.axes = ([opsz] if multi else []) + [weight, width]
        params = [copy.copy(p) for p in source._customParameters
                  if p.name not in ("Axis Location", "WWSFamilyName", "WWSSubfamilyName", "Master Icon Glyph Name")]
        params.append(GSCustomParameter("Axis Location", ([{"Axis": "Optical size", "Location": opsz}] if multi else []) + [
            {"Axis": "Weight", "Location": weight}, {"Axis": "Width", "Location": width}]))
        master._customParameters = params
        return master

    def _lean(self, name, data, x, y, slant, round_dots=True):
        """Put an italic's slant back: it leaves the pipeline upright. A round dot is moved to where the
        slant takes its centre and keeps its shape; leaned over, a circle is an oval tilted at 45 degrees."""
        slope, shift = slant
        if not slope:
            return x
        leaned = x + y * slope + shift
        for (index, _), part in zip(data.comp_idx, (s[1] for s in data.shapes if s[0] == "comp")):
            leaned[index] -= shift * _reach(part)
        if round_dots and name.split(CURSIVE)[0] in DOTTED:
            for start, count, kinds, _ in data.paths:
                if count != 12 or "line" in kinds or kinds.count("offcurve") != 8:
                    continue
                on = [start + k for k in range(count) if kinds[k] != "offcurve"]
                wide, high = x[on].max() - x[on].min(), y[on].max() - y[on].min()
                if high > 0 and 0.7 < wide / high < 1.45:
                    leaned[start:start + count] = x[start:start + count] + (y[on].max() + y[on].min()) / 2 * slope + shift
        return leaned

    def _layer(self, mid, data, x, y, adv, slant=(0.0, 0.0), name="", round_dots=True):
        x = self._lean(name, data, x, y, slant, round_dots)
        layer = GSLayer()
        layer.layerId = mid
        layer.associatedMasterId = mid
        layer.width = int(round(adv))
        shapes, i = [], 0
        for shape in data.shapes:
            if shape[0] == "path":
                path = GSPath()
                path.closed = shape[1]
                path.nodes = [GSNode((int(round(x[i + k])), int(round(y[i + k]))), kind, smooth)
                              for k, (kind, smooth) in enumerate(shape[2])]
                i += len(shape[2])
                shapes.append(path)
            else:
                src = shape[1]
                comp = GSComponent(src.name, (int(round(x[i])), int(round(y[i]))), tuple(src.scale))
                comp.rotation = src.rotation
                comp.alignment = -1          # positions are final; never re-align
                comp.anchor = src.anchor
                i += 1
                shapes.append(comp)
        layer.shapes = shapes
        layer.anchors = [GSAnchor(name, Point(int(round(x[i + k])), int(round(y[i + k]))))
                         for k, name in enumerate(data.anchor_names)]
        return layer

    def _kerning(self, loc):
        tables = self.sources[loc["opsz"]].kerning
        out = {}
        for left in {l for t in tables for l in t}:
            row = {}
            for right in {r for t in tables for r in t.get(left, {})}:
                vals = np.array([float(t.get(left, {}).get(right, 0)) for t in tables])
                value = int(round(float(lerp3(vals, loc["w_x"])) * loc["s"]))
                if value:
                    row[right] = value
            if row:
                out[left] = row
        return out

    def _instances(self, recipe):
        sizes, names, widths, out = sorted(recipe["optical_sizes"]), style_weights(recipe), style_widths(recipe), []
        multi = len(sizes) > 1
        for opsz in sizes:
            for width in widths:
                for weight, name in names.items():
                    inst = GSInstance()
                    inst.name = style_name(weight, width, multi and opsz == DISPLAY, names, widths, self.italic)
                    inst.axes = ([opsz] if multi else []) + [round(self._new_internal(weight, recipe["weights"]), 2), width]
                    if name in STYLE_NAMES.values():
                        inst.weight = name
                    inst.isBold = False
                    out.append(inst)
        return out


# --- SVG helpers for the live preview ---------------------------------------------------------------

def _segments(contour):
    """Yield ('M'|'L'|'C', points) for one closed contour of (x, y, kind)."""
    n = len(contour)
    first = next((i for i, p in enumerate(contour) if p[2] != "offcurve"), None)
    if first is None:
        return
    yield "M", [contour[first][:2]]
    pending = []
    for step in range(1, n + 1):
        px, py, kind = contour[(first + step) % n]
        if kind == "offcurve":
            pending.append((px, py))
        elif len(pending) == 2:
            yield "C", pending + [(px, py)]
            pending = []
        else:
            yield "L", [(px, py)]
            pending = []


def _svg_path(contours):
    parts = []
    for contour in contours:
        for op, pts in _segments(contour):
            parts.append(op + " ".join(f"{px:.1f} {py:.1f}" for px, py in pts))
        parts.append("Z")
    return "".join(parts)


def _handles(contours):
    """Line segments from each on-curve point to its handles."""
    parts = []
    for contour in contours:
        last = None
        for op, pts in _segments(contour):
            if op == "C":
                parts.append(f"M{last[0]:.1f} {last[1]:.1f}L{pts[0][0]:.1f} {pts[0][1]:.1f}"
                             f"M{pts[2][0]:.1f} {pts[2][1]:.1f}L{pts[1][0]:.1f} {pts[1][1]:.1f}")
            last = pts[-1]
    return "".join(parts)


def _comb(contours, samples=14, scale=26000.0):
    """Curvature comb: a tooth along the normal at each sample, as long as the curve is tight."""
    parts = []
    for contour in contours:
        last = None
        for op, pts in _segments(contour):
            if op == "C":
                p0, p1, p2, p3 = last, pts[0], pts[1], pts[2]
                tips = []
                for i in range(samples + 1):
                    t = i / samples
                    mt = 1 - t
                    px = mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0]
                    py = mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]
                    dx = 3 * (mt * mt * (p1[0] - p0[0]) + 2 * mt * t * (p2[0] - p1[0]) + t * t * (p3[0] - p2[0]))
                    dy = 3 * (mt * mt * (p1[1] - p0[1]) + 2 * mt * t * (p2[1] - p1[1]) + t * t * (p3[1] - p2[1]))
                    ddx = 6 * (mt * (p2[0] - 2 * p1[0] + p0[0]) + t * (p3[0] - 2 * p2[0] + p1[0]))
                    ddy = 6 * (mt * (p2[1] - 2 * p1[1] + p0[1]) + t * (p3[1] - 2 * p2[1] + p1[1]))
                    speed = math.hypot(dx, dy)
                    if speed < 1e-6:
                        continue
                    k = (dx * ddy - dy * ddx) / speed ** 3
                    length = max(-260.0, min(260.0, k * scale))
                    tx, ty = px + dy / speed * length, py - dx / speed * length
                    parts.append(f"M{px:.1f} {py:.1f}L{tx:.1f} {ty:.1f}")
                    tips.append((tx, ty))
                if tips:
                    parts.append("M" + "L".join(f"{tx:.1f} {ty:.1f}" for tx, ty in tips))
            last = pts[-1]
    return "".join(parts)


# --- build --------------------------------------------------------------------------------------------

def finish(font, recipe, italic=False, angle=0.0):
    """Tables that font checkers and apps expect, which the compiler does not write for us."""
    from fontTools.otlLib.builder import buildStatTable
    narrow, _, wide = recipe["widths"]
    names = style_weights(recipe)
    sizes = sorted(recipe["optical_sizes"])
    for inst in font["fvar"].instances:                # exact named positions, not rounded-trip values
        inst.coordinates["wght"] = min(names, key=lambda w: abs(w - inst.coordinates["wght"]))
    optical = [] if len(sizes) < 2 else [
        {"tag": "opsz", "name": "Optical size",
         "values": [{"value": sizes[0], "name": "Text", "flags": 0x2}, {"value": sizes[-1], "name": "Display"}]}]
    buildStatTable(font, optical + [
        {"tag": "wght", "name": "Weight",
         "values": [{"value": w, "name": n, **({"flags": 0x2} if w == 400 else {})} for w, n in names.items()]},
        {"tag": "wdth", "name": "Width",
         "values": [{"value": w, "name": n or "Normal", **({"flags": 0x2} if w == 100 else {})}
                    for w, n in style_widths(recipe).items()]},
        # The roman and the italic are two files of one family: each says which it is, and the roman points at the italic.
        {"tag": "ital", "name": "Italic",
         "values": [{"value": 1, "name": "Italic"}] if italic else [{"value": 0, "name": "Roman", "flags": 0x2, "linkedValue": 1}]},
    ], elidedFallbackName=2, macNames=False)
    if italic:
        slug, family = recipe["family"].replace(" ", ""), recipe["family"]
        for name_id, value in ((2, "Italic"), (4, f"{family} Italic"), (6, f"{slug}-Italic"), (17, "Italic")):
            font["name"].setName(value, name_id, 3, 1, 0x409)
        font["post"].italicAngle = -angle
        font["hhea"].caretSlopeRise, font["hhea"].caretSlopeRun = font["head"].unitsPerEm, round(font["head"].unitsPerEm * math.tan(math.radians(angle)))
        font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0b1000000) | 0b1
        font["head"].macStyle |= 0b10
    for record in font["name"].names:                  # stray spaces in the source's notices
        text = record.toUnicode()
        if text != text.strip():
            record.string = text.strip()
    glyf, os2 = font["glyf"], font["OS/2"]
    tops = [glyf[g].yMax for g in font.getGlyphOrder() if getattr(glyf[g], "yMax", None) is not None]
    bottoms = [glyf[g].yMin for g in font.getGlyphOrder() if getattr(glyf[g], "yMin", None) is not None]
    os2.usWinAscent = max(os2.usWinAscent, max(tops))          # Windows clips anything outside these
    os2.usWinDescent = max(os2.usWinDescent, -min(bottoms))
    os2.recalcAvgCharWidth(font)
    from fontTools.varLib.instancer import setMacOverlapFlags     # strokes cross inside glyphs: say so, or some
    setMacOverlapFlags(glyf)                                      # rasterisers leave holes or darken where they do
    font["maxp"].maxStackElements = max(font["maxp"].maxStackElements, 2)
    from fontTools.ttLib import newTable                       # unhinted font: smooth rendering at all sizes
    font["gasp"] = gasp = newTable("gasp")
    gasp.gaspRange = {0xFFFF: 15}
    font["prep"] = prep = newTable("prep")
    from fontTools.ttLib.tables.ttProgram import Program
    prep.program = Program()
    prep.program.fromBytecode(b"\xb8\x01\xff\x85\xb0\x04\x8d")


def compile_font(recipe, glyphs_path, out_dir, woff2=True, italic=False, angle=0.0):
    """fontc -> full variable TTF -> subset TTF (+ WOFF2). Returns paths and sizes."""
    slug = recipe["family"].replace(" ", "") + ("-Italic" if italic else "")
    full = out_dir / f"{slug}-full.ttf"
    result = subprocess.run([str(FONTC), str(glyphs_path), "--build-dir", str(out_dir / ("fontc-italic" if italic else "fontc")), "-o", str(full),
                             "--flatten-components=true", "--decompose-transformed-components"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        errors = [l for l in (result.stderr + result.stdout).splitlines() if "WARN" not in l]
        raise RuntimeError("fontc failed:\n" + "\n".join(errors[-25:]))
    font = TTFont(full)
    ranges = SUBSETS[recipe["subset"]]
    unicodes = sorted(font.getBestCmap()) if ranges is None else [cp for lo, hi in ranges for cp in range(lo, hi + 1)]
    features = {r.FeatureTag for tag in ("GSUB", "GPOS") if tag in font for r in font[tag].table.FeatureList.FeatureRecord}
    if recipe.get("round_commas"):
        features.discard("ss03")                # the round set is the default now; the feature would only undo its spacing
    options = subset.Options(layout_features=sorted(features), glyph_names=True, notdef_outline=True,
                             name_IDs=["*"], name_languages=["*"], drop_tables=["DSIG"])
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)
    finish(font, recipe, italic, angle)
    ttf = out_dir / f"{slug}-VF.ttf"
    font.save(ttf)
    out = {"ttf": ttf, "glyphs": len(font.getGlyphOrder()), "glyph_names": font.getGlyphOrder(),
           "kb": round(ttf.stat().st_size / 1024, 1)}
    if woff2:
        font.flavor = "woff2"
        out["woff2"] = out_dir / f"{slug}-VF.woff2"
        font.save(out["woff2"])
        out["kb"] = round(out["woff2"].stat().st_size / 1024, 1)
    return out


def build(pipeline, recipe, out_dir=BUILD / "headline", woff2=True):
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    glyphs_path = out_dir / ("derived-italic.glyphs" if pipeline.italic else "derived.glyphs")
    report = pipeline.derive(recipe, glyphs_path)
    report["derive_seconds"] = round(time.time() - started, 2)
    started = time.time()
    report.update(compile_font(recipe, glyphs_path, out_dir, woff2, pipeline.italic, pipeline.font.masters[0].italicAngle or 0.0))
    report["compile_seconds"] = round(time.time() - started, 2)
    pipeline.remember_needed(report.pop("glyph_names"))
    return report


def parse_value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def print_quality(pipeline, recipe, title):
    print(title)
    for row in pipeline.quality(recipe):
        print(f"  wght {row['weight']} wdth {row['width']:>3}: {row['joins']} smooth joins, "
              f"{row['kinks']} kinked >1 deg, {row['jumps']} curvature jumps >35%"
              f"   worst kink {row['worst_kinks'][0][0]} deg ({row['worst_kinks'][0][2]})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recipe", default=None)
    ap.add_argument("--out", default=None, help="output directory (default build/headline)")
    ap.add_argument("--report", action="store_true", help="print curve-quality numbers and exit")
    ap.add_argument("--italic", action="store_true", help="build the italic (from sources/raveo-italic.glyphs) instead of the roman")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a recipe value, e.g. terminal_angle=30 or squareness.condensed=0.5")
    args = ap.parse_args()
    overrides = {k: parse_value(v) for k, v in (item.split("=", 1) for item in args.set)}
    recipe = load_recipe(args.recipe, overrides)
    pipeline = Pipeline(italic=args.italic)
    if args.report:
        print_quality(pipeline, {**recipe, **PLAIN}, "Width scaling only (no smoothing, no design operations):")
        print_quality(pipeline, recipe, "With the recipe:")
        return
    report = build(pipeline, recipe, Path(args.out) if args.out else BUILD / "headline")
    print(f"source loaded in {pipeline.load_seconds:.1f}s, derived in {report['derive_seconds']}s, "
          f"compiled in {report['compile_seconds']}s")
    for m in report["masters"]:
        flag = "  <- extrapolated past the source weights" if m["extrapolated"] else ""
        print(f"  opsz {m['opsz']} wght {m['weight']:>3} wdth {m['width']:>3}: x from weight {m['x_from_weight']:>6}, "
              f"stem {m['stem']}{flag}")
    print(f"{report['glyphs']} glyphs, {report['kb']} KB woff2 -> {report['woff2']}")


if __name__ == "__main__":
    main()
