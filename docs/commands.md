# Command reference

Every command name here is the **default**. All of them can be renamed, aliased or switched off
per installation — see [Configuration → Command names](configuration.md#command-names).

Commands come from three places, which is why "where does it work" varies:

| Source | Works on |
|---|---|
| **Feature commands** — declared by a feature | every platform with a chat, automatically |
| **Platform commands** — code in a platform package | that platform only |
| **Static commands** — plain text in the platform's JSON | that platform only |

Adding, changing, renaming and deleting a static command all take effect while the bot runs — no
restart. Their text can carry `{placeholders}`: `{u}` for the caller, `{time}`, and whatever you
define yourself, including short Python expressions. See
[Configuration → Variables](configuration.md#variables).

`!commands` lists only what the caller may actually use: a non-moderator never sees mod commands
suggested, because trying one would just get the message deleted.

---

## Public

### Everywhere (feature commands)

| Command | Does |
|---|---|
| `!rank [@user]` | XP and level of a user. Without an argument it needs to know *you* — which only works on Discord, since a Twitch chatter can't be mapped to a Discord account. Naming a user works from either chat |
| `!top` | the level leaderboard (top 10 by default) |
| `!streamstats` | metrics of the running stream, or the last finished one if currently offline |
| `!highscores` | all-time per-stream records |
| `!leaderboard` | top cheerers and top gifters, all-time |
| `!companion <text>` | make your [companion](companion.md) say something on the OBS companion page — costs `min_bits_to_speak` bits per shown message (default 100) from your cheered-minus-spent balance (mods/broadcaster exempt), filtered by moderation either way. Subscribers (and mods/the broadcaster) only — everyone else's chat still works, they just have no companion to speak through |
| `!companion set <hash>` | give your [companion](companion.md) a custom look instead of one based on your name — costs `min_bits_to_set_seed` bits per change (default 300; mods/broadcaster exempt), from the same balance as `!companion <text>` |
| `!vtubbi` | link to the [companion](companion.md) project |

The level scores are always the Discord ones — there is no other set. Anonymous cheers and gift
subs count towards the totals but are kept out of the leaderboards, where "Anonymous" would
otherwise sit permanently on top.

### Twitch

| Command | Does |
|---|---|
| `!commands` / `!help` | lists the commands the caller may use |
| `!bug <text>` / `!report <text>` | files a bug report — see [below](#cross-platform-bug-reports) |
| `!rules` | the `rules` text from `twitch.json` |
| `!uptime` | how long the stream has been live |
| `!followage` | how long the caller has followed |
| `!chatters` | current chatter count |
| `!subs` | subscriber count |
| `!bits` | bits leaderboard |
| `!hypetrain` | current or last hype train |
| `!clip` | creates a clip; also posted to the Discord clip channel |
| `!time` | the current time — a static command using `{time}`, see below |
| `!discord`, `!lurk`, `!chef`, `!socials` | static text from `twitch.json` |

### Discord

| Command | Does |
|---|---|
| `!bug <text>` / `!report <text>` | files a bug report |
| `!rules` | the `rules` text from `discord.json` |
| `!roles` | the reaction-role overview |
| `!chef` | static text from `discord.json` |

---

## Moderator-only

On Twitch that means broadcaster or moderator; on Discord the configured moderator role, or
Administrator. A non-moderator trying one on Twitch gets the message deleted and a reply saying
so, rather than silence.

### Twitch

| Command | Usage | Does |
|---|---|---|
| `!title` | `!title <new title>` | change the stream title |
| `!game` | `!game <category>` | change the category |
| `!so` | `!so <username>` | send a shoutout |
| `!raid` | `!raid <channel>` | start a raid |
| `!slow` | `!slow <seconds\|off>` | slow mode |
| `!timeout` | `!timeout <user> <seconds> [reason]` | time a user out |
| `!ban` | `!ban <user> [reason]` | permanent ban |
| `!unban` | `!unban <user>` | lift a ban or timeout |
| `!warn` | `!warn <user> <reason>` | issue an official Twitch warning |
| `!poll` | `!poll <title> ; <choice 1> ; <choice 2> [; …]` | start a poll, max 5 choices |
| `!prediction` | `!prediction <title> ; <outcome 1> ; <outcome 2> [; …]` | start a prediction, max 10 outcomes |
| `!vip` | `!vip <add\|remove> <user>` | manage VIPs |
| `!mod` | `!mod <add\|remove> <user>` | manage moderators |
| `!approve` | `!approve <key>` | allow a message held by AutoMod |
| `!deny` | `!deny <key>` | reject a message held by AutoMod |
| `!giveaway` | `!giveaway start <points> <title>` | channel-points giveaway; entry is a reward redemption |
| | `!giveaway pick` | draw a winner and refund the losers automatically |
| | `!giveaway cancel` | cancel and refund everyone |

### Discord

| Command | Usage | Does |
|---|---|---|
| `!announce` | `!announce <text>` | post to the announcements channel |
| `!warn` | `!warn @user <reason>` | warn a member |
| `!purge` | `!purge <1-100>` | bulk-delete messages |
| `!slowmode` | `!slowmode <seconds, 0 = off>` | slow mode |
| `!timeout` | `!timeout @user <minutes> [reason]` | time a member out |
| `!ban` | `!ban @user [reason]` | ban a member |
| `!unban` | `!unban <user id>` | lift a ban |
| `!setup` | `!setup confirm` | build the server structure — **see the warning** |

> ### ⚠️ `!setup` deletes every channel except the one it is called in
>
> Run it on a new or empty server only. Two safety catches: it requires real **Administrator**
> rights — the moderator role is not enough — and it must be confirmed with `!setup confirm`. A
> bare `!setup` only prints the warning. See [Discord](discord.md#the-setup-blueprint) for what
> it builds.

### Everywhere

| Command | Does |
|---|---|
| `!stats` | totals across all platforms, on demand — the same report Discord posts hourly |
| `!obs` | OBS status: scene, stream/recording state, dropped frames, performance |
| `!scene` | `!scene <name>` switches (partial names work), bare `!scene` lists them |
| `!rec` | `!rec start\|stop\|pause\|resume`, or bare to show the state |
| `!replay` | save the replay buffer (must be running in OBS) |
| `!obssource` | `!obssource <name> on\|off` — show/hide a source, in all scenes and groups |

The OBS commands exist only when OBS is connected — see [OBS](obs.md). Without a connected relay
they answer "OBS is not connected" rather than failing.

---

## Things that need no command

**Live alerts.** Follow, sub, gift sub, resub, cheer and raid are posted to Twitch chat as the
EventSub events arrive. Follows arriving close together are batched into one line so a follow
raid doesn't flood the chat.

**The hourly status report** on Discord, controlled by `status_report_hours` in `discord.json`
(`0` disables it).

**Level-ups**, announced by the `levels` feature, optionally granting a role via
`levels.role_thresholds`.

**The ad panel.** When Twitch reports a commercial, OBS shows the configured source for exactly
that long and hides it again. Neither platform knows the other exists — one publishes that ads
are running, the other happens to listen.

<a id="cross-platform-bug-reports"></a>
## Cross-platform bug reports

`!bug` is the clearest example of the announcement bus: typed in Twitch chat, it becomes a
neutral `Announcement` that Discord renders as an embed in the bug-report channel. The reply in
Twitch chat depends on whether it landed anywhere at all — `announce()` returns how many
platforms actually posted it, so if Discord is down the user is told rather than thanked.
