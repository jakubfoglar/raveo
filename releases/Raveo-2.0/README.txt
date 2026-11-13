Raveo 2.0

Raveo is a sans in seven weights and three widths, each with an italic. It covers the Latin script
of European languages, 826 characters in all.
Specimen and tester: https://raveo.jakubfoglar.com


INSTALLING

The folder "desktop" holds the typeface in two forms. Install the one your programs can use:

variable/   Raveo-VF.ttf and Raveo-Italic-VF.ttf, with every style inside them. For Figma,
            Keynote, Adobe apps and other programs that support variable fonts. Font menus
            list them as "Raveo Variable".

static/     One file for each of the 84 styles, for programs that do not support variable
            fonts, such as Word and PowerPoint. Font menus list them as "Raveo".
            The styles named Display are drawn for headlines, the others for text.

If Raveo 1.1 is installed, remove it first.


ON A WEBSITE

The folder "web" has the variable fonts as WOFF2. Put them beside your stylesheet and add:

    @font-face { font-family: "Raveo"; src: url("Raveo-VF.woff2") format("woff2");
                 font-weight: 300 900; font-stretch: 60% 125%; font-style: normal; }
    @font-face { font-family: "Raveo"; src: url("Raveo-Italic-VF.woff2") format("woff2");
                 font-weight: 300 900; font-stretch: 60% 125%; font-style: italic; }

    body { font-family: "Raveo", sans-serif; }
    h1   { font-weight: 730; font-stretch: 125%; }

Weight runs from 300 to 900 and width from 60% to 125%. The folder web/static has every style as
a file of its own, for sites that would rather not use a variable font.


IF YOU USED RAVEO 1.1

- Thin and ExtraLight are gone. The lightest weight is now Light (300).
- The Display styles are part of the family "Raveo" now, not a separate "Raveo Display", so
  documents that use them will ask for the font again.
- The files have new names (Raveo-VF, not RaveoVF), so links to them in CSS need updating.


LICENCE

Raveo is free to use, also commercially, under the SIL Open Font License 1.1 (OFL.txt). You may
embed it, change it and pass it on. You may not sell it on its own, and a changed version must keep
the licence and take another name.

Copyright 2024-2026 Jakub Foglar (https://jakubfoglar.com). Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter)
Designer: Jakub Foglar, Rasmus Andersson (Inter)
https://jakubfoglar.com
