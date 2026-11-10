# Themes

`?theme=<name>` loads `<name>.css` from this folder. All four pages here build on the same
four tokens for everything that isn't plain ink/background — `--amber`, `--amber-soft`,
`--amber-dim`, `--glow` — so one theme file recolours titlebars, badges, dots, glow and (in
`stream-boot.html`/`stream-end.html`) the rain canvas across all four at once, as long as the
same `?theme=` is on every source URL.

Shipped:

| `theme=` | Look |
|---|---|
| *(none)* | the shipped look — amber CRT |
| `matrix` | green phosphor terminal |
| `cyan` | cool cyan, tying back to bugbot's own default accent |
| `aurora` | cyan and magenta together, echoing the hairline on `bars.html`'s own default look |
| `glut` | the terminal with the terminal taken out: same layout and positions, but warm honey/coral/cocoa, a rounded sans instead of monospace, plain rounded cards (no dots, file names or PIDs), no prompts or cursors, no rain, glitches, scanlines or ASCII art; boot loses its fake log, end keeps the recap without timestamps/levels. Restyles and hides elements beyond colour; pairs with `bars.html?theme=sunset` |
| `kaminfeuer` | `glut` mixed with the cozy skin's kamin look: imports glut.css, adds the running ember/gold rim around every card, a flickering flame before each heading, serif headings, and rising embers plus a firelight glow in the start/end scenes. Pairs with `bars.html?theme=sunset` |
| `polarlicht` | exactly `kaminfeuer` (imports it), only recoloured to the aurora palette: cyan and magenta on night blue - rim cyan<->magenta, cold cyan flame, cyan sparks, an aurora glow instead of firelight. Pairs with `bars.html?theme=polarlicht` |

The same files exist under
[`features/overlay/client/terminal/themes/`](../../../../features/overlay/client/terminal/themes/)
for the overlay/chat/tubbi trio — kept as separate copies rather than one shared location, since
nothing in this repo imports across a feature/platform boundary (see `docs/architecture.md`) and
a `client/` folder belongs to its counterpart. Keeping both in sync by hand is the price; picking
a `?theme=` and setting it on every source in the scene is what keeps them in sync in practice.

Precedence, low to high: the shipped `:root` in each page → `?theme=` (this folder) →
`?t.<token>=`, where present (always wins — inline style beats any stylesheet regardless of
load order).

Adding one: copy an existing file, keep the token names, change the values.
