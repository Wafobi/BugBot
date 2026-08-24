# Themes

`?theme=<name>` loads `<name>.css` from this folder. All three pages here build on the same
four tokens for everything that isn't plain ink/background — `--amber`, `--amber-soft`,
`--amber-dim`, `--glow` — so one theme file recolours titlebars, badges, dots, glow and (in
`stream-boot.html`) the rain canvas across all three at once, as long as the same `?theme=` is
on all three source URLs.

Shipped:

| `theme=` | Look |
|---|---|
| *(none)* | the shipped look — amber CRT |
| `matrix` | green phosphor terminal |
| `cyan` | cool cyan, tying back to bugbot's own default accent |

The same two files exist under
[`features/overlay/client/terminal/themes/`](../../../../features/overlay/client/terminal/themes/)
for the overlay/chat/tubbi trio — kept as separate copies rather than one shared location, since
nothing in this repo imports across a feature/platform boundary (see `docs/architecture.md`) and
a `client/` folder belongs to its counterpart. Keeping both in sync by hand is the price; picking
a `?theme=` and setting it on every source in the scene is what keeps them in sync in practice.

Precedence, low to high: the shipped `:root` in each page → `?theme=` (this folder) →
`?t.<token>=`, where present (always wins — inline style beats any stylesheet regardless of
load order).

Adding one: copy an existing file, keep the token names, change the values.
