# Extending BugBot

Two APIs, the same shape: a folder, one file, one factory function. Nothing in `core/` or
`bugbot.py` is touched to add either.

- A **platform** is a service the bot acts on — it receives messages, posts things, moderates.
- A **feature** is something the bot *does* — it records, judges, answers, or brings commands.

If you're unsure which you're writing, ask whether it owns a connection to the outside world.
OBS remote control is a feature, not a platform, because it rides the OBS platform's connection
and its commands are typed in someone else's chat.

---

## The Platform API

### The contract

```
platforms/<name>/platform.py   →   create_platform()   →   a core.platform.Platform
```

The folder name and `Platform.name` must match. Startup warns if they don't, because
platform-owned features derive their `owner` from the folder and would otherwise filter on a
name that never matches.

Only two methods are mandatory:

| Method | Contract |
|---|---|
| `async start()` | Bring the platform up. May return as soon as it's running (background tasks keep going) or block for its whole lifetime — `bugbot.py` awaits both the same way under one `gather()`. |
| `async close()` | Shut down cleanly. **Must work even if `start()` never ran or ran halfway** — when another platform crashes, `close()` is still called on everything. |

Everything else is opt-in through `capabilities`:

| Capability | Means | Method to implement |
|---|---|---|
| `CHAT` | can write free text into its main channel | `async send_text(text) -> bool` |
| `ANNOUNCE` | can post structured announcements | `async announce(announcement) -> bool` |
| `STREAM` | reports stream start and end | publishes `STREAM_START` / `STREAM_END` |
| `MODERATE` | filters its own incoming messages | calls the `MODERATION` feature and carries out the `Verdict` |

The base class implements the optional ones as "can't do that" (`return False`), so a platform
that only listens is still a valid platform.

There is one more hook worth knowing:

```python
async def wait_ready(self):
    """Wait until the platform can accept announcements. Default: returns immediately."""
```

Discord overrides it to wait for `on_ready` — before that it doesn't know its guilds and would
discard every announcement silently. This exists for one specific bug: restarting the bot
mid-stream triggers a live reconciliation that announces immediately, into a bot that isn't
logged in yet. OBS deliberately does **not** override it, because the streaming PC is usually
off when the bot starts and waiting would hang everything else.

### What the platform must publish

A platform earns its keep by putting things on the bus. At minimum, if it has a chat:

```python
await events.bus.publish(events.MESSAGE, message=msg)          # before moderation
await events.bus.publish(events.MESSAGE_ACCEPTED, message=msg) # after it passed
```

where `msg` is a `core.feature.Message`:

```python
Message(
    platform="myservice",     # must equal Platform.name
    user_id="123",
    user_name="someone",
    text="hello",
    is_privileged=False,      # broadcaster/mod/admin — exempt from moderation, may use mod commands
    is_subscriber=False,      # exempt from the pure spam heuristics, not from banned words
    command="!uptime",        # filled when the message was recognised as a command
    arg_text="",              # everything after the command word
    raw=original_object,      # the platform-specific original; features should mostly ignore it
)
```

See [Architecture](architecture.md#the-bus) for the full topic list.

### Rendering an announcement

An `Announcement` is deliberately only as structured as everything can display: a kind, title,
text, url, image, colour, and some named fields.

```python
@dataclass(frozen=True)
class Announcement:
    kind: str            # one of STREAM_ONLINE, STREAM_OFFLINE, BUG_REPORT, CLIP, STATUS
    title: str
    text: str = ""
    url: str = ""
    image_url: str = ""
    color: int = 0x3498DB
    source: str = ""     # the platform that triggered it
    author: str = ""     # the user who triggered it
    highlight: bool = False   # important enough to notify — Discord turns this into @everyone
    log: bool = False         # also into the platform's mod log, if it keeps one
    fields: tuple = ()        # tuple of Field(name, value, inline)
```

Your `announce()` decides whether it renders that `kind` at all, and returns `True` only if it
actually posted — the count is what tells `!bug` the report landed somewhere.

For text-only platforms, `announcement.as_text(max_fields=N)` flattens the whole thing to one
line. The `max_fields` cap is not cosmetic: an IRC line is limited to roughly 500 characters, and
a stream closing report has more fields than that allows.

### Minimal example

```python
# platforms/myservice/platform.py
from core import events, platform as platform_api

class MyPlatform(platform_api.Platform):
    name = "myservice"
    capabilities = frozenset({platform_api.CHAT})

    async def start(self):
        self._conn = await connect()
        async for raw in self._conn:
            await events.bus.publish(events.MESSAGE, message=to_message(raw))

    async def close(self):
        if getattr(self, "_conn", None):
            await self._conn.close()

    async def send_text(self, text):
        await self._conn.send(text)
        return True

def create_platform():
    return MyPlatform()
```

Add `platforms/myservice/myservice.json` and it gets hot-reloaded config for free — see
[Configuration](configuration.md).

### Bringing a credentials check

Optional, and discovered the same way the platform itself is: add
`platforms/myservice/credentials.py` with a `check()` that yields `(level, message)` pairs,
where level is `ok`, `warn`, `fail`, `skip` or `detail` (a continuation line).

```python
def check():
    token = os.environ.get("MYSERVICE_TOKEN", "").strip()
    if not token:
        yield "skip", "MYSERVICE_TOKEN nicht gesetzt - die Plattform würde nicht laden."
        return
    ...
    yield "ok", "Token gültig."
```

`check_credentials.py` picks it up with no changes to itself. Two rules worth keeping:

- **Read the environment directly**, don't import your own `config.py`. That module uses
  `os.environ[...]`, which raises on exactly the missing variable you're trying to report.
- **Read-only calls only.** People run this to diagnose, sometimes against production.

---

## The Feature API

### The contract

```
features/<name>/feature.py                     →  create_feature()  →  a core.feature.Feature
platforms/<p>/features/<name>/feature.py       →  same, but owned by platform <p>
```

Put it in `features/` if it uses only topics any platform can publish. Put it under a platform
if it lives on events only that service emits — it will then be discovered only when that
platform is. See [Architecture](architecture.md#where-a-feature-lives-says-what-it-depends-on).

Nothing on the base class is mandatory. A feature that only listens overrides `setup()`; one
that only contributes commands overrides `commands()`.

### Class attributes

| Attribute | Meaning |
|---|---|
| `name` | short unique name; the key in the bus's feature directory |
| `provides` | frozenset of capabilities this feature offers |
| `requires` | capabilities it needs from other features. **Missing → the feature is skipped** |
| `optional` | capabilities it takes along if present. Affects ordering only, never whether it loads |
| `platform_capabilities` | what a *platform* must be able to do for its messages to concern this feature. Empty = all |
| `config` | its own `LiveConfig`, or None. Setting one buys texts *and* command renaming for free |
| `owner` | set by the registry from the folder path — don't set it yourself |
| `bus` | set by the registry before `setup()` — don't set it yourself |

The capabilities a feature can provide or require:

| Capability | Meaning |
|---|---|
| `STORAGE` | persistent storage for other features (`features/sql_db`) |
| `RECORDING` | writes down what happens on the platforms |
| `MODERATION` | returns a `Verdict` for a message |
| `STATS` | answers metric queries |
| `LEVELS` | XP/level per user |
| `SESSIONS` | knows the running stream session |
| `CHAT_LOG` | keeps the full message text |
| `RAW_LOG` | keeps platform notifications verbatim |
| `VARIABLES` | fills the `{placeholders}` in static commands |

### Methods

| Method | When |
|---|---|
| `async setup(bus)` | once at startup, **before platforms start**: create tables, subscribe to topics, restore state. Look up your `requires` here via `bus.feature_with(...)` |
| `async close()` | at shutdown. Must work even if `setup()` never finished |
| `commands()` | return a tuple of `Command` |
| `handles(platform_name)` | "does a report from this platform concern me?" — resolves `owner` / `platform_capabilities` |

### Commands

```python
Command(name="!rank", handler=self.cmd_rank, mod_only=False, help="show your level")
```

The handler takes a `Message` (with `command` and `arg_text` filled) and returns a string, an
`Announcement`, or None. Discord renders an `Announcement` as an embed and Twitch as one chat
line — from the same handler. That is why `!rank`, `!top`, `!highscores`, `!streamstats` and
`!leaderboard` work on every platform without anyone writing Discord or Twitch code.

You do **not** need to handle renaming: if your feature has a `config`, the bus applies its
`command_names` section when collecting, and hands back a `Command` carrying the *actual* name.

### Verdicts

A `MODERATION` feature returns this, and the platform carries it out:

```python
Verdict(
    reason="banned_word",       # machine-readable
    label="unerlaubtes Wort",   # human-readable, for chat and log
    detail="",                  # optional: the match itself
    delete=True,
    timeout_seconds=0,          # 0 = no timeout
    violation_count=1,
)
```

The escalation — how many violations before a timeout, and how long — lives in the feature, not
in each platform. See [Moderation](moderation.md).

### Minimal example

```python
# features/greeter/feature.py
from core import events, feature as feature_api, runtime_config

DEFAULTS = {"texts": {"hello": "👋 Hallo {user}!"}}

class GreeterFeature(feature_api.Feature):
    name = "greeter"
    provides = frozenset()
    requires = frozenset()

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)

    async def setup(self, bus):
        bus.subscribe(events.MESSAGE_ACCEPTED, self.on_message)

    async def on_message(self, message):
        ...

    def commands(self):
        return (feature_api.Command("!hello", self.cmd_hello),)

    async def cmd_hello(self, message):
        return self.config.text("hello", user=message.user_name)

def create_feature():
    return GreeterFeature()
```

Add `features/greeter/greeter.json` next to it and every text and command name becomes editable
at runtime.

---

## Blocking work

Everything on the bus is async, and the whole bot runs in one event loop. Anything blocking —
SQLite, `requests` — must go through an executor, the way every store does it:

```python
@staticmethod
async def _run(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)
```

Features keep their SQL in a separate `store.py` for exactly this reason: the store is plain
blocking code with no async in it, and the feature is the async wrapper.

---

## Before you commit

```bash
python3 check_config.py
```

It parses every JSON, checks that each `text()` call has a key and that the placeholders in the
text match what the code passes, lists texts nobody uses any more, and catches a `command_names`
entry naming a command that doesn't exist or one that collides. Silence plus one ✅ means the
edit is sound.

Adding a Twitch feature that needs a new OAuth scope? See [Twitch](twitch.md#scopes) — the scope
list is code, and existing tokens are never widened retroactively.
