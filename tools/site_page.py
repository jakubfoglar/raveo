"""The public specimen page: gathers what site/index.html shows and the files it serves.

    python tools/site_page.py            # fills site/ from the newest folder in releases/

site/
  index.html              the page (kept in the repo, not generated)
  data.json               facts about the font: axes, named styles, characters, features, file sizes
  fonts/                  the release's web fonts (roman and italic) and their stylesheet, plus a few letters of Raveo 1.1
                          for the "what changed" comparison
  favicon.svg, og.png     the page's icon (the a with the tail) and the picture shown when the page is shared,
                          both drawn from the release's own fonts

The page's download is the zip attached to the version's release on GitHub (tag v<version>), so the
14 MB archive is not kept in the repository. site/ is a plain static folder: any static host can serve it. While designing, the workbench server
shows the same page with the font built last, at http://localhost:4173/site/?build
"""
import io
import json
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from fontTools import subset
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import BUILD, ROOT, load_recipe  # noqa: E402
import release  # noqa: E402

SITE = ROOT / "site"
OLD = ROOT / "reference" / "RaveoVF-1.1-shipped.ttf"
GITHUB = "https://github.com/jakubfoglar/raveo"
COMPARE_CHARS = "racesijRG1ďť„“;?gyft"            # letters the page sets in Raveo 1.1 next to the new ones

# One sentence per language; a language is listed only if the font has every character of its sentence.
LANGUAGES = [
    ("English", "en", "The quick brown fox jumps over the lazy dog, twice."),
    ("Čeština", "cs", "Příliš žluťoučký kůň úpěl ďábelské ódy."),
    ("Slovenčina", "sk", "Kŕdeľ ďatľov učí koňa žrať kôru."),
    ("Polski", "pl", "Zażółć gęślą jaźń, pchnąć w tę łódź jeża."),
    ("Deutsch", "de", "Falsches Üben von Xylophonmusik quält jeden größeren Zwerg."),
    ("Français", "fr", "Dès Noël, où un zéphyr haï me vêt de glaçons würmiens."),
    ("Español", "es", "El pingüino Wenceslao hizo kilómetros bajo exhaustiva lluvia."),
    ("Português", "pt", "À noite, vovô Kowalsky vê o ímã cair no pé do pinguim."),
    ("Italiano", "it", "Quel vituperabile xenofobo zelante assaggia il whisky."),
    ("Nederlands", "nl", "Pa’s wijze lynx bezag vroom het fikse aquaduct."),
    ("Svenska", "sv", "Flygande bäckasiner söka hwila på mjuka tuvor."),
    ("Dansk", "da", "Høj bly gom vandt fræk sexquiz på wc."),
    ("Suomi", "fi", "Törkylempijävongahdus on hyvä šakkitermi."),
    ("Íslenska", "is", "Kæmi ný öxi hér, ykist þjófum nú bæði víl og ádrepa."),
    ("Magyar", "hu", "Árvíztűrő tükörfúrógép."),
    ("Română", "ro", "Muzicologă în bej vând whisky și tequila, preț fix."),
    ("Türkçe", "tr", "Pijamalı hasta yağız şoföre çabucak güvendi."),
    ("Hrvatski", "hr", "Gojazni đačić s biciklom drži hmelj i finu vatu u džepu."),
    ("Slovenščina", "sl", "V kožuščku hudobnega fanta stopiclja mizar."),
    ("Eesti", "et", "Põdur Zagrebi tšellomängija-följetonist Ciqo külmetas."),
    ("Latviešu", "lv", "Glāžšķūņa rūķīši dzērumā čiepj Baha koncertflīģeļu vākus."),
    ("Lietuvių", "lt", "Įlinkdama fechtuotojo špaga sublykčiojusi pragręžė apvalų arbūzą."),
    ("Tiếng Việt", "vi", "Dù hoa xuân thắm, bé vẫn mong ngày nắng ấm."),
]


ENGLISH = {"Tiếng Việt": "Vietnamese"}      # what a language is called in a sentence of the page


def _measure(path, location):
    """Stem, x-height and average letter width of one style, in font units."""
    font = instancer.instantiateVariableFont(TTFont(path), location)
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()

    def bounds(char):
        pen = BoundsPen(glyphs)
        glyphs[cmap[ord(char)]].draw(pen)
        return pen.bounds

    stem = bounds("l")
    letters = "abcdefghijklmnopqrstuvwxyz"
    return {"stem": round(stem[2] - stem[0]), "x_height": round(bounds("x")[3]), "cap_height": round(bounds("H")[3]),
            "advance": round(sum(font["hmtx"][cmap[ord(c)]][0] for c in letters) / len(letters))}


_cache = {}


def describe(path, recipe):
    """What the page needs to know about a font file. Measuring takes a few seconds, so it is remembered per file."""
    path = Path(path)
    key = (str(path), path.stat().st_mtime)
    if key in _cache:
        return _cache[key]
    font = TTFont(path)
    names, cmap = font["name"], font.getBestCmap()
    axes = {a.axisTag: [a.minValue, a.defaultValue, a.maxValue] for a in font["fvar"].axes}
    features = set()
    for tag in ("GSUB", "GPOS"):
        if tag in font:
            features |= {r.FeatureTag for r in font[tag].table.FeatureList.FeatureRecord}
    have = set(cmap)
    data = {
        "family": recipe["family"],
        "version": release.version_label(recipe["version"]),
        "date": time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime)),
        "axes": axes,
        "styles": [{"name": names.getDebugName(i.subfamilyNameID), **{k: round(v) for k, v in i.coordinates.items()}}
                   for i in font["fvar"].instances],
        "features": sorted(features),
        "characters": [[cp, unicodedata.name(chr(cp), ""), unicodedata.category(chr(cp))]
                       for cp in sorted(cmap) if cp > 0x20 and cp != 0xA0 and not 0xE000 <= cp <= 0xF8FF],
        "glyphs": len(font.getGlyphOrder()),
        "character_count": sum(1 for cp in cmap if not 0xE000 <= cp <= 0xF8FF),      # without private-use alternates
        "languages": [{"name": name, "lang": lang, "text": text} for name, lang, text in LANGUAGES
                      if all(ord(ch) in have for ch in text if not ch.isspace())],
        "unsupported": [ENGLISH.get(name, name) for name, _, text in LANGUAGES if not all(ord(ch) in have for ch in text if not ch.isspace())],
        "github": GITHUB,
    }
    if "opsz" in axes:
        base = {"wght": 400, "wdth": 100}
        data["sizes"] = {"text": _measure(path, {**base, "opsz": axes["opsz"][0]}),
                         "display": _measure(path, {**base, "opsz": axes["opsz"][2]})}
    _cache.clear()
    _cache[key] = data
    return data


def working(recipe=None):
    """The page's data for the font built last, as the workbench server hands it out."""
    recipe = recipe or load_recipe()
    path = BUILD / "headline" / f"{recipe['family'].replace(' ', '')}-VF.ttf"
    data = dict(describe(path, recipe))
    data["font"] = {"family": "Raveo Build", "url": f"/{path.relative_to(ROOT)}?v={int(path.stat().st_mtime)}"}
    slanted = path.with_name(path.name.replace("-VF", "-Italic-VF"))
    if slanted.exists():
        data["font"]["italic"] = f"/{slanted.relative_to(ROOT)}?v={int(slanted.stat().st_mtime)}"
        data["italic"] = {"angle": round(-TTFont(slanted)["post"].italicAngle, 1)}
    data["compare"] = {"family": "Raveo 1.1", "url": f"/{OLD.relative_to(ROOT)}"}
    data["working"] = True
    return data


INK, PAPER, RULE = "#0d0d0d", "#f6f6f3", "#3a3a3c"


def _favicon(variable, out):
    """The page's icon: Raveo's a, light on dark."""
    font = instancer.instantiateVariableFont(TTFont(variable), {"wght": 630, "wdth": 110, **({"opsz": 32} if "opsz" in {a.axisTag for a in TTFont(variable)["fvar"].axes} else {})})
    glyphs, name = font.getGlyphSet(), font.getBestCmap()[ord("a")]
    box = BoundsPen(glyphs)
    glyphs[name].draw(box)
    x0, y0, x1, y1 = box.bounds
    size, pad = 64, 13
    scale = (size - 2 * pad) / max(x1 - x0, y1 - y0)
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.1f}")
    glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, (size - (x1 - x0) * scale) / 2 - x0 * scale,
                                         (size + (y1 - y0) * scale) / 2 + y0 * scale)))
    out.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}"><rect width="{size}" height="{size}" rx="12" '
                   f'fill="{INK}"/><path fill="{PAPER}" d="{pen.getCommands()}"/></svg>\n')


def _share_image(variable, slanted, out):
    """The picture link previews show: the name in the wide Medium over three lines about the typeface, one in
    each width. Every line is set so that its ink, not its box, begins and ends on the margins. Set with
    HarfBuzz (hb-view), so it is kerned as the font is; skipped when hb-view or Pillow is not installed."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    metrics = TTFont(variable)
    ascent = metrics["hhea"].ascent / metrics["head"].unitsPerEm          # the baseline, below the top of a line hb-view draws

    def line(text, size, variations):
        png = subprocess.run(["hb-view", str(variable), text, f"--font-size={size:.3f}", f"--variations={variations}", "--margin=0",
                              f"--foreground={PAPER[1:]}", f"--background={INK[1:]}", "-O", "png"], capture_output=True, check=True).stdout
        image = Image.open(io.BytesIO(png)).convert("RGB")
        return image, image.convert("L").point(lambda v: 255 if v > 40 else 0).getbbox()

    def flush(text, variations, room):
        """The line at the size at which its ink is `room` wide, cut to its ink at both ends, and its baseline."""
        size = 200
        for _ in range(2):                                   # the second pass takes up what rounding left
            image, ink = line(text, size, variations)
            size *= room / (ink[2] - ink[0])
        image, ink = line(text, size, variations)
        image = image.crop((ink[0], 0, ink[2], image.height))
        return (image if image.width == room else image.resize((room, image.height), Image.LANCZOS)), round(size * ascent)

    try:
        width, height, margin, first, step = 1200, 630, 64, 330, 84
        room = width - 2 * margin
        page = Image.new("RGB", (width, height), INK)
        draw = ImageDraw.Draw(page)
        name, base = flush("Raveo", "opsz=32,wght=520,wdth=125", room)
        page.paste(name, (margin, 286 - base))
        rows = [("A warm grotesque for interfaces, running text and headlines", 60),
                ("Seven weights, three widths" + (", italics" if slanted else ""), 100), ("Variable, free and open source", 125)]
        for i, (text, wdth) in enumerate(rows):
            row, base = flush(text, f"opsz=32,wght=630,wdth={wdth}", room)
            page.paste(row, (margin, first + i * step + 62 - base))
        for i in range(len(rows) + 1):                       # the rules go on last, over the edge of any line box
            draw.rectangle([margin, first + i * step, width - margin - 1, first + i * step + 1], fill=RULE)
        page.save(out)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _kb(path):
    return round(path.stat().st_size / 1024)


def build():
    """Fill site/ from the newest release."""
    newest = release.releases()[0]
    folder = release.RELEASES / newest["folder"]
    slug = newest["family"].replace(" ", "")
    recipe = json.loads((folder / "recipe.json").read_text())
    variable = folder / "desktop" / "variable" / f"{slug}-VF.ttf"
    data = dict(describe(variable, recipe))
    data["date"] = newest["date"]

    fonts = SITE / "fonts"
    if fonts.exists():
        shutil.rmtree(fonts)
    fonts.mkdir(parents=True)
    for source in sorted((folder / "web").glob("*.woff2")) + sorted((folder / "web").glob("*.css")):
        shutil.copy(source, fonts / source.name)
    css = next((folder / "web").glob("*.css"))
    data["font"] = {"family": newest["family"], "css": f"fonts/{css.name}"}
    slanted = folder / "desktop" / "variable" / f"{slug}-Italic-VF.ttf"
    if slanted.exists():                                # the stylesheet already carries its @font-face rules
        data["italic"] = {"angle": round(-TTFont(slanted)["post"].italicAngle, 1)}

    _favicon(variable, SITE / "favicon.svg")
    _share_image(variable, slanted if slanted.exists() else None, SITE / "og.png")

    old = TTFont(OLD)                                   # a few letters of Raveo 1.1, for the comparison tiles
    options = subset.Options(layout_features=["kern", "mark", "mkmk", "ccmp"], notdef_outline=True, drop_tables=["DSIG"],
                             name_IDs=["*"])              # with its copyright and licence lines
    cutter = subset.Subsetter(options)
    cutter.populate(text=COMPARE_CHARS)
    cutter.subset(old)
    old.flavor = "woff2"
    old.save(fonts / "Raveo-1.1-letters.woff2")
    data["compare"] = {"family": "Raveo 1.1", "url": "fonts/Raveo-1.1-letters.woff2"}

    archive = release.RELEASES / f"{newest['folder']}.zip"
    files = {"variable": {"name": variable.name, "kb": _kb(variable)},
             "web": {"name": f"{slug}-VF.woff2", "kb": _kb(folder / "web" / f"{slug}-VF.woff2")},
             "statics": len(list((folder / "desktop" / "static").glob("*.ttf")))}
    if slanted.exists():
        files["italic"] = {"name": slanted.name, "kb": _kb(slanted)}
    slim = folder / "web" / "regular-width" / f"{slug}-VF.woff2"
    if slim.exists():
        files["regular_width"] = {"name": slim.name, "kb": _kb(slim)}
    if archive.exists():
        files["zip"] = {"url": f"{GITHUB}/releases/download/v{data['version']}/{archive.name}", "name": archive.name, "kb": _kb(archive)}
    data["files"] = files
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return data


if __name__ == "__main__":
    result = build()
    print(f"site/: {result['family']} {result['version']}, {len(result['styles'])} styles, {len(result['characters'])} characters, "
          f"{len(result['languages'])} language samples" + (f" (left out: {', '.join(result['unsupported'])})" if result["unsupported"] else ""))
