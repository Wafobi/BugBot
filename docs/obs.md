# OBS

OBS is the platform that calls **us**. Everything odd about this package follows from that one
fact, so it's worth stating plainly.

Discord and Twitch are services in the cloud that the bot dials. OBS is not: it runs on the
streamer's PC, and its `obs-websocket` plugin is itself a *server* listening there. A bot sitting
on a server elsewhere could only reach it if a port at home were exposed to the internet — a port
that remote-controls a live stream. So the direction is reversed instead.

```
OBS machine                                        server
┌─────────────────────┐                      ┌──────────────────────┐
│ OBS + obs-websocket │                      │ BugBot (container)   │
│       :4455 (local) │◄── ws, local ────────│  platforms/obs       │
│            ▲        │                      │  listens :4456       │
│   obs_bridge.py ────┘                      │  token check         │
│         └─── ws ──► :4456 ══ SSH -L ══════►│  127.0.0.1:4456 only │
└─────────────────────┘   encrypted tunnel   └──────────────────────┘
```

The OBS platform carries only `ANNOUNCE` — a text source in the picture. It has no chat, it
moderates nothing, and it deliberately does **not** claim `STREAM`: the stream session belongs to
Twitch, and two reporters would mean two sessions for one evening.

```
platforms/obs/
  platform.py             OBSPlatform — implements the Platform API
  link.py                 obs-websocket 5 spoken over an incoming connection
  bot.py                  events onto the bus, ad panel, announcements, command helpers
  config.py               listener port and shared secret from .env
  obs.json                sources, announcement kinds, timings, texts
  features/obs_control/   !obs, !scene, !rec, !replay, !obssource

  client/                 ── everything below runs on the OBS machine, not the server ──
    obs_bridge.py         the relay, never imported by the bot
    obs_bridge_script.py  the same relay packaged as an OBS script
    setup-tunnel.sh       installs the tunnel as a systemd user service
    disable-tunnel.sh     takes it back out
```

The `client/` split is the file layout answering a question that otherwise gets asked once per
setup: *which of these do I copy to the streaming PC?* Everything in it, nothing else. The bot
never imports from there, and `core/registry.py` skips the directory on its own — it only
descends into directories containing a `platform.py` or `feature.py`.

---

## The relay

`obs_bridge.py` is a single standalone file whose only dependency is `websockets`. It connects to
the local `obs-websocket`, dials the bot, and pipes frames both ways **without reading them**.

That is the design's main payoff: everything above `link.py` speaks ordinary obs-websocket 5 —
Hello/Identify, requests, events — and only the handshake comes from the other side. The reversed
direction costs one file and no protocol changes.

`obs_bridge_script.py` next to it is the same relay packaged as an OBS script. OBS loads it under
*Tools → Scripts*, the server address and token become fields in that dialog, and the relay starts
and stops with OBS.

It runs the relay as a **child process** rather than inside OBS's script interpreter. Three
reasons: it keeps the networking in the file that is actually tested, a crash there cannot take
OBS down with it, and stopping is a signal rather than a thread that has to be talked down.

Nothing else needs installing on the OBS side — obs-websocket ships with OBS 28 and later.

---

## Security

Two secrets guard two legs, and they are not interchangeable:

| Secret | Between | Checked |
|---|---|---|
| `OBS_BRIDGE_TOKEN` | bot ↔ relay | during the **HTTP handshake**, so a wrong one never becomes a WebSocket connection at all |
| `OBS_PASSWORD` | bot ↔ OBS | by OBS itself; it travels through the relay untouched — **the relay never learns it** |

And nothing is exposed to the internet:

- The listener is reached through an **SSH tunnel** from the OBS machine, so the relay dials
  *its own* localhost. That also encrypts the leg obs-websocket itself leaves in the clear.
- The container publishes the port to the server's **loopback only**
  (`PublishPort=127.0.0.1:4456:4456`), which is exactly where the tunnel comes out.

The token is the second lock, not the only one: a listener answering strangers never exists in
the first place.

Inside the container the bot still binds `0.0.0.0`, which looks wrong and isn't: a container has
its own network namespace that Podman's port forwarding does not reach via loopback. The
restriction belongs on the host side of that forward. Running the bot **outside** a container,
set `OBS_BRIDGE_BIND=127.0.0.1` instead.

---

## Setup

**Skipping this is fine.** Without `OBS_BRIDGE_TOKEN` the OBS platform simply isn't loaded — one
warning line at startup, and its commands don't exist.

### On the OBS machine

1. *Tools → WebSocket Server Settings* → enable the server, keep authentication on, note the
   password. Port 4455 stays as it is and needs **no** firewall rule — only the relay talks to
   it, over localhost.

2. Open an SSH tunnel to the server and keep it open:

   ```bash
   ssh -N -L 4456:127.0.0.1:4456 you@your-server.example
   ```

   That is the one-off version. For permanent use run `client/setup-tunnel.sh you@your-server`
   instead: same forward, but as a systemd user service that comes back after a dropped line
   and a reboot, with the keepalive that makes a *dead* link get noticed in the first place.
   → [The SSH tunnel](tunnel.md)

3. Copy the whole of `platforms/obs/client/` to that machine and
   `pip install websockets`. Then in OBS: *Tools → Scripts → +*, pick `obs_bridge_script.py`, and
   fill in the BugBot address (`ws://127.0.0.1:4456` — your end of the tunnel) and the token.

   The relay now starts and stops with OBS, and its messages appear in the script log. If OBS
   can't find a Python with `websockets` by itself, point *Python-Programm* at the right
   interpreter.

   Prefer it without OBS involved — headless machine, no interactive login? The relay also runs
   on its own and reconnects by itself:

   ```bash
   python3 obs_bridge.py --server ws://127.0.0.1:4456 --token <TOKEN>
   ```

   Either way it doesn't care whether OBS, the tunnel or the bot comes up first.

### On the server

4. Generate the shared secret into `OBS_BRIDGE_TOKEN`:

   ```bash
   openssl rand -hex 32
   ```

   and put the OBS password into `OBS_PASSWORD`. Nothing needs opening in the firewall.

5. **Optional.** Both `ad_break.source` and `announce.text_source` in `obs.json` default to
   empty, i.e. off - the OBS platform works with zero sources configured, this step only
   turns those two panels on. `ad_break.source` is the item shown while Twitch runs a
   commercial, `announce.text_source` the text source announcements are written into. Create
   a source with that exact name in any scene (or group - both are looked up **by name
   across all scenes and groups**) first, then enter the name here.

`!obs` in Twitch or Discord chat then reports whether the relay is connected and what OBS is
doing.

---

## Connection behaviour

**Only one session exists at a time.** A newly arriving relay takes over and the old connection
is closed, so a zombie left by an OBS restart can't sit there swallowing requests.

**Nothing waits for OBS.** `start()` returns as soon as the port is open, and `wait_ready()` is
deliberately *not* overridden — the streaming PC is usually off when the bot starts, and waiting
would hang Twitch's live reconciliation on a machine that boots in the evening.

**Both sides reconnect.** The relay retries the local `obs-websocket` every 5s (OBS usually
starts later than the relay) and the bot with a 5s→60s backoff, and it tears down both legs as
soon as one drops, so a half-open line can't linger. On the bot side a 20s WebSocket ping
notices a silently vanished home connection.

**`hide_on_connect`** (default `true`) hides the managed sources when the relay connects, so a
crash mid-ad doesn't leave the panel frozen on screen.

---

## What the bot does with the connection

### Reports

Every OBS event goes onto the bus verbatim as `RAW_EVENT`, and the meaningful ones
(`scene_changed`, `record_started`, `stream_stopped`, `replay_saved`, …) additionally as
`PLATFORM_EVENT`. So `stats` counts them and `raw_log` keeps them without either knowing what
OBS is.

`"raw_events": false` in `obs.json` turns the verbatim half off — `eventsub_log` is the
fastest-growing table in the database.

### Shows the ad panel

Twitch publishes `AD_BREAK` when a commercial starts; OBS subscribes and shows
`ad_break.source` for exactly that long (plus `extra_seconds`), then hides it again.

The two platforms still know nothing about each other — one publishes that ads are running, the
other happens to listen. Source locations are cached until OBS reports a scene change.

### Puts announcements on stream

Kinds listed under `announce.kinds` are written into `announce.text_source` and shown for
`announce.hide_after_seconds`. Empty by default, for the same reason Twitch mirrors nothing by
default: what the chat already sees needn't also be in frame.

### Takes orders

`!obs`, `!scene`, `!rec`, `!replay`, `!obssource` — all mod-only, since they reach into a live
stream. Each answers "OBS is not connected" instead of failing when the relay isn't dialled in.

These are a **feature** (`platforms/obs/features/obs_control/`), not platform code, because OBS
has no chat to type them into. As a feature they mount on Twitch and Discord alike, without
either of them knowing OBS exists.

---

## Troubleshooting

| Symptom | Look at |
|---|---|
| `!obs` says not connected | Is the SSH tunnel up? Is the relay running (OBS script log)? |
| Relay connects, then drops immediately | Token mismatch — the handshake rejects before the WebSocket opens |
| Relay connects to the bot but reports no OBS | obs-websocket not enabled, or the wrong `OBS_PASSWORD` |
| Sources not found | The names in `obs.json` must match OBS exactly; groups are searched too |
| Panel stuck on screen | `hide_on_connect` handles this on the next connect |

```bash
python3 check_credentials.py obs
```

checks the half that is checkable from the server: are the secrets set, is the token long
enough to be worth anything, is the listener port free, does the bind address make sense for
where the bot runs. Whether OBS is up, the relay dialled in and `OBS_PASSWORD` correct is
something only the running bot knows — `!obs` in chat is the answer to that.

## See also

- [Commands](commands.md#everywhere) — the OBS commands
- [Configuration](configuration.md#platformsobsobsjson) — every key in `obs.json`
- [Architecture](architecture.md#where-a-feature-lives-says-what-it-depends-on) — why the remote
  control is a feature
