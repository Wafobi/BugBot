# BugBot documentation

The [main README](../README.md) is the short version: what BugBot is, how to get it running,
and the command list. These pages are the long version — the reasoning behind the design and
the reference material you need when changing something.

## Understanding it

| Page | What's in it |
|---|---|
| [Architecture](architecture.md) | How the pieces fit: the bus, the registry, the startup sequence, and why nothing imports across package boundaries |
| [Extending it](extending.md) | The Platform API and the Feature API in full — and how to add one of each |
| [Moderation](moderation.md) | The filters, the thresholds, the escalation from delete to timeout |
| [Database](database.md) | Every table, every column, and how the stream session stamps them |

## Reference

| Page | What's in it |
|---|---|
| [Configuration](configuration.md) | Every JSON file and every key in it, plus how the layered fallback works |
| [Commands](commands.md) | Every command, its arguments, where it works, and who may use it |

## Per platform

| Page | What's in it |
|---|---|
| [Twitch](twitch.md) | OAuth scopes and tokens, EventSub, the IRC client, staying connected |
| [Discord](discord.md) | Roles and channels, the `!setup` server blueprint, reaction roles, levels |
| [OBS](obs.md) | The relay that dials in, the two secrets, and what the bot does with the connection |

## Running it

| Page | What's in it |
|---|---|
| [Deployment](deployment.md) | Podman, systemd Quadlet, the update scripts, logs, and what to run after changing what |
| [The SSH tunnel](tunnel.md) | How the OBS machine reaches the bot's two loopback listeners, and the systemd user service that keeps it up |

---

## Where to start

- **Adapting BugBot to your own server** → [Configuration](configuration.md), then
  [Discord](discord.md) for the role and channel names, which are load-bearing.
- **Adding a feature or a platform** → [Extending it](extending.md), with
  [Architecture](architecture.md) for the context.
- **Something isn't working** → run `python3 check_credentials.py`, then
  [Deployment](deployment.md#troubleshooting).
- **Querying the recorded data** → [Database](database.md).
