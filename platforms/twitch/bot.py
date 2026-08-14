import asyncio
import json
import random
import websockets
from datetime import datetime, timedelta, timezone
from core import events
from core import feature as feature_api
from core import platform as platform_api
from . import config
from . import commands as twitch_commands_file
from . import api as twitch_api
from . import scopes as twitch_scopes

_reader = None
_writer = None
_listener_task = None
_eventsub_task = None
_token_task = None
_viewer_task = None
_reconcile_task = None

# Set as soon as Twitch has confirmed the IRC sign-in with "001 Welcome, GLHF!", and cleared
# again when the connection is lost - start_twitch_bot waits for it before writing the startup
# message into the chat.
_connected = asyncio.Event()

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"

# All timings live in twitch.json under "timings" and are explained there; only the access is
# here. Reading them at the point of use rather than freezing them at import time is the reason
# a change takes effect without a restart - it applies on the loop's next pass.
#
# What they are for, in short: Twitch itself only pings about every 5 minutes, and when the
# connection quietly drops away (router/firewall timeout, network change) not even a FIN
# arrives - recv() then blocks forever. Without a sign of life of its own the bot does not
# notice for days that it has gone deaf (irc_ping_interval). Twitch user tokens are usually
# valid only ~4h; if the token expires during a break between streams, every reconnect fails
# and Twitch revokes all EventSub subscriptions (token_check_interval/token_refresh_margin).
# And if EventSub's session_keepalive stops coming, the session is dead -
# eventsub_keepalive_grace is the buffer against network jitter.


def timing(key, default):
    return TWITCH_CONFIG.section("timings").get(key, default)

BROADCASTER_ID = None
MODERATOR_ID = None

# Short, incrementing key -> the real AutoMod msg_id, so mods can type "!approve 3" in chat
# instead of the long Helix msg_id. Filled by handle_automod_hold and consumed by
# !approve/!deny (see TWITCH_BOT_MOD_COMMANDS below).
_automod_queue = {}
_automod_queue_counter = 0

# State of the running channel points giveaway (see !giveaway below), or None when none is
# running. entries maps redemption_id -> (user_id, user_name) and is filled by
# handle_reward_redemption.
_giveaway = None

# Runs during an ad break and reports its end (see handle_ad_break_begin).
_ad_break_task = None

# Follows are collected rather than answered individually: a follow-bot surge would otherwise
# produce one chat message per follow and break Twitch's limit of 100 messages per 30 seconds -
# Twitch then silently discards them and may close the connection. Every single follow is still
# recorded; only the announcement is bundled. Window and number of names: twitch.json,
# "timings".
_pending_follows = []
_follow_batch_task = None

# Is a stream running? Maintained by _go_live/_go_offline, both triggered via
# stream.online/stream.offline (EventSub) resp. the live reconciliation at startup. This used to
# sit as `is_live` in the Discord bot - which had to import the Twitch bot for it, even though
# it is a purely Twitch-side state.
_is_live = False

# Viewer counts are known only to the Helix API; there is no EventSub event for them - so this
# one value stays polling (see _viewer_sample_loop, viewer_sample_interval).

# Everything adjustable - texts, timings, colours, command names, rules, static commands,
# moderation thresholds - comes from twitch.json and is re-read on change (see
# core/runtime_config.py). The file itself lives in config.py so that commands.py can read it
# too; here only under the familiar name.
TWITCH_CONFIG = config.TWITCH_CONFIG
text = config.text


async def _render(template, user_name):
    """A static command from twitch.json, ready for the chat.

    What the platform knows itself, it fills in itself: {u}/{user} is whoever wrote the
    command, {channel} the channel. Everything else - {time}, {date} and whatever the operator
    defined in features/variables/variables.json - comes from the VARIABLES feature, and
    through its capability rather than an import: if the bot runs without that feature, only
    the three here remain and the rest stands there as text. A command therefore never fails
    entirely."""
    values = {"u": user_name, "user": user_name, "channel": config.TWITCH_CHANNEL}
    for variables in events.bus.features_with(feature_api.VARIABLES):
        values.update(await variables.resolve(template, **values))
    return TWITCH_CONFIG.render(template, **values)


def _clock(moment):
    """`moment` (with timezone) as a time of day for the chat.

    In the same timezone as {time}, and from the same source: the VARIABLES feature's
    configuration, fetched through its capability rather than an import - as in _render above.
    That way the timezone need not appear a second time in twitch.json, and a change there
    takes effect here immediately.

    If the feature is missing, or no timezone is entered there, the process's own applies. In
    the container that is the host's (bugbot.container, Timezone=), and UTC without that line -
    which is why the entry in variables.json is the more reliable one."""
    for variables in events.bus.features_with(feature_api.VARIABLES):
        zone = variables.zone()
        if zone:
            return moment.astimezone(zone).strftime("%H:%M:%S")
    return moment.astimezone().strftime("%H:%M:%S")


def get_twitch_commands():
    # Keys with a leading underscore are explanations for whoever edits the file (JSON has no
    # comments), not a command - otherwise "_comment" would show up right in !commands.
    commands_map = {
        name: value for name, value in TWITCH_CONFIG.get("commands", {}).items()
        if not name.startswith("_")
    }
    rules = TWITCH_CONFIG.get("rules", "")
    # u deliberately stays a placeholder: the command table is built once, and the caller
    # fills the name in per message (as with every static command).
    commands_map.setdefault("!rules", TWITCH_CONFIG.text("rules.line", u="{u}", rules=rules))
    return commands_map


# Platform name as it appears in every bus notification and in the DB. Has to match
# platform.py:TwitchPlatform.name.
NAME = "twitch"


async def _publish_event(event_type, user_name, amount=0):
    """Reports a live event (follow/sub/cheer/raid/...) onto the bus. Who records it - and
    whether anybody does - is not visible from here; previously each of these places held a
    stats.record_event with the full signature."""
    await events.bus.publish(
        events.PLATFORM_EVENT, platform=NAME, event_type=event_type,
        user_name=user_name, amount=amount,
    )


async def _publish_mod_action(user_name, reason, action):
    await events.bus.publish(
        events.MOD_ACTION, platform=NAME, user_name=user_name, reason=reason, action=action,
    )


def moderation_overrides():
    """The "moderation" section from twitch.json, exactly as it stands. Merging and evaluating
    happen in the moderation feature - what remains here is only where it comes from, so the
    hot-reload configuration keeps working."""
    return TWITCH_CONFIG.get("moderation", {})


# Small commands belonging to the bot itself (introspection, cross-platform bug reports) -
# unlike commands.py, which bundles the pure Helix API commands.

async def cmd_list_commands(ctx, user_name, arg_text):
    """Lists only the commands user_name may actually use - mod commands therefore only for
    the broadcaster/moderators (ctx.is_privileged); otherwise !commands would itself suggest
    commands to non-mods that deny_mod_command would delete again straight away."""
    feature_commands = events.bus.commands()
    names = set(get_twitch_commands()) | set(dynamic_commands()) | set(bot_commands())
    names |= {name for name, cmd in feature_commands.items() if not cmd.mod_only}
    if ctx.is_privileged:
        names |= (
            set(TWITCH_CONFIG.section("mod_commands"))
            | set(dynamic_mod_commands())
            | set(bot_mod_commands())
            | {name for name, cmd in feature_commands.items() if cmd.mod_only}
        )
    label = text("commands.label_mod" if ctx.is_privileged else "commands.label")
    return text("commands.list", label=label, names=", ".join(sorted(names)))


async def cmd_bug(ctx, user_name, arg_text):
    """The bug report goes to the event bus, not to Discord: which platform presents it (and
    whether any does) is only decided there. Which is why the failure case no longer mentions
    Discord here either."""
    if not arg_text.strip():
        return text("bug.usage")
    delivered = await events.bus.announce(platform_api.Announcement(
        kind=platform_api.BUG_REPORT,
        title=text("bug.title"),
        text=arg_text.strip(),
        color=TWITCH_CONFIG.color("bug", 0xE67E22),
        source=NAME,
        author=user_name,
    ))
    if delivered:
        return text("bug.thanks", user=user_name)
    return text("bug.nowhere")


TWITCH_BOT_COMMANDS = {
    "!commands": cmd_list_commands,
    "!help": cmd_list_commands,
    "!bug": cmd_bug,
    "!report": cmd_bug,
}


def bot_commands():
    """The bot-side commands under their actual names (twitch.json, "command_names")."""
    return TWITCH_CONFIG.resolve_commands(TWITCH_BOT_COMMANDS)


def bot_mod_commands():
    return TWITCH_CONFIG.resolve_commands(TWITCH_BOT_MOD_COMMANDS)


def dynamic_commands():
    return TWITCH_CONFIG.resolve_commands(twitch_commands_file.TWITCH_DYNAMIC_COMMANDS)


def dynamic_mod_commands():
    return TWITCH_CONFIG.resolve_commands(twitch_commands_file.TWITCH_DYNAMIC_MOD_COMMANDS)


async def _resolve_automod(ctx, key, action):
    key = key.strip()
    msg_id = _automod_queue.pop(key, None)
    if not msg_id:
        return text("automod.unknown_key", key=key)
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, twitch_api.resolve_automod_message, ctx.moderator_id, msg_id, action, ctx.access_token)
    verb = text("automod.approved" if action == "ALLOW" else "automod.denied")
    return text("automod.done", key=key, verb=verb) if ok else text("automod.failed")


async def cmd_automod_approve(ctx, user_name, arg_text):
    if not arg_text.strip():
        return text("automod.approve_usage")
    return await _resolve_automod(ctx, arg_text, "ALLOW")


async def cmd_automod_deny(ctx, user_name, arg_text):
    if not arg_text.strip():
        return text("automod.deny_usage")
    return await _resolve_automod(ctx, arg_text, "DENY")


# The bot's own mod commands - like TWITCH_BOT_COMMANDS they need no TwitchContext import
# detour, here additionally because they reach directly into _automod_queue (module-global
# state). Like twitch_commands_file.TWITCH_DYNAMIC_MOD_COMMANDS they are protected from
# non-moderators via is_mod_command in twitch_chat_listener.
async def _giveaway_start(ctx, rest):
    global _giveaway
    if _giveaway is not None:
        return text("giveaway.running", title=_giveaway["title"])
    cost_str, _, title = rest.partition(" ")
    title = title.strip()
    if not cost_str.isdigit() or not title:
        return text("giveaway.start_usage")
    if not ctx.broadcaster_id:
        return text("giveaway.unavailable")
    loop = asyncio.get_event_loop()
    reward = await loop.run_in_executor(
        None, twitch_api.create_custom_reward, ctx.broadcaster_id, title, int(cost_str), ctx.access_token
    )
    if not reward:
        return text("giveaway.reward_failed")
    _giveaway = {"reward_id": reward["id"], "title": title, "entries": {}}
    return text("giveaway.started", title=title, cost=cost_str)


async def _giveaway_pick(ctx):
    global _giveaway
    if _giveaway is None:
        return text("giveaway.none")
    title = _giveaway["title"]
    reward_id = _giveaway["reward_id"]
    entries = _giveaway["entries"]
    loop = asyncio.get_event_loop()
    if not entries:
        await loop.run_in_executor(None, twitch_api.delete_custom_reward, ctx.broadcaster_id, reward_id, ctx.access_token)
        _giveaway = None
        return text("giveaway.no_entries", title=title)
    winner_redemption_id, (_, winner_user_name) = random.choice(list(entries.items()))
    await loop.run_in_executor(
        None, twitch_api.update_redemption_status, ctx.broadcaster_id, reward_id, winner_redemption_id, "FULFILLED", ctx.access_token
    )
    for redemption_id in entries:
        if redemption_id != winner_redemption_id:
            await loop.run_in_executor(
                None, twitch_api.update_redemption_status, ctx.broadcaster_id, reward_id, redemption_id, "CANCELED", ctx.access_token
            )
    await loop.run_in_executor(None, twitch_api.delete_custom_reward, ctx.broadcaster_id, reward_id, ctx.access_token)
    _giveaway = None
    return text("giveaway.winner", title=title, winner=winner_user_name)


async def _giveaway_cancel(ctx):
    global _giveaway
    if _giveaway is None:
        return text("giveaway.none")
    title = _giveaway["title"]
    reward_id = _giveaway["reward_id"]
    loop = asyncio.get_event_loop()
    for redemption_id in _giveaway["entries"]:
        await loop.run_in_executor(
            None, twitch_api.update_redemption_status, ctx.broadcaster_id, reward_id, redemption_id, "CANCELED", ctx.access_token
        )
    await loop.run_in_executor(None, twitch_api.delete_custom_reward, ctx.broadcaster_id, reward_id, ctx.access_token)
    _giveaway = None
    return text("giveaway.cancelled", title=title)


async def cmd_giveaway(ctx, user_name, arg_text):
    """!giveaway start <points> <title> | !giveaway pick | !giveaway cancel - the state lives
    in _giveaway (module-global), and entries come in through handle_reward_redemption via
    EventSub as soon as somebody redeems the matching channel points reward."""
    sub, _, rest = arg_text.partition(" ")
    sub = sub.lower()
    if sub == "start":
        return await _giveaway_start(ctx, rest)
    if sub == "pick":
        return await _giveaway_pick(ctx)
    if sub == "cancel":
        return await _giveaway_cancel(ctx)
    return text("giveaway.usage")


TWITCH_BOT_MOD_COMMANDS = {
    "!approve": cmd_automod_approve,
    "!deny": cmd_automod_deny,
    "!giveaway": cmd_giveaway,
}

def parse_irc_tags(raw_tags):
    """Parses the tags prefix of an IRC line (e.g. '@badges=moderator/1;id=abc-123') into a dict."""
    tags = {}
    for pair in raw_tags.lstrip("@").split(";"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            tags[key] = value
    return tags

class _AuthFailed(Exception):
    """Twitch rejected the IRC sign-in - the access token has to be renewed before a reconnect
    makes any sense (see twitch_chat_listener)."""


async def _send_raw(line):
    """Sends a raw IRC line. Raises when no connection stands (any more) - the reader loop
    catches that and rebuilds the connection."""
    if _writer is None:
        raise ConnectionError("no Twitch IRC connection")
    _writer.write(f"{line}\r\n".encode("utf-8"))
    await _writer.drain()


async def send_twitch_chat(message_text):
    """True when the message went out - which at the same time fulfils
    core.platform.Platform.send_text (see platforms/twitch/platform.py)."""
    try:
        await _send_raw(f"PRIVMSG #{config.TWITCH_CHANNEL.lower()} :{message_text}")
        print(f"💬 Twitch-Chat gesendet: {message_text}")
        return True
    except Exception as e:
        print(f"⚠️ Error while sending to Twitch: {e}")
        return False


async def _connect_and_auth(token):
    """Establishes the IRC connection. asyncio streams rather than a raw socket, because
    readline() buffers line by line - the earlier recv(2048) could miss PINGs in the middle of a
    chunk (and thereby provoke being kicked by Twitch) and tore the decoder apart on a UTF-8
    character cut across the chunk boundary."""
    global _reader, _writer
    _reader, _writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)

    # Tags provides badges (mod/subscriber) and the message id (for /delete); without this
    # capability we cannot moderate in a targeted way at all.
    await _send_raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
    await _send_raw(f"PASS oauth:{token}")
    await _send_raw(f"NICK {config.TWITCH_CHANNEL}")
    await _send_raw(f"JOIN #{config.TWITCH_CHANNEL.lower()}")


async def _close_connection():
    global _reader, _writer
    writer, _writer, _reader = _writer, None, None
    _connected.clear()
    if writer is not None:
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, ConnectionError):
            pass

def _eventsub_subscriptions():
    """List of all EventSub subscriptions that are (re-)registered on every fresh
    session_welcome: (type, version, condition, plain-text label for the log).
    automod.message.hold and channel.follow additionally need MODERATOR_ID in the condition -
    they are skipped if that is not (yet) resolved. Called as a function (not a module
    constant) so that BROADCASTER_ID/MODERATOR_ID are current at call time."""
    subs = [
        ("channel.ad_break.begin", "1", {"broadcaster_user_id": BROADCASTER_ID}, "📺 Ad-Break"),
        ("channel.subscribe", "1", {"broadcaster_user_id": BROADCASTER_ID}, "⭐ Sub"),
        ("channel.subscription.gift", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🎁 Gift-Sub"),
        ("channel.subscription.message", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🔁 Resub"),
        ("channel.cheer", "1", {"broadcaster_user_id": BROADCASTER_ID}, "💎 Cheer"),
        ("channel.raid", "1", {"to_broadcaster_user_id": BROADCASTER_ID}, "🚨 Raid"),
        ("channel.hype_train.progress", "2", {"broadcaster_user_id": BROADCASTER_ID}, "🚂 Hype-Train"),
        (
            "channel.channel_points_custom_reward_redemption.add", "1",
            {"broadcaster_user_id": BROADCASTER_ID}, "🎟️ Reward-Redemption",
        ),
        ("stream.online", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🟢 Stream-Online"),
        ("stream.offline", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🔴 Stream-Offline"),
        # From here on: completeness of the record. All of it lands in the raw log via
        # record_eventsub_notification anyway - handlers exist only for what should
        # additionally go into a typed table or into the chat.
        ("channel.update", "2", {"broadcaster_user_id": BROADCASTER_ID}, "📝 Titel/Kategorie"),
        ("channel.hype_train.begin", "2", {"broadcaster_user_id": BROADCASTER_ID}, "🚂 Hype-Train-Start"),
        ("channel.hype_train.end", "2", {"broadcaster_user_id": BROADCASTER_ID}, "🚂 Hype-Train-Ende"),
        ("channel.subscription.end", "1", {"broadcaster_user_id": BROADCASTER_ID}, "💔 Sub beendet"),
        ("channel.poll.end", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🗳️ Umfrage-Ende"),
        ("channel.prediction.end", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🔮 Prediction-Ende"),
        ("channel.ban", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🔨 Ban/Timeout"),
        ("channel.unban", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🕊️ Unban"),
        ("channel.goal.end", "1", {"broadcaster_user_id": BROADCASTER_ID}, "🎯 Ziel erreicht"),
    ]
    if MODERATOR_ID:
        subs += [
            ("automod.message.hold", "2", {"broadcaster_user_id": BROADCASTER_ID, "moderator_user_id": MODERATOR_ID}, "🚧 AutoMod-Hold"),
            ("channel.follow", "2", {"broadcaster_user_id": BROADCASTER_ID, "moderator_user_id": MODERATOR_ID}, "❤️ Follow"),
            ("channel.shoutout.receive", "1", {"broadcaster_user_id": BROADCASTER_ID, "moderator_user_id": MODERATOR_ID}, "📣 Shoutout erhalten"),
        ]
    return subs


async def _announce_ad_break_end(delay_seconds):
    """Reports the end of the ad break. Twitch has no EventSub event for it - only
    channel.ad_break.begin with the duration - so we wait it out ourselves. Runs as a task of
    its own so the EventSub listener does not block for minutes."""
    try:
        await asyncio.sleep(delay_seconds)
        await send_twitch_chat(text("ad_break.end"))
        print("📺 Werbepause beendet.")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"⚠️ Werbe-Ende-Meldung fehlgeschlagen: {e}")


async def handle_ad_break_begin(event):
    """Posts the start, duration and end time of an ad break into the Twitch chat - and
    reports back once more when the duration has elapsed and the ads are over."""
    global _ad_break_task
    duration = int(event.get("duration_seconds") or 0)
    try:
        start_at = datetime.fromisoformat(event["started_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        start_at = datetime.now(timezone.utc)
    end_local = _clock(start_at + timedelta(seconds=duration))
    print(f"📺 Werbepause gestartet: {duration}s, Ende ca. {end_local} Uhr.")
    await send_twitch_chat(text("ad_break.start", seconds=duration, end_time=end_local))
    await events.bus.publish(events.AD_BREAK, platform=NAME, duration_seconds=duration)

    # Remaining duration from now, not from started_at - the notification can arrive delayed,
    # and the all-clear would otherwise come too late.
    remaining = (start_at + timedelta(seconds=duration) - datetime.now(timezone.utc)).total_seconds()
    if _ad_break_task and not _ad_break_task.done():
        _ad_break_task.cancel()
    if remaining > 0:
        _ad_break_task = asyncio.create_task(_announce_ad_break_end(remaining), name="twitch-ad-break-end")


async def handle_automod_hold(event):
    """Assigns a short key to a message held back by AutoMod, remembers the real msg_id in
    _automod_queue and posts the message together with its category into the chat, so a mod can
    decide via !approve/!deny <key>."""
    global _automod_queue_counter
    _automod_queue_counter += 1
    key = str(_automod_queue_counter)
    _automod_queue[key] = event.get("message_id")
    held_text = (event.get("message") or {}).get("text", "")
    user = event.get("user_login", "unbekannt")
    category = event.get("category", "?")
    print(f"🚧 AutoMod is holding back message #{key} from {user} ({category}).")
    await send_twitch_chat(config.text(
        "automod.hold", key=key, user=user, category=category, text=held_text[:200],
    ))


# All stream figures (viewers, subs, bits, follows, hype train, ...) are only reported onto
# the bus now; they are evaluated in the statistics feature, which assigns them to a stream
# session. The peak values used to run along in parallel as a counter dict in RAM, which did
# not survive any bot restart mid-stream.


async def handle_channel_subscribe(event):
    """Fires for gift sub recipients too (event['is_gift'] == True) - those are only counted
    here and not separately announced in chat, because handle_channel_subscription_gift already
    posts a collective announcement for the gifter (otherwise a duplicate notice)."""
    user_name = event.get("user_name") or event.get("user_login") or "jemand"
    await _publish_event("sub", user_name)
    if not event.get("is_gift"):
        tier = event.get("tier", "1000")
        await send_twitch_chat(text("sub.new", user=user_name, tier=tier[0]))


async def handle_channel_subscription_gift(event):
    """Anonymous gifters are recorded as 'gift_sub_anon': they count towards the stream total
    but stay out of the !leaderboard rankings (where 'Anonymous' would otherwise sit
    permanently at the top)."""
    user_name = event.get("user_name") or event.get("user_login")
    total = int(event.get("total") or 0)
    if event.get("is_anonymous") or not user_name:
        await _publish_event("gift_sub_anon", "Anonym", total)
        return
    await _publish_event("gift_sub", user_name, total)
    await send_twitch_chat(text("sub.gift", user=user_name, count=total))


async def handle_channel_subscription_message(event):
    user_name = event.get("user_name") or event.get("user_login") or "jemand"
    months = int(event.get("cumulative_months") or 0)
    message_text = (event.get("message") or {}).get("text", "")
    await _publish_event("resub", user_name, months)
    suffix = config.text("sub.message_suffix", text=message_text[:200]) if message_text else ""
    await send_twitch_chat(config.text("sub.resub", user=user_name, months=months, message=suffix))


async def handle_channel_cheer(event):
    bits = int(event.get("bits") or 0)
    user_name = event.get("user_name") or event.get("user_login")
    if event.get("is_anonymous") or not user_name:
        # As with the gift subs: counts towards the stream's bits total, not the leaderboard.
        await _publish_event("cheer_anon", "Anonym", bits)
        return
    await _publish_event("cheer", user_name, bits)
    message_text = event.get("message", "")
    suffix = config.text("sub.message_suffix", text=message_text[:200]) if message_text else ""
    await send_twitch_chat(config.text("cheer", user=user_name, bits=bits, message=suffix))


async def _flush_follow_batch():
    """Waits briefly for further follows and then posts a collective notice. With very many
    follows at once only the first names are given and the rest as a count - an IRC message may
    only be about 500 characters long anyway."""
    global _follow_batch_task
    try:
        await asyncio.sleep(timing("follow_batch_window", 8))
        names, _pending_follows[:] = list(_pending_follows), []
        if not names:
            return
        if len(names) == 1:
            await send_twitch_chat(text("follow.single", user=names[0]))
        else:
            shown = ", ".join(f"@{n}" for n in names[:timing("follow_batch_names_shown", 5)])
            rest = len(names) - timing("follow_batch_names_shown", 5)
            suffix = text("follow.rest", count=rest) if rest > 0 else ""
            await send_twitch_chat(text("follow.many", count=len(names), names=shown, rest=suffix))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"⚠️ Follow-Sammelmeldung fehlgeschlagen: {e}")
    finally:
        _follow_batch_task = None


async def handle_channel_follow(event):
    """Every follow is recorded individually in the DB; the chat announcement runs collected
    through _flush_follow_batch (window: twitch.json, timings.follow_batch_window)."""
    global _follow_batch_task
    user_name = event.get("user_name") or event.get("user_login") or "jemand"
    await _publish_event("follow", user_name)
    _pending_follows.append(user_name)
    if _follow_batch_task is None or _follow_batch_task.done():
        _follow_batch_task = asyncio.create_task(_flush_follow_batch(), name="twitch-follow-batch")


async def handle_channel_raid(event):
    raider = event.get("from_broadcaster_user_name") or event.get("from_broadcaster_user_login") or "jemand"
    viewers = int(event.get("viewers") or 0)
    loop = asyncio.get_event_loop()
    await _publish_event("raid", raider, viewers)
    await send_twitch_chat(text("raid", user=raider, viewers=viewers))
    from_id = event.get("from_broadcaster_user_id")
    if BROADCASTER_ID and MODERATOR_ID and from_id:
        await loop.run_in_executor(
            None, twitch_api.send_shoutout, BROADCASTER_ID, MODERATOR_ID, from_id, config.TWITCH_CHAT_ACCESS_TOKEN
        )


async def handle_reward_redemption(event):
    """Records every channel points redemption; the giveaway logic below only applies while
    !giveaway has a draw running."""
    reward = event.get("reward") or {}
    user_name = event.get("user_name") or event.get("user_login") or "jemand"
    await _publish_event("redemption", user_name, int(reward.get("cost") or 0))
    if _giveaway is None:
        return
    if reward.get("id") != _giveaway["reward_id"]:
        return
    redemption_id = event.get("id")
    if not redemption_id:
        return
    user_name = event.get("user_name") or event.get("user_login") or "jemand"
    _giveaway["entries"][redemption_id] = (event.get("user_id"), user_name)
    print(f"🎟️ Neue Giveaway-Teilnahme: {user_name}")


def _stream_url():
    return f"https://twitch.tv/{config.TWITCH_CHANNEL}"


async def _go_live(stream_info):
    """Reports the stream start: open a new stats session and inform all platforms over the
    event bus. Called both from EventSub (stream.online) and at bot startup, in case the stream
    was already running then - hence the _is_live guard."""
    global _is_live
    if _is_live or not stream_info:
        return
    _is_live = True

    title = stream_info.get("title") or "Live-Stream"
    category = stream_info.get("game_name") or "Ohne Kategorie"
    # {width}/{height} are placeholders in the URL Twitch delivers, and the timestamp keeps
    # Discord from caching the (old) preview image.
    preview_url = stream_info.get("thumbnail_url", "")
    if preview_url:
        preview_url = preview_url.replace("{width}", "1280").replace("{height}", "720")
        preview_url += f"?t={int(datetime.now().timestamp())}"

    # From here on the recording features assign everything reported (chat, commands, mod
    # actions, events, ad breaks, viewer samples) to this session. Deliberately before the
    # announcement: the session has to be open before anything comes in.
    await events.bus.publish(events.STREAM_START, platform=NAME, title=title, category=category)

    await events.bus.announce(platform_api.Announcement(
        kind=platform_api.STREAM_ONLINE,
        title=text("stream.online.title", channel=config.TWITCH_CHANNEL),
        text=title,
        url=_stream_url(),
        image_url=preview_url,
        color=TWITCH_CONFIG.color("stream_online", 0x9146FF),
        source=NAME,
        highlight=True,
        log=True,
        fields=(platform_api.Field(text("stream.online.category"), category),),
    ))


async def _go_offline():
    """Counterpart to _go_live: close the session, reconcile the highscores and distribute the
    closing report as an announcement."""
    global _is_live
    if not _is_live:
        return
    _is_live = False

    # The recording features close the session, reconcile the records and return the figures
    # as finished fields - the Twitch bot need not know the metrics dict for it. If no such
    # feature is loaded, the closing report simply comes without numbers.
    fields = next((f for f in await events.bus.publish(events.STREAM_END, platform=NAME) if f), ())

    await events.bus.announce(platform_api.Announcement(
        kind=platform_api.STREAM_OFFLINE,
        title=text("stream.offline.title", channel=config.TWITCH_CHANNEL),
        text=text("stream.offline.text"),
        url=_stream_url(),
        color=TWITCH_CONFIG.color("stream_offline", 0x95A5A6),
        source=NAME,
        log=True,
        fields=fields,
    ))


async def handle_stream_online(event):
    """The EventSub event contains neither title nor category, hence an additional
    get_stream_info call."""
    loop = asyncio.get_event_loop()
    stream_info = await loop.run_in_executor(
        None, twitch_api.get_stream_info, BROADCASTER_ID, config.TWITCH_CHAT_ACCESS_TOKEN
    )
    await _go_live(stream_info)


async def handle_stream_offline(event):
    await _go_offline()


async def _reconcile_live_status():
    """One-off live reconciliation at startup: EventSub only fires on a *change*, so a restart
    during an already running stream would otherwise never trigger a live notice.

    Waits for the other platforms first: before its on_ready, Discord knows no guilds and would
    silently discard the announcement (see core.platform.Platform.wait_ready)."""
    if not BROADCASTER_ID:
        print("⚠️ Live status reconciliation skipped: broadcaster id not available.")
        return
    await events.bus.wait_ready(timeout=timing("platform_ready_timeout", 120))
    loop = asyncio.get_event_loop()
    stream_info = await loop.run_in_executor(
        None, twitch_api.get_stream_info, BROADCASTER_ID, config.TWITCH_CHAT_ACCESS_TOKEN
    )
    if stream_info:
        await _go_live(stream_info)


async def _viewer_sample_loop():
    """Records the viewer count during a running stream. Twitch has no EventSub event for it,
    so this stays polling. It used to run in the Discord bot, where it had no business being:
    it needed BROADCASTER_ID and the chat token from platforms/twitch for it."""
    while True:
        await asyncio.sleep(timing("viewer_sample_interval", 60))
        if not _is_live or not BROADCASTER_ID:
            continue
        # A propagated exception would indeed have the loop restarted (_supervised), but with
        # timings.task_restart_delay of delay - for pure sampling, carrying on is the better
        # answer.
        try:
            loop = asyncio.get_event_loop()
            stream_info = await loop.run_in_executor(
                None, twitch_api.get_stream_info, BROADCASTER_ID, config.TWITCH_CHAT_ACCESS_TOKEN
            )
            if stream_info:
                await events.bus.publish(
                    events.VIEWERS, platform=NAME, count=int(stream_info.get("viewer_count") or 0)
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ Zuschauer-Sampling fehlgeschlagen: {e}")


async def handle_channel_update(event):
    """Title/category change mid-stream. Without it a stream would run forever under the
    category that was set at switch-on."""
    title = event.get("title") or ""
    game_name = event.get("category_name") or ""
    changed = any(await events.bus.publish(
        events.STREAM_SEGMENT, platform=NAME, title=title, category=game_name
    ))
    if changed:
        print(f"📝 Stream-Update: \"{title}\" ({game_name})")


async def handle_channel_ban(event):
    """Bans/timeouts triggered by a human (or another bot) - our own actions are already
    logged by handle_twitch_violation itself. is_permanent distinguishes a ban from a
    timeout."""
    user_name = event.get("user_name") or event.get("user_login") or "unbekannt"
    action = "ban" if event.get("is_permanent") else "timeout"
    reason = event.get("reason") or "manuell"
    await _publish_mod_action(user_name, reason, action)


async def handle_channel_unban(event):
    user_name = event.get("user_name") or event.get("user_login") or "unbekannt"
    await _publish_mod_action(user_name, "unban", "unban")


async def handle_hypetrain_end(event):
    """The final level reached is more reliable than the last progress update."""
    level = int(event.get("level") or 0)
    await _publish_event("hypetrain", config.TWITCH_CHANNEL, level)
    if level:
        await send_twitch_chat(text("hypetrain.end", level=level))


async def handle_shoutout_receive(event):
    from_name = event.get("from_broadcaster_user_name") or event.get("from_broadcaster_user_login") or "jemand"
    viewers = int(event.get("viewer_count") or 0)
    await _publish_event("shoutout_in", from_name, viewers)


async def handle_subscription_end(event):
    user_name = event.get("user_name") or event.get("user_login") or "jemand"
    await _publish_event("sub_end", user_name)


async def handle_hypetrain_progress(event):
    """Every progress update is stored as its own event with the level as `amount` - the
    evaluation takes the maximum per stream from those (see features/stats/store.py)."""
    level = int(event.get("level") or 0)
    await _publish_event("hypetrain", config.TWITCH_CHANNEL, level)


# subscription_type -> handler, consulted in the "notification" branch of
# twitch_eventsub_listener. Every handler receives only the raw event dict.
_EVENTSUB_HANDLERS = {
    "channel.ad_break.begin": handle_ad_break_begin,
    "automod.message.hold": handle_automod_hold,
    "channel.subscribe": handle_channel_subscribe,
    "channel.subscription.gift": handle_channel_subscription_gift,
    "channel.subscription.message": handle_channel_subscription_message,
    "channel.cheer": handle_channel_cheer,
    "channel.follow": handle_channel_follow,
    "channel.raid": handle_channel_raid,
    "channel.channel_points_custom_reward_redemption.add": handle_reward_redemption,
    "stream.online": handle_stream_online,
    "stream.offline": handle_stream_offline,
    "channel.hype_train.progress": handle_hypetrain_progress,
    "channel.hype_train.end": handle_hypetrain_end,
    "channel.update": handle_channel_update,
    "channel.ban": handle_channel_ban,
    "channel.unban": handle_channel_unban,
    "channel.shoutout.receive": handle_shoutout_receive,
    "channel.subscription.end": handle_subscription_end,
    # channel.hype_train.begin, channel.poll.end, channel.prediction.end and
    # channel.goal.end deliberately have no handler - they are recorded in full via the raw log
    # (record_eventsub_notification) and can be evaluated there.
}


async def twitch_eventsub_listener():
    """Holds an EventSub WebSocket connection open, registers every subscription from
    _eventsub_subscriptions() after each fresh session_welcome and dispatches their
    notifications through _EVENTSUB_HANDLERS. On a session_reconnect, existing subscriptions
    migrate to the new session automatically according to Twitch (no re-subscribe needed);
    after a dropped connection, by contrast, the old subscriptions are gone and have to be
    registered again. If the session_keepalive messages stop coming, the session counts as
    dead - without that check the listener could hang silently on a long-dead session."""
    url = EVENTSUB_WS_URL
    resubscribe = True
    loop = asyncio.get_event_loop()
    while True:
        try:
            async with websockets.connect(url) as ws:
                # A reconnect_url is valid for exactly this one connection attempt.
                url = EVENTSUB_WS_URL
                keepalive_timeout = 30
                while True:
                    try:
                        deadline = keepalive_timeout + timing("eventsub_keepalive_grace", 10)
                        raw = await asyncio.wait_for(ws.recv(), timeout=deadline)
                    except asyncio.TimeoutError:
                        raise ConnectionError(f"no keepalive for {deadline}s")

                    msg = json.loads(raw)
                    metadata = msg.get("metadata", {})
                    msg_type = metadata.get("message_type")

                    if msg_type == "session_welcome":
                        session = msg["payload"]["session"]
                        keepalive_timeout = int(session.get("keepalive_timeout_seconds") or 30)
                        if resubscribe:
                            for sub_type, version, condition, label in _eventsub_subscriptions():
                                ok = await loop.run_in_executor(
                                    None, twitch_api.create_eventsub_subscription,
                                    sub_type, version, condition, session["id"], config.TWITCH_CHAT_ACCESS_TOKEN,
                                )
                                print(f"{label}-EventSub-Abo eingerichtet." if ok else f"⚠️ {label}-EventSub-Abo fehlgeschlagen.")
                            resubscribe = False

                    elif msg_type == "session_reconnect":
                        url = msg["payload"]["session"]["reconnect_url"]
                        break

                    elif msg_type == "notification":
                        sub_type = metadata.get("subscription_type")
                        event = msg["payload"]["event"]
                        # Write it away first, process afterwards: an error in the handler
                        # must not result in the event being documented nowhere.
                        await events.bus.publish(events.RAW_EVENT, platform=NAME, event_type=sub_type, payload=event)
                        handler = _EVENTSUB_HANDLERS.get(sub_type)
                        if handler:
                            try:
                                await handler(event)
                            except asyncio.CancelledError:
                                raise
                            except Exception as e:
                                # A broken handler must not drag the whole EventSub session
                                # down - all other events would then be lost.
                                print(f"⚠️ Error in the EventSub handler for {sub_type}: {e}")

                    elif msg_type == "revocation":
                        subscription = msg["payload"]["subscription"]
                        status = subscription.get("status")
                        print(f"⚠️ EventSub-Abo {subscription.get('type')} widerrufen ({status}).")
                        if status == "authorization_revoked":
                            # Token has gone invalid: renew first, then re-subscribe
                            # everything with a fresh session. Merely noting it would have
                            # achieved nothing here - this session delivers nothing more.
                            await loop.run_in_executor(None, twitch_api.refresh_chat_token)
                            resubscribe = True
                            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ EventSub-Verbindung unterbrochen: {e}")
            url = EVENTSUB_WS_URL
            resubscribe = True
            await asyncio.sleep(10)


def log_token_capabilities(scopes):
    """Logs at startup which permissions the chat token actually has and what the bot can do
    with them - the plain-text counterpart to checking manually via
    https://id.twitch.tv/oauth2/validate."""
    if scopes is None:
        print("⚠️ Token scopes could not be queried (see the error above).")
        return

    print(f"🔑 Twitch-Token hat {len(scopes)} Scope(s):")
    known = [s for s in scopes if s in twitch_scopes.CAPABILITIES]
    unknown = [s for s in scopes if s not in twitch_scopes.CAPABILITIES]
    for scope in known:
        print(f"   ✅ {scope} -> {twitch_scopes.CAPABILITIES[scope]}")
    if unknown:
        print(f"   ℹ️ weitere Scopes ohne Bot-Funktion: {', '.join(unknown)}")

    # Against the same list get_token.py requests when creating one: if a scope is added in
    # config without the token being fetched again, startup says so here.
    missing = [s for s in twitch_scopes.REQUIRED if s not in scopes]
    if missing:
        print(f"   ⚠️ {len(missing)} required scope(s) missing: {', '.join(missing)}")
        print("      -> create the token afresh with 'python3 -m platforms.twitch.get_token' "
              "(Twitch does not extend existing tokens after the fact).")

    for scope, warning in twitch_scopes.DANGEROUS_UNNEEDED.items():
        if scope in scopes:
            print(f"   🚨 needlessly risky scope present: {scope} ({warning}) - remove it at the next token refresh")


async def twitch_token_refresh_loop():
    """Renews the chat token before it expires. The Helix calls do refresh by themselves on a
    401 (see api._helix_request), but those are exactly what does not happen while idle: after
    a few quiet hours the token would otherwise be dead, the next IRC reconnect would fail and
    the EventSub subscriptions would be revoked."""
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(timing("token_check_interval", 1800))
        info = await loop.run_in_executor(None, twitch_api.validate_token_info, config.TWITCH_CHAT_ACCESS_TOKEN)
        expires_in = info.get("expires_in") if info else None
        # For Twitch, expires_in == 0 means "never expires" (long-lived tokens of some apps),
        # not "just expired" - renewing such tokens here would not only be unnecessary, it
        # previously ran into a failed refresh every 30 minutes.
        if expires_in == 0:
            continue
        if expires_in is None or expires_in < timing("token_refresh_margin", 3600):
            await loop.run_in_executor(None, twitch_api.refresh_chat_token)


async def _supervised(name, coro_factory):
    """Keeps one of the endless background loops alive. Previously a crashed task was merely
    logged and then stayed dead - the bot ran on, but e.g. without EventSub events or without
    token renewal, until somebody restarted it by hand. Exactly the kind of silent partial
    failure that makes uptime look good on paper."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            delay = timing("task_restart_delay", 10)
            print(f"⚠️ Background task {name} crashed: {e!r} - restarting in {delay}s")
        else:
            delay = timing("task_restart_delay", 10)
            print(f"⚠️ Hintergrundtask {name} unerwartet beendet - Neustart in {delay}s")
        await asyncio.sleep(delay)


def _warn_if_task_died(task):
    """Last line of defence: the supervisor itself ending should never happen - and if it
    does, it must not happen silently."""
    if task.cancelled():
        return
    print(f"🚨 Twitch-Supervisor {task.get_name()} beendet: {task.exception()!r}")


async def start_twitch_bot():
    global BROADCASTER_ID, MODERATOR_ID
    global _listener_task, _eventsub_task, _token_task, _viewer_task, _reconcile_task
    loop = asyncio.get_event_loop()

    # The reader establishes the connection itself (and re-establishes it again and again) -
    # hence starting it first here: even when Twitch is unreachable at startup it keeps trying,
    # instead of leaving the Twitch part of the bot permanently dead.
    _listener_task = asyncio.create_task(_supervised("twitch-irc", twitch_chat_listener), name="twitch-irc")
    _token_task = asyncio.create_task(_supervised("twitch-token", twitch_token_refresh_loop), name="twitch-token")

    scopes = await loop.run_in_executor(None, twitch_api.validate_token, config.TWITCH_CHAT_ACCESS_TOKEN)
    if scopes is None and await loop.run_in_executor(None, twitch_api.refresh_chat_token):
        scopes = await loop.run_in_executor(None, twitch_api.validate_token, config.TWITCH_CHAT_ACCESS_TOKEN)
    log_token_capabilities(scopes)

    BROADCASTER_ID = await loop.run_in_executor(None, twitch_api.get_broadcaster_id, config.TWITCH_CHANNEL)
    MODERATOR_ID = await loop.run_in_executor(None, twitch_api.get_moderator_id, config.TWITCH_CHAT_ACCESS_TOKEN)
    if not BROADCASTER_ID or not MODERATOR_ID:
        print(
            "⚠️ Broadcaster/moderator id not resolvable - Twitch delete/timeout are disabled. "
            "Check that TWITCH_CHAT_ACCESS_TOKEN holds the scopes moderator:manage:chat_messages "
            "and moderator:manage:banned_users, and that the account is a moderator in the channel."
        )

    if BROADCASTER_ID:
        _eventsub_task = asyncio.create_task(
            _supervised("twitch-eventsub", twitch_eventsub_listener), name="twitch-eventsub"
        )
        _viewer_task = asyncio.create_task(
            _supervised("twitch-viewers", _viewer_sample_loop), name="twitch-viewers"
        )

    for task in (_listener_task, _token_task, _eventsub_task, _viewer_task):
        if task:
            task.add_done_callback(_warn_if_task_died)

    # Runs as a task of its own because it waits for the other platforms to be ready (up to
    # timings.platform_ready_timeout). Inline, a platform that never becomes ready would block
    # the entire Twitch startup here - including the startup message in the chat.
    _reconcile_task = asyncio.create_task(_reconcile_live_status(), name="twitch-live-reconcile")

    try:
        await asyncio.wait_for(_connected.wait(), timeout=60)
        await send_twitch_chat(text("startup"))
    except asyncio.TimeoutError:
        print("⚠️ Twitch IRC still not connected after 60s - startup message skipped, the reader keeps trying.")


async def close():
    """Ends all background tasks (chat reader, EventSub listener, token watchdog, viewer
    sampling, live reconciliation, running announcements) and the IRC connection cleanly - e.g.
    on Ctrl+C, or when another platform crashes."""
    global _listener_task, _eventsub_task, _token_task, _viewer_task, _reconcile_task
    global _ad_break_task, _follow_batch_task
    tasks = (
        _listener_task, _eventsub_task, _token_task, _viewer_task, _reconcile_task,
        _ad_break_task, _follow_batch_task,
    )
    for task in tasks:
        if task:
            task.remove_done_callback(_warn_if_task_died)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _listener_task = _eventsub_task = _token_task = _viewer_task = _reconcile_task = None
    _ad_break_task = _follow_batch_task = None
    await _close_connection()
    print("🔌 Twitch-IRC-Verbindung geschlossen.")


async def handle_twitch_violation(message, msg_id, verdict):
    """Carries out the moderation feature's verdict. What counts as an offence and when a
    timeout is due is no longer decided by this function (that used to stand word for word in
    the Discord bot too) but by features/moderation - what remains here is only how to delete
    and time out on Twitch."""
    detail_suffix = f" ('{verdict.detail}')" if verdict.detail else ""
    loop = asyncio.get_event_loop()

    if verdict.delete and BROADCASTER_ID and MODERATOR_ID and msg_id:
        await loop.run_in_executor(
            None, twitch_api.delete_chat_message,
            BROADCASTER_ID, MODERATOR_ID, msg_id, config.TWITCH_CHAT_ACCESS_TOKEN,
        )

    print(
        f"🧹 Twitch message deleted: {message.user_name} - {verdict.label}{detail_suffix} "
        f"(offence #{verdict.violation_count})"
    )
    # Post the reason as a category (label), not as a detail - otherwise a deleted banned word
    # or a blocked link would land in the chat again through the bot's own message.
    await send_twitch_chat(text("violation.deleted", user=message.user_name, label=verdict.label))
    await _publish_mod_action(message.user_name, verdict.reason, "delete")

    if verdict.timeout_seconds:
        if BROADCASTER_ID and MODERATOR_ID and message.user_id:
            await loop.run_in_executor(
                None, twitch_api.timeout_user,
                BROADCASTER_ID, MODERATOR_ID, message.user_id, verdict.timeout_seconds,
                verdict.label, config.TWITCH_CHAT_ACCESS_TOKEN,
            )
        await _publish_mod_action(message.user_name, verdict.reason, "timeout")
        print(f"⏱️ Twitch-Timeout: {message.user_name} ({verdict.timeout_seconds}s, {verdict.label})")


async def deny_mod_command(user_name, msg_id, command_word):
    """Deletes a non-moderator's attempt to use a mod command and posts a short refusal -
    previously this was simply ignored in silence."""
    if BROADCASTER_ID and MODERATOR_ID and msg_id:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, twitch_api.delete_chat_message,
            BROADCASTER_ID, MODERATOR_ID, msg_id, config.TWITCH_CHAT_ACCESS_TOKEN,
        )
    print(f"🚫 Mod command {command_word} from {user_name} refused (no moderator rights).")
    await send_twitch_chat(text("mod_only", user=user_name))


async def twitch_chat_listener():
    """Owns the complete connection lifecycle: connect, read, and reconnect with growing
    backoff after every break. Previously it connected exactly once, and a socket closed by
    Twitch (recv() -> b"") was acknowledged with nothing but a sleep(0.5) - the bot then ran on
    endlessly without ever seeing another message."""
    backoff = 5
    print("👀 Twitch chat reader running in the background...")

    while True:
        try:
            await _connect_and_auth(config.TWITCH_CHAT_ACCESS_TOKEN)
            await _read_until_disconnect()
        except asyncio.CancelledError:
            raise
        except _AuthFailed:
            print("⚠️ Twitch login rejected, renewing the token and reconnecting...")
            await asyncio.get_event_loop().run_in_executor(None, twitch_api.refresh_chat_token)
        except Exception as e:
            print(f"⚠️ Twitch-IRC-Verbindung verloren: {e}")

        # Only retry quickly after a connection that really stood - otherwise we would hammer
        # Twitch on a broken token or a dead network.
        backoff = 5 if _connected.is_set() else min(backoff * 2, timing("irc_reconnect_backoff_max", 300))
        await _close_connection()
        print(f"🔄 Next Twitch IRC connection attempt in {backoff}s...")
        await asyncio.sleep(backoff)


async def _read_until_disconnect():
    """Reads line by line until the connection is dead, and then raises - upon which the
    caller reconnects. "Dead" means: EOF, socket error, or no answer to our own PING (the case
    a quietly dropped connection would otherwise leave unnoticed)."""
    reader = _reader
    awaiting_pong = False

    while True:
        try:
            ping_interval = timing("irc_ping_interval", 180)
            raw_line = await asyncio.wait_for(reader.readline(), timeout=ping_interval)
        except asyncio.TimeoutError:
            if awaiting_pong:
                raise ConnectionError(f"no answer to our own PING within {ping_interval}s")
            await _send_raw("PING :tmi.twitch.tv")
            awaiting_pong = True
            continue

        if not raw_line:
            raise ConnectionError("Verbindung von Twitch geschlossen (EOF)")

        # Every incoming byte is a sign of life - be it a PONG, chat, or a system line.
        awaiting_pong = False
        await _handle_irc_line(raw_line.decode("utf-8", "replace").rstrip("\r\n"))


async def _handle_irc_line(line):
    """Processes exactly one IRC line. PING/login errors first, so that keeping the connection
    alive does not depend on whatever else is happening in the chat."""
    if line.startswith("PING"):
        await _send_raw(f"PONG {line[5:] or ':tmi.twitch.tv'}")
        return

    if "Login authentication failed" in line or "Improperly formatted auth" in line:
        raise _AuthFailed(line)

    # :tmi.twitch.tv 001 <nick> :Welcome, GLHF! - only from here on is the sign-in complete.
    if " 001 " in line:
        _connected.set()
        print(f"✅ Twitch-IRC verbunden (#{config.TWITCH_CHANNEL.lower()}).")
        return

    if "PRIVMSG" in line and ":" in line:
        try:
            await _handle_privmsg(line)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # An error in message processing must not cost us the connection.
            print(f"⚠️ Error while processing a Twitch message: {e}")


async def _send_command_reply(reply):
    """Send a command's reply into the chat. Feature commands may return an Announcement too
    (Discord builds an embed from it) - on IRC that becomes a single line. Line breaks have to
    go in the process: an IRC message is single-line, and multi-line replies (e.g. !top) would
    otherwise arrive truncated."""
    if not reply:
        return
    if isinstance(reply, platform_api.Announcement):
        reply = reply.as_text(max_fields=3)
    separator = text("reply.separator")
    await send_twitch_chat(separator.join(part.strip() for part in reply.splitlines() if part.strip()))


async def _record_command(name, user_name):
    await events.bus.publish(events.COMMAND, platform=NAME, command=name, user_name=user_name)


async def _handle_privmsg(line):
    tags = {}
    if line.startswith("@"):
        raw_tags, line = line.split(" ", 1)
        tags = parse_irc_tags(raw_tags)

    parts = line.split(":", 2)
    if len(parts) < 3:
        return

    # Pulls the plain username out cleanly and without cruft
    raw_user = parts[1].split("!")[0]
    user_name = raw_user.replace(":", "").strip()
    message = parts[2].strip()

    print(f"[Twitch Chat] {user_name}: {message}")

    badges = tags.get("badges", "")
    is_privileged = any(b.startswith(("broadcaster", "moderator")) for b in badges.split(","))
    is_subscriber = any(b.startswith(("subscriber", "founder")) for b in badges.split(","))

    msg_lower = message.lower()
    command_word, _, arg_text = message.partition(" ")
    command_word = command_word.lower()
    arg_text = arg_text.strip()

    msg = feature_api.Message(
        platform=NAME,
        user_id=tags.get("user-id", ""),
        user_name=user_name,
        text=message,
        is_privileged=is_privileged,
        is_subscriber=is_subscriber,
        command=command_word,
        arg_text=arg_text,
    )

    # Deliberately before moderation: the messages deleted later are precisely the ones you
    # want to be able to read back afterwards. What is made of it (recording only during a
    # stream) is decided by the features - the bot only reports.
    await events.bus.publish(events.MESSAGE, message=msg)

    # Moderation: the first feature to object wins. If none is loaded, there simply is no
    # moderation - the rest of the bot runs on unchanged.
    for moderator in events.bus.features_with(feature_api.MODERATION):
        verdict = await moderator.review(msg, moderation_overrides())
        if verdict:
            await handle_twitch_violation(msg, tags.get("id"), verdict)
            return

    await events.bus.publish(events.MESSAGE_ACCEPTED, message=msg)

    # Commands in a fixed order: first the platform's own (Helix calls in commands.py, bot
    # internals here), then the features', and last the static text maps from twitch.json. The
    # platform takes precedence - a feature must not be able to override a command that
    # twitch.json explicitly assigns otherwise.
    # Each of the four command tables goes through the renames from twitch.json
    # ("command_names") - a command can thereby be called something else, have several names or
    # be missing entirely, without any of that appearing here.
    ctx = twitch_commands_file.TwitchContext(
        BROADCASTER_ID, MODERATOR_ID, config.TWITCH_CHAT_ACCESS_TOKEN, config.TWITCH_CHANNEL,
        is_privileged=is_privileged,
    )
    mod_commands = TWITCH_CONFIG.section("mod_commands")
    commands_map = get_twitch_commands()
    feature_command = events.bus.commands().get(command_word)
    dynamic = dynamic_commands()
    dynamic_mod = dynamic_mod_commands()
    own = bot_commands()
    own_mod = bot_mod_commands()

    is_mod_command = (
        command_word in dynamic_mod
        or command_word in own_mod
        or msg_lower in mod_commands
        or (feature_command is not None and feature_command.mod_only)
    )

    if is_mod_command and not is_privileged:
        # Non-mods attempting a mod command: delete the message rather than simply ignoring
        # it.
        await deny_mod_command(user_name, tags.get("id"), command_word)
    elif command_word in dynamic_mod:
        await _record_command(command_word, user_name)
        await _send_command_reply(await dynamic_mod[command_word](ctx, user_name, arg_text))
    elif command_word in own_mod:
        await _record_command(command_word, user_name)
        await _send_command_reply(await own_mod[command_word](ctx, user_name, arg_text))
    elif msg_lower in mod_commands:
        await _record_command(msg_lower, user_name)
        await send_twitch_chat(await _render(mod_commands[msg_lower], user_name))
    elif command_word in dynamic:
        await _record_command(command_word, user_name)
        await _send_command_reply(await dynamic[command_word](ctx, user_name, arg_text))
    elif command_word in own:
        await _record_command(command_word, user_name)
        await _send_command_reply(await own[command_word](ctx, user_name, arg_text))
    elif feature_command is not None:
        await _record_command(command_word, user_name)
        await _send_command_reply(await feature_command.handler(msg))
    elif msg_lower in commands_map:
        await _record_command(msg_lower, user_name)
        await send_twitch_chat(await _render(commands_map[msg_lower], user_name))
