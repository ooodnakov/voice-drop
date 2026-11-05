---
name: VoiceDrop
description: A portable Telegram sender shaped like one printed consignment label.
colors:
  bench: "#1d2021"
  label-paper: "#32302f"
  label-tint: "#3c3836"
  ink: "#ebdbb2"
  ink-muted: "#b1a190"
  rule-hairline: "#665c54"
  control-line: "#8a7d70"
  signal: "#fe8019"
  success: "#b8bb26"
  danger: "#fd6a55"
  warning: "#fabd2f"
  sheet-edge: "#4a4441"
  scrim: "rgba(20, 20, 19, 0.9)"
  scrim-shadow: "rgba(0, 0, 0, 0.55)"
typography:
  display-xl:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.15
  display-lg:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "21px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.005em"
  display:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.2
  display-sm:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.2
  title-sm:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    letterSpacing: "0.1em"
  heading:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
  wordmark:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 700
    letterSpacing: "0.18em"
  compact-title:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
  data-sm:
    fontFamily: "Cascadia Mono, Consolas, ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 400
  title:
    fontFamily: "Bahnschrift, DIN Alternate, Segoe UI, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.01em"
  body:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Cascadia Mono, Consolas, ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 400
    letterSpacing: "0.1em"
  data:
    fontFamily: "Cascadia Mono, Consolas, ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  field: "2px"
  sheet: "3px"
spacing:
  hair: "4px"
  tight: "6px"
  base: "8px"
  label: "10px"
  gutter: "12px"
  roomy: "15px"
components:
  select-recipient:
    backgroundColor: "{colors.label-paper}"
    textColor: "{colors.ink}"
    typography: "{typography.display}"
    rounded: "{rounded.field}"
    padding: "5px 28px 5px 10px"
    height: "38px"
  select-sender:
    backgroundColor: "{colors.label-paper}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.field}"
    padding: "3px 20px 3px 6px"
    height: "24px"
  input-field:
    backgroundColor: "{colors.label-paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "5px 7px"
    height: "26px"
  button-action:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.label-paper}"
    typography: "{typography.label}"
    rounded: "{rounded.field}"
    padding: "6px 13px"
    height: "27px"
  chip:
    backgroundColor: "{colors.label-paper}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.field}"
    size: "23px"
  theme-chip:
    backgroundColor: "{colors.label-paper}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.field}"
    padding: "3px 8px"
    height: "21px"
---

## Overview

VoiceDrop is a working window that behaves like one printed consignment label:
destination at the top, a fill field in the body, and a perforated tracking
ledger at the bottom edge. The world is thermal-label printing on a matte bench —
paper stock, thermal ink, hairline rules, crop marks and a single courier signal.
Expression never competes with the task: every control is a standard select,
checkbox, button or input, dressed in the label's paper and ink.

The signature moment is printing: a drop stamps a tracking line into the ledger
and the row arrives with a short clip-path sweep.

## Colors

Four stocks ship as themes; `gruvbox-dark` is the default, `system` follows the
OS light/dark preference and resolves to gruvbox.

| role | gruvbox dark | gruvbox light | paper | slate |
|---|---|---|---|---|
| bench | #1d2021 | #3c3836 | #1b1c1f | #242933 |
| paper | #32302f | #fbf1c7 | #f5f3ed | #3b4252 |
| tint | #3c3836 | #f2e5bc | #efece2 | #434c5e |
| ink | #ebdbb2 | #3c3836 | #141516 | #eceff4 |
| muted ink | #b1a190 | #6f6259 | #6b665c | #b3bdd0 |
| signal | #fe8019 | #af3a03 | #d8412f | #88c0d0 |
| success | #b8bb26 | #6d6906 | #1f6b4a | #a3be8c |
| danger | #fd6a55 | #9d0006 | #b3261e | #ef9aa0 |

Colour does real work: signal marks the actionable and the drag-over state,
success/danger live only in ledger text (never colour alone — the text says
`voice · #id` or the failure reason). All body text passes 4.5:1 on both paper
and tint; control borders meet 3:1 against paper.

## Typography

Three voices, each with a job. Condensed label-grotesque (Bahnschrift) carries
the wordmark and the recipient; monospace (Cascadia Mono/Consolas) carries only
real codes — timestamps, ids, api fields, paths, micro labels; Segoe UI carries
body copy and inputs. Nothing else appears at display scale: the recipient line
is the largest thing on the sheet.

Micro labels are 9px mono, uppercase, tracked 0.14em, muted. Data rows are 11px
mono at 1.5 line height. No font is loaded from the network.

## Layout

One sheet, 10px of bench around it, max-width 640px. Vertical order: title strip
(drag handle + window controls), sender row, recipient block, fill field,
perforated tracking ledger, footer action.

Height is shared, not fixed: the fill field is `flex: 2` capped at 340px, the
ledger takes the remainder and scrolls. Below 400px window height the layout
switches to a compact mode — tighter paddings, the file-type line hidden, ledger
capped at a single readable line and its scrollbar removed. Above 700px width the
sheet widens to 640px and type/paddings scale once (recipient 24px, rows 12px).
Minimum window 340×260; nothing clips at any size.

## Elevation & Depth

One plane, one shadow: the sheet floats on the bench with
`0 12px 28px rgba(0,0,0,.42)` and a 1px inner ink hairline for the printed edge.
Overlays (sign in, API & runtime) are paper cards on a solid 88% bench scrim, with
a deeper offset shadow. No blur, no glass, no gradient.

## Shapes

2px on fields, chips and buttons; 3px on the sheet and cards. Rules are 1px at
12–13% ink; the ledger's top edge is a 5px-on/5px-off perforation. The fill field
carries four corner crop marks. No pills, no squircles, no coloured side bars.

## Components

- **Chip** (23×23): account add/remove and recipient refresh; drawn 12px SVG
  strokes, hover turns the border and glyph to signal.
- **Select**: native element, `appearance: none`, custom 4px caret; the recipient
  select is 21–24px condensed and owns the width, the sender select is 11.5px.
- **Row** (tracking): mono, hairline separators, name left / state right, 0.28s
  clip-path stamp on arrival, `pending` shows a sweeping 14px line.
- **Action** (Sign in / Save): ink-filled rectangle, paper text, mono 9.5px
  tracked uppercase; hover shifts to signal.
- **Theme chip**: mono 9px tracked uppercase, active inverts to ink on paper.
- **Window controls**: 30×22 hit areas, drawn 1px minus and 1px crossed lines;
  close tints danger on hover.

## Do's and Don'ts

- Do keep the recipient line the loudest element; it is the only thing a
  mis-send depends on.
- Do keep monospace for codes and measurements only — never for prose.
- Do let the ledger be blank: the empty state reads `no entries yet`.
- Don't add gradients, glass, pills, or emoji/Unicode glyphs as icons.
- Don't introduce a second accent; success and danger are state, not brand.
- Don't let the label world dictate navigation — selects and buttons stay
  native and predictable.
