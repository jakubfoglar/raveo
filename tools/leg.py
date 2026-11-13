"""The leg of the R, laid out again at every width from Raveo's own drawing.

Raveo's R has a curled leg: it leaves the bowl, swings down and stands on the line with a small kick
to the right. Squeezing or stretching the letter takes its x from one weight and its y from another,
which is harmless for stems and bowls but not for a curved diagonal stroke: the leg comes out thick
in one place and thin in another, and differently at each width and weight.

So the leg is not taken from the squeezed letter. It is laid out again from the R of the same weight
at the regular width: its middle line is fitted between the bowl and the baseline of the squeezed
letter, and its thickness follows the stems all along its length. The pipeline's curve smoothing
then runs over it as over every other outline. The controls move it:

    leg_curl     how far the middle line bows away from a straight line to the foot (1 = Raveo's curl)
    leg_reach    the foot stands further right (+) or left; the stroke follows, most at the bottom
    leg_weight   the leg thicker (+) or thinner, in units
    leg_flare    the leg widens (+) or narrows toward the foot, as a share of its thickness

Distances are in font units at the regular width; sideways ones scale with the width.
"""
import math

KEYS = ("leg_curl", "leg_reach", "leg_weight", "leg_flare")
KINDS = ["line", "offcurve", "offcurve", "curve", "offcurve", "offcurve", "curve", "offcurve", "offcurve", "curve",
         "line", "offcurve", "offcurve", "curve", "offcurve", "offcurve", "curve", "offcurve", "offcurve", "curve"]
OUTER = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)               # the right edge, from the foot up to where it enters the bowl
INNER = (19, 18, 17, 16, 15, 14, 13, 12, 11, 10)     # the left edge, point for point opposite the right one
BAR, BOWL = 10, 14                                   # in the first contour: the underside of the bowl's bar, its right extreme


def shape(data, x, y, ref_x, ref_y, s, stem, ref_stem, settings):
    """Lay the leg out again in x and y. Returns how much further right the letter's ink now reaches.

    `ref_x`, `ref_y`: the same letter at the regular width and the same weight. `stem`, `ref_stem`:
    stem thickness here and there. `settings`: the keys above. `s` is width / 100.
    """
    if len(data.paths) != 2 or data.paths[1][2] != KINDS:
        return 0.0
    start = data.paths[1][0]
    get = lambda key, default=0.0: float(settings[key]) if settings.get(key) is not None else default
    root_ref, root = (ref_x[start + INNER[-1]], ref_y[BAR]), (x[start + INNER[-1]], y[BAR])
    ax = (x[BOWL] - root[0]) / (ref_x[BOWL] - root_ref[0])      # how the room between the leg's root and the bowl's edge changed
    ay = root[1] / root_ref[1]                                  # and the height of the bar above the line
    grow = stem / ref_stem
    before = max(x[start + i] for i in OUTER)

    middle, across = [], []
    for o, i in zip(OUTER, INNER):
        ox, oy, ix, iy = ref_x[start + o], ref_y[start + o], ref_x[start + i], ref_y[start + i]
        middle.append((root[0] + ((ox + ix) / 2 - root_ref[0]) * ax, (oy + iy) / 2 * ay))
        hx, hy = (ox - ix) / 2 / ax, (oy - iy) / 2 / ay          # across the stroke, as the fitted middle line runs
        length = math.hypot(hx, hy) or 1.0
        across.append((hx / length, hy / length, math.hypot(ox - ix, oy - iy) / 2 * grow))

    top, foot = middle[-1], middle[0]
    height = max(1.0, top[1] - foot[1])
    curl, reach, weight, flare = get("leg_curl", 1.0), get("leg_reach") * s, get("leg_weight"), get("leg_flare")
    for point, (nx, ny, half), o, i in zip(middle, across, OUTER, INNER):
        down = max(0.0, min(1.0, (top[1] - point[1]) / height))               # 0 at the bowl, 1 on the line
        straight = top[0] + (foot[0] - top[0]) * down
        cx = straight + (point[0] - straight) * curl + reach * down * down
        half = max(1.0, (half + weight / 2 * min(1.0, 3 * down)) * (1 + flare * down * down))
        x[start + o], y[start + o] = cx + nx * half, point[1] + ny * half
        x[start + i], y[start + i] = cx - nx * half, point[1] - ny * half
    y[start + INNER[-1]] = root[1]                             # the inner edge leaves the bar exactly on its underside
    y[start + OUTER[0]] = y[start + INNER[0]] = 0.0            # and the foot stands on the line
    return max(x[start + i] for i in OUTER) - before
