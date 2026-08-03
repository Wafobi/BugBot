# commands.py
# Dynamische Befehle mit Argumenten/Live-API-Aufrufen für Twitch - also alles, was
# wirklich Helix braucht (Uptime, Titel ändern, Raid, Timeout, ...). Die statischen
# Text-Befehle, Mod-Befehle und Regeln sind kein Python-Code mehr, sondern leben in
# twitch.json (siehe core/runtime_config.py) - so lassen sie sich zur Laufzeit editieren,
# ohne den Bot neu zu starten.
#
# Nicht mehr hier: !stats, !streamstats, !highscores und !leaderboard. Die brauchen kein
# Twitch, sondern nur die aufgezeichneten Zahlen - sie kommen jetzt aus features/stats und
# funktionieren dadurch auf jeder Plattform (siehe core/feature.py).

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from core import events
from core import platform as platform_api
from . import api as twitch_api
from .config import text


@dataclass
class TwitchContext:
    """Live-Zustand, den twitch_bot.py den Befehlen unten pro Nachricht mitgibt -
    kein Import von twitch_bot.py hier, sonst gäbe es einen Zirkelimport
    (twitch_bot.py importiert bereits commands.py)."""
    broadcaster_id: str
    moderator_id: str
    access_token: str
    channel: str
    is_privileged: bool = False


# Befehle mit Argumenten/Live-API-Aufrufen - anders als die statischen Text-Maps in
# twitch.json, da sie echte Helix-Calls brauchen (Uptime, Titel ändern, Raid, ...).
# Handler bekommen (ctx, user_name, arg_text) und geben den zu postenden Text zurück
# (oder None).

async def cmd_uptime(ctx, user_name, arg_text):
    if not ctx.broadcaster_id:
        return text("uptime.unavailable")
    loop = asyncio.get_event_loop()
    stream = await loop.run_in_executor(None, twitch_api.get_stream_info, ctx.broadcaster_id, ctx.access_token)
    if not stream:
        return text("uptime.offline", channel=ctx.channel)
    started = datetime.fromisoformat(stream["started_at"].replace("Z", "+00:00"))
    hours, rem = divmod(int((datetime.now(timezone.utc) - started).total_seconds()), 3600)
    minutes, _ = divmod(rem, 60)
    return text("uptime.live", hours=hours, minutes=minutes)


async def cmd_followage(ctx, user_name, arg_text):
    if not ctx.broadcaster_id:
        return text("followage.unavailable")
    target_login = arg_text.strip() or user_name
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    followed_at = await loop.run_in_executor(
        None, twitch_api.get_followage, ctx.broadcaster_id, users[0]["id"], ctx.access_token
    )
    if not followed_at:
        return text("followage.not_following", user=users[0]["display_name"], channel=ctx.channel)
    since = datetime.fromisoformat(followed_at.replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - since).days
    return text("followage.done", user=users[0]["display_name"], days=days, since=since.date())


async def cmd_clip(ctx, user_name, arg_text):
    if not ctx.broadcaster_id:
        return text("clip.unavailable")
    loop = asyncio.get_event_loop()
    clip_url = await loop.run_in_executor(None, twitch_api.create_clip, ctx.broadcaster_id, ctx.access_token)
    if not clip_url:
        return text("clip.failed")
    # Über den Event-Bus statt direkt an Discord: dieses Modul weiß nicht (und muss
    # nicht wissen), wer den Clip am Ende wo postet.
    await events.bus.announce(platform_api.Announcement(
        kind=platform_api.CLIP,
        title=text("clip.title"),
        url=clip_url,
        color=0x9146FF,
        source="twitch",
        author=user_name,
    ))
    return text("clip.done", user=user_name, url=clip_url)


async def cmd_chatters(ctx, user_name, arg_text):
    if not ctx.broadcaster_id or not ctx.moderator_id:
        return text("chatters.unavailable")
    loop = asyncio.get_event_loop()
    total = await loop.run_in_executor(None, twitch_api.get_chatters_count, ctx.broadcaster_id, ctx.moderator_id, ctx.access_token)
    if total is None:
        return text("chatters.failed")
    return text("chatters.done", count=total)


async def cmd_subs(ctx, user_name, arg_text):
    if not ctx.broadcaster_id:
        return text("subs.unavailable")
    loop = asyncio.get_event_loop()
    total, points = await loop.run_in_executor(None, twitch_api.get_subscriber_count, ctx.broadcaster_id, ctx.access_token)
    if total is None:
        return text("subs.failed")
    return text("subs.done", count=total, points=points)


async def cmd_bits(ctx, user_name, arg_text):
    loop = asyncio.get_event_loop()
    top = await loop.run_in_executor(None, twitch_api.get_bits_leaderboard, ctx.access_token)
    if not top:
        return text("bits.none")
    return text("bits.done", user=top["user_name"], bits=top["score"])


async def cmd_hypetrain(ctx, user_name, arg_text):
    if not ctx.broadcaster_id:
        return text("hypetrain.unavailable")
    loop = asyncio.get_event_loop()
    event = await loop.run_in_executor(None, twitch_api.get_hype_train_status, ctx.broadcaster_id, ctx.access_token)
    if not event:
        return text("hypetrain.none")
    data = event.get("event_data", {})
    level, goal, total = data.get("level"), data.get("goal"), data.get("total")
    return text("hypetrain.done", level=level, total=total, goal=goal)


TWITCH_DYNAMIC_COMMANDS = {
    "!uptime": cmd_uptime,
    "!followage": cmd_followage,
    "!clip": cmd_clip,
    "!chatters": cmd_chatters,
    "!subs": cmd_subs,
    "!bits": cmd_bits,
    "!hypetrain": cmd_hypetrain,
}


async def cmd_title(ctx, user_name, arg_text):
    if not arg_text:
        return text("title.usage")
    if not ctx.broadcaster_id:
        return text("title.unavailable")
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, twitch_api.modify_channel, ctx.broadcaster_id, ctx.access_token, arg_text, None)
    return text("title.done", title=arg_text) if ok else text("title.failed")


async def cmd_game(ctx, user_name, arg_text):
    if not arg_text:
        return text("game.usage")
    if not ctx.broadcaster_id:
        return text("game.unavailable")
    loop = asyncio.get_event_loop()
    game_id = await loop.run_in_executor(None, twitch_api.get_game_id, arg_text, ctx.access_token)
    if not game_id:
        return text("game.not_found", game=arg_text)
    ok = await loop.run_in_executor(None, twitch_api.modify_channel, ctx.broadcaster_id, ctx.access_token, None, game_id)
    return text("game.done", game=arg_text) if ok else text("game.failed")


async def cmd_shoutout(ctx, user_name, arg_text):
    target_login = arg_text.strip()
    if not target_login:
        return text("shoutout.usage")
    if not ctx.broadcaster_id or not ctx.moderator_id:
        return text("shoutout.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    ok = await loop.run_in_executor(
        None, twitch_api.send_shoutout, ctx.broadcaster_id, ctx.moderator_id, users[0]["id"], ctx.access_token
    )
    if ok:
        return text("shoutout.done", user=users[0]["display_name"], login=users[0]["login"])
    return text("shoutout.failed")


async def cmd_raid(ctx, user_name, arg_text):
    target_login = arg_text.strip()
    if not target_login:
        return text("raid.usage")
    if not ctx.broadcaster_id:
        return text("raid.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("raid.not_found", channel=target_login)
    ok = await loop.run_in_executor(None, twitch_api.start_raid, ctx.broadcaster_id, users[0]["id"], ctx.access_token)
    return text("raid.done", user=users[0]["display_name"]) if ok else text("raid.failed")


async def cmd_slow(ctx, user_name, arg_text):
    arg = arg_text.strip().lower()
    if not ctx.broadcaster_id or not ctx.moderator_id:
        return text("slow.unavailable")
    loop = asyncio.get_event_loop()
    if arg in ("off", "0", "aus"):
        ok = await loop.run_in_executor(
            None, twitch_api.update_chat_settings, ctx.broadcaster_id, ctx.moderator_id, ctx.access_token, {"slow_mode": False}
        )
        return text("slow.off") if ok else text("slow.failed")
    try:
        seconds = int(arg) if arg else 30
    except ValueError:
        return text("slow.usage")
    ok = await loop.run_in_executor(
        None, twitch_api.update_chat_settings, ctx.broadcaster_id, ctx.moderator_id, ctx.access_token,
        {"slow_mode": True, "slow_mode_wait_time": seconds},
    )
    return text("slow.on", seconds=seconds) if ok else text("slow.failed")


async def cmd_timeout(ctx, user_name, arg_text):
    parts = arg_text.split(" ", 2)
    if len(parts) < 2:
        return text("timeout.usage")
    target_login, seconds_str = parts[0], parts[1]
    reason = parts[2].strip() if len(parts) > 2 else text("reason.none")
    try:
        seconds = max(1, min(int(seconds_str), 1209600))  # Twitch-Limit: max. 14 Tage
    except ValueError:
        return text("timeout.usage")
    if not ctx.broadcaster_id or not ctx.moderator_id:
        return text("timeout.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    ok = await loop.run_in_executor(
        None, twitch_api.timeout_user, ctx.broadcaster_id, ctx.moderator_id, users[0]["id"], seconds, reason, ctx.access_token
    )
    return text("timeout.done", user=users[0]["display_name"], seconds=seconds, reason=reason) if ok else text("timeout.failed")


async def cmd_ban(ctx, user_name, arg_text):
    target_login, _, reason = arg_text.partition(" ")
    if not target_login:
        return text("ban.usage")
    reason = reason.strip() or text("reason.none")
    if not ctx.broadcaster_id or not ctx.moderator_id:
        return text("ban.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    ok = await loop.run_in_executor(
        None, twitch_api.ban_user, ctx.broadcaster_id, ctx.moderator_id, users[0]["id"], reason, ctx.access_token
    )
    return text("ban.done", user=users[0]["display_name"], reason=reason) if ok else text("ban.failed")


async def cmd_unban(ctx, user_name, arg_text):
    target_login = arg_text.strip()
    if not target_login:
        return text("unban.usage")
    if not ctx.broadcaster_id or not ctx.moderator_id:
        return text("unban.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    ok = await loop.run_in_executor(
        None, twitch_api.unban_user, ctx.broadcaster_id, ctx.moderator_id, users[0]["id"], ctx.access_token
    )
    return text("unban.done", user=users[0]["display_name"]) if ok else text("unban.failed")


async def cmd_poll(ctx, user_name, arg_text):
    parts = [p.strip() for p in arg_text.split(";") if p.strip()]
    if len(parts) < 3:
        return text("poll.usage")
    title, choices = parts[0], parts[1:6]
    if not ctx.broadcaster_id:
        return text("poll.unavailable")
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, twitch_api.create_poll, ctx.broadcaster_id, title, choices, 60, ctx.access_token)
    return text("poll.done", title=title, choices=", ".join(choices)) if ok else text("poll.failed")


async def cmd_prediction(ctx, user_name, arg_text):
    parts = [p.strip() for p in arg_text.split(";") if p.strip()]
    if len(parts) < 3:
        return text("prediction.usage")
    title, outcomes = parts[0], parts[1:11]
    if not ctx.broadcaster_id:
        return text("prediction.unavailable")
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, twitch_api.create_prediction, ctx.broadcaster_id, title, outcomes, 120, ctx.access_token)
    return text("prediction.done", title=title, outcomes=", ".join(outcomes)) if ok else text("prediction.failed")


async def cmd_vip(ctx, user_name, arg_text):
    action, _, target_login = arg_text.partition(" ")
    action, target_login = action.strip().lower(), target_login.strip()
    if action not in ("add", "remove") or not target_login:
        return text("vip.usage")
    if not ctx.broadcaster_id:
        return text("vip.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    fn = twitch_api.add_channel_vip if action == "add" else twitch_api.remove_channel_vip
    ok = await loop.run_in_executor(None, fn, ctx.broadcaster_id, users[0]["id"], ctx.access_token)
    verb = text("vip.added" if action == "add" else "vip.removed")
    return text("vip.done", user=users[0]["display_name"], verb=verb) if ok else text("vip.failed")


async def cmd_mod(ctx, user_name, arg_text):
    action, _, target_login = arg_text.partition(" ")
    action, target_login = action.strip().lower(), target_login.strip()
    if action not in ("add", "remove") or not target_login:
        return text("mod.usage")
    if not ctx.broadcaster_id:
        return text("mod.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    fn = twitch_api.add_channel_moderator if action == "add" else twitch_api.remove_channel_moderator
    ok = await loop.run_in_executor(None, fn, ctx.broadcaster_id, users[0]["id"], ctx.access_token)
    verb = text("mod.added" if action == "add" else "mod.removed")
    return text("mod.done", user=users[0]["display_name"], verb=verb) if ok else text("mod.failed")


async def cmd_warn(ctx, user_name, arg_text):
    target_login, _, reason = arg_text.partition(" ")
    if not target_login:
        return text("warn.usage")
    reason = reason.strip() or text("reason.none")
    if not ctx.broadcaster_id or not ctx.moderator_id:
        return text("warn.unavailable")
    loop = asyncio.get_event_loop()
    users = await loop.run_in_executor(None, twitch_api.get_users, [target_login], ctx.access_token)
    if not users:
        return text("user.not_found", user=target_login)
    ok = await loop.run_in_executor(
        None, twitch_api.warn_user, ctx.broadcaster_id, ctx.moderator_id, users[0]["id"], reason, ctx.access_token
    )
    if ok:
        await events.bus.publish(
            events.MOD_ACTION, platform="twitch",
            user_name=users[0]["display_name"], reason=reason, action="warn",
        )
        return text("warn.done", user=users[0]["display_name"], reason=reason)
    return text("warn.failed")


TWITCH_DYNAMIC_MOD_COMMANDS = {
    "!title": cmd_title,
    "!game": cmd_game,
    "!so": cmd_shoutout,
    "!raid": cmd_raid,
    "!slow": cmd_slow,
    "!timeout": cmd_timeout,
    "!ban": cmd_ban,
    "!unban": cmd_unban,
    "!poll": cmd_poll,
    "!prediction": cmd_prediction,
    "!vip": cmd_vip,
    "!mod": cmd_mod,
    "!warn": cmd_warn,
}
