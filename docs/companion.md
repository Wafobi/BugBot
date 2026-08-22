# Companion

A browser source with one small pet per eligible chatter, seeded from their name. Same reversed
direction as [Overlay](overlay.md) and [Chat panel](chat_panel.md), and for the same reason: the
page runs in OBS on the streamer's PC, the bot on a server. So the page *dials the bot*, and the
bot pushes.

```
OBS machine                                     server
┌──────────────────────┐                  ┌──────────────────────┐
│ Browser source        │                  │ BugBot (container)   │
│   companion.html       │                  │  features/companion  │
│        │               │                  │  listens :4459       │
│        └── ws ──► :4459 ══ SSH ══════════►  token check          │
└──────────────────────┘  encrypted tunnel └──────────────────────┘
```

```
features/companion/
  feature.py      presence, the !companion/!vtubbi commands
  server.py       the listener — token check, connections, broadcast
  store.py        what IS persisted: a custom seed and a spend ledger, both from !companion
  config.py       token/port/bind from .env
  companion.json  bits thresholds, speech duration, idle timeout, texts

  client/         ── these do not run on the server ──
    companion.html  the browser source itself, opened in OBS
    preview.html     the same page in a scaled frame, for looking at it in a browser
    info.md          short, German text explaining !companion — for pasting straight into a
                      Twitch "About" panel description, not a page to host
    dicebear.md      likewise, but explaining DiceBear
    vtubbi.md        likewise, but for the vtubbi project this feature is part of
```

---

## The idea

A **subscriber** who writes an accepted chat message gets a small DiceBear "sprouts" creature,
seeded from their name by default — the same seed the standalone
[vtubbi](https://github.com/Wafobi/vtubbi) avatar uses, so a viewer's companion here and their
own avatar (if they run vtubbi themselves) look the same creature until they pick something else
(see `!companion set` below). Mods and the broadcaster get one too, everyone else's chat works
exactly as before, they simply have nothing on screen. Presence itself is not persisted — who
currently has a companion lives in RAM only, exactly like `features/chat_panel`'s history. A
companion that has not chatted in `idle_minutes` (default 20) quietly leaves the pond.

**`!companion <text>`** makes that person's companion say something. What actually reaches the
screen passes two more gates on top of the subscriber check:

- **Moderation.** The text is checked again by the bot's own moderation feature
  (`features/moderation`) — deliberately again, because a mod's or the broadcaster's own message
  never reaches moderation in the first place (they're exempt from chat moderation), and a
  companion's speech bubble is exactly as public as anyone else's. A blocked attempt gets a reply
  saying why; nothing reaches the overlay.
- **Bits.** Showing the text costs `min_bits_to_speak` bits — *per use*, not a one-off threshold
  (default 100), deducted from a shared balance: what someone has cheered all-time
  (`features/stats`) minus what they have already spent on `!companion`, on *either* subcommand
  (see below). Below that balance the companion is still there and the command still runs, just
  with nothing legible in the bubble and nothing deducted, and chat gets one reply naming the
  balance instead of a bubble nobody asked to see.

**`!companion set <hash>`** gives that person's companion a custom look: `<hash>` is just a
DiceBear seed — any text works, the appeal is finding one that renders well. It costs
`min_bits_to_set_seed` bits (default 300) — *per change*, from that same shared balance, not a
separate pot. That is deliberate: two independent balances would let someone spend their bits on
messages and still get a free style change out of the same total (or the reverse). The chosen
seed itself is kept until the next `!companion set` changes it — only the bits are spent per use,
not the look. Both the seed and the spend ledger are persisted in `bugbot.db`
(`features/companion/store.py`, tables `companion_seeds` and `companion_spend`), so they survive
a restart; without a loaded `STORAGE` feature both still work for the running process, just reset
on the next start.

**Mods and the broadcaster are exempt from both bit thresholds** (not from moderation, not from
the subscriber check on presence — they get a companion through the same `is_privileged`
exemption used everywhere else) — they already run the stream and should not have to donate to it
to use a chat feature.

**`!vtubbi`** is a separate, argument-less command: it posts a link to the project behind the
companions. It does not make anything talk and is not gated by any of the above.

All three optional capabilities degrade gracefully: without `MODERATION` nothing is filtered (as
everywhere else in the bot), without `STATS` nobody has any bits on record so `!companion` never
shows or restyles anything until an operator adds one, and without `STORAGE` a custom seed is
forgotten on the next restart.

### Movement

Each companion picks a fresh behaviour whenever its current one runs out — a long walk to
somewhere else in the pond, standing and fidgeting in place, ambling towards another companion
that happens to be around, or a quick short dash — so the pond reads as several creatures with
different moods rather than one wander loop repeated per companion. On top of that, blinking and
"talking" (an open-mouth frame while a speech bubble is up) come straight from the same technique
`vtubbi/src/companion.ts` uses — swap the SVG's eyes/mouth group for an alternate one — and a
small whole-body wiggle stands in for everything a sprout has no separately animatable part for
(no ears, no tail).

---

## What it sends

Four frame types, all JSON:

| `type` | when | `data` |
|---|---|---|
| `state` | once, on connect | every companion currently in the pond |
| `join` | a new person's first accepted message, **or** an existing companion's seed changed via `!companion set` | one companion: `key`, `seed`, `platform`, `user_name` |
| `leave` | a companion has been idle past `idle_minutes` | `{key}` |
| `speak` | `!companion <text>` cleared both gates | `key`, `seed`, `platform`, `user_name`, `text`, `ttl` |

A `join` for a `key` the page already knows is a restyle, not a duplicate — `companion.html`
re-fetches that companion's art for the new seed in place, instead of it popping out and back in.

`key` is `platform:user_id` (falling back to the lower-cased name) — stable across a display-name
change, the same shape `features/moderation` uses for its own violation tracking.

---

## Setup

**1. A token, and not the other three.**

```bash
openssl rand -hex 32        # → COMPANION_TOKEN in .env
```

Same reasoning as `OVERLAY_TOKEN`/`CHAT_PANEL_TOKEN`: it ends up in the browser source URL and
therefore in plaintext in the scene collection, because the browser's WebSocket API cannot set
request headers. This one grants read access to who is currently chatting and what they typed
into `!companion` — reuse none of the other three tokens.

Without `COMPANION_TOKEN` the bot opens no port — the commands still run, there is just nowhere
to show what they produce.

**2. Publish the port** — already in `bugbot.container`:

```
PublishPort=127.0.0.1:4459:4459
```

**3. The tunnel**, on the OBS machine — the same one that already carries the other three:

```bash
platforms/obs/client/setup-tunnel.sh user@your-server
```

That forwards all four ports (4456/4457/4458/4459) in one go — see [The SSH tunnel](tunnel.md).

**4. The browser source.** Width/height to taste — like `chat.html`, this page is not a fixed
canvas but fills whatever size the source is given. As URL:

```
file:///path/to/bugbot/features/companion/client/companion.html?token=YOUR_COMPANION_TOKEN
```

To see it without a bot running, open **`preview.html`** next to it, or append `?demo=1` to
`companion.html` directly.

**5. Optional: the Twitch panel.** `client/info.md` is short, German text explaining
`!companion` / `!companion set` to viewers. `client/dicebear.md` and `client/vtubbi.md` are
separate, even shorter texts explaining DiceBear and the vtubbi project respectively, for whoever
wants those explained on their own panel instead of folded into info.md. Paste one of these
straight into a Twitch "About" panel's description — nothing to host or connect for it.

Twitch's panel description editor only renders a narrow slice of Markdown: **bold**, plain
`[text](url)` links, and blank-line paragraph breaks. No `#` headings, no `` ` `` code spans, no
`- ` bullet lists (they show up as a literal dash), and a bare `<word>` risks being stripped as
HTML — which is why these three files use a plain `•` character for lists and spell out
placeholders in prose ("gefolgt von deinem Text") instead of `<text>`. Keep any edit to them
inside that subset, or it renders raggedly on Twitch even though it looks fine as Markdown
anywhere else.

---

## Parameters

| | |
|---|---|
| `token=` | the `COMPANION_TOKEN`. Without it, no connection |
| `host=` / `port=` | where the tunnel ends on *this* machine (default `127.0.0.1:4459`) |
| `size=` | height of one companion in pixels (default `96`) |
| `max=` | how many companions are shown at once — oldest makes room for a new one past this (default `24`) |
| `floor=` | the ground line from the bottom, in % of the source's height (default `10`) |
| `t.NAME=` | override any single design token, e.g. `t.name-size=20px` |
| `link=1` | show the connection lamp, top right — off by default |
| `demo=1` | sample companions, no connection |

---

## Configuration

`companion.json` (hot-reloaded, no restart needed):

| Key | Meaning |
|---|---|
| `platforms` | which platforms' chat can spawn companions. Empty = all with a chat |
| `min_bits_to_speak` | bits spent per shown `!companion` message (default `100`) |
| `min_bits_to_set_seed` | bits spent per `!companion set` (default `300`) — same shared balance as above |
| `speech_ttl_seconds` | how long a speech bubble stays up (default `8`) |
| `idle_minutes` | how long without a message before a companion leaves the pond (default `20`) |
| `command_names` | rename/alias/disable `!companion` and `!vtubbi`, same as every other feature |

---

## See also

- [Overlay](overlay.md) · [Chat panel](chat_panel.md) — the same push design, in more detail
- [The SSH tunnel](tunnel.md) — how the listener is reached from the OBS machine
- [Moderation](moderation.md) — what actually filters `!companion <text>`
- [Database](database.md) — `companion_seeds`, the one table this feature persists
