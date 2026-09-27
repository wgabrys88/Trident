# Desktop UI reference

Deterministic checklist for a later Gemma compare against `desktop_ui_reference.png`.
Boxes are measured on this file. They are not estimated from a different screenshot.

Origin is the top-left pixel. Normalized coordinates are `x / 1920` and `y / 1080`.
A box is `(x0, y0)–(x1, y1)`. A center is the midpoint of that box.

## Source

- Dataset: [zonghanHZH/ScreenSpot-v2](https://huggingface.co/datasets/zonghanHZH/ScreenSpot-v2)
- License: Apache-2.0, from the dataset card metadata `license: apache-2.0`. The dataset repository has no separate `LICENSE` file; the card is the license declaration. Apache-2.0 allows redistribution of this file with that attribution.
- Same license tag on the mirror [OS-Copilot/ScreenSpot-v2](https://huggingface.co/datasets/OS-Copilot/ScreenSpot-v2). The bytes in this tree were downloaded from `zonghanHZH/ScreenSpot-v2`.
- File id: `images/pc_2c6dc6ea-fd6f-4cbb-9954-2d6a68610a48.png`
- Download URL: https://huggingface.co/datasets/zonghanHZH/ScreenSpot-v2/resolve/main/images/pc_2c6dc6ea-fd6f-4cbb-9954-2d6a68610a48.png
- Blob page: https://huggingface.co/datasets/zonghanHZH/ScreenSpot-v2/blob/main/images/pc_2c6dc6ea-fd6f-4cbb-9954-2d6a68610a48.png
- Viewer row: split `desktop`, id `screenspot_2`, instruction `view solitaire daily challenges`, `data_source` `windows`, `data_type` `text`, `img_size` `[1920, 1080]`
- Dataset target box for that row: `[1477, 450, 184, 230]`. Read as x, y, width, height, it covers the Solitaire Daily Challenges tile (C18).
- Download date: 2026-09-27
- SHA256: `4915e070e5112ac965663f6e2db858705a57ccf8344bde573d040686b6cb91fa`
- Dimensions: 1920 × 1080
- Local path: `artifacts/reference/desktop_ui_reference.png`

## How the boxes were measured

Text boxes are Windows OCR (`Windows.Media.Ocr`, language `en-US`) on this PNG.
FreeCell and Pyramid in tile row 1, and Klondike in the hero, were missed by the full-frame pass and read from 6× crops. Those crop boxes are mapped back onto the 1920×1080 pixels.

The title bar ends at the horizontal edge `y=32`. Its three buttons are the dark glyphs in `y=0–31` at glyph centers `x=1804`, `x=1850`, and `x=1896` (46px apart). Each control box is a 46px slot, and the Close slot ends at `x=1919`.

The taskbar starts at the horizontal edge `y=1040`. The search field is the connected component `(48, 1041)–(391, 1077)`. Start is the slot from `x=0` to that component, ending at `x=48`.
Other taskbar icons are connected components of pixels that differ from the empty-taskbar color RGB `(32, 51, 64)`, in `y=1041–1077`, keeping components of at least 40 pixels. Fragments within 8px in x were merged.

Tile columns are the strong vertical edges in `y=500–710`. There are seven columns. Row 1 is `y=466–702`: card pixels leave the blue gutter at about `y=466`, and a horizontal edge sits at `y=701`. Row 2 is `y=710–1036`, from that edge down to just above the taskbar. The click box in the table is the column rectangle. The note gives the OCR label box, which is the exact text.

## Layout map

| Region | Normalized box | Pixel box | What is there |
| --- | --- | --- | --- |
| Title bar | (0.0000, 0.0000)–(1.0000, 0.0296) | (0, 0)–(1920, 32) | White window chrome across the full width. Title text on the left. Minimize, Maximize, and Close on the right. |
| App canvas | (0.0000, 0.0296)–(1.0000, 0.9630) | (0, 32)–(1920, 1040) | Microsoft Solitaire & Casual Games, filling the screen above the taskbar. |
| Hero panel | (0.1250, 0.1296)–(0.8750, 0.3444) | (240, 140)–(1680, 372) | Large panel. Wordmark, title, Play, and a row of game names. |
| Section heading | (0.1250, 0.3444)–(0.8750, 0.4315) | (240, 372)–(1680, 466) | Heading Microsoft Casual Games on the blue canvas. |
| Tile row 1 | (0.1354, 0.4315)–(0.8646, 0.6500) | (260, 466)–(1660, 702) | Seven game tiles. Columns are listed under the controls. |
| Tile row 2 | (0.1354, 0.6574)–(0.8646, 0.9593) | (260, 710)–(1660, 1036) | Seven game tiles. |
| Taskbar | (0.0000, 0.9630)–(1.0000, 1.0000) | (0, 1040)–(1920, 1080) | Windows taskbar. Start, search, pinned buttons, empty middle, tray, clock. |
| Empty taskbar middle | (0.4083, 0.9630)–(0.8026, 1.0000) | (784, 1040)–(1541, 1080) | Taskbar background only. No icons. |

Tile columns, shared by both rows. Pixel x, then normalized x.

| Column | Pixel x | Normalized x |
| --- | --- | --- |
| 1 | 260–445 | 0.1354–0.2318 |
| 2 | 463–648 | 0.2411–0.3375 |
| 3 | 665–850 | 0.3464–0.4427 |
| 4 | 868–1053 | 0.4521–0.5484 |
| 5 | 1070–1255 | 0.5573–0.6536 |
| 6 | 1273–1458 | 0.6630–0.7594 |
| 7 | 1475–1660 | 0.7682–0.8646 |

Quadrants, for a coarse check: the title bar is the top edge; the hero sits in the upper middle; the fourteen tiles fill the center and lower middle; the taskbar is the bottom strip. The left and right edges of the canvas are blue margin outside the hero and outside the tile columns.

## Interactive controls

Count: 43.

C01–C03 are window chrome. C04–C11 are controls in the hero. C12–C18 are tile row 1. C19–C25 are tile row 2. C26–C43 are the taskbar. For a tile, the box is the column rectangle (the click target). The label box in the note is the OCR text.

| Id | Name | Type | Pixel box | Normalized box | Center |
| --- | --- | --- | --- | --- | --- |
| C01 | Minimize | window chrome | (1781.0, 0.0)–(1827.0, 32.0) | (0.9276, 0.0000)–(0.9516, 0.0296) | (0.9396, 0.0148) |
| C02 | Maximize | window chrome | (1827.0, 0.0)–(1873.0, 32.0) | (0.9516, 0.0000)–(0.9755, 0.0296) | (0.9635, 0.0148) |
| C03 | Close | window chrome | (1873.0, 0.0)–(1919.0, 32.0) | (0.9755, 0.0000)–(0.9995, 0.0296) | (0.9875, 0.0148) |
| C04 | Menu | button | (14.0, 71.0)–(50.0, 82.5) | (0.0073, 0.0657)–(0.0260, 0.0764) | (0.0167, 0.0711) |
| C05 | Sign in | button | (1705.5, 64.2)–(1778.0, 91.2) | (0.8883, 0.0594)–(0.9260, 0.0844) | (0.9072, 0.0719) |
| C06 | Play | button | (581.7, 303.3)–(639.4, 335.3) | (0.3030, 0.2808)–(0.3330, 0.3105) | (0.3180, 0.2956) |
| C07 | Klondike | button | (1048.8, 300.8)–(1160.0, 329.6) | (0.5463, 0.2785)–(0.6042, 0.3052) | (0.5752, 0.2919) |
| C08 | Spider | button | (1166.3, 268.7)–(1247.0, 301.4) | (0.6074, 0.2488)–(0.6495, 0.2791) | (0.6285, 0.2639) |
| C09 | FreeCell | button | (1273.0, 259.3)–(1363.3, 280.0) | (0.6630, 0.2401)–(0.7101, 0.2593) | (0.6865, 0.2497) |
| C10 | Pyramid | button | (1383.7, 262.0)–(1482.7, 298.0) | (0.7207, 0.2426)–(0.7722, 0.2759) | (0.7465, 0.2593) |
| C11 | TriPeaks | button | (1509.0, 292.0)–(1589.0, 333.0) | (0.7859, 0.2704)–(0.8276, 0.3083) | (0.8068, 0.2894) |
| C12 | Solitaire Collection | button | (260.0, 466.0)–(445.0, 702.0) | (0.1354, 0.4315)–(0.2318, 0.6500) | (0.1836, 0.5407) |
| C13 | Classic Solitaire (Klondike) | button | (463.0, 466.0)–(648.0, 702.0) | (0.2411, 0.4315)–(0.3375, 0.6500) | (0.2893, 0.5407) |
| C14 | Spider | button | (665.0, 466.0)–(850.0, 702.0) | (0.3464, 0.4315)–(0.4427, 0.6500) | (0.3945, 0.5407) |
| C15 | FreeCell | button | (868.0, 466.0)–(1053.0, 702.0) | (0.4521, 0.4315)–(0.5484, 0.6500) | (0.5003, 0.5407) |
| C16 | Pyramid | button | (1070.0, 466.0)–(1255.0, 702.0) | (0.5573, 0.4315)–(0.6536, 0.6500) | (0.6055, 0.5407) |
| C17 | TriPeaks | button | (1273.0, 466.0)–(1458.0, 702.0) | (0.6630, 0.4315)–(0.7594, 0.6500) | (0.7112, 0.5407) |
| C18 | Solitaire Daily Challenges | button | (1475.0, 466.0)–(1660.0, 702.0) | (0.7682, 0.4315)–(0.8646, 0.6500) | (0.8164, 0.5407) |
| C19 | Mahjong | button | (260.0, 710.0)–(445.0, 1036.0) | (0.1354, 0.6574)–(0.2318, 0.9593) | (0.1836, 0.8083) |
| C20 | Jigsaw | button | (463.0, 710.0)–(648.0, 1036.0) | (0.2411, 0.6574)–(0.3375, 0.9593) | (0.2893, 0.8083) |
| C21 | Sudoku | button | (665.0, 710.0)–(850.0, 1036.0) | (0.3464, 0.6574)–(0.4427, 0.9593) | (0.3945, 0.8083) |
| C22 | Ultimate Word Games | button | (868.0, 710.0)–(1053.0, 1036.0) | (0.4521, 0.6574)–(0.5484, 0.9593) | (0.5003, 0.8083) |
| C23 | Gem Drop | button | (1070.0, 710.0)–(1255.0, 1036.0) | (0.5573, 0.6574)–(0.6536, 0.9593) | (0.6055, 0.8083) |
| C24 | Bubble | button | (1273.0, 710.0)–(1458.0, 1036.0) | (0.6630, 0.6574)–(0.7594, 0.9593) | (0.7112, 0.8083) |
| C25 | Jewel 2 | button | (1475.0, 710.0)–(1660.0, 1036.0) | (0.7682, 0.6574)–(0.8646, 0.9593) | (0.8164, 0.8083) |
| C26 | Start | taskbar item | (0.0, 1040.0)–(48.0, 1080.0) | (0.0000, 0.9630)–(0.0250, 1.0000) | (0.0125, 0.9815) |
| C27 | Type here to search | taskbar item | (48.0, 1041.0)–(391.0, 1077.0) | (0.0250, 0.9639)–(0.2036, 0.9972) | (0.1143, 0.9806) |
| C28 | Taskbar button (no text) | taskbar item | (455.0, 1048.0)–(478.0, 1071.0) | (0.2370, 0.9704)–(0.2490, 0.9917) | (0.2430, 0.9810) |
| C29 | Taskbar button (no text) | taskbar item | (505.0, 1051.0)–(526.0, 1068.0) | (0.2630, 0.9731)–(0.2740, 0.9889) | (0.2685, 0.9810) |
| C30 | Taskbar button (no text) | taskbar item | (553.0, 1049.0)–(575.0, 1071.0) | (0.2880, 0.9713)–(0.2995, 0.9917) | (0.2938, 0.9815) |
| C31 | Taskbar button (no text) | taskbar item | (602.0, 1048.0)–(625.0, 1070.0) | (0.3135, 0.9704)–(0.3255, 0.9907) | (0.3195, 0.9806) |
| C32 | Taskbar button (no text) | taskbar item | (651.0, 1048.0)–(673.0, 1071.0) | (0.3391, 0.9704)–(0.3505, 0.9917) | (0.3448, 0.9810) |
| C33 | Taskbar button (no text) | taskbar item | (700.0, 1049.0)–(723.0, 1069.0) | (0.3646, 0.9713)–(0.3766, 0.9898) | (0.3706, 0.9806) |
| C34 | Taskbar button (no text) | taskbar item | (737.0, 1041.0)–(784.0, 1077.0) | (0.3839, 0.9639)–(0.4083, 0.9972) | (0.3961, 0.9806) |
| C35 | Tray icon (no text) | taskbar item | (1541.0, 1048.0)–(1563.0, 1070.0) | (0.8026, 0.9704)–(0.8141, 0.9907) | (0.8083, 0.9806) |
| C36 | Tray icon (no text) | taskbar item | (1602.0, 1054.0)–(1628.0, 1065.0) | (0.8344, 0.9759)–(0.8479, 0.9861) | (0.8411, 0.9810) |
| C37 | Tray icon (no text) | taskbar item | (1677.0, 1052.0)–(1692.0, 1067.0) | (0.8734, 0.9741)–(0.8812, 0.9880) | (0.8773, 0.9810) |
| C38 | Tray icon (no text) | taskbar item | (1701.0, 1054.0)–(1716.0, 1066.0) | (0.8859, 0.9759)–(0.8938, 0.9870) | (0.8898, 0.9815) |
| C39 | Tray icon (no text) | taskbar item | (1747.0, 1052.0)–(1763.0, 1066.0) | (0.9099, 0.9741)–(0.9182, 0.9870) | (0.9141, 0.9806) |
| C40 | Tray icon (no text) | taskbar item | (1777.0, 1052.0)–(1792.0, 1067.0) | (0.9255, 0.9741)–(0.9333, 0.9880) | (0.9294, 0.9810) |
| C41 | 17:37 / 2023/12/23 | taskbar item | (1805.7, 1045.7)–(1864.0, 1074.4) | (0.9405, 0.9682)–(0.9708, 0.9948) | (0.9557, 0.9815) |
| C42 | Tray icon right of the clock (no text) | taskbar item | (1879.0, 1052.0)–(1894.0, 1067.0) | (0.9786, 0.9741)–(0.9865, 0.9880) | (0.9826, 0.9810) |
| C43 | Show desktop | taskbar item | (1914.0, 1040.0)–(1919.0, 1079.0) | (0.9969, 0.9630)–(0.9995, 0.9991) | (0.9982, 0.9810) |

### Notes on each control

- **C01 Minimize.** Glyph is a horizontal dash at (1800, 16)–(1809, 16).
- **C02 Maximize.** Glyph box (1846, 11)–(1855, 20). Single square, so Maximize rather than Restore.
- **C03 Close.** Glyph box (1892, 11)–(1901, 20).
- **C04 Menu.** OCR label Menu.
- **C05 Sign in.** OCR words Sign and in.
- **C06 Play.** OCR label Play, in the hero panel.
- **C07 Klondike.** Hero game choice. One OCR pass included a trailing parenthesis in the same word box.
- **C08 Spider.** Hero game choice.
- **C09 FreeCell.** Hero game choice. OCR spelling Freecell.
- **C10 Pyramid.** Hero game choice.
- **C11 TriPeaks.** Hero game choice. OCR split the word into rip and eaks; the box is their union.
- **C12 Solitaire Collection.** Row 1 column 1. Two-line label Solitaire / Collection. Label box (284.7, 610.3)–(420.0, 666.4). Normalized label (0.1483, 0.5651)–(0.2188, 0.6170).
- **C13 Classic Solitaire (Klondike).** Row 1 column 2. Lines: Classic Solitaire, then Klondike. Label box (479.0, 607.0)–(631.0, 668.4). Normalized label (0.2495, 0.5620)–(0.3286, 0.6189).
- **C14 Spider.** Row 1 column 3. Label box (711.3, 643.7)–(803.3, 675.7). Normalized label (0.3705, 0.5960)–(0.4184, 0.6256).
- **C15 FreeCell.** Row 1 column 4. Read from a 6× crop of the label strip. Label box (904.5, 642.0)–(1015.8, 670.0). Normalized label (0.4711, 0.5944)–(0.5291, 0.6204).
- **C16 Pyramid.** Row 1 column 5. Read from a 6× crop of the label strip. Label box (1104.3, 642.5)–(1220.6, 674.0). Normalized label (0.5752, 0.5949)–(0.6357, 0.6241).
- **C17 TriPeaks.** Row 1 column 6. Label box (1306.7, 643.7)–(1423.4, 668.4). Normalized label (0.6806, 0.5960)–(0.7414, 0.6189).
- **C18 Solitaire Daily Challenges.** Row 1 column 7. Lines: Solitaire Daily / Challenges. Dataset target box for screenspot_2 is [1477, 450, 184, 230] as x, y, width, height. Label box (1484.7, 612.3)–(1653.0, 672.4). Normalized label (0.7733, 0.5669)–(0.8609, 0.6226).
- **C19 Mahjong.** Row 2 column 1. Label box (293.0, 892.3)–(412.0, 923.0). Normalized label (0.1526, 0.8262)–(0.2146, 0.8546).
- **C20 Jigsaw.** Row 2 column 2. Label box (509.3, 893.3)–(599.3, 923.0). Normalized label (0.2653, 0.8271)–(0.3121, 0.8546).
- **C21 Sudoku.** Row 2 column 3. Label box (705.7, 892.3)–(807.0, 916.3). Normalized label (0.3676, 0.8262)–(0.4203, 0.8484).
- **C22 Ultimate Word Games.** Row 2 column 4. Lines: Ultimate, then Word Games. Label box (878.0, 862.0)–(1043.0, 916.3). Normalized label (0.4573, 0.7981)–(0.5432, 0.8484).
- **C23 Gem Drop.** Row 2 column 5. Label box (1095.0, 894.3)–(1234.3, 923.3). Normalized label (0.5703, 0.8281)–(0.6429, 0.8549).
- **C24 Bubble.** Row 2 column 6. Label box (1319.0, 894.7)–(1413.0, 917.7). Normalized label (0.6870, 0.8284)–(0.7359, 0.8497).
- **C25 Jewel 2.** Row 2 column 7. Label box (1519.0, 892.3)–(1618.3, 916.3). Normalized label (0.7911, 0.8262)–(0.8429, 0.8484).
- **C26 Start.** Slot to the left of the search field, which starts at x=48. Logo fragments sit at about (16, 1052)–(31, 1067).
- **C27 Type here to search.** Search box. OCR text Type here to search at (88, 1054.7)–(215.6, 1066.4). A magnifier glyph inside the box was OCR'd as P at about (59.7, 1051.7).
- **C28 Taskbar button (no text).** bright-pixel mean RGB (35, 126, 146)
- **C29 Taskbar button (no text).** bright-pixel mean RGB (182, 174, 107)
- **C30 Taskbar button (no text).** bright-pixel mean RGB (188, 203, 198)
- **C31 Taskbar button (no text).** bright-pixel mean RGB (60, 148, 196)
- **C32 Taskbar button (no text).** bright-pixel mean RGB (180, 108, 92)
- **C33 Taskbar button (no text).** bright-pixel mean RGB (96, 162, 129)
- **C34 Taskbar button (no text).** bright-pixel mean RGB (93, 113, 123). Taller than the others; the component includes a bar under the icon.
- **C35 Tray icon (no text).** bright-pixel mean RGB (203, 104, 38)
- **C36 Tray icon (no text).** two adjacent fragments merged; bright-pixel mean RGB (205, 208, 213)
- **C37 Tray icon (no text).** bright-pixel mean RGB (196, 143, 153)
- **C38 Tray icon (no text).** bright-pixel mean RGB (255, 255, 255)
- **C39 Tray icon (no text).** bright-pixel mean RGB (196, 199, 201)
- **C40 Tray icon (no text).** bright-pixel mean RGB (228, 228, 228)
- **C41 17:37 / 2023/12/23.** Clock. Time 17:37 above date 2023/12/23.
- **C42 Tray icon right of the clock (no text).** Best guess from position: Action Center. No OCR text. Bright-pixel mean RGB (234, 235, 237).
- **C43 Show desktop.** Best guess. A one-pixel-wide light strip at x=1915 (luminance 125) against a taskbar near luminance 38. The strip is the far-right edge of the taskbar.

Taskbar buttons C28–C34 and tray icons C35–C40 and C42 have no OCR text. The name is the role, not an application name. The RGB note is the mean of pixels in the box with `R+G+B ≥ 150`, so it describes the icon rather than the dark taskbar behind it.

## Visible text that is not a control

These strings are on the screen. They are headings or chrome labels, not separate click targets in this checklist.

| Text | Pixel box | Normalized box |
| --- | --- | --- |
| Solitaire & Casual Games (window title) | (12.0, 11.0)–(144.0, 20.0) | (0.0063, 0.0102)–(0.0750, 0.0185) |
| Solitaire & Casual Games (hero heading) | (93.0, 51.0)–(480.0, 79.0) | (0.0484, 0.0472)–(0.2500, 0.0731) |
| Microsoft (hero wordmark) | (496.0, 168.0)–(705.0, 206.0) | (0.2583, 0.1556)–(0.3672, 0.1907) |
| Solitaire Collection (hero title) | (494.0, 227.0)–(909.0, 265.0) | (0.2573, 0.2102)–(0.4734, 0.2454) |
| Microsoft Casual Games (section heading) | (264.0, 406.0)–(600.0, 430.0) | (0.1375, 0.3759)–(0.3125, 0.3981) |

## Non-interactive background

The client background in the gutters between tiles is blue, about RGB `(15, 74, 153)`. The title bar is white. The empty taskbar is about RGB `(32, 51, 64)`.

One window covers the desktop. The visible UI is that window plus the taskbar.

A component pass found a region at pixel `(0, 862)–(147, 1019)` with no OCR text. It is not listed as a control.

Two OCR passes produced the string `EST 1990` on artwork, near pixel `(318, 293)` in the hero and near `(319, 566)` on the first tile. Treat those pixels as artwork. The compare does not require that string.

## Compare rule

A later compare passes a control when the reported name matches the Name column and the reported point falls inside that control's normalized box. For a tile, the label box in the note is the text; the table box is the tile. C28–C34, C35–C40, and C42 match by box and type, not by an application name. C42 and C43 carry a best guess in the note; the required fact is the box.

