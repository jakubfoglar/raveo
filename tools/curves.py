"""Glyph geometry: interpolable glyph data and the curve operations applied to derived masters.

Every operation edits flat x / y arrays in place and keeps the point structure, so results stay
interpolable. They are written to leave curves on their original path wherever possible:

  fix_smooth      re-aligns the two handles of smooth points (mixing x and y from different weights
                  bends them out of line)
  square_arcs     raises curve tension on every simple arc, not only full quarter rounds, so
                  neighbouring arcs keep matching
  harmonize       slides smooth points along their tangent until curvature is equal on both sides
  shift_middle    raises or lowers a letter's middle bar (R, B, E, H, e ...) by stretching the part
                  below it and squeezing the part above, keeping the bar's own thickness
  cut_terminals   re-cuts stroke ends on a line at a set angle: the edge that pulls back is split exactly
                  (de Casteljau) so it keeps its shape, the edge that reaches forward continues along
                  its end tangent
  slant_compensate  gives a letter that is only leaned over the stroke correction of a drawn italic
"""
import math

import numpy as np
from glyphsLib.classes import GSPath

SRC_WEIGHTS = (100.0, 400.0, 900.0)     # internal weights of Raveo's three display masters
SQUARE_TARGET = 0.80                    # handle fraction of a fully "square" arc (a circle is 0.55)
ROUND_TARGET = 0.50                     # handle fraction that negative tension moves toward
HIDDEN = 12                             # a stroke end this far inside other ink is not a stroke end
LEAN_MOST = 0.35                        # slant compensation never changes a handle by more than this share


def lerp3(arr, weight):
    """Piecewise linear through the three source masters, extrapolating past either end."""
    w0, w1, w2 = SRC_WEIGHTS
    if weight <= w1:
        return arr[0] + (arr[1] - arr[0]) * (weight - w0) / (w1 - w0)
    return arr[1] + (arr[2] - arr[1]) * (weight - w1) / (w2 - w1)


def _unit(dx, dy):
    length = math.hypot(dx, dy)
    return None if length < 1e-6 else (dx / length, dy / length)


class GlyphData:
    """One glyph's drawing in the three source masters, as arrays that can be interpolated."""

    def __init__(self, glyph, master_ids):
        layers = [glyph.layers[mid] for mid in master_ids]
        ref = layers[1]
        self.shapes = []          # ("path", closed, [(type, smooth)...]) | ("comp", component)
        self.paths = []           # (start index, node count, node types, smooth flags) per path
        self.anchor_names = [a.name for a in ref.anchors
                             if all(any(b.name == a.name for b in l.anchors) for l in layers)]
        cols = []
        for layer in layers:
            xs, ys = [], []
            for shape in layer.shapes:
                if isinstance(shape, GSPath):
                    for node in shape.nodes:
                        xs.append(node.position.x)
                        ys.append(node.position.y)
                else:
                    xs.append(shape.position.x)
                    ys.append(shape.position.y)
            for name in self.anchor_names:
                anchor = next(a for a in layer.anchors if a.name == name)
                xs.append(anchor.position.x)
                ys.append(anchor.position.y)
            cols.append((xs, ys, layer.width))
        index = 0
        for shape in ref.shapes:
            if isinstance(shape, GSPath):
                kinds = [(n.type, n.smooth) for n in shape.nodes]
                self.shapes.append(("path", shape.closed, kinds))
                self.paths.append((index, len(kinds), [k[0] for k in kinds], [k[1] for k in kinds]))
                index += len(kinds)
            else:
                self.shapes.append(("comp", shape))
                index += 1
        self.compatible = len({len(c[0]) for c in cols}) == 1
        if not self.compatible:   # not interpolable: fall back to the regular master everywhere
            cols = [cols[1]] * 3
        self.x = np.array([c[0] for c in cols], dtype=float)
        self.y = np.array([c[1] for c in cols], dtype=float)
        self.w = np.array([c[2] for c in cols], dtype=float)
        self.n_nodes = sum(p[1] for p in self.paths)
        self.node_idx = np.array([s + k for s, c, _, _ in self.paths for k in range(c)], dtype=int)
        self.comp_idx = []        # (index into x / y, component glyph name)
        index = 0
        for shape in self.shapes:
            if shape[0] == "path":
                index += len(shape[2])
            else:
                self.comp_idx.append((index, shape[1].name))
                index += 1
        self._segments = self._joins = self._terminals = self._polygons = None
        self._middle = False

    @classmethod
    def fitted(cls, drawing, frame):
        """A copy of `drawing` scaled into the box that `frame` occupies, master by master.

        Used to give the upright tick of d-caron and t-caron the shape of the round comma: same place
        and height as the tick, drawn like the comma.
        """
        new = cls.__new__(cls)
        new.__dict__.update(drawing.__dict__)
        new.x, new.y, new.w = drawing.x.copy(), drawing.y.copy(), frame.w.copy()
        new.anchor_names = []
        nodes = drawing.node_idx
        for m in range(3):
            fx, fy = frame.x[m][frame.node_idx], frame.y[m][frame.node_idx]
            dx, dy = drawing.x[m][nodes], drawing.y[m][nodes]
            scale = (fy.max() - fy.min()) / (dy.max() - dy.min())
            new.x[m][nodes] = fx.min() + (dx - dx.min()) * scale
            new.y[m][nodes] = fy.max() - (dy.max() - dy) * scale
        new.x, new.y = new.x[:, :len(nodes)], new.y[:, :len(nodes)]
        new._segments = new._joins = new._terminals = new._polygons = None
        new._middle = False
        return new

    @classmethod
    def placed(cls, parts, x, y, widths):
        """A glyph made only of components. `parts` are the component objects; `x`, `y` hold one row
        per master with each component's position; `widths` the advance per master."""
        new = cls.__new__(cls)
        new.shapes = [("comp", part) for part in parts]
        new.paths, new.anchor_names = [], []
        new.x, new.y, new.w = np.array(x, dtype=float), np.array(y, dtype=float), np.array(widths, dtype=float)
        new.compatible, new.n_nodes = True, 0
        new.node_idx = np.array([], dtype=int)
        new.comp_idx = [(i, part.name) for i, part in enumerate(parts)]
        new._segments = new._joins = new._terminals = new._polygons = None
        new._middle = False
        return new

    @classmethod
    def merged(cls, composite, parts, keep_anchors=()):
        """A composite turned into one drawing: the outlines of `parts` (its components' data, in order,
        each made of paths only) at the places the composite puts them, with the first part's anchors.

        Used where a letter's own shape must not follow the letter it is built on: e-ogonek keeps the
        uncut end of e, because the ogonek grows out of it.
        """
        new = cls.__new__(cls)
        new.shapes, new.paths = [], []
        xs, ys, start = [[], [], []], [[], [], []], 0
        for (index, _), part in zip(composite.comp_idx, parts):
            for shape, (_, count, kinds, smooth) in zip(part.shapes, part.paths):
                new.shapes.append(shape)
                new.paths.append((start, count, kinds, smooth))
                start += count
            for m in range(3):
                xs[m].extend(part.x[m][part.node_idx] + composite.x[m][index])
                ys[m].extend(part.y[m][part.node_idx] + composite.y[m][index])
        base = parts[0]
        new.anchor_names = [name for name in base.anchor_names if name in keep_anchors]
        offset = len(base.x[0]) - len(base.anchor_names)
        for name in new.anchor_names:
            k = offset + base.anchor_names.index(name)
            for m in range(3):
                xs[m].append(base.x[m][k] + composite.x[m][composite.comp_idx[0][0]])
                ys[m].append(base.y[m][k] + composite.y[m][composite.comp_idx[0][0]])
        new.x, new.y, new.w = np.array(xs, dtype=float), np.array(ys, dtype=float), composite.w.copy()
        new.compatible, new.n_nodes = True, start
        new.node_idx = np.arange(start, dtype=int)
        new.comp_idx = []
        new._segments = new._joins = new._terminals = new._polygons = None
        new._middle = False
        return new

    def at(self, weight_x, weight_y):
        return lerp3(self.x, weight_x), lerp3(self.y, weight_y), lerp3(self.w, weight_x)

    def segments(self):
        """Cubic segments as index tuples (p0, p1, p2, p3)."""
        if self._segments is None:
            found = []
            for start, count, kinds, _ in self.paths:
                if count < 4:
                    continue
                for k in range(count):
                    if (kinds[k] == "curve" and kinds[(k - 1) % count] == "offcurve"
                            and kinds[(k - 2) % count] == "offcurve" and kinds[(k - 3) % count] != "offcurve"):
                        found.append(tuple(start + (k - j) % count for j in (3, 2, 1, 0)))
            self._segments = found
        return self._segments

    def joins(self):
        """Smooth on-curve points: (node, before, after, curve before?, curve after?, far before, far after)."""
        if self._joins is None:
            found = []
            for start, count, kinds, smooths in self.paths:
                if count < 3:
                    continue
                for k in range(count):
                    if kinds[k] == "offcurve" or not smooths[k]:
                        continue
                    prev, nxt = (k - 1) % count, (k + 1) % count
                    curve_in, curve_out = kinds[prev] == "offcurve", kinds[nxt] == "offcurve"
                    if not (curve_in or curve_out):
                        continue
                    far_in, far_out = (k - 2) % count, (k + 2) % count
                    full = (curve_in and curve_out and count >= 6
                            and kinds[far_in] == "offcurve" and kinds[far_out] == "offcurve")
                    found.append((start + k, start + prev, start + nxt, curve_in, curve_out,
                                  start + far_in if full else None, start + far_out if full else None))
            self._joins = found
        return self._joins

    def middle_bar(self):
        """The horizontal bar in the middle of a letter: (node on its lower edge, node on its upper edge).

        Found on the regular master as the pair of horizontal edges, one stroke apart and away from
        the top and bottom of the letter, that overlap the most.
        """
        if self._middle is False:
            x, y = self.x[1], self.y[1]
            edges = []
            for start, count, kinds, _ in self.paths:
                for k in range(count):
                    nxt = (k + 1) % count
                    i, j = start + k, start + nxt
                    if kinds[k] != "offcurve" and kinds[nxt] == "line" and abs(y[i] - y[j]) <= 1 and abs(x[i] - x[j]) > 40:
                        edges.append((float(y[i]), min(x[i], x[j]), max(x[i], x[j]), i))
            best = None
            if edges and self.n_nodes:
                on = [s + k for s, c, kinds, _ in self.paths for k in range(c) if kinds[k] != "offcurve"]
                bottom, top = min(y[i] for i in on), max(y[i] for i in on)
                height = top - bottom
                for lo in edges:
                    for hi in edges:
                        if not 60 <= hi[0] - lo[0] <= 300:
                            continue
                        if lo[0] < bottom + 0.2 * height or hi[0] > top - 0.2 * height:
                            continue
                        overlap = min(lo[2], hi[2]) - max(lo[1], hi[1])
                        if overlap > 40 and (best is None or overlap > best[0]):
                            best = (overlap, lo[3], hi[3])
            self._middle = (best[1], best[2]) if best else None
        return self._middle

    def _inked(self, px, py):
        """Whether a point lies in the letter's ink, on the regular master."""
        if self._polygons is None:
            x, y = self.x[1], self.y[1]
            self._polygons = []
            for start, count, kinds, _ in self.paths:
                on = [k for k in range(count) if kinds[k] != "offcurve"]
                points = []
                for n, k in enumerate(on):
                    nxt = on[(n + 1) % len(on)]
                    between = [(k + j) % count for j in range(1, (nxt - k) % count or count)]
                    p0, p3 = (x[start + k], y[start + k]), (x[start + nxt], y[start + nxt])
                    points.append(p0)
                    if len(between) == 2:
                        p = (p0, *((x[start + i], y[start + i]) for i in between), p3)
                        points.extend(_point(p, t / 12) for t in range(1, 12))
                self._polygons.append(points)
        turns = 0
        for points in self._polygons:
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
                if (ay <= py) != (by <= py):
                    side = (bx - ax) * (py - ay) - (px - ax) * (by - ay)
                    turns += 1 if by > ay and side > 0 else -1 if by < ay and side < 0 else 0
        return turns != 0

    def terminals(self, stem):
        """Flat cuts at the end of curved strokes.

        Each is (curve A: p0 p1 p2 a, curve B: b q1 q2 q3, side of a): the stroke's two edges are the
        curve arriving at `a` and the curve leaving `b`; the cut is the line a-b. `side` records which
        end pulls back when the cut is turned, decided on the regular master so all masters agree.
        """
        if self._terminals is None:
            x, y = self.x[1], self.y[1]
            on = [s + k for s, c, kinds, _ in self.paths for k in range(c) if kinds[k] != "offcurve"]
            found = []
            if on:
                cx = (min(x[i] for i in on) + max(x[i] for i in on)) / 2
                cy = (min(y[i] for i in on) + max(y[i] for i in on)) / 2
            for start, count, kinds, _ in self.paths:
                if count < 8:
                    continue
                for k in range(count):
                    idx = [(k + j) % count for j in range(-3, 5)]
                    want = ("on", "offcurve", "offcurve", "curve", "line", "offcurve", "offcurve", "curve")
                    got = [kinds[i] for i in idx]
                    if got[0] == "offcurve" or tuple(got[1:]) != want[1:]:
                        continue
                    p0, p1, ha, a, b, hb, q2, q3 = (start + i for i in idx)
                    ua = _unit(x[a] - x[ha], y[a] - y[ha])
                    ub = _unit(x[b] - x[hb], y[b] - y[hb])
                    cut = (x[b] - x[a], y[b] - y[a])
                    length = math.hypot(*cut)
                    if ua is None or ub is None or not 0.45 * stem < length < 2.4 * stem:
                        continue
                    if ua[0] * ub[0] + ua[1] * ub[1] < 0.4:          # the two edges must run alongside
                        continue
                    u = _unit(ua[0] + ub[0], ua[1] + ub[1])
                    if abs(u[0] * cut[0] + u[1] * cut[1]) / length > 0.55:   # the cut must cross the stroke
                        continue
                    mx, my = (x[a] + x[b]) / 2, (y[a] + y[b]) / 2
                    if self._inked(mx + u[0] * HIDDEN, my + u[1] * HIDDEN):
                        continue                                     # a stroke that ends inside another, as the bar of an italic e
                    # Turn the cut the way a radial cut on a round stroke would face.
                    if abs(u[0]) > abs(u[1]):
                        turn = (0.0, -1.0 if my > cy else 1.0)
                    else:
                        turn = (1.0 if mx > cx else -1.0, 0.0)
                    side_a = 1.0 if (x[a] - mx) * turn[0] + (y[a] - my) * turn[1] > 0 else -1.0
                    found.append(((p0, p1, ha, a), (b, hb, q2, q3), side_a))
            self._terminals = found
        return self._terminals


# --- operations -------------------------------------------------------------------------------------

def fix_smooth(data, x, y):
    """Put the handles of smooth points back in line with each other (or with an adjoining line)."""
    for node, prev, nxt, curve_in, curve_out, _, _ in data.joins():
        px, py = x[node], y[node]
        din = (px - x[prev], py - y[prev])
        dout = (x[nxt] - px, y[nxt] - py)
        lin, lout = math.hypot(*din), math.hypot(*dout)
        if lin < 1e-6 or lout < 1e-6:
            continue
        if curve_in and curve_out:
            u = _unit(din[0] + dout[0], din[1] + dout[1])
        elif curve_out:
            u = (din[0] / lin, din[1] / lin)        # follow the incoming line
        else:
            u = (dout[0] / lout, dout[1] / lout)    # follow the outgoing line
        if u is None:
            continue
        if (din[0] * u[0] + din[1] * u[1]) / lin < 0.94 or (dout[0] * u[0] + dout[1] * u[1]) / lout < 0.94:
            continue                                 # a real corner flagged smooth; leave it
        if curve_in:
            x[prev], y[prev] = px - lin * u[0], py - lin * u[1]
        if curve_out:
            x[nxt], y[nxt] = px + lout * u[0], py + lout * u[1]


def shift_middle(data, x, y, delta):
    """Move the letter's middle bar up (positive) or down by `delta` units."""
    bar = data.middle_bar()
    if bar is None or not delta:
        return False
    yy = y[data.node_idx]
    low, high = float(y[bar[0]]), float(y[bar[1]])
    bottom, top = float(yy.min()), float(yy.max())
    delta = max(min(delta, top - high - 60), -(low - bottom - 60))     # leave room above and below
    below, above = yy <= low, yy >= high
    middle = ~below & ~above
    yy[below] = bottom + (yy[below] - bottom) * (low + delta - bottom) / (low - bottom)
    yy[above] = top - (top - yy[above]) * (top - high - delta) / (top - high)
    yy[middle] += delta
    y[data.node_idx] = yy
    return True


def square_arcs(data, x, y, amount):
    """Change tension on every arc whose end tangents meet in front of it (i.e. no S-curves).

    Positive makes rounds squarer, negative makes them rounder.
    """
    target = SQUARE_TARGET if amount > 0 else ROUND_TARGET
    amount = abs(amount)
    for p0, p1, p2, p3 in data.segments():
        d0 = (x[p1] - x[p0], y[p1] - y[p0])
        d3 = (x[p2] - x[p3], y[p2] - y[p3])
        cross = d0[0] * d3[1] - d0[1] * d3[0]
        if abs(cross) < 1e-6:
            continue
        r = (x[p3] - x[p0], y[p3] - y[p0])
        a = (r[0] * d3[1] - r[1] * d3[0]) / cross      # corner = p0 + a * d0 = p3 + b * d3
        b = (r[0] * d0[1] - r[1] * d0[0]) / cross
        if a < 1.02 or b < 1.02:
            continue
        for node, handle, d, reach in ((p0, p1, d0, a), (p3, p2, d3, b)):
            frac = 1 / reach
            if (frac >= target) == (target == SQUARE_TARGET):      # already past the target
                continue
            scale = (frac + amount * (target - frac)) / frac
            x[handle] = x[node] + d[0] * scale
            y[handle] = y[node] + d[1] * scale


def harmonize(data, x, y, strength=1.0):
    """Make curvature continuous across smooth points by sliding them along their tangent."""
    for node, prev, nxt, _, _, far_in, far_out in data.joins():
        if far_in is None:
            continue
        a1, a2 = (x[far_in], y[far_in]), (x[prev], y[prev])
        b1, b2 = (x[nxt], y[nxt]), (x[far_out], y[far_out])
        da = (a2[0] - a1[0], a2[1] - a1[1])
        db = (b1[0] - b2[0], b1[1] - b2[1])
        cross = da[0] * db[1] - da[1] * db[0]
        if abs(cross) < 1e-6:
            continue
        r = (b2[0] - a1[0], b2[1] - a1[1])
        s = (r[0] * db[1] - r[1] * db[0]) / cross
        t = (r[0] * da[1] - r[1] * da[0]) / cross
        if s <= 1.0 or t <= 1.0:                       # tangent lines must meet beyond both handles
            continue
        d = (a1[0] + s * da[0], a1[1] + s * da[1])
        near_a, near_b = math.hypot(a2[0] - d[0], a2[1] - d[1]), math.hypot(b1[0] - d[0], b1[1] - d[1])
        len_a, len_b = math.hypot(*da), math.hypot(*db)
        if min(near_a, near_b, len_a, len_b) < 1e-6:
            continue
        ratio = math.sqrt((len_a / near_a) * (near_b / len_b))
        pos = ratio / (ratio + 1)
        if not 0.12 < pos < 0.88:
            continue
        nx, ny = a2[0] + (b1[0] - a2[0]) * pos, a2[1] + (b1[1] - a2[1]) * pos
        x[node] += (nx - x[node]) * strength
        y[node] += (ny - y[node]) * strength


def _split(p, t):
    """de Casteljau at t (any t, also beyond 0..1): returns the [0,t] and [t,1] control polygons."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    ax, ay = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
    bx, by = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
    cx, cy = x2 + (x3 - x2) * t, y2 + (y3 - y2) * t
    dx, dy = ax + (bx - ax) * t, ay + (by - ay) * t
    ex, ey = bx + (cx - bx) * t, by + (cy - by) * t
    fx, fy = dx + (ex - dx) * t, dy + (ey - dy) * t
    return ((x0, y0), (ax, ay), (dx, dy), (fx, fy)), ((fx, fy), (ex, ey), (cx, cy), (x3, y3))


def _point(p, t):
    return _split(p, t)[0][3]


def _hit(p, origin, direction, guess, lo, hi):
    """Parameter where cubic `p` crosses the line through `origin` along `direction`.

    Brackets the crossing nearest to `guess` by sampling, then bisects; None if there is none.
    """
    def side(t):
        px, py = _point(p, t)
        return direction[0] * (py - origin[1]) - direction[1] * (px - origin[0])

    steps = 48
    ts = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    fs = [side(t) for t in ts]
    brackets = [(ts[i], ts[i + 1]) for i in range(steps) if fs[i] == 0 or fs[i] * fs[i + 1] < 0]
    if not brackets:
        return None
    t0, t1 = min(brackets, key=lambda br: abs((br[0] + br[1]) / 2 - guess))
    f0 = side(t0)
    for _ in range(40):
        mid = (t0 + t1) / 2
        fm = side(mid)
        if f0 * fm <= 0:
            t1 = mid
        else:
            t0, f0 = mid, fm
    return (t0 + t1) / 2


def cut_terminals(data, x, y, degrees, aperture, stem, skip=(), soften=0.0):
    """Re-cut stroke ends: set the cut `degrees` off the horizontal or vertical it is nearest to, and move
    it `aperture` units back along the stroke.

    The angle is where the cut ends up, not how far it is turned: Raveo's headline drawing cuts its
    strokes level, its text drawing already at about 15 degrees, and both should come out the same.

    `soften` (0..1) then turns the last stretch of both edges toward the cut's own direction, so the
    stroke meets the cut more squarely instead of leaving one acute and one obtuse corner.
    """
    for number, (ca, cb, side_a) in enumerate(data.terminals(stem)):
        if number in skip:
            continue
        a, ha, b, hb = ca[3], ca[2], cb[0], cb[1]
        ua = _unit(x[a] - x[ha], y[a] - y[ha])
        ub = _unit(x[b] - x[hb], y[b] - y[hb])
        if ua is None or ub is None:
            continue
        u = _unit(ua[0] + ub[0], ua[1] + ub[1])
        cut = (x[b] - x[a], y[b] - y[a])
        # Sign of the turn: the end on the `side` flank pulls back, the other reaches forward.
        spin = 1.0 if (-cut[1] * u[0] + cut[0] * u[1]) * side_a > 0 else -1.0
        lean = math.atan2(cut[1], cut[0])
        lean -= round(lean / (math.pi / 2)) * (math.pi / 2)             # how far the cut already is off level or upright
        phi = math.radians(degrees) * spin - lean
        direction = (cut[0] * math.cos(phi) - cut[1] * math.sin(phi),
                     cut[0] * math.sin(phi) + cut[1] * math.cos(phi))
        origin = ((x[a] + x[b]) / 2 - aperture * u[0], (y[a] + y[b]) / 2 - aperture * u[1])
        pa = [(x[i], y[i]) for i in ca]
        pb = [(x[i], y[i]) for i in cb]
        new_a = _recut(pa, 3, ua, origin, direction)
        new_b = _recut(pb, 0, ub, origin, direction)
        # Inter's italic s ends each edge in a very short curve. Where the cut falls behind it, that curve and
        # the one it continues are laid out again along the longer one, so the edge keeps its path and its points.
        far_a = far_b = found = found_b = None
        if new_a is None:
            far_a = _neighbour(data, ca, -1)
            found = far_a and _recut_far([(x[i], y[i]) for i in far_a], 3, origin, direction)
            if found:
                new_a = found[1]
        if new_b is None:
            far_b = _neighbour(data, cb, 1)
            found_b = far_b and _recut_far([(x[i], y[i]) for i in far_b], 0, origin, direction)
            if found_b:
                new_b = found_b[0]
        if new_a is None or new_b is None:
            continue
        if far_a and new_a is found[1]:
            for i, (nx, ny) in zip(far_a, found[0]):
                x[i], y[i] = nx, ny
        if far_b and new_b is found_b[0]:
            for i, (nx, ny) in zip(far_b, found_b[1]):
                x[i], y[i] = nx, ny
        if soften:
            new_a, new_b = _soften(list(new_a), list(new_b), u, soften)
        for i, (nx, ny) in zip(ca, new_a):
            x[i], y[i] = nx, ny
        for i, (nx, ny) in zip(cb, new_b):
            x[i], y[i] = nx, ny


def _soften(pa, pb, stroke, amount):
    """Turn both edge ends toward the cut's normal and shorten the cut to the stroke's own width."""
    (ax, ay), (bx, by) = pa[3], pb[0]
    cut = (bx - ax, by - ay)
    length = math.hypot(*cut)
    normal = (-cut[1] / length, cut[0] / length)
    if normal[0] * stroke[0] + normal[1] * stroke[1] < 0:
        normal = (-normal[0], -normal[1])
    cos = max(0.5, min(1.0, normal[0] * stroke[0] + normal[1] * stroke[1]))
    keep = 1 - amount * (1 - cos)                     # a slanted cut is longer than the stroke is wide
    mx, my = (ax + bx) / 2, (ay + by) / 2
    for pts, end, handle in ((pa, 3, 2), (pb, 0, 1)):
        ex, ey = pts[end]
        hx, hy = pts[handle]
        nx, ny = mx + (ex - mx) * keep, my + (ey - my) * keep
        hx, hy = hx + nx - ex, hy + ny - ey
        reach = math.hypot(nx - hx, ny - hy)
        if reach < 1e-6:
            pts[end] = (nx, ny)
            continue
        tx, ty = (nx - hx) / reach, (ny - hy) / reach
        vx, vy = tx + (normal[0] - tx) * amount, ty + (normal[1] - ty) * amount
        v = math.hypot(vx, vy)
        pts[end] = (nx, ny)
        pts[handle] = (nx - vx / v * reach, ny - vy / v * reach)
    return pa, pb


def _neighbour(data, curve, step):
    """The cubic before (`step` -1) or after (+1) `curve` on the same path, as four indices, or None."""
    for start, count, kinds, _ in data.paths:
        if start <= curve[0] < start + count:
            at = lambda i: start + (i - start) % count
            if step > 0:
                ids = [curve[3], at(curve[3] + 1), at(curve[3] + 2), at(curve[3] + 3)]
            else:
                ids = [at(curve[0] - 3), at(curve[0] - 2), at(curve[0] - 1), curve[0]]
            ok = [kinds[i - start] == "offcurve" for i in ids] == [False, True, True, False]
            return ids if ok else None
    return None


def _piece(p, t0, t1):
    """The part of cubic `p` between parameters t0 and t1."""
    right = _split(p, t0)[1]
    return _split(right, (t1 - t0) / (1 - t0) if t0 < 1 else 0.0)[0]


def _recut_far(p, end, origin, direction, share=0.3):
    """Cut cubic `p`, the longer neighbour of a short end piece, on the line and return two cubics along
    it in path order: for `end` 3 (the stroke arrives at the cut) the remaining long part and a new short
    end piece; for `end` 0 (it leaves the cut) the new short piece and the remaining long part."""
    if end == 3:
        t = _hit(p, origin, direction, 1.0, 0.1, 1.0)
        return None if t is None else (_piece(p, 0.0, t * (1 - share)), _piece(p, t * (1 - share), t))
    t = _hit(p, origin, direction, 0.0, 0.0, 0.9)
    if t is None:
        return None
    mid = t + (1 - t) * share
    return _piece(p, t, mid), _piece(p, mid, 1.0)


def _recut(p, end, outward, origin, direction):
    """Move one end of cubic `p` onto the cut line.

    Pulling back splits the curve exactly, so the edge keeps its shape. Reaching forward continues
    along the end tangent (a cubic extrapolated past its end can curl back on itself).
    """
    ex, ey = p[end]
    denom = outward[0] * direction[1] - outward[1] * direction[0]
    if abs(denom) < 1e-9:
        return None
    reach = ((origin[0] - ex) * direction[1] - (origin[1] - ey) * direction[0]) / denom
    if reach >= 0:
        moved = list(p)
        moved[end] = (ex + reach * outward[0], ey + reach * outward[1])
        return moved
    if end == 3:
        t = _hit(p, origin, direction, 1.0, 0.05, 1.0)
        return None if t is None else _split(p, t)[0]
    t = _hit(p, origin, direction, 0.0, 0.0, 0.95)
    return None if t is None else _split(p, t)[1]


def slant_compensate(data, x, y, k, only=None):
    """Thin and thicken curved strokes the way a drawn italic does, on the upright letter.

    Leaning a letter over makes a curved stroke heavier where it runs from upper left to lower right
    and lighter where it runs the other way: an o ends up with two fat shoulders and two thin ones.
    Inter's italic takes about two thirds of that back, and its drawing differs from a plain slanted
    roman by exactly this: each edge moved along its own normal by -k * sin(2 * direction of the normal).
    That is nothing on stems and bars and most on the diagonals. This does the same for letters whose
    italic was not drawn by Inter (Raveo's a, the leg of its R, the arm of its r, the upright e and f).

    `k` is the largest move in units; `only` limits it to some of the letter's paths.
    Points on stems, bars, corners and straight lines stay; a curve between them changes its tension.
    """
    if not k:
        return
    for number, (start, count, kinds, smooths) in enumerate(data.paths):
        if count < 4 or (only is not None and number not in only):
            continue
        moved = {}
        for j in range(count):                                   # smooth points between two curves ride with the edge
            prev, nxt = (j - 1) % count, (j + 1) % count
            if kinds[j] == "offcurve" or not smooths[j] or kinds[prev] != "offcurve" or kinds[nxt] != "offcurve":
                continue
            t = _unit(x[start + nxt] - x[start + prev], y[start + nxt] - y[start + prev])
            if t is None:
                continue
            nx, ny = t[1], -t[0]                                 # out of the ink: paths run with the ink on their left
            d = -2 * k * nx * ny
            moved[j] = (d * nx, d * ny)
        for j, (dx, dy) in moved.items():
            for i in ((j - 1) % count, j, (j + 1) % count):
                x[start + i] += dx
                y[start + i] += dy
        for j in range(count):                                   # each curve then bulges or flattens between its ends
            if not (kinds[j] == "curve" and kinds[(j - 1) % count] == "offcurve" and kinds[(j - 2) % count] == "offcurve"):
                continue
            i0, i1, i2, i3 = (start + (j - n) % count for n in (3, 2, 1, 0))
            p = [(x[i], y[i]) for i in (i0, i1, i2, i3)]
            (ax, ay), (bx, by) = _split(p, 0.5)[0][2], _split(p, 0.5)[1][1]
            t = _unit(bx - ax, by - ay)
            if t is None:
                continue
            nx, ny = t[1], -t[0]
            ends = [moved.get((j - 3) % count, (0.0, 0.0)), moved.get(j, (0.0, 0.0))]
            want = -2 * k * nx * ny
            have = ((ends[0][0] + ends[1][0]) * nx + (ends[0][1] + ends[1][1]) * ny) / 2
            hx, hy = (p[1][0] - p[0][0]) + (p[2][0] - p[3][0]), (p[1][1] - p[0][1]) + (p[2][1] - p[3][1])
            pull = 0.375 * (hx * nx + hy * ny)                   # how far the middle moves when both handles grow by their length
            if abs(pull) < 0.2 * math.hypot(hx, hy) * 0.375 or abs(pull) < 1e-6:
                continue                                         # an S-curve or a nearly straight piece
            grow = max(-LEAN_MOST, min(LEAN_MOST, (want - have) / pull))
            x[i1], y[i1] = p[0][0] + (p[1][0] - p[0][0]) * (1 + grow), p[0][1] + (p[1][1] - p[0][1]) * (1 + grow)
            x[i2], y[i2] = p[3][0] + (p[2][0] - p[3][0]) * (1 + grow), p[3][1] + (p[2][1] - p[3][1]) * (1 + grow)


# --- the r of the wordmark Raveo was first drawn for -------------------------------------------------------------------
#
# Raveo's r and the wordmark's r have the same point structure (stem, deep junction, two-curve
# underside, slanted cut, two-curve top, notch at the stem), so the wordmark's drawing can be laid
# over Raveo's points in every master. Each entry is (x, y, edge) for one point of the arm:
#   x     distance right of the stem, as a share of the arm's length
#   y     for "top" points: distance below the x-height line, as a share of the x-height
#         for "under" points: distance below the arm's highest point, in arm thicknesses
LOGO_R_ARM = 1.42            # the wordmark's arm is this much longer than Raveo's at the same style
LOGO_R_REGULAR = 0.155       # thickness of the Regular's arm, as a share of the x-height
LOGO_R_DEEP = 0.13           # the junction never sits higher than it would for an arm this thick
LOGO_R = {
    1: (0.0, 2.460, "under"),      # junction on the stem
    2: (0.0, 1.702, "under"),
    3: (0.183, 1.000, "under"),
    4: (0.604, 1.000, "under"),    # flat point of the underside
    5: (0.674, 1.000, "under"),
    6: (0.755, 1.037, "under"),
    7: (0.829, 1.084, "under"),    # lower corner of the cut
    8: (1.0, 0.026, "top"),        # upper corner of the cut
    9: (0.892, 0.004, "top"),
    10: (0.767, -0.020, "top"),
    11: (0.616, -0.020, "top"),    # highest point of the arm
    12: (0.381, -0.020, "top"),
    13: (0.067, 0.030, "top"),
    14: (None, 0.131, "top"),      # where the top edge meets the stem
    15: (None, 0.131, "top"),
}
_R_KINDS = ["line", "line", "offcurve", "offcurve", "curve", "offcurve", "offcurve", "curve", "line",
            "offcurve", "offcurve", "curve", "offcurve", "offcurve", "curve", "line", "line", "line", "line"]


def logo_r(data, x, y, x0, y0, amount, s=1.0):
    """Blend r toward the wordmark's r. Returns how much wider the letter became.

    `s` is width / 100. The wordmark is wide, and its long arm leaves a hole after the r when
    letters are narrow, so below the regular width the arm lengthens less: not at all at half width.

    `x0`, `y0` are the letter's points before any other operation; they give the stem, the x-height,
    the arm's thickness and its length at this master.
    """
    if not data.paths or data.paths[0][2] != _R_KINDS:
        return 0.0
    start = data.paths[0][0]
    stem_right, top, base = x0[start + 1], y0[start + 16], y0[start]
    x_height = top - base
    thickness = y0[start + 11] - y0[start + 4]
    reach = (1 + (LOGO_R_ARM - 1) * max(0.0, min(1.0, (s - 0.5) / 0.5))) * (x0[start + 8] - stem_right)
    peak = top + 0.020 * x_height
    before = x[start + 8]
    # The wordmark was drawn bold. In weights lighter than Regular two of its measures must not
    # follow as drawn: the arm would leave the stem too high up for so thin a stroke, and the top
    # edge would dip at the end by more than the stroke is thick, closing the cut to a point.
    light = min(1.0, thickness / (LOGO_R_REGULAR * x_height))
    deep = max(thickness, LOGO_R_DEEP * x_height)
    for offset, (nx, ny, edge) in LOGO_R.items():
        i = start + offset
        if edge == "top":
            ty = peak - (ny + 0.020) * x_height * light if offset in (8, 9) else top - ny * x_height
        else:
            ty = peak - ny * (deep if offset in (1, 2) else thickness)
        y[i] += (ty - y[i]) * amount
        if nx is not None:
            x[i] += (stem_right + nx * reach - x[i]) * amount
    return 0.7 * (x[start + 8] - before)


# --- measurement --------------------------------------------------------------------------------------

def _end_curvature(p0, p1, p2, at_end):
    """Curvature of a cubic at one end, from the end point, its handle and the opposite handle."""
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    bx, by = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        return 0.0
    k = (2 / 3) * (ax * by - ay * bx) / length ** 3
    return -k if at_end else k


def curve_report(data, x, y):
    """Per smooth curve-to-curve point: (kink in degrees, relative curvature jump, node index)."""
    out = []
    for node, prev, nxt, curve_in, curve_out, far_in, far_out in data.joins():
        if far_in is None:
            continue
        p = (x[node], y[node])
        din = (p[0] - x[prev], p[1] - y[prev])
        dout = (x[nxt] - p[0], y[nxt] - p[1])
        lin, lout = math.hypot(*din), math.hypot(*dout)
        if lin < 1e-6 or lout < 1e-6:
            continue
        cos = max(-1.0, min(1.0, (din[0] * dout[0] + din[1] * dout[1]) / (lin * lout)))
        kink = math.degrees(math.acos(cos))
        k_in = _end_curvature(p, (x[prev], y[prev]), (x[far_in], y[far_in]), True)
        k_out = _end_curvature(p, (x[nxt], y[nxt]), (x[far_out], y[far_out]), False)
        top = max(abs(k_in), abs(k_out))
        jump = abs(k_in - k_out) / top if top > 2e-5 else 0.0
        out.append((kink, jump, node))
    return out
