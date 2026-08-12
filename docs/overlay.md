# Overlay

The browser sources in the picture. Same reversed direction as [OBS](obs.md), and for the same
reason: the overlay runs in OBS on the streamer's PC, the bot runs on a server. So the overlay
*dials the bot*, and the bot pushes.

```
OBS machine                                     server
┌──────────────────────┐                  ┌──────────────────────┐
│ Browser source       │                  │ BugBot (container)   │
│   bars.html          │                  │  features/overlay    │
│        │             │                  │  listens :4457       │
│        └── ws ──► :4457 ══ SSH -L ══════►  token check         │
└──────────────────────┘  encrypted tunnel└──────────────────────┘
```

The payoff of pushing rather than polling: a follower is in the picture the moment they arrive,
not at the next poll. And the bot decides when something changed — a viewer sample that repeats
the previous number sends nothing, so the page does not repaint without reason.

```
features/overlay/
  feature.py      the state, the topics it comes from, the death counter commands
  server.py       the listener — token check, connections, broadcast
  store.py        overlay_counters, so the death count survives a restart
  config.py       token/port/bind from .env
  overlay.json    which event fills which field, and every line the bot says
  bars.html       the page itself — belongs on the OBS machine, like obs_bridge.py
  preview.html    the same page in a scaled frame, for looking at it in a browser
  README.md       every parameter and every design token, as a lookup table
```

---

## What it sends

Two frame types, both JSON:

| `type` | when | `data` |
|---|---|---|
| `state` | once, on connect | every field below, plus `commands` |
| `patch` | whenever a field changes | only the changed fields |

There is deliberately no alert frame. A follow updates `last_follower` and nothing flashes —
the on-screen alerts are Twitch's own alertbox as a separate browser source, and two places
announcing the same thing would be one too many.

The fields: `live`, `started_at` (unix seconds — the page computes uptime itself), `title`,
`game`, `viewers`, `last_follower`, `last_sub`, `last_raid`, `deaths`. `commands` carries the
commands a normal viewer may use; `mod_only` ones are dropped before sending, because what a
viewer cannot use they need not read.

Everything comes from bus topics — `STREAM_START`, `STREAM_SEGMENT`, `VIEWERS`,
`PLATFORM_EVENT`, `STREAM_END`. The feature asks nobody anything and names no platform: a follow
arrives as `PLATFORM_EVENT` with `event_type="follow"`, and a second service reporting the same
would work unchanged. Which event type fills which field is a line in `overlay.json`
(`event_slots`), not a line of code.

---

## Setup

**1. A token, and not the OBS one.**

```bash
openssl rand -hex 32        # → OVERLAY_TOKEN in .env
```

It ends up in the browser source URL and therefore in plaintext in the scene collection. That is
not a shortcut — the browser's WebSocket API cannot set request headers, so the query string is
the only way in. Which is exactly why it must not be `OBS_BRIDGE_TOKEN`: that one grants remote
control of a running broadcast, this one grants read access to a few numbers.

Without `OVERLAY_TOKEN` the bot opens no port at all. The feature still loads and its chat
commands still work — that is how you run the death counter without an overlay.

**2. Publish the port** — already in `bugbot.container`:

```
PublishPort=127.0.0.1:4457:4457
```

The `127.0.0.1:` is the first lock. The bot inside the container still binds `0.0.0.0`
(`OVERLAY_BIND`), because Podman's forwarding does not reach the container's loopback — so the
restriction belongs on the host side. The token is the second lock, and the one still standing if
that prefix is ever forgotten.

**3. The tunnel**, from the OBS machine — the same one that already carries the OBS relay:

```bash
ssh -N -L 4456:127.0.0.1:4456 -L 4457:127.0.0.1:4457 user@your-server
```

**4. The browser source.** Width `2560`, height `1440`, position `(0,0)`, and as URL:

```
file:///path/to/bugbot/features/overlay/bars.html?token=YOUR_OVERLAY_TOKEN
```

Put it *above* the camera source, so the ring frames the camera rather than the camera covering
the ring.

To see the layout without a bot running, open **`preview.html`** next to it in an ordinary
browser. `bars.html` on its own is a poor preview — it is a fixed 2560×1440 with a transparent
background, so a normal window shows you its top-left corner on white. `preview.html` scales it
to the window and puts a stand-in gameplay image and a stand-in camera behind it, which is the
arrangement it will later sit in inside OBS. Bar height, camera and zoom are switches along the
top; anything else goes in the free-text field, and every parameter is passed straight through:

```
preview.html?off=clock&bar=110
```

---

## Parameters

All optional, appended to the URL:

| | |
|---|---|
| `token=` | the `OVERLAY_TOKEN`. Without it, no connection |
| `host=` / `port=` | where the tunnel ends on *this* machine (default `127.0.0.1:4457`) |
| `off=` | hide components, comma-separated: `off=clock,deaths` |
| `only=` | the inverse — keep only these: `only=game,viewers` |
| `bar=` | bar height in pixels — `184` without the crop filter, `110` with it (default `184`) |
| `cam=` | camera ring diameter (default `300`), `cam=0` hides it |
| `camx=` | ring centre from the left (default `230`) |
| `accent=` | accent colour, URL-encoded: `%23FFE100` |
| `t.NAME=` | override any single design token, e.g. `t.bar-line-h=4px` |
| `link=1` | show the connection lamp (off by default — a red dot in the corner is the last thing you want when the bot restarts mid-stream) |
| `demo=1` | sample data, no connection |

---

## Look

The whole appearance lives in the design tokens in `:root` at the top of `bars.html` — indigo
nebula, electric cyan, magenta contour, halftone dots on the bars, after the channel artwork.
Every one of them can be overridden from outside:

```
?t.accent=%2356B893
```

That lands as an inline style on the root element and therefore beats any rule in the stylesheet,
whatever the load order. The rules below `:root` are untouched by it, which is why the look can be
changed but the layout can never be taken apart. The full token list is in
[features/overlay/README.md](../features/overlay/README.md).

There is deliberately **no theme mechanism**. With exactly one appearance, a file next to the page
plus a `?theme=` parameter was a detour: the tokens are the theme. Should several switchable looks
ever be wanted again, that is a `<link>` pulling in a file with one `:root` block — about ten
lines.

### The running line

The hairline moves. Five tokens carry it:

| | |
|---|---|
| `--bar-line` | must be a `repeating-linear-gradient` with **pixel** stops |
| `--bar-line-tile` | exactly that gradient's tile width |
| `--bar-line-anim` | e.g. `bar-line-run 8s linear infinite` |
| `--bar-line-mask` | fades the ends — a tile cannot fray out on its own |
| `--bar-line-dir-bottom` | `reverse` runs the lower bar the other way |

Tile width and `--bar-line-tile` must agree: the animation shifts by exactly one tile, and only
then is the jump back invisible. What moves is the background position of a 2 px strip — no
layout, no reflow.

Worth knowing for a browser source: a static page only repaints when something changes, an
animated one repaints continuously. 30 fps on the source is plenty.

### Components

Every component carries a `data-part` and can be switched off:

| | |
|---|---|
| `top` `bottom` | the two bars as a whole |
| `uptime` `game` `viewers` `clock` | the top bar's four blocks (`uptime` includes the live dot) |
| `deaths` `ticker` `last` | the lower bar's three |
| `cam` | the camera ring |

`off=` removes the listed ones, `only=` keeps the listed ones and removes the rest. The two bars
are exempt from `only` — `?only=clock` means the clock *in* its bar, not a clock without one.

A bar left with nothing visible hides itself, so `top` and `bottom` rarely need naming:
`?only=deaths,ticker,last,cam` already leaves the upper bar gone.

Naming a bar anyway means "I want this one, empty as well", and exempts it from that clean-up.
That is how you get the bars as a bare frame and nothing else:

```
?only=top,bottom
```

Two things adjust themselves so nothing is left dangling: the lower bar switches to
`space-between` when the ticker goes (otherwise the death counter and the last follower would
both stick to the left), and turning off `cam` also drops the cut-out and the space held free for
it — see below.

### The camera ring, and turning it off

The lower bar is masked with a circular cut-out where the camera sits (`--bar-mask`,
`--cam-cut`). Without it the bar's semi-transparent background would darken the lower half of the
camera, which is an OBS source *below* this page. The hairline gets a notch from the same mask,
which makes the ring look set into the bar rather than stuck onto it.

`?cam=0` takes away all three things that exist only for the ring — the ring itself, the cut-out
(otherwise a circle of gameplay would sit in the middle of a dark bar), and the space held free
on the left (`--bottom-pad`, otherwise an empty 428 px gap would remain). The bar then runs
through unbroken and the death counter sits at the normal margin.

Streaming with and without a camera is therefore two URLs on two browser sources, or one source
whose URL you edit — nothing else in the scene has to move.

### The camera ring

The ring is only a frame — its inside is transparent. The camera itself stays an OBS source,
placed *under* the overlay and cut round with Advanced Masks. It sits straddling the edge of the
lower bar, half in the game image and half in the bar.

Right-click the browser source → *Interact* opens a console; the page prints the ring's centre
and diameter there, so the camera can be placed on it without guessing.

---

## The death counter

It lives here because the overlay is where you see it, and because it is counted by a chat
command rather than a file on the OBS machine — which a bot on a server could not reach anyway.

| | | |
|---|---|---|
| `!tode` | everyone | shows the count |
| `!tod` | mods | one more |
| `!todsetzen <n>` | mods | set it |

Names, like everywhere, are renameable in `overlay.json` under `command_names`. The value lives
in `overlay_counters` and survives restarts; the table is kept general so the next counter
(wins, crashes, coffee) needs no new one. Without a `STORAGE` feature the counter still works,
but only until the bot restarts.
