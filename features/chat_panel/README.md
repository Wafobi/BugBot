# Chat panel — parameter reference

Every knob on `chat.html`, in one place. What the feature *is* and how to wire it up lives in
[docs/chat_panel.md](../../docs/chat_panel.md); this file is the lookup table.

```
file:///path/to/bugbot/features/chat_panel/client/chat.html?token=…&source=twitch&max=6
```

---

## URL parameters

| Parameter | Default | Meaning |
|---|---|---|
| `token=` | — | the `CHAT_PANEL_TOKEN` from the bot's `.env`. Without it the page renders but stays empty |
| `host=` | `127.0.0.1` | where the SSH tunnel ends on **this** machine, not the server |
| `port=` | `4458` | must match `CHAT_PANEL_PORT` |
| `source=` | — | which platform(s) this instance shows, comma-separated: `source=twitch` or `source=twitch,discord`. Empty = everything the bot sends. Filtered here, client-side — several instances can hang off one token and each show something different, see [docs/chat_panel.md](../../docs/chat_panel.md#what-it-sends) |
| `max=` | `12` | how many lines stay visible; the oldest scrolls off as new ones arrive |
| `badge=0` | shown | hide the platform tag in front of the name |
| `accent=` | `#5FEAF5` | colour for mod/broadcaster names, URL-encoded: `%23FFE100` for `#FFE100` |
| `opacity=` | `.55` | opacity of the panel's background fill, `0`–`1`. Text and the frame stay fully readable regardless — only the fill fades |
| `t.NAME=` | — | override any single design token, e.g. `t.msg-size=30px` |
| `link=1` | off | show the connection lamp, top right. Off by default so a bot restart mid-stream does not put a red dot on screen |
| `demo=1` | off | sample messages without a connection, for previewing |

`preview.html` next to this file takes the same parameters and adds a stage with a stand-in
gameplay background, plus a toolbar for the ones worth turning without editing a URL by hand
(width, height, rows, source, badge).

---

## Design tokens

All of these are settable as `?t.<name>=<value>`. The defaults below are what `:root` in
`chat.html` ships with.

### Geometry & type

| Token | Default | |
|---|---|---|
| `--font` | `"Roboto", "DejaVu Sans", system-ui, sans-serif` | |
| `--msg-size` | `26px` | |
| `--name-weight` | `700` | |
| `--gap` | `10px` | space between message lines |
| `--pad` | `16px` | padding inside the box |
| `--text-shadow` | `0 1px 3px rgba(0,0,0,.9), 0 0 12px rgba(0,0,0,.5)` | legibility over bright game footage |

### Colour

| Token | Default | |
|---|---|---|
| `--accent` | `#5FEAF5` | mod/broadcaster names, and one end of the border gradient; `accent=` writes this |
| `--sub` | `#F22A80` | subscriber names, and the border gradient's other end |
| `--ink` | `#EAF7FF` | everyone else |
| `--dim` | `rgba(234,247,255,.55)` | the platform badge |
| `--dot-off` / `--dot-ok` | `#2A2C50` / `#34C759` | connection lamp, see `link=1` |

### The box

One framed panel holds every line — the whole page fills the OBS source, and the box sits inset
from its edges so the frame is visible on all sides.

| Token | Default | |
|---|---|---|
| `--margin` | `10px` | gap between the box and the source's edges |
| `--radius` | `12px` | corner radius, box and border alike |
| `--border-w` | `2px` | |
| `--border` | `linear-gradient(165deg, var(--accent), var(--sub))` | the frame — a gradient, not a flat colour, so it takes two stops rather than one to change |
| `--bg-alpha` | `.55` | the panel fill's opacity, `0`–`1`; `opacity=` writes this |
| `--panel-bg` | `rgba(9,10,26,var(--bg-alpha))` | the fill itself — override `--bg-alpha` rather than this directly, unless the colour should change too |

---

## Precedence

Two layers, the second beating the first:

1. the tokens in `:root` in `chat.html` — the appearance as shipped
2. `?accent=` and `?t.<token>=` — inline style on the root element, so they win over any rule
   regardless of load order

Same as the overlay: there is no theme mechanism, and none is planned — with one appearance, the
tokens *are* the theme.
