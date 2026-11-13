"""Make the Raveo italic source: Inter's italic with what Raveo changed in the roman carried over.

    python tools/make_italic.py [folder with Inter-Italic.glyphspackage and Inter-Roman.glyphspackage]

Writes sources/raveo-italic.glyphs. It is run once, and again only if Raveo's roman source changes.
The folder defaults to build/inter-src; the two packages must be the Inter that Raveo was forked
from (github.com/rsms/inter at commit 66647c0, the master of 24 May 2024, version 4.1: its src/ folder),
because the comparison below is point for point.

Inter's italic is not a slanted roman. It is drawn: round letters have their points moved to where
a slanted shape has its extremes and their strokes thinned where the slant fattens them, and three
letters are written rather than leaned over: a has one storey, e a curved bar, f a tail below the line.

Raveo's italic keeps Inter's drawing for everything Raveo left alone, and decides the rest like this:

  the same as an Inter alternate      G is Inter's G.1, 1 is its one.ss01: the italic takes that alternate
  built from other glyphs             Raveo's quotes are made of the comma: the same construction is kept
  redrawn, same points as Inter's     the bowl of R: Raveo's change is added to Inter's italic drawing,
                                      point by point, in the upright position
  redrawn, different points           the leg of R: Raveo's drawing, leaned over
  a, e, f and all built on them       Raveo's roman letter, leaned over: the two-storey a with its tail is
                                      what makes Raveo Raveo, and an upright e and f go with it. Inter's
                                      written forms are kept beside them as a.cursive, e.cursive ... so
                                      the recipe can bring any of the three back ("italic_forms")

"Upright position" means Inter's italic with its slant taken out. That differs from the roman only
by Inter's corrections, so a change made to the roman can be added to it. Letters that are only
leaned over lack those corrections; they are listed in the source (userData "cc.raveo.slant", key
"plain") and the pipeline gives them the same stroke correction by rule (curves.slant_compensate).
"""
import math
import sys
from pathlib import Path

from glyphsLib import GSFont
from glyphsLib.classes import GSAnchor, GSClass, GSComponent, GSFeature, GSFeaturePrefix, GSGlyph, GSLayer, GSNode, GSPath
from glyphsLib.types import Point

ROOT = Path(__file__).resolve().parent.parent
RAVEO = ROOT / "sources" / "raveo.glyphspackage"
OUT = ROOT / "sources" / "raveo-italic.glyphs"
PLUMB = "H"                      # a letter of straight stems only: it shows where each master's slant pivots
WRITTEN = {"a": "a", "a.sups": "a", "e": "e", "e.sups": "e", "ae": "ae", "oe": "e",          # glyphs Inter's italic draws as
           "_part.f_base": "f", "f.i": "f", "f.i.1": "f", "f.sups": "f"}                        # written letters, and which letter
TWIN = ".cursive"
QUOTES = ("quotesinglbase", "quotedblright", "quotedblleft", "quotedblreversed", "quotedblbase")    # set as close as the roman's


def master_ids(font):
    return {tuple(m.axes): m.id for m in font.masters}


def read(layer):
    """A layer as plain data: shapes, anchors, width."""
    shapes = []
    for shape in layer.shapes:
        if isinstance(shape, GSPath):
            shapes.append(("path", shape.closed, [(n.position.x, n.position.y, n.type, n.smooth) for n in shape.nodes]))
        else:
            shapes.append(("comp", shape.name, shape.position.x, shape.position.y, tuple(shape.scale), shape.rotation, shape.anchor))
    return shapes, [(a.name, a.position.x, a.position.y) for a in layer.anchors], layer.width


def same(a, b, tolerance=0.5):
    """Two layers drawn alike: same shapes, same points within `tolerance`, same width."""
    if len(a[0]) != len(b[0]) or abs(a[2] - b[2]) > tolerance:
        return False
    for s, t in zip(a[0], b[0]):
        if s[0] != t[0]:
            return False
        if s[0] == "comp":
            if s[1] != t[1] or abs(s[2] - t[2]) > tolerance or abs(s[3] - t[3]) > tolerance or s[4:6] != t[4:6]:
                return False
        elif len(s[2]) != len(t[2]) or any(p[2] != q[2] or abs(p[0] - q[0]) > tolerance or abs(p[1] - q[1]) > tolerance
                                           for p, q in zip(s[2], t[2])):
            return False
    return True


def reach(scale, rotation):
    """How a component's own sideways shift carries into the glyph: 1 as is, -1 turned half round."""
    return scale[0] * math.cos(math.radians(rotation))


def upright(data, slope, shift):
    """A slanted layer with the slant taken out."""
    shapes = []
    for shape in data[0]:
        if shape[0] == "path":
            shapes.append(("path", shape[1], [(x - y * slope - shift, y, kind, smooth) for x, y, kind, smooth in shape[2]]))
        else:
            name, x, y, scale, rotation, anchor = shape[1:]
            shapes.append(("comp", name, x - y * slope - shift * (1 - reach(scale, rotation)), y, scale, rotation, anchor))
    return shapes, [(name, x - y * slope - shift, y) for name, x, y in data[1]], data[2]


def slanted(data, slope, shift):
    return upright(data, -slope, -shift)


def write(layer, data):
    shapes = []
    for shape in data[0]:
        if shape[0] == "path":
            path = GSPath()
            path.closed = shape[1]
            path.nodes = [GSNode((round(x), round(y)), kind, smooth) for x, y, kind, smooth in shape[2]]
            shapes.append(path)
        else:
            name, x, y, scale, rotation, anchor = shape[1:]
            part = GSComponent(name, (round(x), round(y)), scale)
            part.rotation = rotation
            part.alignment = -1
            part.anchor = anchor
            shapes.append(part)
    layer.shapes = shapes
    layer.anchors = [GSAnchor(name, Point(round(x), round(y))) for name, x, y in data[1]]
    layer.width = round(data[2])


def carried(raveo, roman, italic_up):
    """Raveo's change to the roman, added to Inter's upright italic where the points correspond.

    Returns the layer and the numbers of the paths that are Raveo's own drawing, without Inter's corrections.
    """
    shapes, own = [], []
    for k, shape in enumerate(raveo[0]):
        partners = [d[0][k] if k < len(d[0]) else None for d in (roman, italic_up)]
        alike = shape[0] == "path" and all(p and p[0] == "path" and [n[2] for n in p[2]] == [n[2] for n in shape[2]] for p in partners)
        if alike:
            shapes.append(("path", shape[1], [(x + ix - rx, y + iy - ry, kind, smooth)
                                              for (x, y, kind, smooth), (rx, ry, _, _), (ix, iy, _, _) in zip(shape[2], partners[0][2], partners[1][2])]))
        else:
            shapes.append(shape)
            if shape[0] == "path":
                own.append(sum(1 for other in raveo[0][:k] if other[0] == "path"))
    if shapes == list(raveo[0]):                                # nothing corresponds: Raveo's drawing stands as it is
        return raveo, own
    theirs, mine = dict((n, (x, y)) for n, x, y in roman[1]), dict((n, (x, y)) for n, x, y in italic_up[1])
    anchors = [(n, x + mine[n][0] - theirs[n][0], y + mine[n][1] - theirs[n][1]) if n in theirs and n in mine else (n, x, y)
               for n, x, y in raveo[1]]
    return (shapes, anchors, raveo[2] + italic_up[2] - roman[2]), own


def written(font, name, depth=0):
    """The written letters (a, e, f) a glyph is drawn as or built on."""
    if name in WRITTEN:
        return set(WRITTEN[name])
    glyph = font.glyphs[name]
    if glyph is None or depth > 6:
        return set()
    return set().union(*(written(font, part.name, depth + 1) for part in glyph.layers[0].shapes if isinstance(part, GSComponent)))


def main(folder):
    italic, roman, raveo = (GSFont(str(p)) for p in (folder / "Inter-Italic.glyphspackage", folder / "Inter-Roman.glyphspackage", RAVEO))
    ids = {name: master_ids(font) for name, font in (("italic", italic), ("roman", roman), ("raveo", raveo))}
    slope = math.tan(math.radians(italic.masters[0].italicAngle))
    shift = {}
    for axes, mid in ids["italic"].items():                     # where the slant pivots: stems of H against the roman's
        mine, theirs = read(italic.glyphs[PLUMB].layers[mid]), read(roman.glyphs[PLUMB].layers[ids["roman"][axes]])
        offsets = sorted(p[0] - p[1] * slope - q[0] for s, t in zip(mine[0], theirs[0]) for p, q in zip(s[2], t[2]))
        shift[axes] = offsets[len(offsets) // 2]

    log = {"alternate": [], "construction": [], "carried": [], "raveo's drawing": [], "width": [], "upright form": [], "always written": []}
    plain, forms = {}, {}                                       # paths without Inter's corrections; glyphs with a written twin
    originals = {g.name: {axes: read(g.layers[mid]) for axes, mid in ids["italic"].items()} for g in italic.glyphs}
    inter_groups = {g.name: (g.leftKerningGroup, g.rightKerningGroup) for g in italic.glyphs}
    letters = {g.name: written(italic, g.name) for g in italic.glyphs}
    # A glyph built on a, e or f takes Raveo's roman form if that is built on the same letters from parts the italic has.
    upright_form = {name for name, found in letters.items() if found and raveo.glyphs[name] is not None and roman.glyphs[name] is not None
                    and written(raveo, name) == found
                    and all(italic.glyphs[part.name] is not None for part in raveo.glyphs[name].layers[0].shapes if isinstance(part, GSComponent))}
    # An accent glyph of Inter's italic may hang from another point than the roman's (the acute does, by 25 to
    # 130 units). A composite taken from Raveo's roman places it for the roman's accent, so it is moved by the
    # difference between the two accents' own attaching anchors.
    hang = {}
    for glyph in italic.glyphs:
        theirs = raveo.glyphs[glyph.name]
        if theirs is None:
            continue
        for axes, mid in ids["italic"].items():
            mine = dict((n, (x, y)) for n, x, y in upright(originals[glyph.name][axes], slope, shift[axes])[1] if n.startswith("_"))
            ours = dict((n, (x, y)) for n, x, y in read(theirs.layers[ids["raveo"][axes]])[1] if n.startswith("_"))
            both = [n for n in ours if n in mine]
            if both:
                hang.setdefault(glyph.name, {})[axes] = (sum(ours[n][0] - mine[n][0] for n in both) / len(both),
                                                         sum(ours[n][1] - mine[n][1] for n in both) / len(both))

    def hung(data, axes):
        """A composite from Raveo's roman with its accents placed for the italic's accent glyphs."""
        shapes = [(s[0], s[1], s[2] + hang[s[1]][axes][0], s[3] + hang[s[1]][axes][1], *s[4:])
                  if s[0] == "comp" and s[1] in hang and s[1] not in upright_form else s for s in data[0]]
        return shapes, data[1], data[2]

    for glyph in list(italic.glyphs):
        theirs, base = raveo.glyphs[glyph.name], roman.glyphs[glyph.name]
        if theirs is None or base is None:
            if theirs is None:
                glyph.export = False                            # a part only Inter's italic has (the long crossbar of its written f)
            continue
        glyph.export = theirs.export
        new = {axes: read(theirs.layers[mid]) for axes, mid in ids["raveo"].items()}
        old = {axes: read(base.layers[mid]) for axes, mid in ids["roman"].items()}
        clean = lambda: [glyph.layers.__delitem__(l.layerId) for l in list(glyph.layers) if l.layerId not in ids["italic"].values()]
        if glyph.name in upright_form:
            twin = GSGlyph(glyph.name + TWIN)                   # Inter's written form, for the recipe to switch to
            twin.export = False
            twin.leftKerningGroup, twin.rightKerningGroup = inter_groups[glyph.name]
            italic.glyphs.append(twin)
            for axes, mid in ids["italic"].items():
                layer = GSLayer()
                layer.layerId = layer.associatedMasterId = mid
                twin.layers[mid] = layer
                write(layer, originals[glyph.name][axes])
                write(glyph.layers[mid], slanted(hung(new[axes], axes), slope, shift[axes]))
            glyph.leftKerningGroup, glyph.rightKerningGroup = theirs.leftKerningGroup, theirs.rightKerningGroup
            forms[glyph.name] = "".join(sorted(letters[glyph.name]))
            if any(shape[0] == "path" for shape in new[(32, 400)][0]):
                plain[glyph.name] = []
            log["upright form"].append(glyph.name)
            clean()
            continue
        if letters[glyph.name]:                                 # the hook of a d on a one-storey a, and its like: Inter's, as written
            for layer in glyph.layers:
                for part in layer.shapes:
                    if isinstance(part, GSComponent) and part.name in upright_form:
                        part.name += TWIN
            log["always written"].append(glyph.name)
            continue
        built = all(s[0] == "comp" for s in new[(32, 400)][0])
        # A mirrored part of a leaning letter leans the wrong way: such a glyph keeps the drawing Inter's italic has for it.
        flipped = any(s[0] == "comp" and s[4][0] * s[4][1] < 0 for s in new[(32, 400)][0])
        if glyph.name in QUOTES and built and not flipped:     # Inter's italic spaces its quotes wider than its roman; Raveo's stay as in the roman
            for axes, mid in ids["italic"].items():
                write(glyph.layers[mid], slanted(new[axes], slope, shift[axes]))
            log["construction"].append(glyph.name)
            clean()
            continue
        if all(same(new[axes], old[axes]) for axes in new) or (built and flipped):
            continue
        bare = lambda data: (data[0], [], 0)                   # the drawing alone, whatever its width
        outline_same = all(same(bare(new[axes]), bare(old[axes])) for axes in new)
        twin = None if outline_same else next(
            (other.name for other in roman.glyphs if other.name != glyph.name and other.name in originals
             and all(same(bare(new[axes]), bare(read(other.layers[ids["roman"][axes]]))) for axes in new)), None)
        own = None
        for axes, mid in ids["italic"].items():
            mine_up = upright(originals[glyph.name][axes], slope, shift[axes])
            if outline_same:                                    # only the spacing differs
                result = (mine_up[0], mine_up[1], mine_up[2] + new[axes][2] - old[axes][2])
            elif twin:                                          # Inter's alternate, in the room Raveo gives the letter
                result = upright(originals[twin][axes], slope, shift[axes])
                result = (result[0], result[1], result[2] + new[axes][2] - read(roman.glyphs[twin].layers[ids["roman"][axes]])[2])
            elif all(s[0] == "comp" for s in new[axes][0]):
                result = hung(new[axes], axes)
            else:
                result, own = carried(new[axes], old[axes], mine_up)
            write(glyph.layers[mid], slanted(result, slope, shift[axes]))
        if own:
            plain[glyph.name] = own
        kind = "width" if outline_same else "alternate" if twin else "construction" if all(s[0] == "comp" for s in new[(32, 400)][0]) else \
            "carried" if own is not None and len(own) < sum(1 for s in new[(32, 400)][0] if s[0] == "path") else "raveo's drawing"
        log[kind].append(glyph.name + (f" = {twin}" if twin else ""))
        clean()

    # Kerning: the few pairs Raveo changed, and all of Raveo's kerning for a side of a letter whose shape
    # is now Raveo's (the left of the two-storey a is not the round left of Inter's written a).
    regrouped = set()
    for name in upright_form:
        (old_left, old_right), glyph = inter_groups[name], italic.glyphs[name]
        if glyph.leftKerningGroup and glyph.leftKerningGroup != old_left:
            regrouped.add("@MMK_R_" + glyph.leftKerningGroup)
        if glyph.rightKerningGroup and glyph.rightKerningGroup != old_right:
            regrouped.add("@MMK_L_" + glyph.rightKerningGroup)
    changed = 0
    for axes, mid in ids["italic"].items():
        new, old, mine = (font.kerning.get(m, {}) for font, m in ((raveo, ids["raveo"][axes]), (roman, ids["roman"][axes]), (italic, mid)))
        for left in set(new) | set(old) | set(mine):
            for right in set(new.get(left, {})) | set(old.get(left, {})) | set(mine.get(left, {})):
                a, b = new.get(left, {}).get(right), old.get(left, {}).get(right)
                if a != b or ((left in regrouped or right in regrouped) and mine.get(left, {}).get(right) != a):
                    changed += 1
                    if a is None:
                        mine.get(left, {}).pop(right, None)
                    else:
                        mine.setdefault(left, {})[right] = a

    # Raveo's OpenType features, so the italic behaves as the roman does. (Inter's italic source reads its
    # features from separate files and has ones Raveo switched off.)
    for field, make in (("features", GSFeature), ("featurePrefixes", GSFeaturePrefix), ("classes", GSClass)):
        copies = []
        for item in getattr(raveo, field):
            new = make(item.name, item.code)
            for flag in ("automatic", "disabled", "notes"):
                if hasattr(item, flag):
                    setattr(new, flag, getattr(item, flag))
            copies.append(new)
        setattr(italic, field, copies)

    # Where each master's slant pivots, for the pipeline: it works on the letters with the slant taken out.
    # Also: which paths are only leaned over ("plain": glyph -> path numbers, none = all), and which glyphs
    # have Inter's written form beside them ("forms": glyph -> the letters it is built on, as in "ae").
    italic.userData["cc.raveo.slant"] = {"shift": {mid: round(shift[axes], 3) for axes, mid in ids["italic"].items()},
                                         "plain": plain, "forms": forms}
    italic.familyName = raveo.familyName
    # Raveo's own credits, licence and trademark lines: the italic is Raveo's modified version of Inter's
    # italic, not Inter's, and must not carry Inter's name fields or vendor as its own.
    italic.properties = list(raveo.properties)
    for field in ("versionMajor", "versionMinor"):
        if getattr(raveo, field, None) is not None:
            setattr(italic, field, getattr(raveo, field))
    italic.save(str(OUT))
    for kind, names in log.items():
        print(f"{len(names):>4}  {kind}: {', '.join(names[:14])}{' ...' if len(names) > 14 else ''}")
    print(f"kerning regrouped: {sorted(regrouped)}")
    print(f"{changed} kerning values taken from Raveo; slant {italic.masters[0].italicAngle} degrees; pivots {sorted(set(round(v) for v in shift.values()))}")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "inter-src")
