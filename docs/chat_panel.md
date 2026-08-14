# Chat panel

A browser source that mirrors chat in the picture. Same reversed direction as
[OBS](obs.md) and [Overlay](overlay.md), and for the same reason: the panel runs in OBS on the
streamer's PC, the bot on a server. So the panel *dials the bot*, and the bot pushes.

```
OBS machine                                     server
┌──────────────────────┐                  ┌──────────────────────┐
│ Browser source        │                  │ BugBot (container)   │
│   chat.html            │                  │  features/chat_panel │
│        │               │                  │  listens :4458       │
│        └── ws ──► :4458 ══ SSH -L ══════►  token check           │
└──────────────────────┘  encrypted tunnel └──────────────────────┘
```

```
features/chat_panel/
  feature.py      the history, the MESSAGE_ACCEPTED subscription
  server.py       the listener — token check, connections, broadcast
  config.py       token/port/bind from .env
  chat_panel.json which platforms, whether "!" lines are hidden, how much history

  client/         ── runs on the OBS machine, not the server ──
    chat.html     the page itself, opened as the browser source
    preview.html  the same page in a scaled frame, for looking at it in a browser
```

---

## What it sends

Two frame types, both JSON:

| `type` | when | `data` |
|---|---|---|
| `history` | once, on connect | the recent messages, oldest first |
| `message` | for every accepted message | one message |

A message: `platform`, `user_name`, `text`, `is_privileged` (mod/broadcaster),
`is_subscriber`.

**Deliberately `MESSAGE_ACCEPTED`, not `MESSAGE`.** A line moderation deletes never reaches this
feature, so it can never flash on screen for the second it takes the bot to remove it — there is
no "take this back" frame to the panel, because there is nothing to take back. That is also why
the history lives only in RAM: it exists to catch up a browser source that reloads mid-stream, not
to be queried afterwards — [`features/chat_log`](database.md) is already the record, and it keeps
the opposite half on purpose (everything, so a deleted message can still be read back by a mod).

Lines starting with `!` are left out by default (`hide_commands` in `chat_panel.json`) — they are
addressed to the bot, not to whoever is reading the panel.

**Two different knobs decide "which platform".** `chat_panel.json`'s `platforms` is server-side:
it decides what ever reaches *any* connected browser source in the first place. `?source=` on the
page's own URL is client-side: it decides what that one instance renders out of what it receives.
Several browser sources can hang on the same `CHAT_PANEL_TOKEN` and show different things — one
`?source=twitch`, another `?source=discord` — without touching the JSON at all; `platforms` only
needs setting when you want a platform excluded from the panel everywhere, not just from one
scene.

---

## Setup

**1. A token, and not the other two.**

```bash
openssl rand -hex 32        # → CHAT_PANEL_TOKEN in .env
```

Same reasoning as `OVERLAY_TOKEN`: it ends up in the browser source URL and therefore in
plaintext in the scene collection, because the browser's WebSocket API cannot set request
headers. That is exactly why it must be its own secret — this one grants read access to chat that
already passed moderation, `OBS_BRIDGE_TOKEN` grants remote control of a live broadcast.

Without `CHAT_PANEL_TOKEN` the bot opens no port — that is how you run without the panel.

**2. Publish the port** — already in `bugbot.container`:

```
PublishPort=127.0.0.1:4458:4458
```

**3. The tunnel**, on the OBS machine — the same one that already carries the OBS relay and the
overlay:

```bash
platforms/obs/client/setup-tunnel.sh user@your-server
```

That forwards all three ports (4456/4457/4458) in one go — see
[The SSH tunnel](tunnel.md).

**4. The browser source.** Width and height to taste — unlike `bars.html`, this page is not a
fixed 2560×1440 canvas but fills whatever size the source is given. As URL:

```
file:///path/to/bugbot/features/chat_panel/client/chat.html?token=YOUR_CHAT_PANEL_TOKEN
```

To see it without a bot running, open **`preview.html`** next to it, or append `?demo=1` to
`chat.html` directly.

---

## Parameters

| | |
|---|---|
| `token=` | the `CHAT_PANEL_TOKEN`. Without it, no connection |
| `host=` / `port=` | where the tunnel ends on *this* machine (default `127.0.0.1:4458`) |
| `source=` | which platform(s) this instance shows, comma-separated: `source=twitch` or `source=twitch,discord`. Empty = everything the bot sends (default) |
| `max=` | how many lines stay visible before the oldest scrolls off (default `12`) |
| `badge=0` | hide the small platform tag in front of the name |
| `accent=` | accent colour for mods/broadcaster, URL-encoded: `%23FFE100` |
| `t.NAME=` | override any single design token, e.g. `t.msg-size=30px` |
| `link=1` | show the connection lamp, top right — off by default |
| `demo=1` | sample messages, no connection |

The full design-token list (colours, sizing) is in
[features/chat_panel/README.md](../features/chat_panel/README.md).

---

## See also

- [Overlay](overlay.md) — the stat bars, and the same push design in more detail
- [The SSH tunnel](tunnel.md) — how the listener is reached from the OBS machine
- [Database](database.md#chat_log) — the full, unfiltered record this panel deliberately isn't
