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
    over it instead, so the r's stem stays as near to that letter as it was. A letter with a part above
    the arm that comes further left (2, 3, 7, ?) is not reached under.
    old_right, new_right: (bands,) right profiles before and after; lefts: (n, bands); top: the x-height.
    """
    body = slice(max(0, int((0 - Y0) // BAND)), int((top - Y0) // BAND) + 3)     # up to just above the x-height: no accents
    reach_new, reach_old = new_right[body].max(), old_right[body].max()
    growth = float(reach_new - reach_old)
    if growth <= 0 or reach_new < -BIG / 2 or reach_old < -BIG / 2:
        return np.zeros(lefts.shape[0])
    arm = np.zeros(new_right.shape[0], dtype=bool)
    arm[body] = new_right[body] >= reach_new - 40
    arm[np.argmax(arm): int((1.45 * top - Y0) // BAND) + 1] = True      # and whatever stands above the arm: the hook
    leftmost = lefts[:, body].min(axis=1)                              # of a 2 or a 3, the bar of a 7
    setback = np.where(leftmost < BIG / 2, lefts[:, arm].min(axis=1) - leftmost, 0.0)
    return np.clip(np.minimum(growth, setback), 0.0, None)


def shortfall(reach, lefts, kern, floor):
    """How far a right-hand glyph falls short of `floor` behind a left-hand glyph (negative = clear).

    reach: (bands,) advance minus right profile of the left glyph; lefts: (n, bands) left profiles; or
    (n, bands) and (bands,) for many left glyphs before one right glyph. kern, floor: (n,).
    """
    bands = reach.shape[-1]
    worst = np.full(np.broadcast(reach[..., 0], lefts[..., 0]).shape, -BIG)
    for k in range(-(int(math.ceil(float(np.max(floor)) / BAND)) + 1), int(math.ceil(float(np.max(floor)) / BAND)) + 2):
        height = max(0, abs(k) - 1) * BAND                     # bands next to each other may touch
        need = np.sqrt(np.maximum(0.0, floor * floor - height * height))
        if not need.any():
            continue
        room = ((reach[..., :bands - k] + lefts[..., k:]) if k >= 0 else (reach[..., -k:] + lefts[..., :bands + k])).min(axis=-1)
        worst = np.maximum(worst, np.where(need > 0, need - (room + kern), -BIG))
    return worst


def apart(reach, lefts, kern, limit):
    """The narrowest distance in any direction, looked for no further than `limit` up and down, and
    measured as shortfall() measures it. Shapes as in shortfall()."""
    bands, span = reach.shape[-1], int(math.ceil(limit / BAND)) + 1
    best = np.full(np.broadcast(reach[..., 0], lefts[..., 0]).shape, float(BIG))
    for k in range(-span, span + 1):
        room = ((reach[..., :bands - k] + lefts[..., k:]) if k >= 0 else (reach[..., -k:] + lefts[..., :bands + k])).min(axis=-1) + kern
        best = np.minimum(best, np.hypot(np.clip(room, 0.0, None), max(0, abs(k) - 1) * BAND))
    return best


def kept_apart(was, limit, reach, lefts, kern):
    """The kerning change that sets pairs `was` apart again; none where they were out of each other's reach."""
    near = (was > 1.0) & (was < limit)
    change = shortfall(reach, lefts, kern, np.where(near, was, 1.0))
    return np.where(near & (np.abs(change) < 2 * limit), change, 0.0)     # the new drawing may be out of reach altogether


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

    def keep_apart(self, name, old_profile, old_advance, limit):
        """Re-kern a redrawn glyph on both sides, so it stands as far from every other glyph, in any
        direction, as its old drawing did: a round quote above an a shares no height with it.
        Returns the changes after it and before it, and applies them."""
        i = self.names.index(name)
        reach = self.advance[:, None] - self.right
        after = kept_apart(apart(old_advance - old_profile[1], self.left, self.kern[i], limit), limit,
                           reach[i], self.left, self.kern[i])
        self.kern[i] += after
        before = kept_apart(apart(reach, old_profile[0], self.kern[:, i], limit), limit, reach, self.left[i], self.kern[:, i])
        self.kern[:, i] += before
        return after, before

    def nearest(self, limit):
        """(n, n) narrowest distance of every pair in any direction, kerning included."""
        reach = self.advance[:, None] - self.right
        return np.array([apart(reach[i], self.left, self.kern[i], limit) for i in range(len(self.names))])

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


def pair_nearest(left_profile, left_advance, right_profile, kern, limit):
    """The narrowest distance of one pair in any direction: Clearance.nearest for two glyphs."""
    return float(apart(left_advance - left_profile[1], right_profile[0], kern, limit))


def shortfalls(clearance, is_text, stem, floors, joiners=(), ticks=(), tick_floor=None, tick_height=0.0, source=None):
    """{(left, right): units to add} for every pair in `clearance` closer than its floor.

    Two glyphs of one set in `joiners` are left alone: f and t, whose crossbars are drawn to meet, and
    the dashes, which are drawn to run on. Shortfalls too small to see are left alone as well. Left
    glyphs in `ticks` (d-caron and its kin) also keep their tick, the part above `tick_height`, at
    least `tick_floor` units from whatever follows, joiner or not.

    With `source`, the (n, n) distances the same pairs have in the untouched source, two letters stay
    floors["kept"] of a stem apart, or as far as the source set them if that was closer: tightening
    the spacing of a whole style must not close pairs that were kerned tight already (V and W).
    """
    names = clearance.names
    text = np.array([is_text[n] for n in names])
    sets = [np.array([n in group for n in names]) for group in joiners]
    out = {}
    for i, left in enumerate(names):
        letters = np.full(len(names), floors["letters"] * stem)
        if source is not None and floors.get("kept"):
            letters = np.maximum(letters, np.minimum(floors["kept"] * stem, source[i]))
        floor = np.where(text[i] & text, letters, floors["punctuation"] * stem)
        short = clearance.short(i, floor)
        for members in sets:
            if members[i]:
                short = np.where(members, -BIG, short)
        if left in ticks and tick_floor:
            short = np.maximum(short, clearance.short(i, np.full(len(names), float(tick_floor)), tick_height))
        for j in np.nonzero(short > 0.04 * stem)[0]:
            out[(left, names[j])] = float(short[j])
    return out
