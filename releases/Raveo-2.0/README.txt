Raveo 2.0
2026-11-13

A typeface for text and headlines with three axes: optical size (14 to 32), weight
(300 to 900) and width (60 to 125; 100 is the regular width).
826 characters: Latin script for Western, Central and Northern European languages,
the Baltics, Romanian and Turkish, with symbols and arrows. No
Cyrillic, Greek or Vietnamese; for those, use Inter.


OPTICAL SIZE

The font holds two drawings of every letter: one for text (optical size 14: taller small
letters, looser spacing) and one for headlines (optical size 32: tighter, with finer details).
Browsers, Figma and Adobe apps pick between them by type size on their own: the text drawing up to
14 px, the headline drawing from 32 px, a blend in between. Styles named "Display" are the
headline drawing. To choose yourself in CSS:

    font-variation-settings: "opsz" 32;      /* headline drawing at any size */
    font-optical-sizing: none;                  /* text drawing at any size */


ITALIC

Every style has an italic, leaned 9.4 degrees. It is a second font file, not an axis: programs find it
with the I button, CSS with font-style: italic. It is drawn, not slanted by the program: Inter's italic
with Raveo's letters carried into it. The italic styles are named like the upright ones with "Italic"
added: Italic, Bold Italic, Narrow Medium Italic, Display Wide Black Italic.


UPGRADING FROM RAVEO 1.1

Remove Raveo 1.1 before installing: several files have the same names inside and would sit
installed twice. What changes:

  Lightest weights     The weight axis now starts at 300. Thin and ExtraLight (and their Display
                       versions) are gone; CSS weights below 300 show the Light.
  Display styles       They are part of the family "Raveo" now ("Raveo Display Bold"), no longer a
                       family "Raveo Display". Documents set in a Display style ask for the font again.
  Quotes and commas    Round; they were square.
  File names           Static fonts are TrueType (.ttf) named Raveo-<Style>.ttf; the variable font is
                       Raveo-VF.ttf and Raveo-VF.woff2 (was RaveoVF). Update paths in CSS.
  Kept                 Every character of 1.1, the variable font's names ("Raveo Variable") and the
                       line height. At the regular width only r, a, the semicolon, the quotes and
                       ď ť ľ are spaced differently.


DESKTOP

Install the variable fonts or the static fonts; one set is enough. The variable font is listed as
"Raveo Variable", the static fonts as "Raveo", so they do not answer to each other's names.

desktop/variable/Raveo-VF.ttf
    One file with every upright style. Use this in Figma, Keynote, Adobe apps and browsers.

desktop/variable/Raveo-Italic-VF.ttf
    The italic of every style. Install it together with the file above.

desktop/static/
    One file per named style, for apps that do not support variable fonts (PowerPoint, Word).
    The styles, each also as Italic:
    Narrow Light, Narrow Regular, Narrow Medium, Narrow SemiBold, Narrow Bold, Narrow ExtraBold,
    Narrow Black, Light, Regular, Medium, SemiBold, Bold,
    ExtraBold, Black, Wide Light, Wide Regular, Wide Medium, Wide SemiBold,
    Wide Bold, Wide ExtraBold, Wide Black, Display Narrow Light, Display Narrow Regular, Display Narrow Medium,
    Display Narrow SemiBold, Display Narrow Bold, Display Narrow ExtraBold, Display Narrow Black, Display Light, Display Regular,
    Display Medium, Display SemiBold, Display Bold, Display ExtraBold, Display Black, Display Wide Light,
    Display Wide Regular, Display Wide Medium, Display Wide SemiBold, Display Wide Bold, Display Wide ExtraBold, Display Wide Black


WEB

web/Raveo-VF.woff2 and web/Raveo-Italic-VF.woff2
    The variable fonts. A page names their ranges itself, or browsers fake the bold and the italic:

        @font-face { font-family: "Raveo"; src: url("Raveo-VF.woff2") format("woff2");
                     font-weight: 300 900; font-stretch: 60% 125%; font-style: normal; }
        @font-face { font-family: "Raveo"; src: url("Raveo-Italic-VF.woff2") format("woff2");
                     font-weight: 300 900; font-stretch: 60% 125%; font-style: italic; }

        h1 { font-family: "Raveo", sans-serif; font-weight: 520; font-stretch: 125%; }

web/static/
    One file per named style, as WOFF2.



LICENCE AND CREDITS

Raveo is derived from Raveo 1.1, which is derived from Inter. It is licensed under the
SIL Open Font License 1.1 (OFL.txt): free to use, embed, modify and redistribute, also
commercially; it may not be sold on its own, and modified versions must keep this licence.

Copyright 2024-2026 Jakub Foglar (https://jakubfoglar.com). Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter)
Designer: Jakub Foglar, Rasmus Andersson (Inter)
https://jakubfoglar.com
