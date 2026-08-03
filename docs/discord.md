# Discord

The Discord platform carries `ANNOUNCE` and `MODERATE`, but **not** `CHAT` — it has no single
"main channel" that free text would belong in, so everything it posts goes somewhere specific.
It owns the `levels` feature.

```
platforms/discord/
  platform.py    DiscordPlatform — implements the Platform API
  bot.py         roles, permissions, honeypot, moderation, status report, !setup
  config.py      credentials from .env
  discord.json   roles, channels, texts, the setup blueprint, moderation overrides
  features/
    levels/      XP per message, !rank and !top
```

## Setting up the app

1. [Developer Portal](https://discord.com/developers/applications) → new application → **Bot** →
   copy the token into `DISCORD_TOKEN`.
2. Enable the **Server Members** and **Message Content** privileged gateway intents. Both are
   required; without them the bot sees no message text and no joins.
3. **OAuth2 → URL Generator** → scope `bot`, plus permissions: manage roles, manage channels,
   ban members, moderate members (timeout), manage messages, send messages, embed links. Invite
   with the generated URL.

The bot's own role must sit **above** any role it is expected to hand out or remove. Discord
refuses role changes from below, and it will look like the bot is ignoring you.

---

## Names are the configuration

> Roles and channels are found **by their names**, as literal strings from `discord.json`. Get
> `roles.moderator` wrong and nobody on that server is a moderator — the mod commands then do
> nothing, silently. This is the first thing to change on a new server.

| Key | Default | Used for |
|---|---|---|
| `roles.moderator` | `🛡️ Moderator` | who may use mod commands |
| `roles.member` | `👥 Mitglied` | the baseline role |
| `channels.log` | `bugbot-reports` | the mod log |
| `channels.commands` | `🤖-bot-befehle` | where bot commands are expected |
| `channels.honeypot` | `🍯-honey-pot` | the anti-bot trap, see below |
| `channels.announcements` | `📢-ankündigungen` | target of `!announce` |
| `channels.role_selection` | `🎭-rollen-auswahl` | where the reaction-role message lives |
| `clip_channel` | `🎬-clips` | where `!clip` results are posted |

An **empty** name is a supported way to switch a function off without deleting the channel.

### Announcement routing

`announce_channels` maps an announcement kind to a channel name:

```json
"announce_channels": {
  "stream.online":  "🎥-stream-live",
  "stream.offline": "🎥-stream-live",
  "bug.report":     "🐛-bug-reports",
  "clip.created":   "🎬-clips",
  "status":         "bugbot-reports"
}
```

This is the whole of what makes `!bug` typed in **Twitch** chat land in a Discord channel: Twitch
publishes a neutral `Announcement`, Discord looks up the kind here and renders an embed. Neither
platform imports the other.

An announcement with `highlight` set becomes an `@everyone` — reserved for real events like a
stream going live.

### Reaction roles

`reaction_roles` maps an emoji to a role name:

```json
"reaction_roles": { "🎮": "🎮 Gamer", "🧪": "🧪 Testsubjekt", "💻": "🧙‍♂️ Code-Magier" }
```

Reacting to the role-selection message grants the role, removing the reaction takes it away.
`!roles` prints the overview. The role names here must match real roles — same rule as above.

---

<a id="the-setup-blueprint"></a>
## The `!setup` blueprint

> ### ⚠️ `!setup` deletes every channel except the one it is called in
>
> Run it on a new or empty server only. Two safety catches: it requires real **Administrator**
> rights — the moderator role alone is not enough — and it must be confirmed with
> `!setup confirm`. A bare `!setup` only prints the warning.

The whole server structure comes from the `setup` section of `discord.json`, not from a script.
Editing that section is how you change what gets built.

| Key | Holds |
|---|---|
| `roles` | roles to create, with colours |
| `permission_profiles` | named permission sets, referenced by categories |
| `categories` | categories, each with a permission profile and its channels |
| `rules_channel` | where the rules text gets posted |
| `roles_channel` | where the role-selection message gets posted |
| `role_selection_message` | that message's text |

A permission profile is a small object describing `everyone` and then per-role overrides:

```json
"gamer_only": {
  "everyone": { "read": false },
  "roles": {
    "🎮 Gamer":       { "read": true, "send": true },
    "👑 Admin / Dev": { "read": true, "send": true },
    "🛡️ Moderator":  { "read": true, "send": true }
  }
}
```

The shipped blueprint builds five categories — Welcome & Info (admin-writable), Community &
Gaming (members write), Game Corner (gamers only), Game Development (members write), and an
internal team-only category — with seven roles from Admin down to Member.

---

## The honeypot

`channels.honeypot` is a channel no human has a reason to post in. Anyone who does is banned
immediately, with a day of their messages deleted, and the ban is written to the mod log.

It works because self-bot spam waves post in every channel they can see. Leave the channel
visible but obviously off-limits, and don't mention it in the rules. Setting the name to empty
disables it.

---

## Moderation

Discord's moderation is the shared feature — same filters, same escalation, same word list as
Twitch — with per-server overrides in the `moderation` section of `discord.json`. Timeouts are
carried out with `Member.timeout()`, which needs the **Moderate Members** permission.

Admins and the moderator role are exempt, decided in the feature rather than here. See
[Moderation](moderation.md).

---

## Levels

`levels` is owned by Discord because the XP belongs to that server, and so do the roles that
hang off it. Its commands, though, are feature commands: `!rank` and `!top` work from Twitch
chat too.

| Key in `levels.json` | Default | Meaning |
|---|---|---|
| `xp_min`, `xp_max` | 15, 25 | XP granted per qualifying message, picked at random in this range |
| `xp_cooldown_seconds` | 60 | no XP again within this window — the anti-spam measure |
| `announce_level_up` | `true` | post when someone levels up |
| `top_limit` | 10 | entries in `!top` |

XP is granted on `MESSAGE_ACCEPTED`, so a message that got moderated away earns nothing. The
curve is the familiar MEE6 one: going from level *n* to *n+1* costs `5n² + 50n + 100` XP.

Role rewards live in `discord.json`, not here, because they're server structure:

```json
"levels": { "role_thresholds": { "5": "🎮 Gamer", "10": "🧪 Testsubjekt" } }
```

The level is a **string** key. The role must exist and sit below the bot's own role.

`!rank` without an argument needs to know who you are, which only works on Discord — a Twitch
chatter can't be mapped to a Discord account. Naming a user works from either chat.

---

## The status report

`status_report_hours` in `discord.json` controls the automatic status post; `0` disables it. The
same report is available on demand as `!stats`. It is produced by the `stats` feature, so its
labels and lines live in `stats.json`, not in `discord.json`.

## Checking it

```bash
python3 check_credentials.py discord
```

Validates the token, reports whether the two privileged intents are actually switched on in the
developer portal, lists the servers the bot is on — and then compares the role and channel names
in `discord.json` against what really exists on each of them.

That last check is the point: a wrong name here breaks a function *silently*, and it is the most
common thing to get wrong when adapting the bot to a new server.

## See also

- [Commands](commands.md#discord) — every Discord command and its arguments
- [Configuration](configuration.md#platformsdiscorddiscordjson) — every key in `discord.json`
- [Moderation](moderation.md) — the filters and the escalation
