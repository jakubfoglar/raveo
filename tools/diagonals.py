"""Diagonal strokes keep their weight when a letter is squeezed or stretched.

Changing the width changes the thickness of diagonals (N, M, V, W, A, K, X, Y, Z ...) differently
from the stems. Each diagonal stroke is found as two facing edges, measured, compared with the same
stroke in the regular width, and its edges are moved apart or together sideways.
"""
import math


def find(data, stem):
    """(edges, pairs) for a glyph, found on its regular master and cached on it.

    An edge is a run of straight, slanted segments: a line, or a line continued by a nearly straight
    curve. A pair is two edges that face each other across one stroke.
    """
    cached = getattr(data, "_diagonals", None)
    if cached is not None:
        return cached
    x, y = data.x[1], data.y[1]
    edges = []
    for start, count, kinds, _ in data.paths:
        on = [k for k in range(count) if kinds[k] != "offcurve"]
        if len(on) < 3:
            continue
        segs = []
        for j, k in enumerate(on):
            nxt = on[(j + 1) % len(on)]
            handles = [start + (k + 1 + h) % count for h in range((nxt - k - 1) % count)]
            a, b = start + k, start + nxt
            dx, dy = x[b] - x[a], y[b] - y[a]
            length = math.hypot(dx, dy)
            straight = all(abs((x[h] - x[a]) * dy - (y[h] - y[a]) * dx) < 0.08 * length * length for h in handles)
            slanted = length > 0.7 * stem and abs(dy) > 1 and 0.12 < abs(dx) / abs(dy) < 3.5
            segs.append({"a": a, "b": b, "handles": handles, "angle": math.atan2(dy, dx),
                         "ok": straight and slanted and len(handles) in (0, 2)})
        n = len(segs)
        first = next((j for j in range(n) if not segs[j]["ok"]), None)
        if first is None:
            continue
        run = []
        for step in range(1, n + 1):
            j = (first + step) % n
            seg = segs[j]
            same = run and abs(math.remainder(seg["angle"] - segs[run[-1]]["angle"], math.tau)) < math.radians(12)
            if seg["ok"] and (not run or same):
                run.append(j)
                continue
            if run:
                edges.append(_edge(segs, run, n))
            run = [j] if seg["ok"] else []
        if run:
            edges.append(_edge(segs, run, n))
    # Two edges are one stroke if they face each other and run side by side. The pair that overlaps over
    # the larger share of the longer edge wins: an italic's diagonals are not all equally thick, and by
    # thickness alone the long edge of a y's arm pairs with the short edge of its tail.
    rise = lambda e: abs(y[e["end"]] - y[e["start"]])
    candidates = []
    for i, e in enumerate(edges):
        for j in range(i + 1, len(edges)):
            gap = _stroke(x, y, e, edges[j])
            if gap and 0.3 * stem < gap[0] < 1.8 * stem:
                low, high = _span(x, y, e, edges[j])[:2]
                candidates.append((-round((high - low) / max(1.0, rise(e), rise(edges[j])), 2), gap[0], i, j))
    chosen, used = [], set()
    for _, gap, i, j in sorted(candidates):
        if i not in used and j not in used:
            chosen.append((gap, i, j))
            used |= {i, j}
    pairs = [(i, j) for _, i, j in sorted(chosen)]             # thinnest stroke first, the order they are worked on
    data._diagonals = (edges, pairs)
    return data._diagonals


def _edge(segs, run, n):
    first, last = segs[run[0]], segs[run[-1]]
    return {"start": first["a"], "end": last["b"], "inner": [segs[j]["b"] for j in run[:-1]],
            "handles": [h for j in run for h in segs[j]["handles"]],
            "before": segs[(run[0] - 1) % n], "after": segs[(run[-1] + 1) % n]}


def _stroke(x, y, e1, e2):
    """(thickness, sine of slope) of the stroke between two facing edges, or None if they do not face."""
    (ax, ay), (bx, by) = (x[e1["start"]], y[e1["start"]]), (x[e1["end"]], y[e1["end"]])
    (cx, cy), (dx, dy) = (x[e2["start"]], y[e2["start"]]), (x[e2["end"]], y[e2["end"]])
    v1, v2 = (bx - ax, by - ay), (dx - cx, dy - cy)
    l1, l2 = math.hypot(*v1), math.hypot(*v2)
    if l1 < 1 or l2 < 1 or (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2) > -0.97:      # must run opposite ways
        return None
    low, high = max(min(ay, by), min(cy, dy)), min(max(ay, by), max(cy, dy))
    if high - low < 0.3 * min(abs(v1[1]), abs(v2[1])):
        return None
    mid = (low + high) / 2
    x1 = ax + (mid - ay) / v1[1] * v1[0]
    x2 = cx + (mid - cy) / v2[1] * v2[0]
    if (x2 - x1) * -v1[1] <= 0:                    # ink lies to the left of the direction of travel
        return None
    sine = (abs(v1[1]) / l1 + abs(v2[1]) / l2) / 2
    return abs(x2 - x1) * sine, sine


def _cross_point(p, d, q, e):
    """Where the line through p along d meets the line through q along e (None if nearly parallel)."""
    det = d[0] * e[1] - d[1] * e[0]
    if abs(det) < 0.2 * math.hypot(*d) * math.hypot(*e):
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / det
    return p[0] + t * d[0], p[1] + t * d[1]


def _span(x, y, e1, e2):
    """Where two facing edges overlap in height, and each edge's x at the bottom and top of that span:
    (low, high, (x1 at low, x1 at high), (x2 at low, x2 at high)); None if they do not face."""
    if not _stroke(x, y, e1, e2):
        return None
    ends = []
    for e in (e1, e2):
        (ax, ay), (bx, by) = (x[e["start"]], y[e["start"]]), (x[e["end"]], y[e["end"]])
        ends.append((ax, ay, bx, by))
    low = max(min(ends[0][1], ends[0][3]), min(ends[1][1], ends[1][3]))
    high = min(max(ends[0][1], ends[0][3]), max(ends[1][1], ends[1][3]))
    at = lambda e, h: e[0] + (h - e[1]) / (e[3] - e[1]) * (e[2] - e[0])
    return low, high, (at(ends[0], low), at(ends[0], high)), (at(ends[1], low), at(ends[1], high))


def fix(data, x, y, ref_x, ref_y, stem, ref_stem, amount, taper, regular_stem):
    """Give each diagonal stroke the weight it has in the regular width, at both of its ends.

    Squeezing a letter and taking its x from a heavier weight changes a diagonal twice: it gets a
    different thickness from the stems, and its two edges stop running parallel, so the stroke turns
    into a wedge. Each edge is therefore put back on a line of its own: at the bottom and at the top
    of the stroke it is as thick, measured against the stem, as the same stroke in the regular width
    (`ref_x`, `ref_y`), and it stays centred where it was. `taper` (<1) asks for lighter diagonals,
    as narrow designs need. `regular_stem` is the Regular's stem: strokes are looked for on the
    Regular's drawing, whichever weight is being shaped.
    """
    edges, pairs = find(data, regular_stem)
    if not pairs:
        return
    x0, y0 = x.copy(), y.copy()
    line = {}                                                   # edge -> (x at y_a, y_a, x at y_b, y_b) of its new line
    for i, j in pairs:
        now, ref = _span(x0, y0, edges[i], edges[j]), _span(ref_x, ref_y, edges[i], edges[j])
        sines = _stroke(x0, y0, edges[i], edges[j]), _stroke(ref_x, ref_y, edges[i], edges[j])
        if not now or not ref or now[1] - now[0] < 1:
            continue
        low, high, a, b = now
        placed = {i: [], j: []}
        for end in (0, 1):                                      # bottom, then top of the stroke
            width = abs(b[end] - a[end])
            wanted = abs(ref[3][end] - ref[2][end]) * sines[1][1] / ref_stem * stem * taper / sines[0][1]
            wanted = width + max(-0.6 * stem, min(0.6 * stem, wanted - width)) * amount
            centre, side = (a[end] + b[end]) / 2, 1 if b[end] > a[end] else -1
            placed[i].append(centre - side * wanted / 2)
            placed[j].append(centre + side * wanted / 2)
        for k in (i, j):
            line[k] = (placed[k][0], low, placed[k][1], high)
    on = lambda k, h: line[k][0] + (h - line[k][1]) / (line[k][3] - line[k][1]) * (line[k][2] - line[k][0])
    ends = {}
    for k in line:
        e = edges[k]
        for i in e["inner"] + e["handles"]:
            x[i] = on(k, y0[i])
        for node in (e["start"], e["end"]):
            ends.setdefault(node, []).append(k)
    for node, ks in ends.items():
        lines = [((line[k][0], line[k][1]), (line[k][2] - line[k][0], line[k][3] - line[k][1])) for k in ks]
        if len(lines) == 2:                                    # a corner shared by two moving edges
            hit = _cross_point(*lines[0], *lines[1])
            if hit and abs(hit[1] - y0[node]) < 0.6 * stem:
                x[node], y[node] = hit
            else:                                              # two steep strokes meet at too flat an angle to cross cleanly
                x[node] = (on(ks[0], y0[node]) + on(ks[1], y0[node])) / 2
            continue
        e, dx = edges[ks[0]], on(ks[0], y0[node]) - x0[node]
        seg = e["before"] if node == e["start"] else e["after"]
        other = seg["a"] if node == e["start"] else seg["b"]
        if seg["handles"]:                                     # a curve leaves here: carry its near handle along
            near = seg["handles"][-1] if node == e["start"] else seg["handles"][0]
            x[node], x[near] = x0[node] + dx, x0[near] + dx
        elif math.hypot(x0[other] - x0[node], y0[other] - y0[node]) < 0.4 * ref_stem:
            x[node], x[other] = x0[node] + dx, x0[other] + dx   # a tiny facet: move it as a whole
        else:
            hit = _cross_point(*lines[0], (x0[node], y0[node]), (x0[other] - x0[node], y0[other] - y0[node]))
            x[node], y[node] = hit if hit else (x0[node] + dx, y0[node])
