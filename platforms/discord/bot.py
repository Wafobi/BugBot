import discord
from discord.ext import commands, tasks
import asyncio
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta
from core import events, runtime_config
from core import feature as feature_api
from core import platform as platform_api

intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Läuft gerade ein Stream? Wird ausschließlich über den Event-Bus gepflegt (siehe
# _on_stream_online/_on_stream_offline weiter unten) - der Discord-Bot fragt Twitch dafür
# nicht mehr selbst, sondern erfährt es von der Plattform, die es ohnehin weiß.
is_live = False
start_time = datetime.now()
TOTAL_BANS = defaultdict(int)
TOTAL_MOD_ACTIONS = defaultdict(int)

# Alles Einstellbare - Rollen- und Kanalnamen, Farben, Texte, Befehlsnamen, Regeln,
# statische Befehle, Moderations-Schwellen - kommt aus discord.json und wird bei Änderung
# neu gelesen (siehe core/runtime_config.py). Im Code steht nichts davon noch einmal: die
# mitgelieferte Datei ist der Default-Stand, auf den gelöschte Texte zurückfallen.
#
# Namen sind hier keine Kosmetik. Der Bot findet Rollen und Kanäle über genau diese
# Zeichenketten - stimmt der Moderator-Rollenname nicht, ist auf diesem Server niemand
# Moderator, und das fällt erst auf, wenn ein Mod-Befehl wortlos nichts tut.
DISCORD_CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "discord.json")


def role_name(key):
    return DISCORD_CONFIG.section("roles").get(key, "")


def channel_name(key):
    return DISCORD_CONFIG.section("channels").get(key, "")


def reaction_roles():
    """Emoji -> Rollenname. Die _comment-Zeile aus der JSON ist keine Rolle."""
    return {
        emoji: name
        for emoji, name in DISCORD_CONFIG.section("reaction_roles").items()
        if not emoji.startswith("_")
    }


def find_channel(guild, key):
    """Der Kanal zu einem Schlüssel aus "channels", oder None - auch dann, wenn der Name
    leer ist (so schaltet man die Funktion ab, ohne den Kanal löschen zu müssen)."""
    name = channel_name(key)
    return discord.utils.get(guild.text_channels, name=name) if name else None


async def _render(template, author):
    """Ein statischer Befehl aus discord.json, fertig zum Absenden - dieselben Platzhalter
    wie auf Twitch, aus demselben Feature (features/variables). Genau dafür ist es eines:
    wer sich {steam} einmal definiert, hat es auf beiden Plattformen.

    Nur der Kontext ist plattformeigen: {u} bleibt die Erwähnung, damit der Befehl den
    Angesprochenen anpingt, {user} ist der reine Name für Sätze, in denen ein Ping stört,
    und {channel} ist hier der Server."""
    values = {
        "u": author.mention,
        "user": author.display_name,
        "channel": author.guild.name if author.guild else "",
    }
    for variables in events.bus.features_with(feature_api.VARIABLES):
        values.update(await variables.resolve(template, **values))
    return DISCORD_CONFIG.render(template, **values)


def get_discord_commands():
    # "_..."-Schlüssel sind Kommentare für den Bearbeiter der Datei, keine Befehle.
    commands_map = {
        name: value for name, value in DISCORD_CONFIG.get("commands", {}).items()
        if not name.startswith("_")
    }
    commands_map.setdefault("!rules", DISCORD_CONFIG.get("rules", ""))
    return commands_map


# Plattformname, wie er in jeder Bus-Meldung und in der DB auftaucht. Muss zu
# platform.py:DiscordPlatform.name passen.
NAME = "discord"


async def _publish_mod_action(user_name, reason, action):
    """Meldet eine Moderationsaktion auf den Bus. Wer sie mitschreibt - und ob überhaupt
    jemand - ist von hier aus nicht zu sehen."""
    await events.bus.publish(
        events.MOD_ACTION, platform=NAME, user_name=user_name, reason=reason, action=action,
    )


async def _record_command(name, user_name):
    await events.bus.publish(events.COMMAND, platform=NAME, command=name, user_name=user_name)


async def _send_command_reply(message, reply):
    """Antwort eines Befehls in den Kanal schicken, aus dem er kam. Feature-Befehle
    dürfen statt eines Strings eine Announcement zurückgeben - daraus wird hier ein Embed,
    auf Twitch eine Textzeile. So kann ein Feature einmal formulieren und trotzdem überall
    passend aussehen."""
    if not reply:
        return
    if isinstance(reply, platform_api.Announcement):
        await message.channel.send(embed=build_announcement_embed(reply))
    else:
        await message.channel.send(reply)


def moderation_overrides():
    """Der "moderation"-Abschnitt aus discord.json, so wie er dasteht. Gemergt und
    ausgewertet wird er im Moderations-Feature."""
    return DISCORD_CONFIG.get("moderation", {})


def is_discord_mod(member):
    """Administrator oder die in discord.json unter roles.moderator genannte Rolle."""
    moderator = role_name("moderator")
    return member.guild_permissions.administrator or (
        bool(moderator) and discord.utils.get(member.roles, name=moderator) is not None
    )

async def log_action(guild, title, description, color=None):
    report_channel = find_channel(guild, "log")
    if report_channel:
        color = DISCORD_CONFIG.color("log") if color is None else color
        embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
        await report_channel.send(embed=embed)


# --- Ankündigungen vom Event-Bus ---------------------------------------------------
# Discords Seite der Platform-API: aus einer neutralen core.platform.Announcement wird
# hier ein Embed im passenden Kanal. Ersetzt die früheren Einzelfunktionen
# post_bug_report/post_clip_announcement/announce_stream_online/offline, die jede für
# sich fast dasselbe taten - und die Twitch per verzögertem Import direkt aufrief.

def announce_channel_name(kind):
    """Kanalname für eine Ankündigungs-Art, oder None wenn Discord sie nicht darstellt.
    Die Zuordnung steht vollständig in discord.json unter "announce_channels" - eine neue
    Art bekommt dort einen Kanal, ohne dass hier etwas dazukommt."""
    channels = DISCORD_CONFIG.section("announce_channels")
    # "clip_channel" ist der Vorgänger von "announce_channels" und wird weiter beachtet,
    # damit bestehende discord.json-Dateien unverändert weiterlaufen.
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
    """Erfüllt core.platform.Platform.announce für Discord (siehe platform.py). True,
    sobald die Ankündigung in mindestens einem Server gepostet wurde."""
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
                print(f"⚠️ Ankündigung '{announcement.kind}' in #{channel_name} fehlgeschlagen: {e}")
        if announcement.log:
            await log_action(guild, announcement.title, announcement.text or announcement.url, announcement.color)
    return posted


# Zustand nachziehen, unabhängig davon, ob die Ankündigung auch gepostet wurde: der
# Live-Status im Statusbericht soll auch dann stimmen, wenn #🎥-stream-live fehlt.
async def _on_stream_online(announcement):
    global is_live
    is_live = True


async def _on_stream_offline(announcement):
    global is_live
    is_live = False


events.bus.subscribe(platform_api.STREAM_ONLINE, _on_stream_online)
events.bus.subscribe(platform_api.STREAM_OFFLINE, _on_stream_offline)


async def build_status_embed(guild):
    """Der Stundenbericht: eigene Zahlen dieses Bots (Laufzeit, was er in dieser Session
    weggeräumt hat) plus die All-Time-Zahlen aus dem Statistik-Feature. Die kennt Discord
    nicht selbst - fehlt das Feature, bleibt der Bericht eben auf den lokalen Teil
    beschränkt, statt gar nicht zu erscheinen."""
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


# Befehle mit Argumenten/Serveraktionen - anders als die statischen Text-Maps in
# discord.json. Handler bekommen (message, arg_text) und geben den zu postenden Text
# zurück (oder None für keine zusätzliche Antwort).

async def cmd_roles(message, arg_text):
    lines = "\n".join(
        DISCORD_CONFIG.text("roles.line", emoji=emoji, name=name)
        for emoji, name in reaction_roles().items()
    )
    title = DISCORD_CONFIG.text("roles.title", channel=channel_name("role_selection"))
    return f"{title}\n{lines}"


async def cmd_bug(message, arg_text):
    """Geht wie der Twitch-!bug über den Event-Bus - derselbe Weg für beide Plattformen,
    auch wenn er hier fast immer wieder in Discord selbst landet."""
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
        await message.channel.purge(limit=count + 1)  # +1 löscht auch den !purge-Aufruf selbst
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
    """Baut die komplette Kanal-/Rollenstruktur laut discord.json["setup"] neu auf -
    Nachfolger von server_setup.py, jetzt als Mod-Befehl statt separatem Skript.
    Löscht dabei ALLE Kanäle außer dem aktuellen, daher die Bestätigungspflicht und
    die eigene, striktere Administrator-Prüfung (is_discord_mod allein reicht hier
    nicht - eine reine Moderator-Rolle darf den Server nicht plattmachen können)."""
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

    # 1. Kanäle löschen (außer dem aktuellen)
    for channel in guild.channels:
        if channel.id != current_channel_id:
            try:
                await channel.delete()
            except Exception as e:
                print(f"⚠️ Kanal {channel.name} konnte nicht gelöscht werden: {e}")

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

    # 3. Kategorien & Kanäle erstellen (mit optionalem Permission-Override pro Kanal,
    # z.B. #📢-dev-logs strenger als der Rest seiner Kategorie)
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

    # 6. Aufräumen
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
    print("=== LIVE-BOT AKTIV ===")
    print(f"Eingeloggt als: {bot.user.name}")
    # Intervall aus discord.json, 0 schaltet den Bericht ab. Gesetzt wird es erst hier:
    # der Decorator unten läuft beim Import, und change_interval greift nur an einem
    # Loop, der noch nicht gestartet ist.
    hours = DISCORD_CONFIG.get("status_report_hours", 1)
    if not hours:
        print("ℹ️ Statusbericht deaktiviert (status_report_hours = 0).")
        return
    if status_report.is_running():
        # on_ready kommt nach jedem Reconnect erneut - ein zweites start() wäre ein
        # RuntimeError mitten im Ready-Handler.
        status_report.change_interval(hours=hours)
        return
    status_report.change_interval(hours=hours)
    status_report.start()

@tasks.loop(hours=1)
async def status_report():
    # discord.ext.tasks beendet einen Loop bei einer unbehandelten Exception endgültig -
    # ohne dieses try/except wäre der Statusbericht nach einem einzelnen Fehler (z.B.
    # fehlende Kanalrechte) bis zum nächsten Bot-Neustart weg.
    try:
        await bot.wait_until_ready()
        for guild in bot.guilds:
            report_channel = find_channel(guild, "log")
            if report_channel:
                await report_channel.send(embed=await build_status_embed(guild))
    except Exception as e:
        print(f"⚠️ Statusbericht fehlgeschlagen: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user: return
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
            print(f"⚠️ Honey-Pot-Bann fehlgeschlagen für {user} ({user.id}): {e}")

    msg_lower = message.content.lower()
    command_word, _, arg_text = message.content.partition(" ")
    command_word = command_word.lower()
    arg_text = arg_text.strip()

    msg = feature_api.Message(
        platform=NAME,
        user_id=str(message.author.id),
        user_name=message.author.display_name,
        text=message.content,
        # Mods/Admins sind von der Spam-/Bannwort-Filterung ausgenommen - dass daraus
        # "wird nicht moderiert" folgt, entscheidet das Moderations-Feature.
        is_privileged=is_discord_mod(message.author),
        command=command_word,
        arg_text=arg_text,
        raw=message,
    )

    await events.bus.publish(events.MESSAGE, message=msg)

    # Moderation: das erste Feature, das etwas beanstandet, gewinnt. Ist keines geladen,
    # wird schlicht nicht moderiert.
    for moderator in events.bus.features_with(feature_api.MODERATION):
        verdict = await moderator.review(msg, moderation_overrides())
        if verdict:
            await handle_discord_violation(message, verdict)
            return

    await events.bus.publish(events.MESSAGE_ACCEPTED, message=msg)

    # Befehle: Mod-Befehle (dynamisch + statisch) dürfen in jedem Kanal laufen - z.B.
    # !purge direkt dort, wo gerade Spam passiert - und nur für die Moderator-Rolle bzw.
    # Admins (roles.moderator). Öffentliche Befehle nur im Befehlskanal
    # (channels.commands); beide Namen stehen in discord.json.
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
        # Mod-Befehle der Features verhalten sich wie die eigenen: überall erlaubt,
        # aber nur für Moderatoren.
        if is_discord_mod(message.author):
            await _record_command(command_word, message.author.name)
            await _send_command_reply(message, await feature_command.handler(msg))
    elif not channel_name("commands") or message.channel.name == channel_name("commands"):
        # Öffentliche Befehle nur im dafür vorgesehenen Kanal - ist keiner konfiguriert,
        # gelten sie überall.
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
    """Führt das Urteil des Moderations-Features aus. Was ein Verstoß ist und ab wann ein
    Timeout fällig wird, steht nicht mehr hier (und auch nicht mehr ein zweites Mal im
    Twitch-Bot), sondern in features/moderation - hier bleibt nur, wie man auf Discord
    löscht und stummschaltet."""
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
            print(f"⚠️ Nachricht konnte nicht gelöscht werden ({message.id}): {e}")

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
            print(f"⚠️ Timeout fehlgeschlagen für {message.author} ({message.author.id}): {e}")


async def _on_level_up(message, level):
    """Der Levelaufstieg kommt vom Level-Feature; welche Rolle es dafür gibt, weiß nur
    Discord (discord.json "levels"). Genau diese Teilung ist der Grund, warum das Feature
    kein Rollen-Wissen braucht."""
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
