import discord
from discord.ext import commands, tasks
import asyncio
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta
from core import events, runtime_config
from core import feature as feature_api
from core import platform as platform_api

log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True
intents.members = True
intents.reactions = True

# The bot may mention individual people, but never everyone.
#
# Without this setting discord.py pings anything in a text that looks like a mention -
# including what just came out of the chat. An "!announce @everyone ..." would thereby be a
# way of borrowing the bot's rights: whoever may not do @everyone themselves could do it
# through the bot. The same via the reasons of !warn, !timeout and !ban, which are free text.
#
# users stays on, everyone and roles go off. The line runs there because a single mention is
# what these commands are for - a !warn notifies the person warned - whereas @everyone and a
# role ping wake half the guild. Where a message really is meant to reach everyone, you pass
# an allowed_mentions of your own at the send(); the intent then stands where it applies.
bot = commands.Bot(
    command_prefix="!", intents=intents,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
)

# Is a stream running? Maintained exclusively through the event bus (see
# _on_stream_online/_on_stream_offline further down) - the Discord bot no longer asks Twitch
# itself, but learns it from the platform that knows anyway.
is_live = False
start_time = datetime.now()
TOTAL_BANS = defaultdict(int)
TOTAL_MOD_ACTIONS = defaultdict(int)

# Everything adjustable - role and channel names, colours, texts, command names, rules,
# static commands, moderation thresholds - comes from discord.json and is re-read on change
# (see core/runtime_config.py). None of it appears a second time in the code: the shipped file
# is the default state deleted texts fall back to.
#
# Names are not cosmetics here. The bot finds roles and channels by exactly these strings - if
# the moderator role name is wrong, nobody on that server is a moderator, and that only
# becomes apparent when a mod command silently does nothing.
DISCORD_CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "discord.json")


def role_name(key):
    return DISCORD_CONFIG.section("roles").get(key, "")


def channel_name(key):
    return DISCORD_CONFIG.section("channels").get(key, "")


def reaction_roles():
    """Emoji -> role name. The _comment line from the JSON is not a role."""
    return {
        emoji: name
        for emoji, name in DISCORD_CONFIG.section("reaction_roles").items()
        if not emoji.startswith("_")
    }


def find_channel(guild, key):
    """The channel for a key from "channels", or None - including when the name is empty
    (that is how you switch the function off without having to delete the channel)."""
    name = channel_name(key)
    return discord.utils.get(guild.text_channels, name=name) if name else None


async def _render(template, author):
    """A static command from discord.json, ready to send - the same placeholders as on
    Twitch, from the same feature (features/variables). That is exactly why it is one: define
    {steam} once and you have it on both platforms.

    Only the context is platform-specific: {u} stays the mention, so the command pings the
    person addressed, {user} is the plain name for sentences a ping would disturb, and
    {channel} is the server here."""
    values = {
        "u": author.mention,
        "user": author.display_name,
        "channel": author.guild.name if author.guild else "",
    }
    for variables in events.bus.features_with(feature_api.VARIABLES):
        values.update(await variables.resolve(template, **values))
    return DISCORD_CONFIG.render(template, **values)


def get_discord_commands():
    # "_..." keys are comments for whoever edits the file, not commands.
    commands_map = {
        name: value for name, value in DISCORD_CONFIG.get("commands", {}).items()
        if not name.startswith("_")
    }
    commands_map.setdefault("!rules", DISCORD_CONFIG.get("rules", ""))
    return commands_map


# Platform name as it appears in every bus notification and in the DB. Has to match
# platform.py:DiscordPlatform.name.
NAME = "discord"


async def _publish_mod_action(user_name, reason, action):
    """Reports a moderation action onto the bus. Who records it - and whether anybody does -
    is not visible from here."""
    await events.bus.publish(
        events.MOD_ACTION, platform=NAME, user_name=user_name, reason=reason, action=action,
    )


async def _record_command(name, user_name):
    await events.bus.publish(events.COMMAND, platform=NAME, command=name, user_name=user_name)


async def _send_command_reply(message, reply):
    """Send a command's reply into the channel it came from. Feature commands may return an
    Announcement instead of a string - here that becomes an embed, on Twitch a line of text.
    So a feature can word things once and still look right everywhere."""
    if not reply:
        return
    if isinstance(reply, platform_api.Announcement):
        await message.channel.send(embed=build_announcement_embed(reply))
    else:
        await message.channel.send(reply)


def moderation_overrides():
    """The "moderation" section from discord.json, exactly as it stands. Merging and
    evaluating happen in the moderation feature."""
    return DISCORD_CONFIG.get("moderation", {})


def is_discord_mod(member):
    """Administrator, or the role named under roles.moderator in discord.json. False for
    anyone without guild_permissions (a discord.User rather than Member - a DM sender, or
    someone who has since left the guild) instead of raising."""
    permissions = getattr(member, "guild_permissions", None)
    if permissions is None:
        return False
    moderator = role_name("moderator")
    return permissions.administrator or (
        bool(moderator) and discord.utils.get(member.roles, name=moderator) is not None
    )

async def log_action(guild, title, description, color=None):
    report_channel = find_channel(guild, "log")
    if report_channel:
        color = DISCORD_CONFIG.color("log") if color is None else color
        embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
        await report_channel.send(embed=embed)


# --- Announcements from the event bus ----------------------------------------------
# Discord's side of the Platform API: a neutral core.platform.Announcement becomes an embed in
# the matching channel here. Replaces the former individual functions
# post_bug_report/post_clip_announcement/announce_stream_online/offline, which each did almost
# the same thing - and which Twitch called directly via a deferred import.

def announce_channel_name(kind):
    """Channel name for a kind of announcement, or None when Discord does not present it.
    The mapping lives entirely in discord.json under "announce_channels" - a new kind gets a
    channel there, without anything being added here."""
    channels = DISCORD_CONFIG.section("announce_channels")
    # "clip_channel" is the predecessor of "announce_channels" and is still honoured, so
    # that existing discord.json files keep running unchanged.
    if kind == platform_api.CLIP:
        return DISCORD_CONFIG.get("clip_channel") or channels.get(kind)
    return channels.get(kind)


def build_announcement_embed(announcement):
    embed = discord.Embed(
        title=announcement.title,
        description=announcement.text,
        color=announcement.color,
        timestamp=discord.utils.utcnow(),
    )
    if announcement.url:
        embed.url = announcement.url
    if announcement.image_url:
        embed.set_image(url=announcement.image_url)
    for field in announcement.fields:
        embed.add_field(name=field.name, value=field.value, inline=field.inline)
    footer = " · via ".join(part for part in (announcement.author, announcement.source) if part)
    if footer:
        embed.set_footer(text=footer)
    return embed


async def post_announcement(announcement):
    """Fulfils core.platform.Platform.announce for Discord (see platform.py). True as soon
    as the announcement was posted in at least one guild."""
    channel_name = announce_channel_name(announcement.kind)
    if not channel_name:
        return False

    embed = build_announcement_embed(announcement)
    content = "@everyone" if announcement.highlight else None
    posted = False

    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel:
            try:
                await channel.send(content=content, embed=embed)
                posted = True
            except discord.HTTPException as e:
                log.warning(f"Announcement '{announcement.kind}' in #{channel_name} failed: {e}")
        if announcement.log:
            await log_action(guild, announcement.title, announcement.text or announcement.url, announcement.color)
    return posted


# Update the state regardless of whether the announcement was posted: the live status in
# the status report should be right even when #🎥-stream-live is missing.
async def _on_stream_online(announcement):
    global is_live
    is_live = True


async def _on_stream_offline(announcement):
    global is_live
    is_live = False


events.bus.subscribe(platform_api.STREAM_ONLINE, _on_stream_online)
events.bus.subscribe(platform_api.STREAM_OFFLINE, _on_stream_offline)


async def build_status_embed(guild):
    """The hourly report: this bot's own numbers (uptime, what it cleared away this session)
    plus the all-time numbers from the statistics feature. Discord does not know those itself -
    if the feature is missing, the report simply stays limited to the local part rather than
    not appearing at all."""
    uptime = datetime.now() - start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    stream_status = DISCORD_CONFIG.text("status.stream.live" if is_live else "status.stream.offline")

    embed = discord.Embed(
        title=DISCORD_CONFIG.text("status.title"), color=DISCORD_CONFIG.color("status"),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name=DISCORD_CONFIG.text("status.uptime.name"),
                    value=DISCORD_CONFIG.text("status.uptime.value", hours=hours, minutes=minutes), inline=True)
    embed.add_field(name=DISCORD_CONFIG.text("status.stream.name"),
                    value=DISCORD_CONFIG.text("status.stream.value", status=stream_status), inline=True)
    embed.add_field(name=DISCORD_CONFIG.text("status.bans.name"),
                    value=DISCORD_CONFIG.text("status.bans.value", count=TOTAL_BANS[guild.id]), inline=False)
    embed.add_field(name=DISCORD_CONFIG.text("status.cleaned.name"),
                    value=DISCORD_CONFIG.text("status.cleaned.value", count=TOTAL_MOD_ACTIONS[guild.id]), inline=False)

    stats = events.bus.feature_with(feature_api.STATS)
    if stats is not None:
        for field in stats.summary_fields(await stats.summary()):
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
    return embed


# Commands with arguments/server actions - unlike the static text maps in discord.json.
# Handlers receive (message, arg_text) and return the text to post (or None for no additional
# reply).

async def cmd_roles(message, arg_text):
    lines = "\n".join(
        DISCORD_CONFIG.text("roles.line", emoji=emoji, name=name)
        for emoji, name in reaction_roles().items()
    )
    title = DISCORD_CONFIG.text("roles.title", channel=channel_name("role_selection"))
    return f"{title}\n{lines}"


async def cmd_bug(message, arg_text):
    """Goes over the event bus like the Twitch !bug - the same route for both platforms, even
    though here it almost always lands back in Discord itself."""
    if not arg_text:
        return DISCORD_CONFIG.text("bug.usage")
    delivered = await events.bus.announce(platform_api.Announcement(
        kind=platform_api.BUG_REPORT,
        title=DISCORD_CONFIG.text("bug.title"),
        text=arg_text,
        color=DISCORD_CONFIG.color("bug"),
        source=NAME,
        author=message.author.display_name,
    ))
    if delivered:
        return DISCORD_CONFIG.text("bug.thanks")
    return DISCORD_CONFIG.text(
        "bug.no_channel", channel=announce_channel_name(platform_api.BUG_REPORT) or "?",
    )


DISCORD_DYNAMIC_COMMANDS = {
    "!roles": cmd_roles,
    "!bug": cmd_bug,
    "!report": cmd_bug,
}


async def cmd_announce(message, arg_text):
    if not arg_text:
        return DISCORD_CONFIG.text("announce.usage")
    channel = find_channel(message.guild, "announcements")
    if not channel:
        return DISCORD_CONFIG.text("announce.no_channel", channel=channel_name("announcements"))
    try:
        await channel.send(DISCORD_CONFIG.text("announce.message", text=arg_text))
    except discord.Forbidden:
        return DISCORD_CONFIG.text("announce.forbidden", channel=channel_name("announcements"))
    return DISCORD_CONFIG.text("announce.done", channel=channel.mention)


async def cmd_warn(message, arg_text):
    if not message.mentions:
        return DISCORD_CONFIG.text("warn.usage")
    target = message.mentions[0]
    reason = arg_text.split(" ", 1)[1].strip() if " " in arg_text else DISCORD_CONFIG.text("reason.none")
    await log_action(
        message.guild, DISCORD_CONFIG.text("warn.log_title"),
        DISCORD_CONFIG.text("warn.log_text", target=target.mention,
                            moderator=message.author.mention, reason=reason),
        DISCORD_CONFIG.color("warn"),
    )
    await _publish_mod_action(target.name, reason, "warn")
    return DISCORD_CONFIG.text("warn.done", target=target.mention, reason=reason)


async def cmd_purge(message, arg_text):
    try:
        count = int(arg_text.strip())
    except ValueError:
        return DISCORD_CONFIG.text("purge.usage")
    count = max(1, min(count, 100))
    try:
        await message.channel.purge(limit=count + 1)  # +1 also deletes the !purge call itself
    except discord.Forbidden:
        return DISCORD_CONFIG.text("purge.forbidden")
    return None


async def cmd_slowmode(message, arg_text):
    try:
        seconds = int(arg_text.strip())
    except ValueError:
        return DISCORD_CONFIG.text("slowmode.usage")
    seconds = max(0, min(seconds, 21600))  # Discord-Limit: max. 6h
    try:
        await message.channel.edit(slowmode_delay=seconds)
    except discord.Forbidden:
        return DISCORD_CONFIG.text("slowmode.forbidden")
    if seconds:
        return DISCORD_CONFIG.text("slowmode.on", channel=message.channel.mention, seconds=seconds)
    return DISCORD_CONFIG.text("slowmode.off", channel=message.channel.mention)


async def cmd_timeout(message, arg_text):
    if not message.mentions:
        return DISCORD_CONFIG.text("timeout.usage")
    target = message.mentions[0]
    rest = arg_text.split(" ", 1)[1] if " " in arg_text else ""
    minutes_str, _, reason = rest.partition(" ")
    try:
        minutes = max(1, min(int(minutes_str), 40320))  # Discord-Limit: max. 28 Tage
    except ValueError:
        return DISCORD_CONFIG.text("timeout.usage")
    reason = reason.strip() or DISCORD_CONFIG.text("reason.none")
    try:
        await target.timeout(timedelta(minutes=minutes), reason=reason)
    except discord.Forbidden:
        return DISCORD_CONFIG.text("timeout.forbidden")
    await log_action(
        message.guild, DISCORD_CONFIG.text("timeout.log_title"),
        DISCORD_CONFIG.text("timeout.log_text", target=target.mention,
                            moderator=message.author.mention, minutes=minutes, reason=reason),
        DISCORD_CONFIG.color("danger"),
    )
    await _publish_mod_action(target.name, reason, "timeout")
    return DISCORD_CONFIG.text("timeout.done", target=target.mention, minutes=minutes, reason=reason)


async def cmd_ban(message, arg_text):
    if not message.mentions:
        return DISCORD_CONFIG.text("ban.usage")
    target = message.mentions[0]
    reason = arg_text.split(" ", 1)[1].strip() if " " in arg_text else DISCORD_CONFIG.text("reason.none")
    try:
        await message.guild.ban(target, reason=reason, delete_message_days=0)
    except discord.Forbidden:
        return DISCORD_CONFIG.text("ban.forbidden")
    await log_action(
        message.guild, DISCORD_CONFIG.text("ban.log_title"),
        DISCORD_CONFIG.text("ban.log_text", target=target.mention,
                            moderator=message.author.mention, reason=reason),
        DISCORD_CONFIG.color("danger"),
    )
    await _publish_mod_action(target.name, reason, "ban")
    return DISCORD_CONFIG.text("ban.done", target=target.mention, reason=reason)


async def cmd_unban(message, arg_text):
    user_id = arg_text.strip().lstrip("<@!").rstrip(">")
    if not user_id.isdigit():
        return DISCORD_CONFIG.text("unban.usage")
    try:
        user = await bot.fetch_user(int(user_id))
        await message.guild.unban(
            user, reason=DISCORD_CONFIG.text("unban.audit_reason", moderator=message.author),
        )
    except discord.NotFound:
        return DISCORD_CONFIG.text("unban.not_found")
    except discord.Forbidden:
        return DISCORD_CONFIG.text("unban.forbidden")
    await log_action(
        message.guild, DISCORD_CONFIG.text("unban.log_title"),
        DISCORD_CONFIG.text("unban.log_text", target=user.mention, moderator=message.author.mention),
        DISCORD_CONFIG.color("unban"),
    )
    await _publish_mod_action(user.name, "unban", "unban")
    return DISCORD_CONFIG.text("unban.done", target=user.mention)


def _build_overwrites(profile, everyone_role, role_objects):
    overwrites = {}
    everyone_perms = profile.get("everyone")
    if everyone_perms is not None:
        overwrites[everyone_role] = discord.PermissionOverwrite(
            read_messages=everyone_perms.get("read", True),
            send_messages=everyone_perms.get("send", True),
        )
    for role_name, perms in profile.get("roles", {}).items():
        role = role_objects.get(role_name)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=perms.get("read", True),
                send_messages=perms.get("send", True),
            )
    return overwrites


async def cmd_setup(message, arg_text):
    """Rebuilds the complete channel/role structure per discord.json["setup"] - successor to
    server_setup.py, now a mod command instead of a separate script. It deletes ALL channels
    except the current one, hence the mandatory confirmation and its own, stricter administrator
    check (is_discord_mod alone is not enough here - a plain moderator role must not be able to
    flatten the server)."""
    if not message.author.guild_permissions.administrator:
        return DISCORD_CONFIG.text("setup.admin_only")
    if arg_text.strip().lower() != "confirm":
        return DISCORD_CONFIG.text("setup.confirm")

    setup_cfg = DISCORD_CONFIG.section("setup")
    if not setup_cfg:
        return DISCORD_CONFIG.text("setup.missing")

    guild = message.guild
    current_channel_id = message.channel.id
    await message.channel.send(DISCORD_CONFIG.text("setup.working"))

    # 1. Delete channels (except the current one)
    for channel in guild.channels:
        if channel.id != current_channel_id:
            try:
                await channel.delete()
            except Exception as e:
                log.warning(f"Channel {channel.name} could not be deleted: {e}")

    # 2. Rollen erstellen
    role_objects = {}
    for role_def in setup_cfg.get("roles", []):
        role = discord.utils.get(guild.roles, name=role_def["name"])
        if not role:
            color = discord.Color(int(role_def["color"].lstrip("#"), 16))
            role = await guild.create_role(name=role_def["name"], color=color, hoist=True)
        role_objects[role_def["name"]] = role

    everyone_role = guild.default_role
    permission_profiles = setup_cfg.get("permission_profiles", {})
    rules_channel_name = setup_cfg.get("rules_channel")
    roles_channel_name = setup_cfg.get("roles_channel")
    rules_channel = None
    roles_channel = None

    # 3. Create categories and channels (with an optional permission override per channel,
    # e.g. #📢-dev-logs stricter than the rest of its category)
    for category_def in setup_cfg.get("categories", []):
        profile = permission_profiles.get(category_def.get("permission_profile"), {})
        overwrites = _build_overwrites(profile, everyone_role, role_objects)
        category = await guild.create_category(category_def["name"], overwrites=overwrites)

        for channel_entry in category_def.get("channels", []):
            if isinstance(channel_entry, dict):
                channel_name = channel_entry["name"]
                channel_profile_name = channel_entry.get("permission_profile")
            else:
                channel_name = channel_entry
                channel_profile_name = None

            if channel_profile_name:
                channel_profile = permission_profiles.get(channel_profile_name, {})
                channel_overwrites = _build_overwrites(channel_profile, everyone_role, role_objects)
                ch = await guild.create_text_channel(channel_name, category=category, overwrites=channel_overwrites)
            else:
                ch = await guild.create_text_channel(channel_name, category=category)

            if channel_name == rules_channel_name:
                rules_channel = ch
            elif channel_name == roles_channel_name:
                roles_channel = ch

    # 4. Regeln posten
    if rules_channel:
        await rules_channel.send(DISCORD_CONFIG.get("rules", "").strip())

    # 5. Rollenauswahl posten
    if roles_channel:
        msg = await roles_channel.send(setup_cfg.get("role_selection_message", ""))
        for emoji in reaction_roles():
            await msg.add_reaction(emoji)

    # 6. Clean up
    await message.channel.send(DISCORD_CONFIG.text("setup.done"))
    await asyncio.sleep(2)
    await message.channel.delete()
    return None


DISCORD_DYNAMIC_MOD_COMMANDS = {
    "!announce": cmd_announce,
    "!warn": cmd_warn,
    "!purge": cmd_purge,
    "!slowmode": cmd_slowmode,
    "!setup": cmd_setup,
    "!timeout": cmd_timeout,
    "!ban": cmd_ban,
    "!unban": cmd_unban,
}

@bot.event
async def on_ready():
    log.info("=== LIVE-BOT AKTIV ===")
    log.info(f"Eingeloggt als: {bot.user.name}")
    # Interval from discord.json, 0 switches the report off. It is only set here: the
    # decorator below runs at import time, and change_interval only takes on a loop that has
    # not been started yet.
    hours = DISCORD_CONFIG.get("status_report_hours", 1)
    if not hours:
        log.info("Statusbericht deaktiviert (status_report_hours = 0).")
        return
    if status_report.is_running():
        # on_ready comes again after every reconnect - a second start() would be a
        # RuntimeError in the middle of the ready handler.
        status_report.change_interval(hours=hours)
        return
    status_report.change_interval(hours=hours)
    status_report.start()

@tasks.loop(hours=1)
async def status_report():
    # discord.ext.tasks ends a loop for good on an unhandled exception - without this
    # try/except the status report would be gone after a single failure (e.g. missing channel
    # permissions) until the next bot restart.
    try:
        await bot.wait_until_ready()
        for guild in bot.guilds:
            report_channel = find_channel(guild, "log")
            if report_channel:
                await report_channel.send(embed=await build_status_embed(guild))
    except Exception as e:
        log.warning(f"Statusbericht fehlgeschlagen: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user: return
    if message.guild is None:
        # A DM, not a guild message. Moderation, roles and the honeypot channel all need a
        # guild context that simply is not there, so DMs are deliberately not processed at
        # all rather than risk half-working through them.
        return
    guild = message.guild

    honeypot = channel_name("honeypot")
    if honeypot and message.channel.name == honeypot:
        user = message.author
        if is_discord_mod(user): return
        try:
            await guild.ban(user, reason=DISCORD_CONFIG.text("honeypot.reason"), delete_message_days=1)
            TOTAL_BANS[guild.id] += 1
            await log_action(
                guild, DISCORD_CONFIG.text("honeypot.log_title"),
                DISCORD_CONFIG.text("honeypot.log_text", user=user.name, user_id=user.id),
                DISCORD_CONFIG.color("danger"),
            )
            await _publish_mod_action(user.name, "honey_pot", "ban")
            return
        except Exception as e:
            log.warning(f"Honeypot ban failed for {user} ({user.id}): {e}")

    msg_lower = message.content.lower()
    command_word, _, arg_text = message.content.partition(" ")
    command_word = command_word.lower()
    arg_text = arg_text.strip()

    msg = feature_api.Message(
        platform=NAME,
        user_id=str(message.author.id),
        user_name=message.author.display_name,
        text=message.content,
        # Mods/admins are exempt from the spam/banned-word filtering - whether that amounts
        # to "is not moderated" is decided by the moderation feature.
        is_privileged=is_discord_mod(message.author),
        command=command_word,
        arg_text=arg_text,
        raw=message,
    )

    await events.bus.publish(events.MESSAGE, message=msg)

    # Moderation: the first feature to object wins. If none is loaded, there simply is no
    # moderation.
    for moderator in events.bus.features_with(feature_api.MODERATION):
        verdict = await moderator.review(msg, moderation_overrides())
        if verdict:
            await handle_discord_violation(message, verdict)
            return

    await events.bus.publish(events.MESSAGE_ACCEPTED, message=msg)

    # Commands: mod commands (dynamic + static) may run in any channel - e.g. !purge right
    # where the spam is happening - and only for the moderator role resp. admins
    # (roles.moderator). Public commands only in the command channel (channels.commands); both
    # names live in discord.json.
    #
    # A non-moderator's mod-command attempt is silently ignored below (unlike Twitch, which
    # deletes the message and posts a refusal - see platforms/twitch/bot.py:deny_mod_command)
    # - a deliberate difference, not an inconsistency: deleting a Discord message is a
    # heavier, more visible action there (audit log entry, "message deleted" notice) than
    # dropping one IRC line, so a quiet no-op is the more proportionate response here.
    mod_commands = DISCORD_CONFIG.section("mod_commands")
    feature_command = events.bus.commands().get(command_word)
    dynamic_mod_commands = DISCORD_CONFIG.resolve_commands(DISCORD_DYNAMIC_MOD_COMMANDS)
    dynamic_commands = DISCORD_CONFIG.resolve_commands(DISCORD_DYNAMIC_COMMANDS)

    if command_word in dynamic_mod_commands:
        if is_discord_mod(message.author):
            await _record_command(command_word, message.author.name)
            await _send_command_reply(message, await dynamic_mod_commands[command_word](message, arg_text))
    elif msg_lower in mod_commands:
        if is_discord_mod(message.author):
            await _record_command(msg_lower, message.author.name)
            await message.channel.send(await _render(mod_commands[msg_lower], message.author))
    elif feature_command is not None and feature_command.mod_only:
        # The features' mod commands behave like our own: allowed everywhere, but only for
        # moderators.
        if is_discord_mod(message.author):
            await _record_command(command_word, message.author.name)
            await _send_command_reply(message, await feature_command.handler(msg))
    elif not channel_name("commands") or message.channel.name == channel_name("commands"):
        # Public commands only in the channel meant for them - if none is configured, they
        # apply everywhere.
        if command_word in dynamic_commands:
            await _record_command(command_word, message.author.name)
            await _send_command_reply(message, await dynamic_commands[command_word](message, arg_text))
        elif feature_command is not None:
            await _record_command(command_word, message.author.name)
            await _send_command_reply(message, await feature_command.handler(msg))
        else:
            commands_map = get_discord_commands()
            if msg_lower in commands_map:
                await _record_command(msg_lower, message.author.name)
                await message.channel.send(await _render(commands_map[msg_lower], message.author))

    await bot.process_commands(message)


async def handle_discord_violation(message, verdict):
    """Carries out the moderation feature's verdict. What counts as an offence and when a
    timeout is due no longer stands here (nor a second time in the Twitch bot), but in
    features/moderation - what remains here is only how to delete and time out on Discord."""
    guild = message.guild
    detail_suffix = DISCORD_CONFIG.text("violation.detail", detail=verdict.detail) if verdict.detail else ""

    if verdict.delete:
        try:
            await message.delete()
            TOTAL_MOD_ACTIONS[guild.id] += 1
            await log_action(
                guild, DISCORD_CONFIG.text("violation.delete.title"),
                DISCORD_CONFIG.text("violation.delete.text", author=message.author.mention,
                                    label=verdict.label, detail=detail_suffix),
                DISCORD_CONFIG.color("filtered"),
            )
            await _publish_mod_action(message.author.name, verdict.reason, "delete")
        except Exception as e:
            log.warning(f"Message could not be deleted ({message.id}): {e}")

    if verdict.timeout_seconds:
        try:
            await message.author.timeout(timedelta(seconds=verdict.timeout_seconds), reason=verdict.label)
            await log_action(
                guild, DISCORD_CONFIG.text("violation.timeout.title"),
                DISCORD_CONFIG.text("violation.timeout.text", author=message.author.mention,
                                    seconds=verdict.timeout_seconds, label=verdict.label,
                                    count=verdict.violation_count),
                DISCORD_CONFIG.color("danger"),
            )
            await _publish_mod_action(message.author.name, verdict.reason, "timeout")
        except Exception as e:
            log.warning(f"Timeout failed for {message.author} ({message.author.id}): {e}")


async def _on_level_up(message, level):
    """The level-up comes from the levels feature; which role you get for it is known only to
    Discord (discord.json "levels"). Exactly that division is why the feature needs no
    knowledge of roles."""
    if message.platform != NAME or message.raw is None:
        return
    origin = message.raw
    await origin.channel.send(
        DISCORD_CONFIG.text("levelup.message", user=origin.author.mention, level=level)
    )
    level_role = DISCORD_CONFIG.section("levels").get("role_thresholds", {}).get(str(level))
    if not level_role:
        return
    role = discord.utils.get(origin.guild.roles, name=level_role)
    if role:
        try:
            await origin.author.add_roles(role)
        except discord.Forbidden:
            pass


events.bus.subscribe(events.LEVEL_UP, _on_level_up)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id: return
    emoji_str = str(payload.emoji)
    roles = reaction_roles()
    if emoji_str in roles:
        guild = bot.get_guild(payload.guild_id)
        role = discord.utils.get(guild.roles, name=roles[emoji_str])
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.add_roles(role)
            member_role_name = role_name("member")
            member_role = discord.utils.get(guild.roles, name=member_role_name) if member_role_name else None
            if member_role: await member.add_roles(member_role)
            await log_action(
                guild, DISCORD_CONFIG.text("role.added.title"),
                DISCORD_CONFIG.text("role.added.text", user=member.name, role=role.name),
                DISCORD_CONFIG.color("role_added"),
            )

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.user_id == bot.user.id: return
    emoji_str = str(payload.emoji)
    roles = reaction_roles()
    if emoji_str in roles:
        guild = bot.get_guild(payload.guild_id)
        role = discord.utils.get(guild.roles, name=roles[emoji_str])
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.remove_roles(role)
            await log_action(
                guild, DISCORD_CONFIG.text("role.removed.title"),
                DISCORD_CONFIG.text("role.removed.text", user=member.name, role=role.name),
                DISCORD_CONFIG.color("role_removed"),
            )
