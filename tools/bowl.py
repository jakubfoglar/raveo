"""Shape controls for the bowl of the a.

The a's bowl is the closed lower part; its "bowl stroke" is the stroke that leaves the stem and runs
left before turning down. These controls move named groups of the a's points, so they work the same
in every master and can be set separately for the narrowest, regular and widest width.

    bowl_height   whole bowl stroke up (+) or down, i.e. where it leaves the stem
    bowl_slant    left end of the bowl stroke drops by this much (+ = the stroke rises toward the stem)
    bowl_arch     middle of the bowl stroke bows upward (+) or sags
    bowl_weight   bowl stroke thicker (+) or thinner
    join_thin     the stroke thins as it enters the stem (lifts its underside near the stem)
    bowl_corner   corner between the stem and the top of the bowl stroke: rounder (+) or tighter
    bowl_width    bowl reaches further left (+) or less far
    bowl_round    the bowl's curves squarer (+) or rounder (-), from -1 to 1
    join_notch    Raveo cuts a small notch into the stem where the bowl stroke's underside arrives (an ink
                  trap inherited from Inter). 0 removes it for a clean corner, 1 keeps Raveo's.

Distances are in font units as they would be at the regular width; sideways ones scale with width.
"""
import math

OUTER, COUNTER = 43, 14
KEYS = ("bowl_height", "bowl_slant", "bowl_arch", "bowl_weight", "join_thin", "bowl_corner", "bowl_width",
        "bowl_round", "join_notch")

# Raveo's a, by point index. Outside contour: 0-42, counter: 43-56.
STEM_EDGE, CORNER = 30, (30, 31, 32, 33)                 # stem's inner edge; the corner into the bowl stroke
TOP_EDGE = (33, 34, 35, 36, 37)                          # top of the bowl stroke, right to left
UNDERSIDE = (47, 48, 49, 50, 51)                         # its underside (the counter's top), left to right
STEM_JOIN = 52                                           # where the counter meets the stem
LEFT_OUT, LEFT_IN = (38, 39, 40), (44, 45, 46)           # leftmost points and their handles
ARCS = ((36, 37, 38, 39), (39, 40, 41, 42), (42, 0, 1, 2),          # outside: top-left, bottom-left, bottom-right
        (45, 46, 47, 48), (56, 43, 44, 45), (53, 54, 55, 56))       # counter: top-left, bottom-left, bottom-right


def _tension(x, y, arc, amount):
    """Move an arc's handles toward a squarer (+) or rounder (-) curve."""
    p0, p1, p2, p3 = arc
    d0, d3 = (x[p1] - x[p0], y[p1] - y[p0]), (x[p2] - x[p3], y[p2] - y[p3])
    cross = d0[0] * d3[1] - d0[1] * d3[0]
    if abs(cross) < 1e-6:
        return
    r = (x[p3] - x[p0], y[p3] - y[p0])
    reach = ((r[0] * d3[1] - r[1] * d3[0]) / cross, (r[0] * d0[1] - r[1] * d0[0]) / cross)
    if min(reach) < 1.02:
        return
    target = 0.8 if amount > 0 else 0.5
    for node, handle, d, far in ((p0, p1, d0, reach[0]), (p3, p2, d3, reach[1])):
        frac = 1 / far
        scale = (frac + abs(amount) * (target - frac)) / frac
        x[handle], y[handle] = x[node] + d[0] * scale, y[node] + d[1] * scale


def shape(x, y, settings, s):
    """Apply the bowl settings (a dict of the keys above) to the a's points. `s` is width / 100."""
    if len(x) < OUTER + COUNTER:
        return
    get = lambda key: float(settings.get(key) or 0)
    stem, left = x[STEM_EDGE], x[36]
    span = max(stem - left, 1.0)
    along = lambda i: min(1.3, max(0.0, (stem - x[i]) / span))        # 0 at the stem, 1 at the stroke's left end
    stroke = TOP_EDGE + UNDERSIDE

    height, slant, arch = get("bowl_height"), get("bowl_slant"), get("bowl_arch")
    for i in stroke + CORNER[:3] + (STEM_JOIN,):
        t = along(i)
        y[i] += height - slant * t + arch * 4 * min(t, 1.0) * (1 - min(t, 1.0))
    weight, thin = get("bowl_weight"), get("join_thin")
    for i in UNDERSIDE:
        y[i] += -weight + thin * (1 - min(along(i), 1.0)) ** 2
    y[STEM_JOIN] += thin

    notch = max(0.0, min(1.0, get("join_notch")))
    if notch < 1:                                       # end the underside exactly on the stem's edge
        tip, before, edge = UNDERSIDE[-1], UNDERSIDE[-2], x[STEM_JOIN + 1]
        run = x[tip] - x[before]
        clean_y = y[tip] if abs(run) < 1 else y[before] + (edge - x[before]) * (y[tip] - y[before]) / run
        for i in (tip, STEM_JOIN):
            x[i] = edge + (x[i] - edge) * notch
            y[i] = clean_y + (y[i] - clean_y) * notch

    corner = get("bowl_corner")
    if corner:
        qx = x[STEM_EDGE]                                             # where the stem edge and the stroke's top meet
        slope = (y[36] - y[33]) / (x[36] - x[33]) if abs(x[36] - x[33]) > 1 else 0.0
        qy = y[33] + (qx - x[33]) * slope
        for i in CORNER:
            x[i], y[i] = qx + (x[i] - qx) * (1 + corner), qy + (y[i] - qy) * (1 + corner)

    width = get("bowl_width") * s
    for i in LEFT_OUT + LEFT_IN:
        x[i] -= width
    roundness = max(-1.0, min(1.0, get("bowl_round")))
    if roundness:
        for arc in ARCS:
            _tension(x, y, arc, roundness)
