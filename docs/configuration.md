# Configuration

Everything an operator would want to change lives in JSON, never in Python. One file per
package, named after it, sitting next to the code that reads it — that's the whole convention
(`core/runtime_config.py`, `LiveConfig`).

All of them are re-read when their mtime changes. Editing and saving is enough: no restart, no
rebuild, no reload command.

> With the containerised deployment there is one way to edit into the void — see
> [Editing a config that the container can't see](deployment.md#editing-in-place). If a change
> seems to be ignored, that is the first thing to check, and `python3 check_config.py` on the
> host checks it for you.

## The files

| File | Belongs to | Holds |
|---|---|---|
| `platforms/twitch/twitch.json` | Twitch platform | rules, static commands, moderation overrides, texts, timings, colours, command names |
| `platforms/discord/discord.json` | Discord platform | the same, plus role and channel names, reaction roles, announce channels, the `setup` blueprint |
| `platforms/obs/obs.json` | OBS platform | ad-break source, on-stream announcements, texts, timeouts |
| `platforms/discord/features/levels/levels.json` | `levels` feature | XP rate and cooldown, level-up texts |
| `features/moderation/moderation.json` | `moderation` feature | thresholds, the banned-word list, violation labels |
| `features/stats/stats.json` | `stats` feature | every label, field and line the statistics print |
| `features/variables/variables.json` | `variables` feature | the `{placeholders}` usable in static commands, on every platform |
| `features/chat_log/chat_log.json` | `chat_log` feature | which platforms get logged |
| `features/overlay/overlay.json` | `overlay` feature | which event fills which field on screen, death-counter texts |
| `features/chat_panel/chat_panel.json` | `chat_panel` feature | which platforms, whether `!` lines are hidden, how much history a reconnect gets |
| `features/sql_db/sql_db.json` | `sql_db` feature | where the database file lives |

Keys beginning with `_` are comments for the human editing the file — JSON has none of its own.
They are ignored everywhere, including in `command_names`.

---

## How the layering works

Two layers lie on top of each other, the upper beating the lower:

| Layer | What it is |
|---|---|
| `defaults` | what the code brings along — the few values a module can't work without |
| current file | whatever is in it now |

What the file says is what applies. Delete a static command and it is gone; delete a threshold
and the value from the code applies again. That is the point of the whole mechanism — a change
you can't take back without restarting the bot isn't a runtime change.

Nested dicts are merged, not replaced: setting *one* threshold doesn't cost you the others.
Lists are treated as a single value — a list in the file replaces the default list completely,
because otherwise you could never remove anything.

There is a third layer, but only for **texts**, and only as a fallback string: the content at
the first successful load, i.e. the file as shipped in the repo. It is why the texts don't have
to exist twice — a feature that is almost entirely sentences would otherwise carry them once in
Python and once in JSON, two copies that drift apart without anyone noticing. Delete a text key
and the shipped sentence still comes out. For anything other than texts the same underlay would
just be a way of keeping deleted things alive, which is why it isn't there.

**You cannot lock yourself out.** Break the JSON or remove the file entirely and the last good
state applies, with one line in the log saying so.

## Texts

Every line the bot says to a human has a key under `"texts"`.

```json
"bug.thanks": "🐛 Danke @{user}! Dein Bug-Report wurde ans Team weitergeleitet."
```

`{placeholders}` get filled by the code and must survive an edit. Getting one wrong is not
fatal: `text()` reports it once per key and falls back to the shipped version, so a typo costs a
log line, not a command. A missing key, a value that isn't a string, an unknown placeholder, and
a stray brace are all handled the same way.

Twitch alone has 127 text keys, Discord 68, OBS 48, `stats` 47. Run `check_config.py` after
editing — it verifies every key exists and that its placeholders match what the code passes.

## Colours

Under `"colors"`, written the way colours are normally written:

```json
"colors": { "status": "#2ECC71", "danger": "#E74C3C" }
```

A plain number works too. Something that is neither is reported once and falls back.

## Variables in static commands

<a id="variables"></a>A static command is plain text in `twitch.json`/`discord.json`, and anything
in `{braces}` is filled in when it is used:

```json
"commands": {
  "!time": "🕒 Es ist {time} Uhr.",
  "!lurk": "🍿 @{u} macht es sich im Lurk-Modus gemütlich."
}
```

Single braces, always. `{{time}}` is Python's escape for a *literal* brace and would arrive in
chat as `{time}`.

Three of them the platform knows by itself, so they work even with the `variables` feature
switched off: **`{u}`** (the caller — a mention on Discord, the name on Twitch), **`{user}`** (the
plain name, for sentences where a ping would be noise) and **`{channel}`**.

Everything else comes from `features/variables/variables.json` and is therefore the same on every
platform — define `{steam}` once and use it in both chats:

| Key | What it does |
|---|---|
| `timezone` | IANA name (`Europe/Berlin`) for `now`, and therefore for `{time}` and `{date}` — and for every other clock time the bot posts, such as when an ad break ends. Empty means the process timezone, which in the container is the host's (`Timezone=local` in `bugbot.container`) and UTC without that line. Set it: it holds regardless of how the bot is started |
| `locale` | language of spelled-out weekdays and months (`%A`, `%B`). Must exist in the image — the `Dockerfile` generates exactly this one via `ARG LOCALE` |
| `variables` | `NAME: text` — fixed strings you need in several places |
| `python` | `NAME: expression` — evaluated when a command uses it |
| `python_timeout_seconds` | 2 — after this the expression is abandoned |
| `cache_seconds` | 3 — how long a result is reused, so chat spam can't re-run it per message |

```json
"variables": { "steam": "https://store.steampowered.com/app/2758910/" },
"python": {
  "time": "now.strftime('%H:%M')",
  "date": "now.strftime('%d.%m.%Y')",
  "wochentag": "now.strftime('%A')",
  "bis_release": "(date(2027, 3, 1) - now.date()).days"
}
```

**`{time}` and `{date}` are ordinary entries in that list**, not something the code holds back —
change the expression and the clock looks different everywhere. Nothing in Python names a
variable any more, so the file is the complete list of what exists. Delete them and the shipped
expressions in `feature.py` (`DEFAULTS`) step in, so a `!time` can't go quiet because of one
deleted line.

An expression has `now` (in your timezone), `datetime`, `date`, `timedelta`, `ZoneInfo`, `math`,
`random`, and the caller's `user`, `u` and `channel`. It must be an *expression* — no `import`,
no `=`. Only variables the command actually mentions are evaluated, so an expensive one costs
nothing until something uses it.

**Variables may use variables**, in both directions and as deep as you like — so a URL you need
in three places is written once:

```json
"variables": {
  "steam": "https://store.steampowered.com/app/2758910/",
  "chef": "Chef's Adventure — {steam}"
},
"python": { "steam_de": "steam + '?l=german'" }
```

A text under `variables` gets its own `{placeholders}` filled before it is inserted; an
expression gets exactly the variables it names, resolved first. Define two that need each other
and the cycle is reported by name (`a -> b -> a`) and the placeholder left standing — the one
mistake here that would otherwise be a hanging bot rather than a log line.

If an expression fails, takes too long or names something unknown, that placeholder alone stays
as `{name}` in the text, the rest of the sentence is still filled, and the reason is logged once.
A typo costs you a word, never the reply.

> **The expression runs inside the bot process, with the bot's rights.** That is a statement about
> who may edit this file, not a sandbox — whoever can write it can already do anything. What the
> limits above are for is *accidents*: a typo, a division by zero, something slow. Never build an
> expression that treats chat text as code; what comes from chat is available as the *values*
> `user`, `u` and `channel`, which is the safe way and the only one needed.

## Command names

<a id="command-names"></a>Under `"command_names"`, in whichever of the four spellings reads best:

```json
"command_names": {
  "!uptime":   "!live",                                  // rename
  "!bug":      ["!bug", "!fehler"],                      // rename + aliases
  "!giveaway": false,                                    // disable
  "!top":      {"name": "!best", "aliases": ["!beste"], "enabled": true}
}
```

Names without `!` get one added — `live` and `!live` mean the same thing. Useful when another
bot in the same chat already answers `!uptime`.

Feature commands are resolved centrally by the bus, platform commands by the platform; both are
hot-reloaded, and a name claimed twice is reported rather than silently dropping one.

---

## Reference

### `platforms/twitch/twitch.json`

| Key | Type | Meaning |
|---|---|---|
| `rules` | string | the text `!rules` prints |
| `commands` | object | static text commands, e.g. `"!discord": "🔗 …"`. `{u}` is the caller |
| `mod_commands` | object | the same, restricted to moderators |
| `announce_kinds` | list | which announcement kinds get echoed into Twitch chat. Empty by default — what chat already sees needn't be repeated |
| `moderation` | object | per-platform overrides of the moderation thresholds — see [Moderation](moderation.md) |
| `timings` | object | see below |
| `texts`, `colors`, `command_names` | object | as described above |

`timings`, all in seconds unless noted:

| Key | Default | Meaning |
|---|---|---|
| `irc_ping_interval` | 180 | quiet time before the bot sends its own PING |
| `irc_reconnect_backoff_max` | 300 | ceiling of the 5s→300s reconnect backoff |
| `token_check_interval` | 1800 | how often the OAuth token's remaining lifetime is checked |
| `token_refresh_margin` | 3600 | refresh this long before expiry |
| `task_restart_delay` | 10 | pause before restarting a crashed background task |
| `eventsub_keepalive_grace` | 10 | extra time on top of Twitch's keepalive before the session counts as dead |
| `follow_batch_window` | 8 | follows arriving within this window are announced as one line |
| `follow_batch_names_shown` | 5 | how many names that line lists |
| `viewer_sample_interval` | 60 | how often the viewer count is sampled (Helix has no event for it) |
| `platform_ready_timeout` | 120 | how long the live reconciliation waits for the other platforms |
| `giveaway_max_entries_shown` | 5 | entrants listed when a giveaway is drawn |

### `platforms/discord/discord.json`

| Key | Type | Meaning |
|---|---|---|
| `roles` | object | **role names, looked up literally** — `moderator`, `member` |
| `channels` | object | **channel names, looked up literally** — `log`, `commands`, `honeypot`, `announcements`, `role_selection` |
| `announce_channels` | object | announcement kind → channel name, e.g. `"bug.report": "🐛-bug-reports"` |
| `reaction_roles` | object | emoji → role name, for the role-selection message |
| `clip_channel` | string | where `!clip` results are posted |
| `status_report_hours` | int | interval of the automatic status report. `0` disables it |
| `levels.role_thresholds` | object | level (as a string) → role name to grant on reaching it |
| `rules`, `commands`, `mod_commands`, `moderation` | | as on Twitch |
| `setup` | object | the server blueprint — see [Discord](discord.md#the-setup-blueprint) |

> **Names are not cosmetics.** Roles and channels are found *by these strings*. Get
> `roles.moderator` wrong and nobody on that server is a moderator — mod commands then do
> nothing, silently. This is the first thing to change on a new server.
>
> An empty channel name is a supported way to switch a function off without deleting the channel.

### `platforms/obs/obs.json`

| Key | Default | Meaning |
|---|---|---|
| `ad_break.source` | `""` | source shown while Twitch runs a commercial. Empty = off until a source with that exact name exists in a scene |
| `ad_break.extra_seconds` | 0 | keep it up this much longer than the ad |
| `announce.kinds` | `[]` | which announcement kinds go on stream. Empty by default |
| `announce.text_source` | `""` | text source they're written into. Empty = off, same as `ad_break.source` |
| `announce.hide_after_seconds` | 20 | how long they stay up |
| `announce.max_fields` | 2 | detail fields included |
| `raw_events` | `true` | put every OBS event on the bus verbatim. **The fastest-growing table** — set false to keep only the meaningful ones |
| `hide_on_connect` | `true` | hide the managed sources when the relay connects, so a crash mid-ad doesn't leave the panel up |
| `timings.request_timeout` | 10 | per obs-websocket request |
| `timings.handshake_timeout` | 20 | for the relay handshake |

Both sources are looked up by name across all scenes **and groups**, so they can live anywhere.

### `features/moderation/moderation.json`

| Key | Default | Meaning |
|---|---|---|
| `settings.allowed_link_domains` | twitch.tv, steampowered.com, discord.gg, … | links to anything else count as spam |
| `settings.caps_min_length` | 10 | shorter messages are never caps-spam |
| `settings.caps_ratio_threshold` | 0.7 | uppercase share that trips it |
| `settings.symbol_min_length` | 8 | shorter messages are never symbol-spam |
| `settings.symbol_ratio_threshold` | 0.5 | non-alphanumeric share that trips it |
| `settings.emote_spam_min_tokens` | 6 | tokens before repeat-checking starts |
| `settings.emote_spam_min_repeats` | 6 | repeats of one token that trip it |
| `settings.violation_window_minutes` | 10 | how long violations are remembered |
| `settings.timeout_threshold` | 3 | violation number at which a timeout is added |
| `settings.timeout_duration_seconds` | 60 | base timeout length |
| `banned_words.use_builtin_list` | `true` | use the curated base list shipped in `filters.py` |
| `banned_words.extra` | `[]` | additional words |
| `banned_words.remove` | `[]` | words to take *out* of the built-in list |

Every `settings` key can be overridden per platform in that platform's `moderation` section.
See [Moderation](moderation.md) for the merge order and the escalation.

### `features/stats/stats.json`

| Key | Default | Meaning |
|---|---|---|
| `leaderboard_limit` | 3 | entries per leaderboard |
| `texts` | 47 keys | every label, field and line the statistics print |
| `colors.summary`, `colors.stream` | | embed colours |

### `features/variables/variables.json`

See [Variables in static commands](#variables) above for the whole of it.

| Key | Default | Meaning |
|---|---|---|
| `timezone` | `Europe/Berlin` | IANA name; applies to `{time}`/`{date}` and to the ad-break end time in Twitch chat. Empty falls back to the process timezone (the host's, via `Timezone=local` in `bugbot.container`) |
| `locale` | `de_DE.UTF-8` | language of `%A`/`%B`; must be generated in the image (`Dockerfile`, `ARG LOCALE`) |
| `variables` | | `NAME: text` |
| `python` | `time`, `date`, `wochentag` | `NAME: expression` — the built-ins live here too |
| `python_timeout_seconds` | 2 | limit per expression |
| `cache_seconds` | 3 | how long a result is reused |

### `features/chat_log/chat_log.json`

| Key | Default | Meaning |
|---|---|---|
| `platforms` | `[]` | which platforms get logged. Empty = all |
| `recent_limit` | 200 | default row count for the recent-messages query |

Prefer a **capability** over a service name: `["stream"]` means "the chat of whichever platform's
stream is being recorded" and stays correct if that service ever changes. `["chat"]` means "the
ones with a chat of their own". A plain name like `"twitch"` still works, but is reported at
startup if no loaded platform carries it — precisely the case that otherwise turns silently into
"never matches".

Logging is live-only, and that isn't a setting: `chat_log.stream_session_id` is `NOT NULL`.
Recording off-stream would need a schema change.

### `features/sql_db/sql_db.json`

| Key | Default | Meaning |
|---|---|---|
| `db_path` | `""` | where the SQLite file lives. Empty = `bugbot.db` in the project folder |

Precedence: `BUGBOT_DB` from the environment, then `db_path`, then the default. The file says
where *this installation's* storage lives; the environment variable is the override for a single
run — a test that shouldn't write to the real DB, or a container with a different mount. Unlike
everything else here, `db_path` is read once at startup.

---

## Environment (`.env`)

Credentials and things that must be known before any config file is read. Copy `.env.example`
and fill it in. Changing `.env` needs a **restart** — it is read once at process start.

| Variable | Needed for |
|---|---|
| `BUGBOT_PLATFORMS`, `BUGBOT_FEATURES` | restrict what loads (comma-separated). Empty = everything |
| `BUGBOT_DB` | override the database path for one run |
| `DISCORD_TOKEN` | the Discord bot |
| `TWITCH_CHANNEL`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | the Twitch app |
| `TWITCH_CHAT_ACCESS_TOKEN`, `TWITCH_CHAT_REFRESH_TOKEN`, `TWITCH_CHAT_CLIENT_ID` | the chat/mod account's user token — written by `get_token.py` |
| `TWITCH_CHAT_CLIENT_SECRET` | only if the chat token came from a *different* app than `TWITCH_CLIENT_ID`. See [Twitch](twitch.md#tokens) |
| `OBS_BRIDGE_TOKEN` | shared secret with the relay. **Without it the OBS platform isn't loaded at all** |
| `OBS_BRIDGE_PORT`, `OBS_BRIDGE_BIND` | listener port (4456) and bind address (0.0.0.0; use 127.0.0.1 outside a container) |
| `OBS_PASSWORD` | the obs-websocket password |

The Twitch token refresh writes rotated tokens back into `.env` by itself — which is why the file
is bind-mounted into the container rather than baked into the image.

---

## Checking your work

```bash
python3 check_config.py
```

It parses every JSON, checks that each `text()` call has a key, that the placeholders in each
text match what the code passes, lists texts nobody uses any more, and catches a `command_names`
entry naming a command that doesn't exist or one that collides with another.

A line about a feature it couldn't load (`obs_control` without `OBS_BRIDGE_TOKEN`) is
informational — it means those commands went unchecked, not that something is wrong.
