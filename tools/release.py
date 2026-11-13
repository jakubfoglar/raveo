"""Finished fonts, written as one folder per version.

    python tools/release.py                 # uses recipe.json, version from the recipe
    python tools/release.py 1.01            # same settings, released as 1.01

releases/
  <Family>-<version>/
    README.txt                          what each file is for and how to use it
    OFL.txt                             the licence, which must travel with the fonts
    recipe.json                         the settings this version was built from (not in the zip)
    desktop/variable/<Family>-VF.ttf    one file with every weight and width
    desktop/variable/<Family>-Italic-VF.ttf   the italic, when the project has one: a second file, as CSS and
                                        desktop apps expect (font-style: italic, the I button)
    desktop/static/<Family>-<Style>.ttf one file per named style, for apps without variable-font support
    web/<Family>-VF.woff2               the variable font for web pages
    web/<Family>-Italic-VF.woff2        its italic
    web/static/<Family>-<Style>.woff2   one file per named style
  <Family>-<version>.zip                what people download: the folder without recipe.json

A release holds what someone installing the typeface needs and nothing else. The specimen page cuts the
variable fonts into parts for itself (tools/site_page.py).

Exporting a version that already exists replaces its folder.
"""
import json
import re
import shutil
import sys
import zipfile
import time
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import BUILD, ITALIC_SOURCE, ROOT, SUBSETS, Pipeline, build, load_recipe, style_weights  # noqa: E402

RELEASES = ROOT / "releases"
WEB_FEATURES = ["kern", "calt", "case", "tnum", "pnum", "ccmp", "locl", "mark", "mkmk", "ordn", "dlig"]
# What the first web part holds besides the European letters: spaces and dashes, modifier and combining accents,
# currency, letter-like signs, arrows, mathematical signs and the shapes interfaces use (● ▶ ▲ √ ∞ ↩).
WEB_COMMON = [(0x2B0, 0x36F), (0x2000, 0x206F), (0x20A0, 0x20CF), (0x2100, 0x214F), (0x2190, 0x23FF), (0x25A0, 0x25FF), (0xFEFF, 0xFEFF)]
PANOSE_WEIGHT = {300: 3, 400: 5, 500: 6, 600: 7, 700: 8, 800: 9, 900: 10}
LICENCE = ("This Font Software is licensed under the SIL Open Font License, Version 1.1. "
           "This license is available with a FAQ at: https://openfontlicense.org")


WEIGHT_CLASS = {"Thin": 100, "ExtraLight": 200, "Light": 300, "Regular": 400, "Medium": 500, "SemiBold": 600, "Bold": 700,
                "ExtraBold": 800, "Black": 900}
OLD = ROOT / "reference" / "RaveoVF-1.1-shipped.ttf"


def _stamp(font, recipe):
    """Version, a unique ID of this file's own, and licence and credit fields where the Raveo source lacks them.

    Call it once the file has its final PostScript name.
    """
    name = font["name"]
    version = f"{float(recipe['version']):.3f}"
    unique = f"{version};{font['OS/2'].achVendID.strip() or 'NONE'};{name.getDebugName(6)}"
    for name_id, value in ((0, recipe["copyright"]), (9, recipe["designer"]), (5, f"Version {version}"), (3, unique),
                           (13, LICENCE), (14, "https://openfontlicense.org")):
        if value:
            name.setName(value, name_id, 3, 1, 0x409)
    font["OS/2"].fsType = 0                         # installable and embeddable
    font["head"].fontRevision = float(recipe["version"])


def _name_variable(font, family, italic=False):
    """Give the variable font names of its own, as Raveo 1.1's had: family "<Family> Variable", PostScript
    "<Family>VF-<Style>". Installed next to the static fonts it then does not answer to their names, and
    documents set in the earlier variable font still find it. The italic is the same family's Italic."""
    name, slug = font["name"], family.replace(" ", "")
    for name_id, value in ((1, f"{family} Variable"), (2, "Italic" if italic else "Regular"),
                           (4, f"{family} Variable Italic" if italic else f"{family} Variable"), (16, f"{family} Variable"),
                           (17, "Italic" if italic else "Regular"),
                           (6, f"{slug}VF-{'Italic' if italic else 'Regular'}"), (25, f"{slug}VF{'Italic' if italic else ''}")):
        name.setName(value, name_id, 3, 1, 0x409)
    for instance in font["fvar"].instances:
        style = name.getDebugName(instance.subfamilyNameID)
        instance.postscriptNameID = name.addName(f"{slug}VF-{style.replace(' ', '')}", platforms=((3, 1, 0x409),))


def _win_metrics(cuts):
    """Windows line metrics (ascent, descent) for every file of the release.

    Word and PowerPoint space lines by these. They cover every European letter in every style; rarer
    letters with two stacked accents may be clipped on screen by old Windows programs. Where Raveo 1.1 is
    at hand its line pitch is kept, so a document set in it does not reflow when the fonts are replaced.
    """
    wanted = {cp for lo, hi in SUBSETS["european"] for cp in range(lo, hi + 1)}
    top, bottom = 0, 0
    for _, cut in cuts:
        glyf = cut["glyf"]
        for cp, glyph in cut.getBestCmap().items():
            if cp in wanted and getattr(glyf[glyph], "yMax", None) is not None:
                top, bottom = max(top, glyf[glyph].yMax), min(bottom, glyf[glyph].yMin)
    if OLD.exists():
        old = TTFont(OLD)["OS/2"]
        pitch = old.usWinAscent + old.usWinDescent
        descent = max(-bottom, old.usWinDescent)
        return max(top, pitch - descent), descent
    return top, -bottom


def _ranges(codepoints):
    """Compact unicode-range value for a CSS @font-face rule."""
    out, start, prev = [], None, None
    for cp in sorted(codepoints):
        if start is None:
            start = prev = cp
        elif cp == prev + 1:
            prev = cp
        else:
            out.append((start, prev))
            start = prev = cp
    if start is not None:
        out.append((start, prev))
    return ", ".join(f"U+{a:04X}" if a == b else f"U+{a:04X}-{b:04X}" for a, b in out)


def _rename(font, family, style, weights=WEIGHT_CLASS):
    """Name a static instance so apps list it as one family with many styles.

    Older programs know four styles per family: Regular, Italic, Bold and Bold Italic. So each width
    and optical size is a group of its own there ("Raveo", "Raveo Narrow", "Raveo Display Wide" ...)
    with those four linked: the B and I buttons in Word then reach the real Bold and the real Italic
    in every group. Every other weight is a group of two, upright and italic ("Raveo Medium").
    `weights` are the family's weight names; an italic Regular is just "Italic".
    """
    parts = style.split(" ")
    italic = parts[-1] == "Italic"
    if italic:
        parts = parts[:-1]
    weight = parts.pop() if parts and parts[-1] in weights else "Regular"
    group = " ".join([family, *parts])
    ribbi = weight in ("Regular", "Bold")
    bold = weight == "Bold"                             # the one weight of its group that the "B" button reaches
    linked = " ".join(word for word in ("Bold" if bold else "", "Italic" if italic else "") if word) or "Regular"
    name = font["name"]
    name.setName(group if ribbi else f"{group} {weight}", 1, 3, 1, 0x409)
    name.setName(linked, 2, 3, 1, 0x409)
    name.setName(f"{family} {style}", 4, 3, 1, 0x409)
    name.setName(f"{family.replace(' ', '')}-{style.replace(' ', '')}", 6, 3, 1, 0x409)
    name.setName(family, 16, 3, 1, 0x409)
    name.setName(style, 17, 3, 1, 0x409)
    os2, head = font["OS/2"], font["head"]
    os2.fsSelection = (os2.fsSelection & ~0b1100001) | (0b0100000 if bold else 0) | (1 if italic else 0) \
        | (0 if bold or italic else 0b1000000)
    head.macStyle = (head.macStyle & ~0b11) | (1 if bold else 0) | (2 if italic else 0)
    os2.usWeightClass = WEIGHT_CLASS.get(weight, int(round(os2.usWeightClass / 100)) * 100)
    # Panose, which Windows uses to pick a stand-in and some programs to sort a family: a sans, as light or heavy,
    # as narrow or wide and as upright or leaning as this style is.
    panose = os2.panose
    panose.bFamilyType, panose.bSerifStyle = 2, 11
    panose.bWeight = PANOSE_WEIGHT.get(os2.usWeightClass, 5)
    panose.bProportion = 6 if os2.usWidthClass < 5 else 5 if os2.usWidthClass > 5 else 3
    panose.bLetterForm = 9 if italic else 2


def version_label(version):
    """'1', '1.0' and '1.000' all name version 1.0; '1.010' names 1.01."""
    if not re.fullmatch(r"\d{1,2}(\.\d{1,3})?", str(version).strip()):
        raise ValueError(f"Version must look like 1.0 or 1.01, not {version!r}.")
    text = f"{float(version):.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def folder_name(recipe, version=None):
    return f"{recipe['family'].replace(' ', '-')}-{version_label(version or recipe['version'])}"


def releases():
    """Versions already exported, newest first."""
    out = []
    for path in RELEASES.glob("*/README.txt") if RELEASES.exists() else ():
        family, _, version = path.parent.name.rpartition("-")
        out.append({"folder": path.parent.name, "family": family.replace("-", " "), "version": version,
                    "date": time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))})
    return sorted(out, key=lambda r: [float(r["version"]) if re.fullmatch(r"[\d.]+", r["version"]) else 0], reverse=True)


def _readme(recipe, label, font, slug, italic=None):
    names = font["name"]
    axes = {a.axisTag: (a.minValue, a.maxValue) for a in font["fvar"].axes}
    styles = [names.getDebugName(i.subfamilyNameID) for i in font["fvar"].instances]
    slanted = italic and f"{-italic['post'].italicAngle:.1f}".rstrip("0").rstrip(".")
    rows = [", ".join(styles[i:i + 6]) for i in range(0, len(styles), 6)]
    credit = "\n".join(line for line in (names.getDebugName(0), names.getDebugName(9) and f"Designer: {names.getDebugName(9)}",
                                         names.getDebugName(11)) if line)
    family = recipe["family"]
    full = SUBSETS[recipe["subset"]] is None
    sized = "opsz" in axes
    about = (f"A typeface for text and headlines with three axes: optical size ({axes['opsz'][0]:g} to {axes['opsz'][1]:g}), weight\n"
             f"({axes['wght'][0]:g} to {axes['wght'][1]:g}) and width ({axes['wdth'][0]:g} to {axes['wdth'][1]:g}; 100 is the regular width)."
             if sized else
             f"A headline typeface with a weight axis ({axes['wght'][0]:g} to {axes['wght'][1]:g}) and a width axis\n"
             f"({axes['wdth'][0]:g} to {axes['wdth'][1]:g}; 100 is the regular width).")
    covers = ("Latin script for Western, Central and Northern European languages,\nthe Baltics, Romanian and Turkish, "
              "with symbols and arrows. No\nCyrillic, Greek or Vietnamese; for those, use Inter." if full else
              "English, Czech, Slovak,\nPolish, Baltic, Hungarian, Romanian, Turkish, Nordic and the Western European languages.")
    count = sum(1 for cp in font.getBestCmap() if not 0xE000 <= cp <= 0xF8FF)        # private-use alternates are not characters
    optical = f"""

OPTICAL SIZE

The font holds two drawings of every letter: one for text (optical size {axes['opsz'][0]:g}: taller small
letters, looser spacing) and one for headlines (optical size {axes['opsz'][1]:g}: tighter, with finer details).
Browsers, Figma and Adobe apps pick between them by type size on their own: the text drawing up to
{axes['opsz'][0]:g} px, the headline drawing from {axes['opsz'][1]:g} px, a blend in between. Styles named "Display" are the
headline drawing. To choose yourself in CSS:

    font-variation-settings: "opsz" {axes['opsz'][1]:g};      /* headline drawing at any size */
    font-optical-sizing: none;                  /* text drawing at any size */
""" if sized else ""
    lightest = f"{axes['wght'][0]:g}"
    upgrade = f"""

UPGRADING FROM RAVEO 1.1

Remove Raveo 1.1 before installing: several files have the same names inside and would sit
installed twice. What changes:

  Lightest weights     The weight axis now starts at {lightest}. Thin and ExtraLight (and their Display
                       versions) are gone; CSS weights below {lightest} show the Light.
  Display styles       They are part of the family "{family}" now ("{family} Display Bold"), no longer a
                       family "Raveo Display". Documents set in a Display style ask for the font again.
  Quotes and commas    Round; they were square.
  File names           Static fonts are TrueType (.ttf) named {slug}-<Style>.ttf; the variable font is
                       {slug}-VF.ttf and {slug}-VF.woff2 (was RaveoVF). Update paths in CSS.
  Kept                 Every character of 1.1, the variable font's names ("Raveo Variable") and the
                       line height. At the regular width only r, a, the semicolon, the quotes and
                       ď ť ľ are spaced differently.
""" if family == "Raveo" else ""
    single = f"""
        @font-face {{{{ font-family: "{family}"; src: url("{slug}-VF.woff2") format("woff2");
                     font-weight: {axes['wght'][0]:g} {axes['wght'][1]:g}; font-stretch: {axes['wdth'][0]:g}% {axes['wdth'][1]:g}%; font-style: normal; }}}}"""
    if italic:
        single += f"""
        @font-face {{{{ font-family: "{family}"; src: url("{slug}-Italic-VF.woff2") format("woff2");
                     font-weight: {axes['wght'][0]:g} {axes['wght'][1]:g}; font-stretch: {axes['wdth'][0]:g}% {axes['wdth'][1]:g}%; font-style: italic; }}}}"""
    single = single.replace("{{", "{").replace("}}", "}")
    italics = f"""

ITALIC

Every style has an italic, leaned {slanted} degrees. It is a second font file, not an axis: programs find it
with the I button, CSS with font-style: italic. It is drawn, not slanted by the program: Inter's italic
with Raveo's letters carried into it. The italic styles are named like the upright ones with "Italic"
added: Italic, Bold Italic, Narrow Medium Italic, Display Wide Black Italic.
""" if italic else ""
    italic_file = f"""
desktop/variable/{slug}-Italic-VF.ttf
    The italic of every style. Install it together with the file above.
""" if italic else ""
    return f"""{family} {label}
{time.strftime("%Y-%m-%d")}

{about}
{count} characters: {covers}
{optical}{italics}{upgrade}

DESKTOP

Install the variable font{"s" if italic else ""} or the static fonts; one set is enough. The variable font is listed as
"{family} Variable", the static fonts as "{family}", so they do not answer to each other's names.

desktop/variable/{slug}-VF.ttf
    One file with every {"upright " if italic else ""}style. Use this in Figma, Keynote, Adobe apps and browsers.
{italic_file}
desktop/static/
    One file per named style, for apps that do not support variable fonts (PowerPoint, Word).
    The styles{", each also as Italic" if italic else ""}:
    {(',' + chr(10) + '    ').join(rows)}


WEB

web/{slug}-VF.woff2{f" and web/{slug}-Italic-VF.woff2" if italic else ""}
    The variable font{"s" if italic else ""}. A page names {"their" if italic else "its"} ranges itself, or browsers fake the bold{" and the italic" if italic else ""}:
{single}

        h1 {{ font-family: "{family}", sans-serif; font-weight: 520; font-stretch: {axes['wdth'][1]:g}%; }}

web/static/
    One file per named style, as WOFF2.



LICENCE AND CREDITS

{family} is derived from {"Raveo 1.1" if family == "Raveo" else "Raveo"}, which is derived from Inter. It is licensed under the
SIL Open Font License 1.1 (OFL.txt): free to use, embed, modify and redistribute, also
commercially; it may not be sold on its own, and modified versions must keep this licence.

{credit}
"""


def _web_parts(source, family, folder, stem, add, label, italic=False):
    """Cut a variable font into unicode-range parts, written into `folder`; returns their @font-face rules."""
    # Letters that can meet in a word must be in one part: a browser shapes each part on its own, so a pair
    # split across two files loses its kerning (Tě, ř., and the room kept after the tick of ď ť ľ). So all
    # European letters and the common signs are one part; the rest, minus private-use alternates that no
    # page can type, is the other.
    have = set(TTFont(source).getBestCmap())
    first = {cp for part in ("latin", "latin-ext") for lo, hi in SUBSETS[part] for cp in range(lo, hi + 1)}
    first |= {cp for lo, hi in WEB_COMMON for cp in range(lo, hi + 1)}
    wanted = {"european": first & have}
    rest = {cp for cp in have - first if not 0xE000 <= cp <= 0xF8FF}
    if rest:
        wanted["more"] = rest
    css = []
    for part, codepoints in wanted.items():
        cut = TTFont(source)
        options = subset.Options(layout_features=WEB_FEATURES, glyph_names=False, notdef_outline=True,
                                 name_IDs=[0, 1, 2, 3, 4, 5, 6, 13, 14, 16, 17], drop_tables=["DSIG"])
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=sorted(codepoints))
        subsetter.subset(cut)
        cut.flavor = "woff2"
        path = folder / f"{stem}-{part}.woff2"
        cut.save(path)
        add(path, f"{label}, {part} part")
        axes = {a.axisTag: (a.minValue, a.maxValue) for a in cut["fvar"].axes}
        stretch = f"  font-stretch: {axes['wdth'][0]:g}% {axes['wdth'][1]:g}%;\n" if "wdth" in axes else ""
        css.append(f"/* {part}{', italic' if italic else ''} */\n@font-face {{\n  font-family: \"{family}\";\n"
                   f"  src: url(\"{path.name}\") format(\"woff2\");\n"
                   f"  font-style: {'italic' if italic else 'normal'};\n"
                   f"  font-weight: {axes['wght'][0]:g} {axes['wght'][1]:g};\n"
                   f"{stretch}  font-display: swap;\n"
                   f"  unicode-range: {_ranges(codepoints)};\n}}\n")
    return css


def pack(folder):
    """The zip people download: the release folder without the settings it was built from."""
    archive = folder.with_suffix(folder.suffix + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.name not in ("recipe.json", ".DS_Store"):
                out.write(path, path.relative_to(folder.parent))
    return archive


def export(pipeline, recipe, statics=True, version=None, italic=None):
    """Write a release. `italic` is the italic's pipeline; without it the release is upright only."""
    started = time.time()
    recipe = {**recipe, "version": version_label(version or recipe["version"])}
    label, name = recipe["version"], folder_name(recipe)
    slug = recipe["family"].replace(" ", "")
    out = BUILD / "release-out" / name                     # built aside, so a failed export leaves the old one alone
    if out.parent.exists():
        shutil.rmtree(out.parent)
    for sub in ("desktop/variable", "desktop/static", "web/static"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    files = []

    def add(path, kind):
        files.append({"path": f"releases/{name}/{path.relative_to(out)}", "kb": round(path.stat().st_size / 1024, 1),
                      "kind": kind})

    # Each cut of the family: the variable font, and every named style cut out of it. The heaviest
    # styles reach higher and lower than the regular, so the Windows clipping limits are taken from
    # all of them, upright and italic, for every file.
    weights = set(style_weights(recipe).values())
    family = []
    for engine in (pipeline, italic) if italic else (pipeline,):
        report = build(engine, recipe, BUILD / "release")
        font = TTFont(report["ttf"])
        _name_variable(font, recipe["family"], engine.italic)
        _stamp(font, recipe)
        cuts = [(font["name"].getDebugName(inst.subfamilyNameID),
                 instancer.instantiateVariableFont(TTFont(report["ttf"]), inst.coordinates,
                                                   overlap=instancer.OverlapMode.REMOVE))
                for inst in font["fvar"].instances]
        family.append((engine.italic, f"{slug}-Italic-VF" if engine.italic else f"{slug}-VF", font, cuts, report))
    credits = [[font["name"].getDebugName(i) for i in (0, 7, 8, 9, 11, 13, 14)] + [font["OS/2"].achVendID] for _, _, font, _, _ in family]
    if any(row != credits[0] for row in credits):
        raise ValueError(f"The italic's credits differ from the roman's: {credits}. Run tools/make_italic.py again.")
    ascent, descent = _win_metrics([cut for _, _, _, cuts, _ in family for cut in cuts])
    first = family[0][2]

    for slanted, stem, font, cuts, report in family:
        kind = "Variable italic" if slanted else "Variable"
        desktop = out / "desktop" / "variable" / f"{stem}.ttf"
        font["OS/2"].usWinAscent, font["OS/2"].usWinDescent = ascent, descent
        font["post"].underlineThickness, font["post"].underlinePosition = first["post"].underlineThickness, first["post"].underlinePosition
        font.save(desktop)
        add(desktop, f"{kind}, desktop")
        font.flavor = "woff2"
        web = out / "web" / f"{stem}.woff2"
        font.save(web)
        add(web, f"{kind}, web")
        if statics:
            for style, cut in cuts:
                _rename(cut, recipe["family"], style, weights)
                _stamp(cut, recipe)
                cut["OS/2"].usWinAscent, cut["OS/2"].usWinDescent = ascent, descent
                cut["post"].underlineThickness = first["post"].underlineThickness      # one underline for the family
                cut["post"].underlinePosition = first["post"].underlinePosition
                path = out / "desktop" / "static" / f"{slug}-{style.replace(' ', '')}.ttf"
                cut.save(path)
                add(path, "Static italic, desktop" if slanted else "Static, desktop")
                cut.flavor = "woff2"
                path = out / "web" / "static" / f"{slug}-{style.replace(' ', '')}.woff2"
                cut.save(path)
                add(path, "Static italic, web" if slanted else "Static, web")
    desktop = out / "desktop" / "variable" / f"{slug}-VF.ttf"
    report = family[0][4]

    shutil.copy(ROOT / "LICENSE.txt", out / "OFL.txt")
    (out / "recipe.json").write_text(json.dumps(recipe, indent=2) + "\n")
    (out / "README.txt").write_text(_readme(recipe, label, TTFont(desktop), slug, family[1][2] if italic else None))
    for extra, kind in (("README.txt", "What each file is for"), ("OFL.txt", "Licence"), ("recipe.json", "Settings of this version")):
        add(out / extra, kind)

    final = RELEASES / name
    replaced = final.exists()
    RELEASES.mkdir(exist_ok=True)
    if replaced:
        shutil.rmtree(final)
    shutil.move(str(out), str(final))
    archive = pack(final)
    files.append({"path": f"releases/{archive.name}", "kb": round(archive.stat().st_size / 1024, 1),
                  "kind": "Everything above, zipped"})
    return {"files": files, "seconds": round(time.time() - started, 1), "glyphs": report["glyphs"],
            "folder": f"releases/{name}", "version": label, "replaced": replaced}


if __name__ == "__main__":
    result = export(Pipeline(), load_recipe(), version=sys.argv[1] if len(sys.argv) > 1 else None,
                    italic=Pipeline(italic=True) if ITALIC_SOURCE.exists() else None)
    for f in result["files"]:
        print(f"{f['kb']:>8} KB  {f['kind']:<34} {f['path']}")
    print(f"{len(result['files'])} files in {result['folder']} ({'replaced' if result['replaced'] else 'new'}), {result['seconds']} s")
