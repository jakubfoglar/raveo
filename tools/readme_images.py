"""The pictures in README.md: eight panels drawn from the released fonts.

The words in them are sample copy, a morning at a railway station, so that the type is seen at work
and not describing itself.

    python tools/readme_images.py [path to InterVariable.ttf]    # writes docs/images/*.svg

Each panel is an SVG of outlines on its own ground (near-black or paper), so it looks the same on a
light and on a dark page and needs no font to show. Text is shaped with HarfBuzz, so it is kerned as
the font is. The two panels that set Raveo against Inter need Inter's variable font; without it they
are left as they are.
"""
import sys
from pathlib import Path

import uharfbuzz as hb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import ROOT  # noqa: E402
import release  # noqa: E402

OUT = ROOT / "docs" / "images"
W, H, M = 1600, 900, 72                           # panel size and margin
GROUND = {"dark": ("#0c0c0c", "#f4f4f2", "#8b8b8d", "#3a3a3c"),     # ground, ink, quiet, rule
          "paper": ("#f1f0ec", "#0c0c0c", "#77777a", "#cfcdc6")}


class Pen:
    """Collects one glyph as SVG path data, scaled and placed."""

    def __init__(self, scale, x, y):
        self.scale, self.x, self.y, self.d = scale, x, y, []

    def _p(self, px, py):
        return f"{self.x + px * self.scale:.1f} {self.y - py * self.scale:.1f}"

    def moveTo(self, p):
        self.d.append("M" + self._p(*p))

    def lineTo(self, p):
        self.d.append("L" + self._p(*p))

    def qCurveTo(self, *points):
        self.d.append("Q" + " ".join(self._p(*p) for p in points))

    def curveTo(self, *points):
        self.d.append("C" + " ".join(self._p(*p) for p in points))

    def closePath(self):
        self.d.append("Z")

    def endPath(self):
        pass


class Face:
    def __init__(self, path, **base):
        self.face, self.base = hb.Face(Path(path).read_bytes()), base
        self.axes = {a.tag for a in self.face.axis_infos}

    def line(self, text, size, x, y, features=None, **location):
        """(path data, width) of one line with its baseline starting at (x, y)."""
        font = hb.Font(self.face)
        font.set_variations({k: v for k, v in {"opsz": 32, "wght": 400, "wdth": 100, **self.base, **location}.items() if k in self.axes})
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(font, buf, features or {})
        scale, pen_x, parts = size / self.face.upem, x, []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            pen = Pen(scale, pen_x + pos.x_offset * scale, y - pos.y_offset * scale)
            font.draw_glyph_with_pen(info.codepoint, pen)
            parts.append("".join(pen.d))
            pen_x += pos.x_advance * scale
        return "".join(parts), pen_x - x

    def width(self, text, size, **location):
        return self.line(text, size, 0, 0, **location)[1]

    def size_for(self, text, width, **location):
        """The type size at which `text` is `width` wide."""
        return 100 * width / self.width(text, 100, **location)


class Panel:
    def __init__(self, ground):
        self.bg, self.ink, self.quiet, self.rule_colour = GROUND[ground]
        self.layers = []

    def put(self, face, text, size, x, y, tone="ink", align="left", features=None, **location):
        d, width = face.line(text, size, 0, 0, features, **location)
        x = x - width if align == "right" else x - width / 2 if align == "centre" else x
        self.layers.append((face.line(text, size, x, y, features, **location)[0], getattr(self, tone)))
        return width

    def run(self, parts, size, x, y, **location):
        """Several faces on one line: `parts` is [(face, text)]."""
        for face, text in parts:
            x += self.put(face, text, size, x, y, **location)

    def rule(self, x, y, width, height=1.5, tone="rule_colour"):
        self.layers.append((f"M{x:.1f} {y:.1f}h{width:.1f}v{height:.1f}h{-width:.1f}Z", getattr(self, tone)))

    def svg(self):
        body = "".join(f'<path fill="{colour}" d="{d}"/>' for d, colour in self.layers)
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="{self.bg}"/>{body}</svg>\n'


def build(inter_path=None):
    newest = release.releases()[0]
    folder = release.RELEASES / newest["folder"] / "desktop" / "variable"
    slug = newest["family"].replace(" ", "")
    roman, italic = Face(folder / f"{slug}-VF.ttf"), Face(folder / f"{slug}-Italic-VF.ttf")
    small = dict(opsz=14, wght=400)
    inner = W - 2 * M
    panels = {}

    # 1. The name.
    p = Panel("dark")
    size = roman.size_for("Raveo", inner * 1.035, wght=520, wdth=125)
    p.put(roman, "Raveo", size, M - size * 0.045, 560, wght=520, wdth=125)
    p.rule(M, 716, inner)
    p.put(roman, "A warm grotesque in seven weights and three widths", 30, M, 772, **small)
    p.put(roman, "upright and italic, free and open source", 30, M, 814, "quiet", **small)
    p.put(roman, f"Version {newest['version']}", 30, W - M, 772, align="right", **small)
    p.put(roman, "SIL Open Font License", 30, W - M, 814, "quiet", align="right", **small)
    panels["01-raveo"] = p

    # 2 and 7. Against Inter: the letters, then punctuation and stroke endings.
    if inter_path and Path(inter_path).exists():
        inter = Face(inter_path)

        def against(name, cells, size, rows):
            """Cells of Inter (quiet) and Raveo (ink), three to a row. With two rows the pair stands side by
            side; with one row, for groups of letters, Inter stands above and Raveo below."""
            p = Panel("paper")
            cell, pitch = inner / 3, (H - 150) / rows
            for k, (chars, note) in enumerate(cells):
                x, top = M + (k % 3) * cell, 70 + (k // 3) * pitch
                p.rule(x, top, cell - 36)
                if rows == 1:
                    p.put(inter, chars, size, x - size * 0.03, top + 290, "quiet", wght=520)
                    p.put(roman, chars, size, x - size * 0.03, top + 590, wght=520)
                else:
                    old = p.put(inter, chars, size, x - size * 0.03, top + pitch - 112, "quiet", wght=520)
                    p.put(roman, chars, size, x + max(old + size * 0.14, (cell - 36) / 2), top + pitch - 112, wght=520)
                p.put(roman, note, 24, x, top + pitch - 44, "quiet", **small)
            p.put(roman, "Inter", 24, W - M - 96, H - 40, "quiet", align="right", **small)
            p.put(roman, "Raveo", 24, W - M, H - 40, align="right", **small)
            panels[name] = p

        against("02-letters", (("a", "An a with a tail"), ("R", "An R with a curled leg"), ("r", "A longer arm on the r"),
                               ("G", "The G with a spur"), ("1", "The 1 with a flag"), ("s", "Strokes end in an angled cut")), 250, 2)
        against("07-details", (("j;,", "Commas as round as the dots"), ("„a“", "Round quotes"), ("ďť", "A tick that sits by its letter")), 264, 1)

    # 3. The width axis.
    p = Panel("dark")
    text, steps = "Morning train to Kraków", (60, 75, 90, 100, 112, 125)
    size = roman.size_for(text, inner - 130, wght=630, wdth=125)
    for k, width in enumerate(steps):
        y = 168 + k * 126
        p.rule(M, y - 98, inner)
        p.put(roman, str(width), 24, M, y, "quiet", features={"tnum": True}, **small)
        p.put(roman, text, size, M + 130, y, wght=630, wdth=width)
    p.rule(M, 168 + len(steps) * 126 - 98, inner)
    panels["03-width"] = p

    # 4. The named weights, upright and italic.
    p = Panel("paper")
    weights = [(300, "Light"), (400, "Regular"), (520, "Medium"), (630, "SemiBold"), (730, "Bold"), (850, "ExtraBold"), (900, "Black")]
    for k, (weight, name) in enumerate(weights):
        y = 158 + k * 108
        p.rule(M, y - 84, inner)
        p.put(roman, str(weight), 24, M, y, "quiet", features={"tnum": True}, **small)
        p.put(roman, name, 82, M + 130, y, wght=weight)
        p.put(italic, "Italic" if name == "Regular" else f"{name} Italic", 82, M + 130 + (inner - 130) / 2, y, wght=weight)
    p.rule(M, 158 + len(weights) * 108 - 84, inner)
    panels["04-weight"] = p

    # 5. The italic at three widths.
    p = Panel("dark")
    for k, (location, name) in enumerate(((dict(wght=730, wdth=60), "Narrow Bold Italic"), (dict(wght=400, wdth=100), "Italic"),
                                          (dict(wght=520, wdth=125), "Wide Medium Italic"))):
        text, top = "Window seat, facing forward", 62 + k * 270
        size = min(190, italic.size_for(text, inner, **location))
        p.rule(M, top, inner)
        p.put(roman, name, 24, M, top + 42, "quiet", **small)
        p.put(italic, text, size, M - size * 0.02, top + 208, **location)
    panels["05-italic"] = p

    # 6. In use: a statement, a table of figures, a narrow headline and a chart.
    p = Panel("dark")
    left, right, gutter = M, 860, 70
    for k, line in enumerate(("Every morning a train leaves", "Prague for Vienna. Four hours,", "one dining car, one river.")):
        p.put(roman, line, 44, left, 128 + k * 54, wght=520, wdth=125)
    poster = roman.size_for("PRAHA HLAVNÍ NÁDRAŽÍ", right - gutter - left, wght=760, wdth=60)
    for k, line in enumerate(("PRAHA HLAVNÍ NÁDRAŽÍ", "WIEN HAUPTBAHNHOF")):
        p.put(roman, line, poster, left - poster * 0.02, 668 + k * poster * 0.9, wght=760, wdth=60)
    rows = (("07:42", "RJ 71", "Wien Hbf", "3"), ("07:55", "EC 172", "Berlin Hbf", "1"), ("08:04", "R 941", "Kolín", "6"),
            ("08:12", "EC 111", "Kraków Główny", "2"), ("08:31", "Ex 143", "Ostrava hl. n.", "5"))
    columns = (right, right + 130, right + 290, W - M)
    for x, head in zip(columns, ("Time", "Train", "To", "Platform")):
        p.put(roman, head, 20, x, 92, "quiet", align="right" if x == columns[-1] else "left", **small)
    for k, row in enumerate(rows):
        y = 150 + k * 52
        p.rule(right, y - 38, W - M - right)
        for x, cell in zip(columns, row):
            p.put(roman, cell, 27, x, y, align="right" if x == columns[-1] else "left", features={"tnum": True}, wght=520, wdth=125)
    p.rule(right, 150 + len(rows) * 52 - 38, W - M - right)
    word = min(200, roman.size_for("Praha", W - M - right - 40, wght=520, wdth=125))
    p.put(roman, "Praha", word, right - word * 0.05, 618, wght=520, wdth=125)
    p.put(roman, "The Railjet to Vienna calls at", 22, right, 668, "quiet", **small)
    for k, (place, time) in enumerate((("Pardubice hl. n.", "08:41"), ("Brno hl. n.", "10:16"), ("Břeclav", "10:49"), ("Wien Hbf", "11:49"))):
        y = 716 + k * 38
        p.rule(right, y - 27, W - M - right)
        p.put(roman, place, 22, right, y, **small)
        p.put(roman, time, 22, W - M, y, "quiet", align="right", features={"tnum": True}, **small)
    panels["06-in-use"] = p

    # 8. Text: the opening of an article and the stops of the journey. The first line of the text stands
    # with its capitals at the height of the headline's.
    p = Panel("paper")
    cap, head, body, first = 0.7275, 104, 24, 196
    for k, line in enumerate(("Four hours", "to Vienna,", "the slow way")):
        p.put(roman, line, head, M - 4, first + k * 108, wght=520, wdth=108)
    lines = ([(roman, "The 07:42 leaves Prague from platform three and is in")],
             [(roman, "Vienna by ten to twelve. You could fly it in fifty")],
             [(roman, "minutes; almost nobody on this train would. There is a")],
             [(roman, "dining car, a river that keeps turning up on the left,")],
             [(roman, "and four hours in which nothing is asked of you.")],
             [(italic, "Moravia goes by in fields and church towers.")],
             [(roman, "By Břeclav the announcements have switched to German,")],
             [(roman, "the coffee costs 20 Kč more, and it is better.")])
    top = first - cap * head + cap * body
    for k, parts in enumerate(lines):
        p.run(parts, body, 880, top + k * 40, **small)
    p.rule(M, 560, inner)
    for k, (place, note) in enumerate((("Praha", "hlavní nádraží, 07:42"), ("Brno", "hlavní nádraží, 10:16"), ("Břeclav", "10:49"), ("Wien", "Hauptbahnhof, 11:49"))):
        x = M + k * inner / 4
        p.put(roman, place, 92, x - 5, 712, wght=520)
        p.put(roman, note, 24, x, 768, "quiet", features={"tnum": True}, **small)
    panels["08-text"] = p

    OUT.mkdir(parents=True, exist_ok=True)
    for name, panel in panels.items():
        (OUT / f"{name}.svg").write_text(panel.svg())
    return sorted(panels)


if __name__ == "__main__":
    print("docs/images:", ", ".join(build(sys.argv[1] if len(sys.argv) > 1 else None)))
