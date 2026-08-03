# Database

One SQLite file, `bugbot.db` by default. Storage is itself a feature — `features/sql_db`,
capability `STORAGE` — which is why `core/` contains no SQL at all and `stats`, `chat_log`,
`levels`, `stream_sessions` and `raw_log` name a dependency rather than an import.

**No feature owns the database; each owns its own tables.** `sql_db` only opens the file and
hands out connections. Every table below is created by the feature that writes it, in its
`init_schema()`.

## Where the file lives

Precedence: `BUGBOT_DB` from the environment → `db_path` in `features/sql_db/sql_db.json` → 
`bugbot.db` in the project folder. Read once at startup, unlike everything else in the JSON.

## Connections

Every call opens its own connection and closes it again. No pool, no shared connection across
threads, no WAL — at the traffic of a single streamer's chat that is unproblematic and much
simpler than the alternatives.

All of it is blocking, so features always call the store through
`loop.run_in_executor(None, ...)`. That is why each feature keeps its SQL in a separate
`store.py`: the store is plain synchronous code, and the feature is the async wrapper.

Schema changes on an existing database go through `add_column_if_missing()` — SQLite has no
`ADD COLUMN IF NOT EXISTS`, so it checks `PRAGMA table_info` first. A database that has been
running since before a column existed picks it up on the next start instead of crashing.

---

## The session stamp

This is the one idea worth understanding before querying anything.

Every recorded row carries the stream session it happened in, and `NULL` means off-stream. So
"overall" and "per stream" are **the same query with and without a `WHERE`**:

```sql
-- messages, all time
SELECT count(*) FROM messages;

-- messages during stream 42
SELECT count(*) FROM messages WHERE stream_session_id = 42;
```

Session-scoped tables: `messages`, `command_usage`, `moderation_actions`, `events`, `ad_breaks`
(these five got the column added by migration), plus `viewer_samples`, `chat_log` and
`eventsub_log`, which had it from the start.

`viewer_samples` and `chat_log` declare it `NOT NULL` — neither means anything off-stream.

---

## Tables

### `stream_sessions` — owned by `stream_sessions` (Twitch)

When a stream ran, under which title and category. Every other table's `stream_session_id` points
here.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | primary key |
| `platform` | TEXT | whose stream it was — so "whose stream is this" is answerable, not assumed |
| `started_at` | TEXT | not null |
| `ended_at` | TEXT | NULL while the stream is running |
| `title` | TEXT | |
| `game_name` | TEXT | |

An open row (`ended_at IS NULL`) is how a restart mid-stream picks the session back up instead of
opening a second one.

### `stream_segments` — owned by `stream_sessions`

Title/category changes within a stream.

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `stream_session_id` | INTEGER not null |
| `title` | TEXT |
| `game_name` | TEXT |
| `started_at` | TEXT default `datetime('now')` |

Index: `(stream_session_id, id)`.

### `messages` — owned by `stats`

One row per accepted message. **The text is not here** — that's `chat_log`, deliberately a
separate feature so it can be left out.

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `platform` | TEXT not null |
| `user_name` | TEXT not null |
| `ts` | TEXT default `datetime('now')` |
| `stream_session_id` | INTEGER (added by migration) |

### `command_usage` — owned by `stats`

| Column | Type |
|---|---|
| `id`, `platform`, `command`, `user_name`, `ts` | as above, `command` TEXT not null |
| `stream_session_id` | INTEGER |

### `moderation_actions` — owned by `stats`

Includes actions taken by *human* moderators, which Twitch reports over EventSub.

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `platform` | TEXT not null |
| `user_name` | TEXT not null |
| `reason` | TEXT not null — `banned_word`, `link_spam`, … |
| `action` | TEXT not null — `delete`, `timeout`, `ban`, `warn`, `unban` |
| `ts` | TEXT |
| `stream_session_id` | INTEGER |

### `events` — owned by `stats`

Typed live events: follows, subs, gift subs, resubs, cheers, raids, hype trains, OBS events.

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `platform` | TEXT not null |
| `event_type` | TEXT not null |
| `user_name` | TEXT not null |
| `amount` | INTEGER default 0 — bits, count, level, or 0 |
| `ts` | TEXT |
| `stream_session_id` | INTEGER |

Indexed on `stream_session_id` and on `event_type`.

Anonymous cheers and gift subs use their own types (`cheer_anon`, `gift_sub_anon`). They count
towards the totals but are excluded from the leaderboards, where "Anonymous" would otherwise sit
permanently on top.

### `viewer_samples` — owned by `stats`

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `stream_session_id` | INTEGER **not null** |
| `viewer_count` | INTEGER not null |
| `ts` | TEXT |

Polled, not evented: Helix has no viewer-count event. Interval is
`timings.viewer_sample_interval` (60s).

### `ad_breaks` — owned by `stats`

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `started_at` | TEXT default `datetime('now')` |
| `duration_seconds` | INTEGER not null |
| `stream_session_id` | INTEGER |

### `highscores` — owned by `stats`

All-time per-stream records. One row per metric, overwritten when beaten.

| Column | Type |
|---|---|
| `metric` | TEXT **primary key** |
| `value` | INTEGER not null |
| `stream_session_id` | INTEGER — which stream set it |
| `achieved_at` | TEXT |

Tracked metrics: `peak_viewers`, `subs_gained`, `bits_cheered`, `follows_gained`,
`hypetrain_level`, `chat_messages`.

The chat record counts everything that was reported. It used to count only Twitch — a platform
that this feature should not know about, and a number that would have been wrong on any
installation with a different mix.

### `chat_log` — owned by `chat_log`

The only place that stores what was actually said.

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `stream_session_id` | INTEGER **not null** |
| `platform` | TEXT not null |
| `user_id` | TEXT |
| `user_name` | TEXT not null |
| `message` | TEXT not null |
| `ts` | TEXT |

Index: `(stream_session_id, id)`.

Subscribes to `MESSAGE`, **not** `MESSAGE_ACCEPTED` — the messages that got deleted are exactly
the ones worth being able to read afterwards. Live-only, because the session column is
`NOT NULL`; recording off-stream would need a schema change, not a setting.

### `eventsub_log` — owned by `raw_log` (Twitch)

Every platform notification verbatim, including ones nothing handles yet.

| Column | Type |
|---|---|
| `id` | INTEGER primary key |
| `stream_session_id` | INTEGER |
| `subscription_type` | TEXT not null |
| `payload` | TEXT not null — the raw JSON |
| `ts` | TEXT |

Indexed on `(stream_session_id, id)` and on `subscription_type`.

This is the fastest-growing table. OBS's contribution can be turned off with
`"raw_events": false` in `obs.json`; the meaningful OBS events still arrive as
`PLATFORM_EVENT`.

Despite belonging to Twitch, it keeps everything it sees — including OBS notifications.
Ownership decides where a feature lives and when it loads, not what it may look at.

### `discord_levels` — owned by `levels` (Discord)

| Column | Type |
|---|---|
| `platform` | TEXT not null |
| `user_id` | TEXT not null |
| `user_name` | TEXT |
| `xp` | INTEGER default 0 |
| `level` | INTEGER default 0 |
| `last_xp_ts` | TEXT |
| | PRIMARY KEY `(platform, user_id)` |

Indexes: a **unique** index on `(platform, user_id)` and one on `xp DESC`. The unique index isn't
just speed — it's the conflict target of the upsert in `add_message_xp`. On an older database the
primary key is still `user_id` alone, and without this index there would be nothing for the
`ON CONFLICT` to point at.

The XP curve is the familiar MEE6 one — the jump from level *n* to *n+1* costs:

```
5n² + 50n + 100
```

---

## Querying it

```bash
sqlite3 bugbot.db
```

A few that come up:

```sql
-- the last ten streams with their headline numbers
SELECT s.id, s.started_at, s.title, s.game_name,
       (SELECT count(*) FROM messages m WHERE m.stream_session_id = s.id) AS msgs,
       (SELECT max(viewer_count) FROM viewer_samples v WHERE v.stream_session_id = s.id) AS peak
FROM stream_sessions s
ORDER BY s.id DESC LIMIT 10;

-- who got moderated most, and for what
SELECT user_name, reason, count(*) AS n
FROM moderation_actions
GROUP BY user_name, reason
ORDER BY n DESC LIMIT 20;

-- read back a stream's chat
SELECT ts, platform, user_name, message
FROM chat_log WHERE stream_session_id = 42 ORDER BY id;

-- EventSub types nothing handles yet
SELECT subscription_type, count(*) FROM eventsub_log GROUP BY 1 ORDER BY 2 DESC;
```

The bot holds no long-lived connection, so reading while it runs is safe. If you intend to
*write*, stop the service first.
