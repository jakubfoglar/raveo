"""Pair clearance: how close two neighbouring glyphs actually get, and a guard that keeps them apart.

Each glyph is reduced to a left and a right profile: its outermost x in horizontal bands. Two glyphs
are too close when some part of the left one comes nearer than the floor to some part of the right
one, measured as a real distance: across, and up or down between bands.

    room(L, R, k) = min over bands b of  advance(L) - right(L)[b] + left(R)[b + k] + kern(L, R)
    short(L, R)   = max over k of  sqrt(floor^2 - height(k)^2) - room(L, R, k)

So a y that passes under the bar of a T is not counted as touching it unless the bar really comes
within the floor of it, however thick the bar is.
"""
import math

import numpy as np

BAND = 20                    # band height in font units
Y0, Y1 = -560, 2080          # vertical range covered
BIG = 1e6


def profile(contours):
    """(left, right) outermost x per band for a decomposed outline; +BIG / -BIG where empty."""
    bands = (Y1 - Y0) // BAND
    left, right = np.full(bands, BIG), np.full(bands, -BIG)
    for contour in contours:
        pts = _flatten(contour)
        if len(pts) < 2:
            continue
        xs, ys = pts[:, 0], pts[:, 1]
        index = np.clip(((ys - Y0) // BAND).astype(int), 0, bands - 1)
        np.minimum.at(left, index, xs)
        np.maximum.at(right, index, xs)
    return left, right


def above(reach, height):
    """A glyph's room to the right, counting only what it has above `height` (the tick of d-caron, say)."""
    out = reach.copy()
    out[:max(0, int((height - Y0) // BAND))] = BIG
    return out


def reach_over(old_right, new_right, lefts, top):
    """How much closer a letter whose arm has grown may come to each right-hand glyph (units, never negative).

    The wordmark r has a longer arm than Raveo's. Before a letter that stands upright at the arm's
    height (n, i, k) the arm's tip keeps the distance Raveo gave it. Before one whose side is set back
    there (the shoulder of o, e, c), or that has nothing there at all (a full stop), the arm reaches
    over it instead, so the r's stem stays as near to that letter as it was.
    old_right, new_right: (bands,) right profiles before and after; lefts: (n, bands); top: the x-height.
    """
    body = slice(max(0, int((0 - Y0) // BAND)), int((top - Y0) // BAND) + 3)     # up to just above the x-height: no accents
    reach_new, reach_old = new_right[body].max(), old_right[body].max()
    growth = float(reach_new - reach_old)
    if growth <= 0 or reach_new < -BIG / 2 or reach_old < -BIG / 2:
        return np.zeros(lefts.shape[0])
    arm = np.zeros(new_right.shape[0], dtype=bool)
    arm[body] = new_right[body] >= reach_new - 40
    leftmost = lefts[:, body].min(axis=1)
    setback = np.where(leftmost < BIG / 2, lefts[:, arm].min(axis=1) - leftmost, 0.0)
    return np.clip(np.minimum(growth, setback), 0.0, None)


def shortfall(reach, lefts, kern, floor):
    """How far each right-hand glyph falls short of `floor` behind one left-hand glyph (negative = clear).

    reach: (bands,) advance minus right profile of the left glyph. lefts: (n, bands) left profiles.
    kern, floor: (n,) per right-hand glyph.
    """
    bands = reach.shape[0]
    worst = np.full(lefts.shape[0], -BIG)
    for k in range(-(int(math.ceil(float(np.max(floor)) / BAND)) + 1), int(math.ceil(float(np.max(floor)) / BAND)) + 2):
        height = max(0, abs(k) - 1) * BAND                     # bands next to each other may touch
        need = np.sqrt(np.maximum(0.0, floor * floor - height * height))
        if not need.any():
            continue
        room = ((reach[None, :bands - k] + lefts[:, k:]) if k >= 0 else (reach[None, -k:] + lefts[:, :bands + k])).min(axis=1)
        worst = np.maximum(worst, np.where(need > 0, need - (room + kern), -BIG))
    return worst


def _flatten(contour):
    """Sample a closed contour of (x, y, kind) densely enough that no band is skipped."""
    n = len(contour)
    first = next((i for i, p in enumerate(contour) if p[2] != "offcurve"), None)
    if first is None:
        return np.zeros((0, 2))
    out, pending, last = [], [], contour[first][:2]
    for step in range(1, n + 1):
        px, py, kind = contour[(first + step) % n]
        if kind == "offcurve":
            pending.append((px, py))
            continue
        if len(pending) == 2:
            (ax, ay), (bx, by) = pending
            span = math.hypot(px - last[0], py - last[1]) + math.hypot(ax - last[0], ay - last[1])
            t = np.linspace(0, 1, max(4, int(span / 14)), endpoint=False)[:, None]
            mt = 1 - t
            out.append(mt ** 3 * np.array(last) + 3 * mt * mt * t * np.array((ax, ay))
                       + 3 * mt * t * t * np.array((bx, by)) + t ** 3 * np.array((px, py)))
        else:
            span = math.hypot(px - last[0], py - last[1])
            t = np.linspace(0, 1, max(2, int(span / 14)), endpoint=False)[:, None]
            out.append(np.array(last) + t * (np.array((px, py)) - np.array(last)))
        pending, last = [], (px, py)
    return np.vstack(out) if out else np.zeros((0, 2))


class Clearance:
    """Profiles and kerning for a set of glyphs at one location."""

    def __init__(self, pipeline, plan, loc, names):
        self.names = names
        lefts, rights, advances = [], [], []
        for name in names:
            contours, adv = pipeline.contours(plan, name, loc)
            l, r = profile(contours)
            lefts.append(l)
            rights.append(r)
            advances.append(adv)
        self.left, self.right = np.array(lefts), np.array(rights)
        self.advance = np.array(advances)
        self.kern = pipeline.kern_matrix(names, loc)

    def keep_gaps(self, name, old_profile, old_advance, top=None, over=1.0):
        """Re-kern one left glyph whose drawing changed, so each pair keeps the gap it had before.

        With `top` (the x-height), a longer arm may reach over a round neighbour by the share `over`
        of its growth: see reach_over.
        Returns the change per right-hand glyph and applies it to this object's kerning.
        """
        i = self.names.index(name)
        before = (old_advance - old_profile[1])[None, :] + self.left
        after = (self.advance[i] - self.right[i])[None, :] + self.left
        old, new = before.min(axis=1), after.min(axis=1)
        change = np.where((old < BIG / 2) & (new < BIG / 2), old - new, 0.0)
        if top is not None:
            change = change - over * reach_over(old_profile[1], self.right[i], self.left, top)
        self.kern[i] += change
        return change

    def short(self, i, floor, height=None):
        """Shortfall behind left glyph `i` for every right glyph; with `height`, for its part above that only."""
        reach = self.advance[i] - self.right[i]
        return shortfall(reach if height is None else above(reach, height), self.left, self.kern[i], floor)


def floor_for(left_text, right_text, stem, floors):
    """Smallest allowed gap for a pair: letters and figures may sit closer than punctuation does."""
    return stem * (floors["letters"] if left_text and right_text else floors["punctuation"])


def pair_shortfall(left_profile, left_advance, right_profile, kern, floor, height=None):
    """How much a single pair falls short of its floor (0 if it clears it)."""
    reach = left_advance - left_profile[1]
    worst = shortfall(reach if height is None else above(reach, height), right_profile[0][None, :], np.array([kern]), np.array([floor]))
    return max(0.0, float(worst[0]))


def shortfalls(clearance, is_text, stem, floors, joiners=(), ticks=(), tick_floor=None, tick_height=0.0):
    """{(left, right): units to add} for every pair in `clearance` closer than its floor.

    Pairs of `joiners` (f and t, whose crossbars are drawn to meet) are left alone, and so are
    shortfalls too small to see. Left glyphs in `ticks` (d-caron and its kin) also keep their tick,
    the part above `tick_height`, at least `tick_floor` units from whatever follows, joiner or not.
    """
    names = clearance.names
    text = np.array([is_text[n] for n in names])
    joins = np.array([n in joiners for n in names])
    out = {}
    for i, left in enumerate(names):
        floor = np.where(text[i] & text, floors["letters"], floors["punctuation"]) * stem
        short = clearance.short(i, floor)
        if joins[i]:
            short = np.where(joins, -BIG, short)
        if left in ticks and tick_floor:
            short = np.maximum(short, clearance.short(i, np.full(len(names), float(tick_floor)), tick_height))
        for j in np.nonzero(short > 0.04 * stem)[0]:
            out[(left, names[j])] = float(short[j])
    return out
