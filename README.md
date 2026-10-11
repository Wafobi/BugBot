# 🤖 BugBot

A modular async bot that links a Twitch channel, a Discord server and (optionally) the OBS
instance the stream runs on. It moderates both chats, answers ~50 commands, records what happens
during a stream into SQLite, feeds the stream overlays, reads cheers out loud, and carries
announcements across platforms — a bug report typed in Twitch chat lands in a Discord channel.

Everything a different operator would want to change — rules, thresholds, timings, role and
channel names, command names, and **every line the bot says** — lives in JSON files next to the
code that reads them, re-read at runtime. Adapting it to another server or another language means
editing JSON, not Python, and needs no restart.

> The bot speaks German to users; the code and these docs are English.

---

## Quick start

Needs Python 3.14 and, for the supported deployment, [Podman](https://podman.io/docs/installation).

```bash
cp .env.example .env                      # fill in the tokens
python3 -m platforms.twitch.get_token     # Twitch OAuth → writes tokens back into .env
python3 check_credentials.py              # asks Twitch and Discord whether the credentials work
python3 check_config.py                   # validates JSON against the code that reads it
./setup-systemd.sh                        # build image + run under systemd (or: python3 bugbot.py)
```

The two checkers answer different questions and are worth running in that order:
`check_credentials.py` tests the `.env` against the actual services (token valid? scopes
complete? bot a moderator? intents on?), `check_config.py` tests the JSON against the code.

Afterwards, `chatlog.py` is the way back into what was said:

```bash
python3 features/chat_log/chatlog.py                # which streams were recorded
python3 features/chat_log/chatlog.py 7              # read the chat of stream #7
python3 features/chat_log/chatlog.py --all --html   # the whole archive as pages to browse
```

Full walkthroughs: [Twitch](docs/twitch.md#tokens) · [Discord](docs/discord.md#setting-up-the-app)
· [OBS](docs/obs.md#setup) · [Deployment](docs/deployment.md)

`BUGBOT_PLATFORMS=twitch` and `BUGBOT_FEATURES=stats,sql_db` (comma-separated) restrict what
loads, without deleting directories.

---

## 📚 Documentation

The [`docs/`](docs/) folder is the long version.

| | |
|---|---|
| [Architecture](docs/architecture.md) | the bus, the registry, the startup sequence, and why nothing imports across package boundaries |
| [Extending it](docs/extending.md) | the Platform API and the Feature API in full — and how to add one of each |
| [Configuration](docs/configuration.md) | every JSON file and every key in it |
| [Commands](docs/commands.md) | every command, its arguments, and who may use it |
| [Moderation](docs/moderation.md) | the filters, the thresholds, the escalation |
| [Database](docs/database.md) | every table and column, and how the stream session stamps them |
| [Overlay](docs/overlay.md) | the browser sources in the picture — stat bars and the chat mirrored alongside them: what the bot pushes, and how to wire one up |
| [Companion](docs/companion.md) | one small pet per chatter, seeded from their name; `!companion`/`!vtubbi` |
| [TTS](docs/tts.md) | cheers with a message and `!tts` read out loud via Piper, released one at a time from the Stream Deck |
| [Twitch](docs/twitch.md) · [Discord](docs/discord.md) · [OBS](docs/obs.md) | per-platform specifics |
| [Deployment](docs/deployment.md) | Podman, systemd, logs, and what to run after changing what |

---

## What it does

**Moderates** both chats against one shared rule set — banned words, disallowed links, caps,
symbol and emote spam — escalating from delete to timeout after repeated violations within a
window. Subscribers are exempt from the pure spam heuristics, nobody is exempt from banned words.
→ [Moderation](docs/moderation.md)

**Answers commands** in three flavours: static text from JSON, live Helix calls (`!uptime`,
`!title`, `!raid`), and feature commands that work on every platform at once (`!rank`, `!top`,
`!streamstats`, `!highscores`, `!leaderboard`, `!companion`, `!tts`). OBS gets remote-controlled from
either chat with `!obs`, `!scene`, `!rec`, `!replay`, `!obssource`.
→ [Commands](docs/commands.md)

**Records** messages, commands, moderation actions, live events, viewer samples and ad breaks
into SQLite — **only while a stream runs**. Cheers are the one exception and always kept, since
every viewer's bits balance is computed from them. Every row carries the stream session it
happened in, so overall and per-stream numbers are the same query with and without a `WHERE`.
→ [Database](docs/database.md)

**Feeds the overlays.** Stat bars, the mirrored chat, the boot/pause/ad-break/end screens and the
companions are browser sources in OBS that dial in to the bot and get every change pushed.
→ [Overlay](docs/overlay.md) · [Companion](docs/companion.md)

**Reads out loud.** Cheers with a message, resub messages and `!tts` go into a queue; a Stream
Deck button releases them one at a time. Speech is made by Piper on the server — a voice pool,
per-viewer voices, robot/retro/radio effects, English chat words pronounced in English — and
every message is kept with its position in the VOD. Tuned on its own admin page.
→ [TTS](docs/tts.md)

**Announces across platforms.** A neutral `Announcement` goes to every platform that can post
one, and each decides how to render it — Discord as an embed, Twitch as a chat line, OBS as a
text source on stream.

**Stays up.** Both Twitch connections keep themselves alive across days of idle, and the OAuth
token refreshes itself before it can expire.
→ [Twitch](docs/twitch.md#staying-connected)

---

## Configuration in one minute

One JSON per package, named after it, next to the code that reads it. All of them are re-read
when their mtime changes — editing and saving is enough.

| File | Holds |
|---|---|
| `platforms/twitch/twitch.json` | rules, static commands, moderation overrides, texts, timings, colours, command names |
| `platforms/discord/discord.json` | the same, plus role and channel names, reaction roles, announce channels, the `setup` blueprint |
| `platforms/obs/obs.json` | ad-break source, on-stream announcements, texts, timeouts |
| `platforms/discord/features/levels/levels.json` | XP rate and cooldown, level-up texts |
| `features/moderation/moderation.json` | thresholds per platform, the banned-word list, violation labels |
| `features/stats/stats.json` | every label, field and line the statistics print |
| `features/chat_log/chat_log.json` | which platforms get logged |
| `features/sql_db/sql_db.json` | where the database file lives |
| `features/variables/variables.json` | timezone, locale and the `{placeholders}` of static commands |
| `features/overlay/overlay.json` | what the overlays show — chat, event slots, texts |
| `features/companion/companion.json` | companion prices and timings, texts |
| `platforms/twitch/features/tts/tts.json` | TTS rules, voice pool, speech settings, replacements, English words, texts |

Five things are worth knowing:

- **Names are not cosmetics.** Discord roles and channels are found *by these strings*. Get
  `roles.moderator` wrong and nobody on that server is a moderator — mod commands then do
  nothing, silently. First thing to change on a new server.
- **Texts.** Every line the bot says has a key under `"texts"`. A wrong `{placeholder}` is not
  fatal: the bot reports it once and prints the shipped version instead.
- **Command names.** `"command_names"` renames, aliases or disables any command:
  `"!uptime": "!live"`, `"!bug": ["!bug", "!fehler"]`, `"!giveaway": false`.
- **You cannot lock yourself out.** Delete a key, break the JSON, remove the file entirely — the
  last good state or the shipped default still applies.
- **Overrides.** What is changed at runtime (today: the TTS admin page) goes into
  `settings/<name>.json`, which lies on top of the repo's file — so the files in git are never
  written and `git pull` never conflicts. → [Overrides](docs/configuration.md#overrides)

Run `python3 check_config.py` after editing. Silence plus one ✅ means the edit is sound.
→ [Configuration](docs/configuration.md)

---

## Architecture in one minute

One rule: **no package imports another package it isn't inside.** Everything crossing a boundary
goes through the bus in `core/events.py`.

```
bugbot.py     names nothing — finds, starts and stops whatever the registry hands it
core/         contracts and wiring only: no moderation rules, no SQL, no Discord, no Twitch
platforms/    where the bot acts — discord/, twitch/, obs/
features/     what the bot does — sql_db/, moderation/, stats/, chat_log/, variables/,
              overlay/, companion/ (+ platform-owned ones, e.g. platforms/twitch/features/tts/)
```

A **platform** provides `platform.py` with `create_platform()`; a **feature** provides
`feature.py` with `create_feature()`. The registry finds both, and neither `bugbot.py` nor
`core/` is touched to add one. Platforms declare what they can do via capabilities (`CHAT`,
`ANNOUNCE`, `STREAM`, `MODERATE`) instead of being recognised by name; features declare what they
need via `requires` and are skipped, loudly, if nothing provides it.

Features are reached by **push** (they subscribe to bus topics) or **pull** (a platform looks one
up by capability when it needs an answer — moderation returns a `Verdict` that the platform then
carries out).

Where a feature lives says what it depends on: `features/` is neutral and works with anything,
`platforms/<name>/features/` is owned by that service and loads only when it does.
→ [Architecture](docs/architecture.md) · [Extending it](docs/extending.md)

---

## Running it

Two machines are involved, and which is which decides what you install where:

| | **Server** — where the bot runs | **OBS machine** — where you stream from |
|---|---|---|
| Runs | the container, all platforms and features | the OBS relay, the overlay and chat pages, the tunnel |
| Files | everything except the `client/` folders | `platforms/obs/client/`, `features/overlay/client/`, `features/companion/client/`, `platforms/twitch/features/tts/client/` |
| Scripts | `./setup-systemd.sh` (once), `./start.sh`, `./update.sh` (day-to-day), `./disable-systemd.sh` | `platforms/obs/client/setup-tunnel.sh`, `disable-tunnel.sh` |
| Needs | Podman, `.env`, the JSON configs | Python + `websockets`, an ssh key to the server |

The rule of thumb: anything inside a **`client/`** folder is the streaming PC's, everything else is
the server's. Nothing in a `client/` folder is ever imported by the bot, so a server that never
copies them is complete.

The tunnel exists because the bot's two listeners (`4456` for the OBS relay, `4457` for everything on screen) are published to the
server's loopback only, so the OBS machine dials in rather than the other way round. Both tunnel scripts are
safe to re-run, and both cope with a machine that already tunnels to that server for something
else. → [The SSH tunnel](docs/tunnel.md)

**Both on one machine?** Then there is no tunnel and no copying — skip `setup-tunnel.sh`, point the
relay and the browser source at `127.0.0.1` as usual, and everything else stays the same. The
tokens are still required. → [When both ends are the same machine](docs/tunnel.md#when-both-ends-are-the-same-machine)

The `Dockerfile` does `COPY . .`, so **the code is baked into the image** while configs and the
DB are mounted over it from your clone:

| You changed | Run |
|---|---|
| any `*.json`, or `settings/*.json` | **nothing** — re-read on mtime change |
| `.env` | `systemctl --user restart bugbot` |
| any `.py`, `requirements.txt`, `Dockerfile` | `./update.sh` |
| `bugbot.container` | `./update.sh` |

> **After pulling, always `./update.sh`.** `git pull` + `systemctl --user restart bugbot` looks
> like it worked — service up, logs healthy — and is still running the old code.

Logs: `journalctl --user-unit=bugbot.service -f` (the `--user-unit=` form matters —
[why](docs/deployment.md#logs)).
→ [Deployment](docs/deployment.md)

