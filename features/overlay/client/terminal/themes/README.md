# Themes

`?theme=<name>` loads `<name>.css` from this folder. Both pages in `terminal/` (`chat.html`,
`tubbi.html`) build on the same four tokens for everything that isn't plain ink/background —
`--amber`, `--amber-soft`, `--amber-dim`, `--glow` — so one theme file recolours titlebars,
badges, the connection dot and the glow across both at once, as long as the same `?theme=` is on
both source URLs.

Shipped:

| `theme=` | Look |
|---|---|
| *(none)* | the shipped look — amber CRT |
| `matrix` | green phosphor terminal |
| `cyan` | cool cyan, tying back to bugbot's own default accent (`bars.html`'s `--accent`) |
| `aurora` | cyan and magenta together, echoing the hairline on `bars.html`'s own default look |

Precedence, low to high: the shipped `:root` in each page → `?theme=` (this folder) →
`?t.<token>=` (always wins — inline style beats any stylesheet regardless of load order).

Adding one: copy an existing file, keep the token names, change the values.
