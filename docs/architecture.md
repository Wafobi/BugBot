# Architecture

BugBot is built around one rule: **no package imports another package it isn't inside.**
`platforms/twitch/` does not import `platforms/discord/`, no platform imports a feature, and
`core/` imports neither. Everything that crosses a boundary goes through the bus in
`core/events.py`.

That rule is not decoration. It is what makes a Discord-only run, a Twitch-only run, and a run
with OBS attached all the same program with different parts discovered.

## The three layers

```
bugbot.py     names nothing — finds, starts and stops whatever the registry hands it
core/         contracts and wiring only: no moderation rules, no SQL, no Discord, no Twitch
platforms/    where the bot acts: one folder per service
features/     what the bot does: one folder per capability
```

`core/` holds five modules and no behaviour:

| Module | Holds |
|---|---|
| `platform.py` | the Platform API: the base class, the four capability flags, and the neutral `Announcement`/`Field` render types |
| `feature.py` | the Feature API: the base class, the eight capability flags, and the neutral `Message`/`Verdict`/`Command` types |
| `events.py` | the bus: the topic vocabulary, publish/subscribe, `announce()`, and the registry of live platforms and features |
| `registry.py` | discovery: finds the packages, builds them, wires them in dependency order |
| `runtime_config.py` | the hot-reload JSON loader (`LiveConfig`) |

## Startup

`bugbot.py` is 70 lines and mentions no service by name:

1. **`load_dotenv()`** — before anything else, because discovery reads `BUGBOT_PLATFORMS` and
   `BUGBOT_FEATURES` from the environment and the packages are imported after that.
2. **Features load first, fully set up.** `registry.load_features()` imports each
   `feature.py`, calls `create_feature()`, sorts them into dependency order, and awaits each
   `setup()`. Features subscribe to their topics there.
3. **Then platforms.** `registry.load()` imports each `platform.py`, calls
   `create_platform()`, and registers the result on the bus.
4. **Then everything starts** under one `asyncio.gather(*(p.start() for p in platforms))`.

The order matters and is the reason it's written down: if platforms started first, the first
message to arrive after startup would be published to a bus nobody had subscribed to yet.

Shutdown reverses it. On SIGINT/SIGTERM the bot closes **platforms first** — they stop
reporting — and **features second**, so they can still write down what was already reported.
Each `close()` is individually guarded: one part choking on shutdown must not leave the others
open.

### What happens when something is broken

Deliberately asymmetric:

- **A platform that fails to load** — missing token, broken import — is skipped with a warning
  and the others keep running. If *none* loads, startup fails: the bot would have nothing to do.
- **A feature that fails to load or set up** is skipped with a warning. Loading no features at
  all is a valid configuration — a bot that only moderates and records nothing still works.
- **A subscriber that raises** during `publish()` takes down neither the publisher nor the other
  subscribers; the error is printed and the loop continues.

## The bus

Three ways through `core/events.py`, and which one applies is a question of whether the caller
needs an answer.

### Push — `bus.publish(topic, **payload)`

"This happened." The publisher does not know who is listening, or whether anyone is. All
recording works this way: a platform reports that a message arrived; whether a `stats` feature
counts it or a `chat_log` feature keeps its text is not the platform's business.

`publish()` returns the list of what the subscribers returned, so a feature can *answer* a
topic. That is how `STREAM_END` hands the finished stream's numbers back to the platform that
reported the stream ended.

The topic vocabulary is deliberately neutral — "an event with a type and an amount", not "a
Twitch cheer":

| Topic | Payload | Fires when |
|---|---|---|
| `MESSAGE` | `message` | every incoming message, **before** moderation |
| `MESSAGE_ACCEPTED` | `message` | the message passed moderation |
| `COMMAND` | `platform`, `command`, `user_name` | a command ran |
| `MOD_ACTION` | `platform`, `user_name`, `reason`, `action` | delete / timeout / ban / warn / unban, by bot or human |
| `PLATFORM_EVENT` | `platform`, `event_type`, `user_name`, `amount` | follow, sub, gift sub, cheer, raid, hype train, … |
| `RAW_EVENT` | `platform`, `event_type`, `payload` | any platform notification, verbatim — including ones nothing handles |
| `STREAM_START` | `platform`, `title`, `category` | the stream went live |
| `STREAM_END` | `platform` | the stream ended; subscribers return the closing metrics |
| `SESSION_ENDED` | `session_id` | the session row is closed and its id is final |
| `STREAM_SEGMENT` | `platform`, `title`, `category` | title or category changed mid-stream |
| `VIEWERS` | `platform`, `count` | a viewer-count sample |
| `AD_BREAK` | `platform`, `duration_seconds` | a commercial started |
| `LEVEL_UP` | `message`, `level` | a user gained a level |

`MESSAGE` versus `MESSAGE_ACCEPTED` is a real distinction: the full chat log subscribes to
`MESSAGE`, because the messages that later got deleted are exactly the ones you want to be able
to read afterwards. Anything that shouldn't reward a violation — message counters, XP —
subscribes to `MESSAGE_ACCEPTED`.

`STREAM_END` and `SESSION_ENDED` are two topics for one moment, and that is on purpose. A
session's id is only settled once the row is closed, so whoever evaluates the finished stream
(highscores, the closing report) has to run *after* the close. Splitting it means neither
depends on subscriber order.

### Announce — `bus.announce(announcement)`

"Post this wherever you can." A neutral `Announcement` goes to every platform carrying the
`ANNOUNCE` capability, and each decides for itself how — or whether — to render that `kind`.
Discord builds an embed, Twitch flattens it to one chat line via `as_text()`, OBS writes it into
a text source.

It returns **how many platforms actually posted it**, which is how `!bug` knows whether the
report landed anywhere at all. It also publishes under the topic `announcement.kind`, so one
event is reported once.

The source platform is not excluded: a `!bug` typed in Discord chat should land in the Discord
bug channel. Whether a platform repeats its own announcements is that platform's decision, made
in its `announce()`.

### Pull — `bus.feature_with(capability)` / `bus.commands()`

The directory. Used where a caller needs an answer before it can continue. Moderation is the
case that matters: the platform asks the feature for a `Verdict` (delete? timeout, how long?
which violation number?) and then carries it out, because only the platform knows how to delete
and mute on its own service.

`bus.commands()` collects every feature's commands into one `{name: Command}` map, which
platforms hang into their own command resolution without knowing any feature. The result is
cached — it runs per chat message — and the cache expires as soon as any feature's config is
reloaded, so renaming a command doesn't become the one change that needs a restart.

## Discovery

Two conventions, one mechanism:

```
platforms/<name>/platform.py   with create_platform() -> core.platform.Platform
features/<name>/feature.py     with create_feature()  -> core.feature.Feature
```

Create the folder, write the one file. Neither `bugbot.py` nor `core/` is touched.

`BUGBOT_PLATFORMS` and `BUGBOT_FEATURES` (comma-separated) restrict what loads without deleting
anything. An entry naming something that doesn't exist is reported rather than silently ignored.

### Where a feature lives says what it depends on

Features are found in two places, and the location carries meaning:

- **`features/<name>/`** — neutral. Uses only topics any platform can publish, so it works
  whatever is running: `moderation`, `stats`, `chat_log`, `sql_db`.
- **`platforms/<p>/features/<name>/`** — platform-owned. Lives on events only that one service
  emits, and is discovered *only when its platform is*. Twitch owns `stream_sessions` (nothing
  else has a stream) and `raw_log` (EventSub notifications); Discord owns `levels`; OBS owns
  `obs_control`.

`obs_control` is the clearest case for why this is a feature and not platform code: OBS has no
chat to type `!scene` into. As a feature its commands mount on Twitch and Discord alike, without
either of them knowing OBS exists.

Past discovery the distinction disappears — a platform-owned feature registers on the same bus,
announces capabilities the same way, and a neutral feature may `require` one. Ownership settles
*where a thing lives and when it loads*, not what it sees: the bus delivers every topic to
everyone, so `levels` filters for Discord messages itself.

### Dependency order

`requires` names capabilities a feature needs from *other* features. The registry sorts so that
each comes after its providers, and **skips any whose needs nothing provides** — a half-working
feature (`stats` with no storage) is worse than no feature.

`optional` names capabilities a feature takes along *if they exist*. These affect ordering only,
never whether it loads. Without it you'd get the mirror image of the problem `requires` solves:
`setup()` would run before the optional provider registered, and the directory lookup would
quietly come back empty. `stats` marks `SESSIONS` optional — it takes the stream session if
Twitch is running, and keeps counting with `stream_session_id` NULL if not.

The sorter is a repeated selection round rather than a real topological sort. With the handful
of features a chat bot has, that's easier to read, and a cycle shows up just as clearly:
something is left over that never becomes ready.

## How a feature talks about platforms

A feature that needs to say *which* platform it means has three ways, and none is writing a
service name into the code:

| Question | Mechanism | Example |
|---|---|---|
| Whose feature am I? | `Feature.owner`, set by the registry from the folder path | `platforms/discord/features/levels` → `"discord"` |
| What *kind* of platform concerns me? | `Feature.platform_capabilities`, resolved against the loaded platforms | "the one that reports a stream" = `{STREAM}` |
| Which one is this row about? | recorded in the data | `stream_sessions.platform`, `messages.platform` |

Because the folder name is load-bearing, startup prints a warning if a platform's `name`
disagrees with its directory — otherwise a platform-owned feature would filter on a name that
never matches and silently do nothing forever.

Capabilities are the vocabulary in config too, not just in code. `chat_log.json` takes
`["stream"]` where it once took `["twitch"]`: the same meaning today, still correct if the
streaming service changes, and `resolve_platforms()` warns about a name no loaded platform
carries instead of letting it quietly never match.

### What this replaced

`PLATFORMS = ("twitch",)` in `chat_log`, `PLATFORM = "discord"` in `levels`, a
`twitch_messages` column in the statistics. All three looked harmless and all three failed the
same way: silently, on an installation the author didn't have.

The same reasoning applies to the session guard. Nothing used to stop a second platform's
`STREAM_START` from opening a rival session for the same evening — the rule against it existed
only as a comment. Now `stream_sessions` ignores stream events that aren't its owner's, so a
platform that grows a `STREAM` capability later cannot corrupt someone else's sessions.

## See also

- [Extending it](extending.md) — the two APIs in full, and how to add one
- [Configuration](configuration.md) — how `LiveConfig` layers defaults, baseline and file
- [Database](database.md) — what the recording features actually write
