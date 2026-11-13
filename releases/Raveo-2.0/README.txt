Raveo 2.0

Weight        300 to 900
Width         60 to 125
Optical size  14 to 32
826 characters, Latin script, upright and italic


INSTALL

One of the two sets, not both. Remove Raveo 1.1 first.

desktop/variable/   Raveo-VF.ttf and Raveo-Italic-VF.ttf, listed as "Raveo Variable"
desktop/static/     one file per style, listed as "Raveo", for apps without variable fonts


WEB

web/                Raveo-VF.woff2 and Raveo-Italic-VF.woff2
web/static/         one file per style

    @font-face { font-family: "Raveo"; src: url("Raveo-VF.woff2") format("woff2");
                 font-weight: 300 900; font-stretch: 60% 125%; font-style: normal; }
    @font-face { font-family: "Raveo"; src: url("Raveo-Italic-VF.woff2") format("woff2");
                 font-weight: 300 900; font-stretch: 60% 125%; font-style: italic; }


COMING FROM RAVEO 1.1

The weight axis starts at 300: Thin and ExtraLight are gone.
Display styles belong to the family "Raveo", not "Raveo Display".
Files are named Raveo-VF, not RaveoVF.


LICENCE

SIL Open Font License 1.1, in OFL.txt: free to use, embed, modify and redistribute, also commercially.
It may not be sold on its own, and modified versions must keep the licence.

Copyright 2024-2026 Jakub Foglar (https://jakubfoglar.com). Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter)
Designer: Jakub Foglar, Rasmus Andersson (Inter)
https://jakubfoglar.com
