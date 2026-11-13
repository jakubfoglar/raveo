# Raveo: font log

Raveo is a sans-serif family for interfaces and headlines, derived from Inter and released under the
SIL Open Font License 1.1. This file records what changed in each version and who made it.

## 2.0 (November 2026)

- A width axis, 60 to 125, with Narrow and Wide as named styles at every weight.
- An italic for every style: a second variable font and 42 static styles. It is made from Inter's
  italic source (rsms/inter at commit `66647c0`, version 4.1) with Raveo's letters carried into it.
  The letters a, e and f keep their upright forms.
- The weight axis runs from 300 to 900 (was 100 to 900). Thin and ExtraLight are gone.
- Curved strokes end in an angled cut; the openings of a, c, e and s are wider.
- Round commas and quotes. The tick of ď ť ľ sits closer to its letter and the next letter is
  kept clear of it.
- A longer arm on the r; a redrawn bowl on the a at the narrow and wide ends.
- The r and the a take the longer arm and the more open bowl of the wordmark they were first drawn for.
- Every style is derived from the hand-drawn masters in `sources/` by the settings in `recipe.json`.
- 51 more characters, among them æ ø ð œ ă ğ ő ű and « »: Danish, Norwegian, Icelandic, Hungarian,
  Romanian and Turkish can be set.
- Static fonts are TrueType. Display styles are part of the family "Raveo".
- Web fonts are WOFF2: the two variable files and every static style.
- Kerning differs from Inter's in one pair: the low quote is no longer pulled toward a.

## 1.1 (October 2024)

- The first public release: Inter with an a with a tail, an R with a curled leg, the G with a spur and
  the alternate 1 as defaults, darker named weights (Medium 520, SemiBold 630, Bold 730), and a glyph
  set cut down to extended Latin.

## Acknowledgements

- Jakub Foglar (https://jakubfoglar.com): design of Raveo.
- Rasmus Andersson and the Inter Project Authors (https://github.com/rsms/inter): Inter, which Raveo is
  derived from.

"Raveo" is a Reserved Font Name. Subsetting the fonts or converting them to another format, without
changing the letters, is permitted under the name Raveo.
