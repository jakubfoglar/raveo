"""The a of the wordmark Raveo was first drawn for, as a correction that can be laid over Raveo's a.

Raveo's a and the wordmark's a share their point structure: 17 corner/curve points on the outside
and 6 on the counter. So the wordmark's drawing can be expressed as "how far each of Raveo's points
has to move", measured once at the wordmark's own style (about weight 620, width 113) and then
applied to any master, scaled to that master's bowl width. Because only differences are carried
over, stroke weight still comes from the master, not from the wordmark.
"""
import numpy as np

LOGO_STYLE = (620, 113)                  # weight and width the wordmark was drawn at
BASELINE, X_HEIGHT = 28.8689, 26.6152    # of the wordmark, in the units it was drawn in
STEM_RIGHT = 141.806                     # right edge of the a's stem, in the same units

# Raveo's a: indices of its on-curve points, in path order, starting at the notch under the bowl.
OUTER_ON = [2, 3, 6, 9, 10, 13, 16, 17, 20, 23, 24, 27, 30, 33, 36, 39, 42]
OUTER_COUNT, COUNTER_COUNT = 43, 14
CUT = (23, 24)                           # the two corners of the cut at the top left: outside, inside


# Where each of Raveo's 57 a-points sits in the wordmark's a, in the units the wordmark was drawn in
# (None = the point is left where it is). Measured once from the wordmark's drawing: its outside has
# the same 17 points as Raveo's a; its counter has six like Raveo's, but the curves fall in different
# places, so those were matched by hand.
A_TARGETS = [
    (129.956, 29.4585),
    (134.252, 28.1733),
    (136.115, 25.4421),
    (136.322, 25.4421),
    (137.098, 28.0661),
    (139.427, 29.2444),
    (141.911, 29.2444),
    (143.309, 29.2444),
    (144.137, 29.1372),
    (145.327, 28.7088),
    (145.327, 23.6213),
    (144.602, 23.782),
    (144.292, 23.782),
    (143.774, 23.782),
    (142.325, 23.782),
    (141.808, 23.2465),
    (141.808, 22.229),
    (141.808, 12.1612),
    (141.808, 4.61037),
    (137.098, 1.71856),
    (129.749, 1.71856),
    (124.056, 1.71856),
    (118.726, 3.75354),
    (116.604, 9.1623),
    (121.934, 11.0366),
    (123.176, 8.25191),
    (126.747, 6.43114),
    (129.956, 6.43114),
    (133.01, 6.43114),
    (135.649, 7.50217),
    (135.649, 9.96558),
    (135.649, 11.1973),
    (135.183, 11.5186),
    (133.838, 11.7328),
    (130.957, 12.1969),
    (128.076, 12.661),
    (125.195, 13.1251),
    (120.382, 13.7678),
    (116.086, 16.1776),
    (116.086, 20.9973),
    (116.086, 26.406),
    (120.951, 29.4585),
    (126.23, 29.4585),
    (124.884, 24.9066),
    (122.504, 23.3),
    (122.504, 20.4618),
    (122.504, 18.3197),
    (124.729, 17.5699),
    (127.11, 17.0879),
    (129.97366666666667, 16.5167),
    (132.83733333333333, 15.945500000000001),
    (135.701, 15.3743),
    (135.701, 15.3743),
    (135.701, 17.9449),
    (135.701, 22.37902),
    (130.5984, 24.9066),
    (127.524, 24.9066),
]


class LogoA:
    """Per-point moves that turn Raveo's a into the wordmark's a."""

    def __init__(self, ref_x, ref_y, font_x_height):
        """`ref_x`, `ref_y`: Raveo's a at the wordmark's own style, before any design operation."""
        scale = font_x_height / X_HEIGHT
        target = A_TARGETS
        n = OUTER_COUNT + COUNTER_COUNT
        self.width = float(ref_x[17] - ref_x[39])                    # stem's right edge to the bowl's left
        self.dx, self.dy = np.zeros(n), np.zeros(n)
        # Raveo runs the counter's top edge on into the stem before turning back. Keep that overlap,
        # but along the wordmark's own edge, so the underside of the bowl stroke meets the stem straight.
        depth = float(np.hypot(ref_x[OUTER_COUNT + 8] - ref_x[OUTER_COUNT + 9], ref_y[OUTER_COUNT + 8] - ref_y[OUTER_COUNT + 9]))
        top, corner = target[OUTER_COUNT + 5], target[OUTER_COUNT + 9]
        run = float(np.hypot(corner[0] - top[0], top[1] - corner[1])) * scale
        poke = ((corner[0] - top[0]) * scale / run * depth, (top[1] - corner[1]) * scale / run * depth)
        for i, point in enumerate(target):
            if point is None:
                continue
            tx = ref_x[17] + (point[0] - STEM_RIGHT) * scale
            ty = (BASELINE - point[1]) * scale
            if i == OUTER_COUNT + 8:
                tx, ty = tx + poke[0], ty + poke[1]
            self.dx[i], self.dy[i] = tx - ref_x[i], ty - ref_y[i]

    def apply(self, x, y, x0, amount, light=1.0):
        """Move the a's points by `amount` of the way; returns the change in advance width.

        `light` is the stroke's thickness relative to the Regular's, 1 from Regular up. The moves
        were measured on a bold stroke; the one that turns the cut at the top left moves the cut's
        two corners apart, so in lighter weights it shrinks with the stroke and the cut keeps its angle.
        """
        n = OUTER_COUNT + COUNTER_COUNT
        if len(x) < n:
            return 0.0
        dx, dy = self.dx, self.dy
        if light < 1:
            dx, dy = dx.copy(), dy.copy()
            outer, inner = CUT
            turn = ((dx[inner] - dx[outer]) * (1 - light), (dy[inner] - dy[outer]) * (1 - light))
            for i in (inner, inner + 1):                              # the corner and its handle
                dx[i] -= turn[0]
                dy[i] -= turn[1]
        k = (x0[17] - x0[39]) / self.width * amount
        left = float(np.min(x[:n] + dx * k) - np.min(x[:n]))          # keep the left side bearing
        tail = float(dx[9] * k)                                       # and the right one, at the tail
        x[:n] += dx * k - left
        y[:n] += dy * amount
        if len(x) > n:
            x[n:] -= left * 0                                          # anchors stay put
        return tail - left
