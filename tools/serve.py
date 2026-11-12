"""Workbench server: serves the repo and rebuilds the font on request.

    python tools/serve.py            # http://localhost:4173/workbench/

GET  /api/recipe          the saved recipe (recipe.json over the defaults)
GET  /api/fontinfo        axes, named styles, features and characters of the last built font
GET  /api/site            the same and more, as the public page site/index.html wants it (open /site/?build)
POST /api/outline         body: recipe + text, wght, opsz -> outlines for the live preview (no build)
POST /api/build           body: recipe + opsz -> builds a quick draft of one optical size for the workbench;
                          with "complete": true, the whole font that the specimen pages and releases use
                          (roman and italic)
Requests that draw or build take "italic": true to work on the italic instead of the roman.
POST /api/quality         body: recipe -> kinked joins and curvature jumps per style
POST /api/pair            body: recipe + pair, wght, wdth, opsz -> kerning, gap and nearest distance of two
                          characters, with the recipe and in the untouched source
GET  /api/findings        ?italic&opsz&wght&wdth -> what tools/kerning_review.py measured in the nearest style:
                          the pairs the recipe moved most, the letter pairs standing closest
GET  /api/releases        versions already exported, and the version the recipe names
POST /api/export          body: recipe + version -> writes releases/<Family>-<version>/, returns the file list
POST /api/recipe          body: recipe -> saves it as recipe.json
"""
import json
import sys
import threading
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import (BUILD, DEFAULT_RECIPE, DISPLAY, ITALIC_SOURCE, POLE_KEYS, ROOT, TEXT, Pipeline, build,  # noqa: E402
                      load_recipe, normalize, style_weights, style_widths)

PORT = 4173
EDITABLE = ("widths", "stem_adapt", "contrast", "square_dots", "terminal_angle", "aperture", "terminal_soften",
            "squareness", "smoothing", "spacing", "glyphs", "punctuation_spacing", "caron_gap", "caron_tuck",
            "kerning_floor", "logo_r", "logo_a", "round_commas", "diagonals", "diagonal_taper", "weights",
            "text", "light_spacing", "named_weights", "named_widths", "caron_floor", "contrast_follow", "spacing_by_side",
            "weight_spread", "leg", "logo_r_over", "italic_forms", "slant_compensation", "italic_round_dots")
lock = threading.Lock()
state = {"pipelines": {}, "version": 0}


def pipeline(italic=False):
    """The roman's engine or the italic's; each loads its source the first time it is asked for."""
    italic = bool(italic) and ITALIC_SOURCE.exists()
    if italic not in state["pipelines"]:
        state["pipelines"][italic] = Pipeline(italic=italic)
    return state["pipelines"][italic]


def ready(italic=False):
    italic = bool(italic) and ITALIC_SOURCE.exists()
    if italic not in state["pipelines"]:
        with lock:
            pipeline(italic)
    return state["pipelines"][italic]


def warm():
    with lock:
        pipeline()


def fontinfo():
    """What the specimen page needs to know about the built font."""
    slug = load_recipe()["family"].replace(" ", "")
    path = BUILD / "headline" / f"{slug}-VF.ttf"
    slanted = BUILD / "headline" / f"{slug}-Italic-VF.ttf"
    font = TTFont(path)
    names = font["name"]
    features = set()
    for tag in ("GSUB", "GPOS"):
        if tag in font:
            features |= {r.FeatureTag for r in font[tag].table.FeatureList.FeatureRecord}
    return {
        "url": f"/{path.relative_to(ROOT)}?v={int(path.stat().st_mtime)}",
        "italic": f"/{slanted.relative_to(ROOT)}?v={int(slanted.stat().st_mtime)}" if slanted.exists() else None,
        "family": names.getDebugName(16) or names.getDebugName(1),
        "axes": {a.axisTag: [a.minValue, a.defaultValue, a.maxValue] for a in font["fvar"].axes},
        "styles": [{"name": names.getDebugName(i.subfamilyNameID), **{k: round(v) for k, v in i.coordinates.items()}}
                   for i in font["fvar"].instances],
        "features": sorted(features),
        "characters": [cp for cp in sorted(font.getBestCmap()) if cp > 0x20 and cp != 0xA0],
        "glyphs": len(font.getGlyphOrder()),
    }


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and "/api/" in str(args[0]):
            super().log_message(fmt, *args)

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path.startswith("/api/recipe"):
            return self._json(load_recipe())
        if self.path.startswith("/api/releases"):
            import release
            recipe = load_recipe()
            return self._json({"family": recipe["family"], "version": release.version_label(recipe["version"]),
                               "releases": release.releases()})
        if self.path.startswith("/api/site"):
            import site_page
            try:
                return self._json(site_page.working())
            except FileNotFoundError:
                return self._json({"error": "No font built yet. Save the recipe in the workbench, or run tools/pipeline.py."}, 404)
        if self.path.startswith("/api/findings"):
            import kerning_review
            from urllib.parse import parse_qs, urlparse
            ask = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            found = kerning_review.findings(ask.get("italic") == "1", int(float(ask.get("opsz", DISPLAY))),
                                            float(ask.get("wght", 400)), float(ask.get("wdth", 100)))
            return self._json(found or {"error": "Nothing measured yet. Run: python tools/kerning_review.py measure"}, 200 if found else 404)
        if self.path.startswith("/api/fontinfo"):
            try:
                return self._json(fontinfo())
            except FileNotFoundError:
                return self._json({"error": "No font built yet. Open the workbench once, or run tools/pipeline.py."}, 404)
        return super().do_GET()

    def _recipe(self, body):
        recipe = load_recipe()
        recipe.update({k: v for k, v in body.items() if k in EDITABLE})
        narrow, _, wide = recipe["widths"]
        recipe["widths"] = [int(max(50, min(96, narrow))), 100, int(max(104, min(160, wide)))]
        light, heavy = int(max(100, min(380, recipe["weights"][0]))), int(max(420, min(900, recipe["weights"][-1])))
        between = sorted({400, *(int(w) for w in recipe["weights"][1:-1] if light < w < heavy)})
        recipe["weights"] = [light, *between, heavy]
        for key, lo, hi in (("named_weights", light, heavy), ("named_widths", recipe["widths"][0], recipe["widths"][-1])):
            seen, named = set(), []                  # one name per position, one position per name, inside the axis
            for value, name in sorted((int(round(float(v))), str(n).strip()[:24]) for v, n in recipe.get(key) or []):
                if name and lo <= value <= hi and value not in seen and name not in seen:
                    seen |= {value, name}
                    named.append([value, name])
            recipe[key] = named or None
        text = recipe.get("text") or {}
        recipe["text"] = {key: value for key, value in text.items() if key in POLE_KEYS or key in ("glyphs", "logo_r_over")}
        forms = recipe.get("italic_forms") or {}
        recipe["italic_forms"] = {letter: "cursive" if forms.get(letter) == "cursive" else "upright" for letter in ("a", "e", "f")}
        recipe["slant_compensation"] = max(0.0, min(1.5, float(recipe.get("slant_compensation") or 0)))
        return normalize(recipe)

    def _size(self, body, recipe):
        """The optical size a preview asks for; the headline drawings unless the recipe has the text size."""
        return TEXT if int(body.get("opsz") or DISPLAY) == TEXT and TEXT in recipe["optical_sizes"] else DISPLAY

    def do_POST(self):
        try:
            body = self._body()
            if self.path.startswith("/api/outline"):
                engine = ready(body.get("italic"))
                recipe = self._recipe(body)
                return self._json(engine.outline(
                    recipe, str(body.get("sample") or "acers")[:24], float(body.get("wght", 630)),
                    [float(w) for w in body.get("row_widths", recipe["widths"])][:4], bool(body.get("comb")),
                    self._size(body, recipe)))
            if self.path.startswith("/api/pair"):
                engine = ready(body.get("italic"))
                recipe = self._recipe(body)
                pair = str(body.get("pair") or "")
                found = engine.pair(recipe, pair[0], pair[1], float(body.get("wght", 630)), float(body.get("wdth", 100)),
                                    self._size(body, recipe)) if len(pair) == 2 else None
                return self._json(found or {"error": "No such pair in the font"}, 200 if found else 404)
            if self.path.startswith("/api/space"):
                # What the map of styles draws: the masters, the named styles as the font will have them, and how
                # heavy each width can get before its shapes would need more than Raveo Black.
                engine = ready(body.get("italic"))
                recipe = self._recipe(body)
                size = self._size(body, recipe)
                narrow, wide = recipe["widths"][0], recipe["widths"][-1]
                steps = [narrow + (100 - narrow) * i / 12 for i in range(13)]
                return self._json({
                    "weights": [[w, n] for w, n in style_weights(recipe).items()],
                    "widths": [[w, n] for w, n in style_widths(recipe).items()],
                    "limit": [[round(w, 1), round(engine.heaviest(recipe, w, size))] for w in steps],
                    "masters": [[width, weight] for width in recipe["widths"] for weight in recipe["weights"]]})
            if self.path.startswith("/api/quality"):
                engine = ready(body.get("italic"))
                recipe = self._recipe(body)
                return self._json({"rows": engine.quality(recipe, opsz=self._size(body, recipe))})
            if self.path.startswith("/api/build"):
                recipe = self._recipe(body)
                out_dir = BUILD / "headline"
                if not body.get("complete"):         # a draft: one optical size and three weights build in a third of the time
                    recipe = {**recipe, "optical_sizes": [self._size(body, recipe)],
                              "weights": [recipe["weights"][0], 400, recipe["weights"][-1]]}
                    out_dir = BUILD / "draft"
                slanted = bool(body.get("italic")) and ITALIC_SOURCE.exists()
                with lock:
                    report = build(pipeline(slanted), recipe, out_dir, woff2=False)
                    other = None
                    if body.get("complete") and ITALIC_SOURCE.exists():     # the whole family: the other cut as well
                        other = build(pipeline(not slanted), recipe, out_dir, woff2=False)
                    state["version"] += 1
                    version = state["version"]
                ttf = report.pop("ttf")
                report["url"] = f"/{ttf.relative_to(ROOT)}?v={version}"
                report["italic"] = slanted
                if other:
                    report["also"] = f"/{other.pop('ttf').relative_to(ROOT)}?v={version}"
                return self._json(report)
            if self.path.startswith("/api/export"):
                import release
                version = release.version_label(body.get("version") or load_recipe()["version"])
                with lock:
                    result = release.export(pipeline(), self._recipe(body), version=version,
                                            italic=pipeline(True) if ITALIC_SOURCE.exists() else None)
                saved = json.loads((ROOT / "recipe.json").read_text())      # remember the number for next time
                if saved.get("version") != version:
                    saved["version"] = version
                    (ROOT / "recipe.json").write_text(json.dumps(saved, indent=2) + "\n")
                return self._json(result)
            if self.path.startswith("/api/recipe"):
                recipe = self._recipe(body)
                ordered = {k: recipe[k] for k in DEFAULT_RECIPE if k in recipe}
                (ROOT / "recipe.json").write_text(json.dumps(ordered, indent=2) + "\n")
                return self._json({"saved": True})
            return self._json({"error": "not found"}, 404)
        except Exception as exc:  # report build failures to the page instead of dropping the request
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)


def main():
    BUILD.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=str(ROOT)))
    print(f"workbench: http://localhost:{PORT}/workbench/")
    threading.Thread(target=warm, daemon=True).start()   # load the source while the page opens
    server.serve_forever()


if __name__ == "__main__":
    main()
