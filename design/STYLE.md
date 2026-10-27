# Dispatch Release style guide

Verity should feel like an airline operational flight plan on a dark operations screen: calm, precise and easy to scan. Use thin rule lines, grid-aligned data, clear labels, monospace numbers and rubber-stamp decisions. Avoid gradients, stock icons, emoji and cartoons.

## Colours

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| Background | #14161A | #FAF8F3 | Page background |
| Surface | #1E2126 | #FFFFFF | Cards and grouped information |
| Text | #F2EFE8 | #15171A | Main text and values |
| Muted | #A9A498 | #5A564D | Secondary labels and supporting text |
| Amber | #F5A524 | #8A5A00 | Key accents, actions and simulated-data badge |
| GO | #3DD68C | #11743F | GO decision |
| NO-GO | #FF6B6B | #B42318 | NO-GO decision |
| Unknown | #9DB4FF | #2D4FB3 | INSUFFICIENT EVIDENCE decision |

Use dark by default. Set `data-theme="light"` on the `html` element for light mode. Use the rule token for thin dividers and card borders.

## Type and components

Use IBM Plex Sans for ordinary text and IBM Plex Mono for all numbers, data values and section headers. If Google Fonts cannot load, the browser uses a similar fallback font. Section headers are uppercase and sit above a thin rule. Cards group related information. Show the amber SIMULATED DATA badge whenever the data is simulated.

Decision stamps are tilted slightly: GO has a solid border, NO-GO has a double border, and INSUFFICIENT EVIDENCE has a dashed border. Use an empty element with the appropriate stamp class; its label is supplied by the CSS. Include the same decision in accessible text, for example with `aria-label="GO"`. Use an empty element with `class="badge-simulated"` and `aria-label="Simulated data"` for the badge.

Colour is never the only signal: always include words and, where useful, different border styles. Show confidence exactly as provided by the data, with no decimals; do not invent, round or change a value. If the supplied confidence contains decimals, ask for a correctly formatted value rather than silently changing it. Show only values supplied by the data.
