# Twitch

The Twitch platform is the only one carrying all four capabilities — `CHAT`, `ANNOUNCE`,
`STREAM` and `MODERATE` — and it owns two features that live on events nobody else emits:
`stream_sessions` and `raw_log`.

```
platforms/twitch/
  platform.py    TwitchPlatform — implements the Platform API
  bot.py         IRC client, EventSub, stream lifecycle, giveaway, bot-owned commands
  commands.py    the commands that are really Helix calls
  api.py         Helix API calls
  config.py      credentials from .env, and the LiveConfig other modules share
  scopes.py      the required OAuth scopes and what each is for
  get_token.py   the one-off OAuth helper
  twitch.json    rules, commands, texts, timings, colours, moderation overrides
  features/
    stream_sessions/   when a stream ran, under which title and category
    raw_log/           every notification verbatim
```

Three connections run at once: **IRC** for chat, **EventSub over WebSocket** for events, and
ordinary **HTTPS** for Helix calls.

---

<a id="tokens"></a>
## Tokens

Two different things, easily confused:

| | What it is | Where it comes from |
|---|---|---|
| **App credentials** | `TWITCH_CLIENT_ID` + `TWITCH_CLIENT_SECRET` | your app in the [Developer Console](https://dev.twitch.tv/console/apps) |
| **User token** | `TWITCH_CHAT_ACCESS_TOKEN` + `TWITCH_CHAT_REFRESH_TOKEN` + `TWITCH_CHAT_CLIENT_ID` | the account that posts and moderates, via the OAuth flow |

### The supported way

```bash
python3 -m platforms.twitch.get_token
```

Run from the project root. It performs the Authorization Code flow against **your own** app,
requests exactly the scopes in `scopes.REQUIRED`, and writes the access token, refresh token and
client id back into `.env`. It needs `http://localhost:3000` registered as an OAuth Redirect URL
on the app.

Because the token is then issued by your own app, refreshing works and
`TWITCH_CHAT_CLIENT_SECRET` can stay empty — the bot falls back to `TWITCH_CLIENT_SECRET` when
the client ids match.

### The token-generator shortcut, and what it costs

A third-party generator hands you a token belonging to **its** app, not yours. So
`TWITCH_CHAT_CLIENT_ID` differs from `TWITCH_CLIENT_ID`, and refreshing needs a client secret
you don't have. Such a token can never be refreshed — the bot says so once at startup and
doesn't retry.

That is fine as long as the token doesn't expire (`expires_in: 0`), which is what generators
typically issue. If it *does* expire, either run the flow above, or set
`TWITCH_CHAT_CLIENT_SECRET` to the issuing app's secret.

### Refreshing

User tokens usually expire after about four hours, so a background task checks the remaining
lifetime every `token_check_interval` (1800s) and refreshes once it's within
`token_refresh_margin` (3600s) of expiry. The rotated tokens are written back into `.env` —
which is why `.env` is bind-mounted into the container rather than baked into the image.

Tokens reporting `expires_in: 0` from `/oauth2/validate` never expire and are left alone.

---

<a id="scopes"></a>
## Scopes

`platforms/twitch/scopes.py` is the single source of truth, and it is deliberately code rather
than configuration: which scopes are required follows directly from which Helix endpoints
`api.py` calls and which EventSub types `bot.py` subscribes to. The module imports nothing, so
both the bot and the standalone `get_token.py` can read it.

The list is kept tight on purpose — the token sits on a server, and every unused scope is only
extra damage if it leaks.

| Group | Scopes |
|---|---|
| Chat | `chat:read`, `chat:edit` |
| Moderation | `moderator:manage:chat_messages`, `moderator:manage:banned_users`, `moderator:manage:warnings`, `moderator:manage:automod`, `moderator:manage:chat_settings`, `moderator:manage:shoutouts` |
| Channel management | `channel:manage:broadcast`, `channel:manage:raids`, `channel:manage:polls`, `channel:manage:predictions`, `channel:manage:vips`, `channel:manage:moderators`, `channel:manage:redemptions`, `clips:edit` |
| Read / EventSub | `moderator:read:chatters`, `moderator:read:followers`, `channel:read:subscriptions`, `bits:read`, `channel:read:hype_train`, `channel:read:ads`, `channel:read:goals`, `channel:moderate` |

At startup the bot validates the token against this list and prints, in plain words, what it can
and cannot do. Missing scopes disable the affected function rather than breaking anything.

**Adding a feature that needs a new scope:** add it to `scopes.REQUIRED`, add a plain-text line
to `scopes.CAPABILITIES`, and re-run `get_token.py`. Twitch never widens an existing token's
scopes retroactively.

### Two account-level catches

- The account whose token this is **must be a moderator in the channel**, or delete and timeout
  fail no matter what the scopes say.
- `channel:read:ads` is only granted for the **broadcaster's own** account. With a separate
  mod-bot account, ad-break announcements stay silently disabled (logged once at startup) while
  everything else keeps working.

---

## EventSub

Subscribed over WebSocket, re-registered on every fresh session:

| Area | Types |
|---|---|
| Stream | `stream.online`, `stream.offline`, `channel.update` |
| Audience | `channel.follow`, `channel.subscribe`, `channel.subscription.gift`, `channel.subscription.message`, `channel.subscription.end`, `channel.cheer`, `channel.raid` |
| Hype train | `channel.hype_train.begin`, `.progress`, `.end` |
| Moderation | `channel.ban`, `channel.unban`, `automod.message.hold` |
| Channel | `channel.poll.end`, `channel.prediction.end`, `channel.goal.end`, `channel.ad_break.begin`, `channel.shoutout.receive` |
| Points | `channel.channel_points_custom_reward_redemption.add` |

Everything arriving here goes onto the bus twice: as a typed `PLATFORM_EVENT` if it has a
meaning the bot knows, and always as a `RAW_EVENT` for `raw_log`. A handler that raises does not
take down the others.

`channel.ban` and `channel.unban` are why `moderation_actions` also contains actions taken by
human moderators, not just the bot's own.

---

## Staying connected

The bot has to survive days between streams without anyone restarting it.

**IRC chat.** Answers Twitch's `PING`. After `irc_ping_interval` (180s) with no incoming data at
all, it sends its own — if nothing answers, the connection counts as dead and is rebuilt. Every
disconnect (server-side close, network drop, rejected login) triggers a reconnect with a
5s→`irc_reconnect_backoff_max` (300s) backoff.

A rejected login is treated separately from a dropped connection: it raises internally, because
reconnecting is pointless until the access token has been renewed.

**EventSub.** The session counts as dead if Twitch's `session_keepalive` messages stop arriving
(plus `eventsub_keepalive_grace`), and all subscriptions are re-registered on the new session.

**Viewer counts** are the one thing that stays polling, every `viewer_sample_interval` (60s) —
Helix has no event for them.

The log lines to look for:

```
✅ Twitch-IRC verbunden
⚠️ Twitch-IRC-Verbindung verloren: …
🔄 Nächster Twitch-IRC-Verbindungsversuch in Ns…
```

## Restarting mid-stream

On startup the bot reconciles: it asks Helix whether the channel is live, and if it is, picks up
the open `stream_sessions` row rather than opening a second one. This is what
`platform_ready_timeout` (120s) is for — it waits for the other platforms to be ready before
announcing anything, because Discord discards announcements sent before `on_ready`.

## Announcements

Twitch mirrors nothing into chat by default: `!bug` and `!clip` already answer the chat
directly, so repeating them as announcements would say everything twice. List the kinds you do
want in `twitch.json` under `announce_kinds`.

When it does render one, it flattens the `Announcement` to a single line via `as_text()`, with a
field cap — an IRC line is limited to roughly 500 characters.

## Follow batching

Follows arriving within `follow_batch_window` (8s) are announced as one line naming up to
`follow_batch_names_shown` (5) of them. Without it, a follow raid becomes a wall of chat
messages.

## Checking it

```bash
python3 check_credentials.py twitch
```

Validates the token against `/oauth2/validate`, compares its scopes to `scopes.REQUIRED` and
names each missing one in plain words, reports the remaining lifetime and whether a refresh is
even possible, tests `TWITCH_CLIENT_ID`/`SECRET` as an app pair, resolves the channel, and —
if the token belongs to someone other than the broadcaster — checks whether that account really
is a moderator there. Read-only throughout.

The app and channel checks run even when the user token is dead, which is exactly when you want
to know whether `get_token.py` has any chance of working.

## See also

- [Commands](commands.md#twitch) — every Twitch command and its arguments
- [Configuration](configuration.md#platformstwitchtwitchjson) — every key in `twitch.json`
- [Database](database.md) — `stream_sessions`, `eventsub_log`
