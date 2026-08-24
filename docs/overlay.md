# Overlay

The browser sources in the picture — the stat bars *and* the chat mirrored alongside them (see
[Chat](#chat) below; formerly its own feature, folded in here). Same reversed direction as
[OBS](obs.md), and for the same reason: the overlay runs in OBS on the streamer's PC, the bot
runs on a server. So the overlay *dials the bot*, and the bot pushes.

```
OBS machine                                     server
┌──────────────────────┐                  ┌──────────────────────┐
│ Browser sources       │                  │ BugBot (container)   │
│   bars.html            │                  │  features/overlay    │
│   chat.html            │                  │  listens :4457       │
│        │               │                  │  token check          │
│        └── ws ──► :4457 ══ SSH -L ══════►  (one listener, both)   │
└──────────────────────┘  encrypted tunnel └──────────────────────┘
```

The payoff of pushing rather than polling: a follower is in the picture the moment they arrive,
not at the next poll. And the bot decides when something changed — a viewer sample that repeats
the previous number sends nothing, so the page does not repaint without reason.

```
features/overlay/
  feature.py      the state, the topics it comes from, the death counter commands,
                   and the chat history/scope (formerly features/chat_panel)
  server.py       the listener — token check, connections, broadcast
  store.py        overlay_counters, so the death count survives a restart
  config.py       token/port/bind from .env
  overlay.json    which event fills which field, every line the bot says, and the
                   "chat" section (platforms, hide_commands, max_messages)
  README.md       every parameter and every design token, as a lookup table

  client/         ── runs on the OBS machine, not the server ──
    bars.html         the stat bars, opened as the browser source
    preview.html       the same page in a scaled frame, for looking at it in a browser
    chat.html          the chat, as its own browser source — see Chat below
    chat_preview.html  chat.html in a stage with a stand-in gameplay background
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
`game`, `viewers`, `last_follower`, `last_sub`, `last_raid`, `deaths`, `ad_break_started_at`/
`ad_break_seconds` (see [`platforms/obs/client/terminal/ad-break.html`](../platforms/obs/client/terminal/ad-break.html)),
`stream_recap` (see below). `commands` carries the commands a normal viewer may use; `mod_only`
ones are dropped before sending, because what a viewer cannot use they need not read.

Everything comes from bus topics — `STREAM_START`, `STREAM_SEGMENT`, `VIEWERS`,
`PLATFORM_EVENT`, `STREAM_END`, `AD_BREAK`, `SESSION_ENDED`. The feature asks nobody anything and
names no platform: a follow arrives as `PLATFORM_EVENT` with `event_type="follow"`, and a second
service reporting the same would work unchanged. Which event type fills which field is a line in
`overlay.json` (`event_slots`), not a line of code.

### `stream_recap`

The running stream's figures so far, re-read from `STATS` (the bot's own persistent count -
`features/stats`, backed by SQLite, not a second tally kept here) every time a browser source
connects — see `snapshot()`/`_refresh_recap` in `features/overlay/feature.py`. A follow, sub,
raid or cheer only ever updates `STATS` itself (it subscribes to `PLATFORM_EVENT`
independently); there is nothing for this feature to do in that moment, only when the answer
is actually about to be shown does it need to be current. `null` until the first connection
while live, and until a `STATS` feature and a running session both exist — without either, it
just never arrives, and nothing else about the overlay is affected.

Deliberately **not** tied to `STREAM_END`/`SESSION_ENDED` either: those fire once the stream
has actually stopped, and a recap that only appears then is a recap neither the audience nor
the VOD recording ever sees. Refreshing on connect instead means whichever browser source
shows it - typically switched to right before the stream actually ends, as a live outro -
always triggers its own fresh read the moment it (re)connects, with nothing needed to trigger
it beforehand.

A dict: the totals — `follows`, `subs`, `resubs`, `gift_subs`, `bits`, `raids`, `raid_viewers`,
`chat_messages` — plus `events`, the individual follows/subs/resubs/gift subs/raids/cheers
behind those totals, oldest first (`[{type, user_name, amount}, ...]` — `type` one of
`follow`/`sub`/`resub`/`gift_sub`/`raid`/`cheer`; anonymous cheers/gifts are counted in the
totals but left out of `events`, nothing to name). Consumed by
[`platforms/obs/client/terminal/stream-end.html`](../platforms/obs/client/terminal/stream-end.html),
a recap screen that shows this while live and falls back to sample data otherwise — see its own
comment and [`platforms/obs/client/terminal/README.md`](../platforms/obs/client/terminal/README.md).

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

**3. The tunnel**, on the OBS machine — the same one that already carries the OBS relay:

```bash
platforms/obs/client/setup-tunnel.sh user@your-server
```

That writes an `~/.ssh/config` entry with both forwards, installs a systemd user service so
the tunnel survives a dropped link and a reboot, and checks that the far end answers before
it calls itself done. `disable-tunnel.sh` next to it takes it back out.

By hand it would be `ssh -N -L 4456:127.0.0.1:4456 -L 4457:127.0.0.1:4457 user@your-server`
— worth knowing for a one-off, but not something to retype before every stream.

Already tunnelling to that machine for something else? Then read
[The SSH tunnel](tunnel.md#two-shapes) first: two ssh connections cannot both forward 4457,
and the script has a way of dealing with that. → [The SSH tunnel](tunnel.md)

**4. The browser source.** Width `2560`, height `1440`, position `(0,0)`, and as URL:

```
file:///path/to/bugbot/features/overlay/client/bars.html?token=YOUR_OVERLAY_TOKEN
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
| `!tode` | everyone | the count for the game running now |
| `!tode <game>` | everyone | …or for any other one, without playing it |
| `!tod` | mods | one more |
| `!todsetzen <n>` | mods | set it |

**Counted per game.** The key is the category the platform reports, so a row reads
`deaths:Elden Ring`. Switching games swaps the number on screen and leaves each total where it
was; switching back brings it straight back. Nothing is reset automatically — a game's count is
its all-time total, which is the only reading of "how often have I died in this game" that
survives a second evening.

The bare key `deaths` stays the pot for everything outside a known game: offline, or when the
platform reports no category. That is exactly what the counter meant before it knew about games,
so there was nothing to migrate.

Names, like everywhere, are renameable in `overlay.json` under `command_names`, and each message
exists twice — with `{game}` and as `…_no_game`, because a single text would leave a placeholder
word in the sentence whenever no game is known. The values live in `overlay_counters` and survive
restarts; the table is kept general so the next counter (wins, crashes, coffee) needs no new one.
Without a `STORAGE` feature the counter still works, but only until the bot restarts.

---

## Chat

A second browser source, `client/chat.html`, mirrors chat in the picture — same listener as
`bars.html`, same `OVERLAY_TOKEN`, but its own file rather than a part of the stat bars. Formerly
its own feature (`chat_panel`, with its own `CHAT_PANEL_TOKEN`/port); folded in here because both
are the same thing underneath — a browser source that dials in and gets pushed whatever changed.
One listener, one token instead of two.

Kept as a separate source rather than merged into `bars.html` itself so it can be **shown or
hidden live from inside OBS** — right-click the source in the Sources dock and toggle the eye
icon, or bind a hotkey to "Toggle Source Visibility". A `?chat=` URL parameter could only ever
take effect on the next page load; the eye icon takes effect immediately, mid-stream, which is
the point.

### What it sends

Three frame types, all JSON, over the same connection as `state`/`patch`:

| `type` | when | `data` |
|---|---|---|
| `history` | once, on connect, right after `state` | the recent messages, oldest first |
| `message` | for every accepted message | one message |
| `clear` | a mod/broadcaster cleared the whole chat on the platform itself | `null` |

A message: `platform`, `user_name`, `text`, `is_privileged` (mod/broadcaster),
`is_subscriber`. `bars.html` and `chat.html` see the same four frame types on the same socket and
each simply ignores the ones it does not render — `bars.html` skips `history`/`message`/`clear`,
`chat.html` skips `state`/`patch`.

**Deliberately `MESSAGE_ACCEPTED`, not `MESSAGE`.** A line moderation deletes never reaches this
part of the feature, so it can never flash on screen for the second it takes the bot to remove it
— there is no per-message "take this back" frame, because there is nothing to take back. That is
also why the history lives only in RAM: it exists to catch up a browser source that reloads
mid-stream, not to be queried afterwards — [`features/chat_log`](database.md) is already the
record, and it keeps the opposite half on purpose (everything, so a deleted message can still be
read back by a mod).

**`clear` is the one exception**, because it is not about any one message. On Twitch, a moderator
or the broadcaster hitting "Clear chat" in Twitch's own UI sends `CLEARCHAT` over IRC with no
target user (a per-user purge/timeout is a different `CLEARCHAT`, and does *not* trigger this —
that already surfaces through `MOD_ACTION` when the bot is the one acting). The bot forwards it as
`events.CHAT_CLEARED`, the feature empties its in-memory chat history, and every connected `chat.
html` wipes its feed the moment the frame arrives — Twitch does not say who cleared it, so neither
does this. A fresh `STREAM_START` clears it too, for the same reason a mid-stream reload should
not find yesterday's chat still sitting there.

Lines starting with `!` are left out by default (`chat.hide_commands` in `overlay.json`) — they
are addressed to the bot, not to whoever is reading the panel.

**Two different knobs decide "which platform".** `overlay.json`'s `chat.platforms` is
server-side: it decides what ever reaches *any* connected browser source in the first place.
`?source=` on `chat.html`'s own URL is client-side: it decides what that one instance renders out
of what it receives. Several browser sources can hang on the same `OVERLAY_TOKEN` and show
different things — one `?source=twitch`, another `?source=discord` — without touching the JSON at
all; `chat.platforms` only needs setting when you want a platform excluded everywhere, not just
from one scene.

### Setup

Add a second **Browser** source, width/height to taste (e.g. `420x900` — unlike `bars.html` this
page is not a fixed `2560×1440` canvas, it fills whatever size the source is given), and as URL:

```
file:///path/to/bugbot/features/overlay/client/chat.html?token=YOUR_OVERLAY_TOKEN
```

Same token, same tunnel, same port — nothing extra to open. To see it without a bot running,
open **`chat_preview.html`** next to it, or append `?demo=1` to `chat.html` directly.

Parameters are documented in [features/overlay/README.md](../features/overlay/README.md#chat--parameter-reference).
