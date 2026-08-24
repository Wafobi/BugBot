# Themes

`?theme=<name>` loads `<name>.css` from this folder — a second `:root` block that recolours a
handful of tokens (accent, ink, the bar/message gradients, the connection dot, the camera ring)
without touching geometry. `bars.html` and `chat.html` read the same file for the same
`?theme=`, so one parameter restyles both browser sources together.

Shipped:

| `theme=` | Look |
|---|---|
| *(none)* | the shipped look — indigo/carmine fog, cyan fill, magenta outline |
| `sunset` | warm amber/coral, no cool colours left |
| `mono` | grayscale, minimal, no pattern |

A theme only needs to set the tokens it wants to change — everything else falls back to the
`:root` block in `bars.html`/`chat.html`. Precedence, low to high:

1. the shipped `:root` in `bars.html`/`chat.html`
2. `?theme=` — this folder
3. `?t.<token>=` / `?accent=` — always wins, inline style beats any stylesheet regardless of
   load order (see the precedence note in [../../README.md](../../README.md))

Adding one: copy an existing file, keep the token names, change the values. No code change and
no registration anywhere — it is just a file this folder now also contains.
