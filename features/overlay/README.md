# Overlay — parameter reference

Every knob on `bars.html`, in one place. What the feature *is* and how to wire it up lives in
[docs/overlay.md](../../docs/overlay.md); this file is the lookup table.

```
file:///path/to/bugbot/features/overlay/client/bars.html?token=…&bar=110
```

---

## URL parameters

| Parameter | Default | Meaning |
|---|---|---|
| `token=` | — | the `OVERLAY_TOKEN` from the bot's `.env`. Without it the page renders but stays empty |
| `host=` | `127.0.0.1` | where the SSH tunnel ends on **this** machine, not the server |
| `port=` | `4457` | must match `OVERLAY_PORT` |
| `off=` | — | hide components, comma-separated: `off=clock,deaths` |
| `only=` | — | the inverse — keep only these: `only=game,viewers` |
| `bar=` | `184` | bar height in px. **Must match the letterbox** — see below |
| `cam=` | `300` | camera ring diameter. `cam=0` removes ring, cut-out and reserved space |
| `camx=` | `230` | ring centre measured from the left |
| `accent=` | `#5FEAF5` | accent colour, URL-encoded: `%23FFE100` for `#FFE100` |
| `t.NAME=` | — | override any single design token, e.g. `t.bar-line-h=3px` |
| `link=1` | off | show the connection lamp, top right. Off by default so a bot restart mid-stream does not put a red dot on screen |
| `demo=1` | off | sample data without a connection, for previewing |

`preview.html` next to this file takes the same parameters and adds a scaled stage, a stand-in
gameplay image and a stand-in camera — the arrangement the page later sits in inside OBS.

### Matching `bar=` to the letterbox

The bar height must equal the black strip left by the game capture, or the hairline floats away
from the picture edge. For a 3440×1440 source on a 2560×1440 canvas:

| Crop filter per side | Rendered | `bar=` |
|---|---|---|
| 0 px | 2560×1072 | `184` |
| 100 px | 2560×1138 | `151` |
| 150 px | 2560×1174 | `133` |
| 210 px | 2560×1221 | `110` |
| 250 px | 2560×1254 | `93` |

`bar = (1440 − 1440 × 2560 / (3440 − 2 × crop)) / 2`

---

## Components

Names for `off=` and `only=`:

| | |
|---|---|
| `top` `bottom` | the two bars as a whole |
| `uptime` | live dot and elapsed time |
| `game` | game, falling back to the stream title |
| `viewers` | viewer count |
| `clock` | wall clock |
| `deaths` | death counter |
| `ticker` | the rotating list of commands open to normal viewers |
| `last` | last follower / sub / raid |
| `cam` | the camera ring |

A bar left with nothing visible hides itself. Naming a bar anyway means "I want this one, empty
as well" — that is how `?only=top,bottom` gives you the bars as a bare frame.

---

## Design tokens

All of these are settable as `?t.<name>=<value>`. The defaults below are what `:root` in
`bars.html` ships with — the appearance taken from the channel artwork.

### Geometry

| Token | Default | |
|---|---|---|
| `--bar` | `184px` | bar height; `bar=` writes this |
| `--pad` | `40px` | horizontal padding inside the bars |
| `--gap` | `32px` | gap between blocks |
| `--bottom-pad` | `calc(var(--camx) + var(--cam) / 2 + 48px)` | left space held free for the ring |

### Type

| Token | Default | |
|---|---|---|
| `--font` | `"Roboto", "DejaVu Sans", system-ui, sans-serif` | |
| `--value-size` | `46px` | the large numbers |
| `--value-small` | `34px` | uptime, clock, last follower |
| `--value-weight` | `700` | |
| `--label-size` | `22px` | the small caption above/beside a value |
| `--label-weight` | `600` | |
| `--label-spacing` | `.2em` | letter spacing on labels |
| `--label-transform` | `uppercase` | set `none` to keep labels as written |
| `--ticker-size` | `32px` | |
| `--text-shadow` | `0 0 16px rgba(95, 234, 245, .16)` | keep it weak, or the text smears |

### Colour

| Token | Default | |
|---|---|---|
| `--accent` | `#5FEAF5` | numbers, hairline, ring |
| `--ink` | `#EAF7FF` | primary text |
| `--dim` | `rgba(234, 247, 255, .5)` | labels and ticker |
| `--dot-live` | `#D6453A` | live dot |
| `--dot-glow` | `rgba(214, 69, 58, .85)` | its halo; `transparent` switches the glow off |
| `--dot-off` | `#2A2C50` | dot while offline |

### Bars

| Token | Default | |
|---|---|---|
| `--bar-bg-top` | `linear-gradient(180deg, rgba(7, 8, 22, .97), rgba(10, 10, 30, .82))` | first colour is the outer edge, second the one facing the game |
| `--bar-bg-bottom` | `linear-gradient(0deg, rgba(7, 8, 22, .97), rgba(10, 10, 30, .82))` | same, mirrored — only the angle differs |
| `--fx` | `radial-gradient(rgba(95, 234, 245, .11) 1px, transparent 1.6px) 0 0 / 7px 7px` | pattern over the bars only, e.g. a halftone dot grid |
| `--fx-opacity` | `1` | |
| `--bar-mask` | see `:root` — 13 cosine-spaced stops, 240 px period | the hole the camera shows through; `cam=0` sets it to `none` |

To darken the bars, both `--bar-bg-*` need setting — one is not enough:

```
&t.bar-bg-top=linear-gradient(180deg,rgba(4,5,14,1),rgba(6,6,18,1))
&t.bar-bg-bottom=linear-gradient(0deg,rgba(4,5,14,1),rgba(6,6,18,1))
```

### The hairline

| Token | Default | |
|---|---|---|
| `--bar-line-h` | `2px` | an odd value survives Twitch's downscale better than an even one |
| `--bar-line` | see `:root` — 13 cosine-spaced stops, 240 px period | |
| `--bar-line-opacity` | `.9` | |
| `--bar-line-tile` | `240px` | for the running line: **exactly** the repeating gradient's tile width |
| `--bar-line-anim` | `bar-line-run 8s linear infinite` | change the seconds for a different speed, nothing else |
| `--bar-line-mask` | `linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent)` | fades the ends; a tile cannot fray out on its own |
| `--bar-line-dir-bottom` | `reverse` | the lower bar runs against the upper one; `normal` makes them parallel |

Animating it needs `--bar-line` to be a `repeating-linear-gradient` with **pixel** stops, and
`--bar-line-tile` to equal that period exactly — the animation shifts by one tile, and only then
is the wrap invisible. Use enough colour stops: three make a triangle wave whose slope flips at
every peak, and that kink travels across the picture as a hard edge.

### Camera ring

| Token | Default | |
|---|---|---|
| `--cam` | `300px` | diameter; `cam=` writes this |
| `--camx` | `230px` | centre from the left; `camx=` writes this |
| `--cam-cut` | `calc(var(--cam) / 2 + 10px)` | radius of the hole in the lower bar |
| `--cam-ring` | `var(--accent)` | |
| `--cam-ring-w` | `3px` | |
| `--cam-halo` | see `:root` — 13 cosine-spaced stops, 240 px period | separates the ring from the picture |
| `--cam-outer` | `rgba(242, 42, 128, .6)` | thin second ring; `transparent` removes it |
| `--cam-outer-w` | `2px` | |
| `--cam-gap` | `20px` | distance of that second ring |

The ring is only a frame — its inside is transparent. The camera stays an OBS source placed
*under* this page and cut round with Advanced Masks. Right-click the browser source → *Interact*
prints the ring's centre and diameter to the console.

---

## Precedence, and two things that bite

Two layers, the second beating the first:

1. the tokens in `:root` in `bars.html` — the appearance as shipped
2. `?accent=` and `?t.<token>=` — inline style on the root element, so they win over any rule
   regardless of load order

There is no theme mechanism: with one appearance, the tokens *are* the theme.

**No spaces in parameter values.** `rgba(4,5,14,1)` is valid CSS and survives a URL;
`rgba(4, 5, 14, 1)` tears the query apart. Everything here can be written without spaces.

**Judge fine lines at 100 %.** A 2 px line scaled to 61 % becomes 1.21 px and is rounded to 1 or
2 depending on where it lands — two identical lines then look different. `preview.html` has a
zoom control for exactly this.

---

# Chat — parameter reference

Every knob on `chat.html`, the chat mirrored alongside the bars — same listener, same
`OVERLAY_TOKEN`, but its own browser source, so it can be shown or hidden live with the eye icon
in OBS's Sources dock without touching a URL. What the feature *is* lives in
[docs/overlay.md](../../docs/overlay.md#chat); this is the lookup table.

```
file:///path/to/bugbot/features/overlay/client/chat.html?token=…&source=twitch&max=6
```

## URL parameters

| Parameter | Default | Meaning |
|---|---|---|
| `token=` | — | the `OVERLAY_TOKEN` from the bot's `.env` — the same one bars.html uses. Without it the page renders but stays empty |
| `host=` | `127.0.0.1` | where the SSH tunnel ends on **this** machine, not the server |
| `port=` | `4457` | must match `OVERLAY_PORT` |
| `source=` | — | which platform(s) this instance shows, comma-separated: `source=twitch` or `source=twitch,discord`. Empty = everything the bot sends. Filtered here, client-side — several instances can hang off one token and each show something different, see [docs/overlay.md](../../docs/overlay.md#chat) |
| `max=` | `12` | how many lines stay visible; the oldest scrolls off as new ones arrive |
| `badge=0` | shown | hide the platform tag in front of the name |
| `accent=` | `#5FEAF5` | colour for mod/broadcaster names, URL-encoded: `%23FFE100` for `#FFE100` |
| `t.NAME=` | — | override any single design token, e.g. `t.msg-size=30px` |
| `link=1` | off | show the connection lamp, top right. Off by default so a bot restart mid-stream does not put a red dot on screen |
| `demo=1` | off | sample messages without a connection, for previewing |

`chat_preview.html` next to this file takes the same parameters and adds a stage with a stand-in
gameplay background, plus a toolbar for the ones worth turning without editing a URL by hand
(width, height, rows, source, badge).

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
| `--accent` | `#5FEAF5` | mod/broadcaster names; `accent=` writes this |
| `--sub` | `#F22A80` | subscriber names |
| `--ink` | `#EAF7FF` | everyone else |
| `--dim` | `rgba(234,247,255,.55)` | the platform badge |
| `--dot-off` / `--dot-ok` | `#2A2C50` / `#34C759` | connection lamp, see `link=1` |

### Layout

No box, no border — just the lines themselves over whatever OBS composites behind this source.

| Token | Default | |
|---|---|---|
| `--margin` | `10px` | gap between the text and the source's edges |
