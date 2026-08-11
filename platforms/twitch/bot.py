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

# Wird gesetzt, sobald Twitch die IRC-Anmeldung mit "001 Welcome, GLHF!" bestätigt hat,
# und beim Verbindungsverlust wieder gelöscht - start_twitch_bot wartet darauf, bevor es
# die Startmeldung in den Chat schreibt.
_connected = asyncio.Event()

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"

# Alle Zeiten stehen in twitch.json unter "timings" und werden dort erklärt; hier nur der
# Zugriff. Sie am Verwendungsort zu lesen statt beim Import einzufrieren ist der Grund,
# warum eine Änderung ohne Neustart wirkt - beim nächsten Durchlauf der Schleife gilt sie.
#
# Wofür sie da sind, in Kürze: Twitch pingt von sich aus nur alle ~5 Minuten, und wenn die
# Verbindung still wegfällt (Router-/Firewall-Timeout, Netzwechsel), kommt nicht einmal ein
# FIN an - recv() blockiert dann für immer. Ohne eigenes Lebenszeichen merkt der Bot
# tagelang nicht, dass er taub ist (irc_ping_interval). Twitch-User-Tokens gelten meist nur
# ~4h; läuft der Token in einer Streampause ab, scheitert jeder Reconnect und Twitch
# widerruft alle EventSub-Abos (token_check_interval/token_refresh_margin). Und bleibt das
# session_keepalive von EventSub aus, ist die Session tot - eventsub_keepalive_grace ist der
# Puffer gegen Netzwerk-Jitter.


def timing(key, default):
    return TWITCH_CONFIG.section("timings").get(key, default)

BROADCASTER_ID = None
MODERATOR_ID = None

# Kurzer, hochzählender Schlüssel -> echte AutoMod-msg_id, damit Mods im Chat
# "!approve 3" statt der langen Helix-msg_id tippen müssen. Wird von handle_automod_hold
# befüllt und von !approve/!deny (siehe TWITCH_BOT_MOD_COMMANDS unten) konsumiert.
_automod_queue = {}
_automod_queue_counter = 0

# Zustand der laufenden Channel-Points-Verlosung (siehe !giveaway unten), oder None,
# wenn gerade keine läuft. entries bildet redemption_id -> (user_id, user_name) ab und
# wird von handle_reward_redemption befüllt.
_giveaway = None

# Läuft während einer Werbepause und meldet deren Ende (siehe handle_ad_break_begin).
_ad_break_task = None

# Follows werden gesammelt statt einzeln beantwortet: ein Follow-Bot-Schwall würde sonst
# eine Chat-Nachricht pro Follow erzeugen und das Twitch-Limit von 100 Nachrichten pro
# 30 Sekunden reißen - Twitch verwirft die dann stillschweigend und kann die Verbindung
# schließen. Erfasst wird weiterhin jeder einzelne Follow, gebündelt wird nur die Ansage.
# Fenster und Namensanzahl: twitch.json, "timings".
_pending_follows = []
_follow_batch_task = None

# Läuft gerade ein Stream? Gepflegt von _go_live/_go_offline, die beide über
# stream.online/stream.offline (EventSub) bzw. den Live-Abgleich beim Start angestoßen
# werden. Lag früher als `is_live` im Discord-Bot - der musste dafür den Twitch-Bot
# importieren, obwohl es ein rein twitchseitiger Zustand ist.
_is_live = False

# Zuschauerzahlen kennt nur die Helix-API, ein EventSub-Event dafür gibt es nicht -
# dieser eine Wert bleibt also Polling (siehe _viewer_sample_loop, viewer_sample_interval).

# Alles Einstellbare - Texte, Zeiten, Farben, Befehlsnamen, Regeln, statische Befehle,
# Moderations-Schwellenwerte - kommt aus twitch.json und wird bei Änderung neu gelesen
# (siehe core/runtime_config.py). Die Datei selbst liegt in config.py, damit auch
# commands.py sie lesen kann; hier nur der gewohnte Name.
TWITCH_CONFIG = config.TWITCH_CONFIG
text = config.text


async def _render(template, user_name):
    """Ein statischer Befehl aus twitch.json, fertig für den Chat.

    Was die Plattform selbst weiß, setzt sie selbst ein: {u}/{user} ist der, der den
    Befehl geschrieben hat, {channel} der Kanal. Alles Weitere - {time}, {date} und was
    der Betreiber sich in features/variables/variables.json definiert hat - kommt vom
    VARIABLES-Feature, und zwar über seine Fähigkeit, nicht über einen Import: läuft der
    Bot ohne dieses Feature, bleiben eben nur die drei hier, und der Rest steht als Text
    da. Ein Befehl fällt dadurch nie ganz aus."""
    values = {"u": user_name, "user": user_name, "channel": config.TWITCH_CHANNEL}
    for variables in events.bus.features_with(feature_api.VARIABLES):
        values.update(await variables.resolve(template, **values))
    return TWITCH_CONFIG.render(template, **values)


def _clock(moment):
    """`moment` (mit Zeitzone) als Uhrzeit für den Chat.

    In derselben Zeitzone wie {time}, und aus derselben Quelle: der Konfiguration des
    VARIABLES-Features, geholt über dessen Fähigkeit statt über einen Import - wie in
    _render darüber. Damit muss die Zeitzone nicht ein zweites Mal in twitch.json stehen,
    und eine Änderung dort wirkt hier sofort mit.

    Fehlt das Feature oder ist dort keine Zeitzone eingetragen, bleibt die des Prozesses.
    Die ist im Container die des Hosts (bugbot.container, Timezone=), ohne diese Zeile
    UTC - deshalb ist der Eintrag in variables.json die verlässlichere Angabe."""
    for variables in events.bus.features_with(feature_api.VARIABLES):
        zone = variables.zone()
        if zone:
            return moment.astimezone(zone).strftime("%H:%M:%S")
    return moment.astimezone().strftime("%H:%M:%S")


def get_twitch_commands():
    # Schlüssel mit Unterstrich sind Erklärungen für den, der die Datei bearbeitet (JSON
    # kennt keine Kommentare), kein Befehl - sonst stünde "_comment" gleich in !commands.
    commands_map = {
        name: value for name, value in TWITCH_CONFIG.get("commands", {}).items()
        if not name.startswith("_")
    }
    rules = TWITCH_CONFIG.get("rules", "")
    # u bleibt absichtlich ein Platzhalter: die Befehlstabelle wird einmal gebaut, den
    # Namen setzt erst der Aufrufer je Nachricht ein (wie bei jedem statischen Befehl).
    commands_map.setdefault("!rules", TWITCH_CONFIG.text("rules.line", u="{u}", rules=rules))
    return commands_map


# Plattformname, wie er in jeder Bus-Meldung und in der DB auftaucht. Muss zu
# platform.py:TwitchPlatform.name passen.
NAME = "twitch"


async def _publish_event(event_type, user_name, amount=0):
    """Meldet ein Live-Ereignis (Follow/Sub/Cheer/Raid/...) auf den Bus. Wer es
    mitschreibt - und ob überhaupt jemand - ist von hier aus nicht zu sehen; vorher stand
    an jeder dieser Stellen ein stats.record_event mit voller Signatur."""
    await events.bus.publish(
        events.PLATFORM_EVENT, platform=NAME, event_type=event_type,
        user_name=user_name, amount=amount,
    )


async def _publish_mod_action(user_name, reason, action):
    await events.bus.publish(
        events.MOD_ACTION, platform=NAME, user_name=user_name, reason=reason, action=action,
    )


def moderation_overrides():
    """Der "moderation"-Abschnitt aus twitch.json, so wie er dasteht. Gemergt und
    ausgewertet wird er im Moderations-Feature - hier bleibt nur, woher er kommt,
    damit die Hot-Reload-Konfiguration weiter greift."""
    return TWITCH_CONFIG.get("moderation", {})


# Kleine, bot-eigene Befehle (Introspektion, plattformübergreifende Bug-Reports) -
# anders als commands.py, das reine Helix-API-Befehle bündelt.

async def cmd_list_commands(ctx, user_name, arg_text):
    """Listet nur die Befehle, die user_name tatsächlich nutzen darf - Mod-Befehle also
    nur für Broadcaster/Moderatoren (ctx.is_privileged), sonst würde !commands selbst
    Nicht-Mods Befehle vorschlagen, die deny_mod_command sofort wieder löschen würde."""
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
    """Der Bug-Report geht an den Event-Bus, nicht an Discord: welche Plattform ihn
    darstellt (und ob überhaupt eine), entscheidet sich erst dort. Deshalb hier auch
    keine Discord-Formulierung mehr im Fehlerfall."""
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
    """Die botseitigen Befehle unter ihren tatsächlichen Namen (twitch.json,
    "command_names")."""
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


# Bot-eigene Mod-Befehle - brauchen wie TWITCH_BOT_COMMANDS keinen TwitchContext-Import-
# Umweg, hier zusätzlich weil sie direkt auf _automod_queue (Modul-globaler Zustand)
# zugreifen. Werden wie twitch_commands_file.TWITCH_DYNAMIC_MOD_COMMANDS per
# is_mod_command in twitch_chat_listener vor Nicht-Moderatoren geschützt.
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
    return (
        f"🎉 Verlosung gestartet: \"{title}\" - löse den Channel-Points-Reward "
        f"\"{title}\" für {cost_str} Punkte ein, um teilzunehmen! Mods beenden mit !giveaway pick."
    )


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
    """!giveaway start <Punkte> <Titel> | !giveaway pick | !giveaway cancel - Zustand
    lebt in _giveaway (Modul-global), Teilnahmen kommen über handle_reward_redemption
    per EventSub rein, sobald jemand den passenden Channel-Points-Reward einlöst."""
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
    """Parst das Tags-Präfix einer IRC-Zeile (z.B. '@badges=moderator/1;id=abc-123') in ein Dict."""
    tags = {}
    for pair in raw_tags.lstrip("@").split(";"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            tags[key] = value
    return tags

class _AuthFailed(Exception):
    """Twitch hat die IRC-Anmeldung abgelehnt - der Access-Token muss erneuert werden,
    bevor ein Reconnect Sinn ergibt (siehe twitch_chat_listener)."""


async def _send_raw(line):
    """Schickt eine rohe IRC-Zeile. Wirft, wenn keine Verbindung (mehr) steht - der
    Reader-Loop fängt das und baut die Verbindung neu auf."""
    if _writer is None:
        raise ConnectionError("keine Twitch-IRC-Verbindung")
    _writer.write(f"{line}\r\n".encode("utf-8"))
    await _writer.drain()


async def send_twitch_chat(message_text):
    """True, wenn die Nachricht rausging - erfüllt damit gleichzeitig
    core.platform.Platform.send_text (siehe platforms/twitch/platform.py)."""
    try:
        await _send_raw(f"PRIVMSG #{config.TWITCH_CHANNEL.lower()} :{message_text}")
        print(f"💬 Twitch-Chat gesendet: {message_text}")
        return True
    except Exception as e:
        print(f"⚠️ Fehler beim Senden an Twitch: {e}")
        return False


async def _connect_and_auth(token):
    """Baut die IRC-Verbindung auf. asyncio-Streams statt eines rohen Sockets, weil
    readline() zeilenweise puffert - der frühere recv(2048) konnte PINGs mitten in einem
    Chunk übersehen (und damit den Rauswurf durch Twitch provozieren) und riss bei einem
    über die Chunk-Grenze zerschnittenen UTF-8-Zeichen den Decoder ab."""
    global _reader, _writer
    _reader, _writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)

    # Tags liefert Badges (mod/subscriber) und die Message-ID (für /delete);
    # ohne diese Capability können wir keine gezielte Moderation durchführen.
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
    """Liste aller EventSub-Abos, die bei jedem frischen session_welcome (neu) angemeldet
    werden: (type, version, condition, Klartext-Label fürs Log). automod.message.hold und
    channel.follow brauchen zusätzlich MODERATOR_ID im condition - werden übersprungen,
    falls die (noch) nicht aufgelöst ist. Wird als Funktion aufgerufen (nicht als
    Modul-Konstante), damit BROADCASTER_ID/MODERATOR_ID zum Aufrufzeitpunkt aktuell sind."""
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
        # Ab hier: Vollständigkeit der Erfassung. Alles davon landet über
        # record_eventsub_notification ohnehin im Rohprotokoll - Handler gibt es nur für
        # das, was zusätzlich in eine typisierte Tabelle oder in den Chat soll.
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
    """Meldet das Ende der Werbepause. Twitch hat dafür kein EventSub-Event - nur
    channel.ad_break.begin mit der Dauer -, also warten wir sie selbst ab. Läuft als
    eigener Task, damit der EventSub-Listener nicht minutenlang blockiert."""
    try:
        await asyncio.sleep(delay_seconds)
        await send_twitch_chat(text("ad_break.end"))
        print("📺 Werbepause beendet.")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"⚠️ Werbe-Ende-Meldung fehlgeschlagen: {e}")


async def handle_ad_break_begin(event):
    """Postet Start, Dauer und Endzeit einer Werbepause in den Twitch-Chat - und meldet
    sich nach Ablauf der Dauer noch einmal, wenn die Werbung durch ist."""
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

    # Restdauer ab jetzt, nicht ab started_at - die Benachrichtigung kann verzögert
    # ankommen, sonst würde die Entwarnung zu spät kommen.
    remaining = (start_at + timedelta(seconds=duration) - datetime.now(timezone.utc)).total_seconds()
    if _ad_break_task and not _ad_break_task.done():
        _ad_break_task.cancel()
    if remaining > 0:
        _ad_break_task = asyncio.create_task(_announce_ad_break_end(remaining), name="twitch-ad-break-end")


async def handle_automod_hold(event):
    """Vergibt einen kurzen Schlüssel für eine von AutoMod zurückgehaltene Nachricht,
    merkt sich die echte msg_id in _automod_queue und postet die Nachricht mitsamt
    Kategorie in den Chat, damit ein Mod per !approve/!deny <Schlüssel> entscheiden kann."""
    global _automod_queue_counter
    _automod_queue_counter += 1
    key = str(_automod_queue_counter)
    _automod_queue[key] = event.get("message_id")
    held_text = (event.get("message") or {}).get("text", "")
    user = event.get("user_login", "unbekannt")
    category = event.get("category", "?")
    print(f"🚧 AutoMod hält Nachricht #{key} von {user} zurück ({category}).")
    await send_twitch_chat(config.text(
        "automod.hold", key=key, user=user, category=category, text=held_text[:200],
    ))


# Sämtliche Stream-Kennzahlen (Zuschauer, Subs, Bits, Follows, Hype Train, ...) werden
# nur noch auf den Bus gemeldet; ausgewertet werden sie im Statistik-Feature, das sie
# einer Stream-Session zuordnet. Früher liefen die Höchstwerte parallel als Zähler-Dict
# im RAM mit, was jeden Bot-Neustart mitten im Stream nicht überlebte.


async def handle_channel_subscribe(event):
    """Feuert auch für Gift-Sub-Empfänger (event['is_gift'] == True) - die werden hier
    nur gezählt, aber nicht extra im Chat angekündigt, weil handle_channel_subscription_gift
    bereits eine Sammel-Ankündigung für den Gifter postet (sonst doppelte Meldung)."""
    user_name = event.get("user_name") or event.get("user_login") or "jemand"
    await _publish_event("sub", user_name)
    if not event.get("is_gift"):
        tier = event.get("tier", "1000")
        await send_twitch_chat(text("sub.new", user=user_name, tier=tier[0]))


async def handle_channel_subscription_gift(event):
    """Anonyme Gifter werden als 'gift_sub_anon' erfasst: sie zählen in die Stream-Summe
    mit, bleiben aber aus der !leaderboard-Bestenliste raus (dort stünde sonst dauerhaft
    'Anonym' oben)."""
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
        # Wie bei den Gift-Subs: zählt in die Bits-Summe des Streams, nicht ins Leaderboard.
        await _publish_event("cheer_anon", "Anonym", bits)
        return
    await _publish_event("cheer", user_name, bits)
    message_text = event.get("message", "")
    suffix = config.text("sub.message_suffix", text=message_text[:200]) if message_text else ""
    await send_twitch_chat(config.text("cheer", user=user_name, bits=bits, message=suffix))


async def _flush_follow_batch():
    """Wartet kurz auf weitere Follows und postet dann eine Sammelmeldung. Bei sehr vielen
    Follows auf einmal werden nur die ersten Namen genannt, der Rest als Anzahl - eine
    IRC-Nachricht darf ohnehin nur ~500 Zeichen lang sein."""
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
    """Jeder Follow wird einzeln in der DB erfasst; die Chat-Ansage läuft über
    _flush_follow_batch gesammelt (Fenster: twitch.json, timings.follow_batch_window)."""
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
    """Erfasst jede Channel-Points-Einlösung; die Giveaway-Logik darunter greift nur,
    während !giveaway eine Verlosung laufen hat."""
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
    """Meldet den Streamstart: neue stats-Session öffnen und alle Plattformen über den
    Event-Bus informieren. Wird sowohl von EventSub (stream.online) als auch beim
    Bot-Start aufgerufen, falls der Stream da schon lief - daher die _is_live-Sperre."""
    global _is_live
    if _is_live or not stream_info:
        return
    _is_live = True

    title = stream_info.get("title") or "Live-Stream"
    category = stream_info.get("game_name") or "Ohne Kategorie"
    # {width}/{height} sind Platzhalter in der von Twitch gelieferten URL, und der
    # Zeitstempel verhindert, dass Discord das (alte) Vorschaubild zwischenspeichert.
    preview_url = stream_info.get("thumbnail_url", "")
    if preview_url:
        preview_url = preview_url.replace("{width}", "1280").replace("{height}", "720")
        preview_url += f"?t={int(datetime.now().timestamp())}"

    # Ab hier ordnen die aufzeichnenden Features alles Gemeldete (Chat, Befehle,
    # Mod-Aktionen, Events, Werbepausen, Zuschauer-Samples) dieser Session zu. Bewusst
    # vor der Ankündigung: die Session muss offen sein, bevor irgendetwas hereinkommt.
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
    """Gegenstück zu _go_live: Session schließen, Highscores abgleichen und den
    Abschlussbericht als Ankündigung verteilen."""
    global _is_live
    if not _is_live:
        return
    _is_live = False

    # Die aufzeichnenden Features schließen die Session, gleichen die Rekorde ab und
    # geben die Kennzahlen als fertige Felder zurück - der Twitch-Bot muss das
    # Kennzahlen-Dict dafür nicht kennen. Ist kein solches Feature geladen, bleibt der
    # Abschlussbericht eben ohne Zahlen.
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
    """Das EventSub-Event enthält weder Titel noch Kategorie, daher ein zusätzlicher
    get_stream_info-Aufruf."""
    loop = asyncio.get_event_loop()
    stream_info = await loop.run_in_executor(
        None, twitch_api.get_stream_info, BROADCASTER_ID, config.TWITCH_CHAT_ACCESS_TOKEN
    )
    await _go_live(stream_info)


async def handle_stream_offline(event):
    await _go_offline()


async def _reconcile_live_status():
    """Einmaliger Live-Abgleich beim Start: EventSub feuert nur bei einem *Wechsel*, ein
    Neustart während eines bereits laufenden Streams würde also sonst nie eine
    Live-Meldung auslösen.

    Wartet vorher auf die übrigen Plattformen: Discord kennt vor seinem on_ready keine
    Server und würde die Ankündigung stillschweigend verwerfen (siehe
    core.platform.Platform.wait_ready)."""
    if not BROADCASTER_ID:
        print("⚠️ Live-Status-Abgleich übersprungen: Broadcaster-ID nicht verfügbar.")
        return
    await events.bus.wait_ready(timeout=timing("platform_ready_timeout", 120))
    loop = asyncio.get_event_loop()
    stream_info = await loop.run_in_executor(
        None, twitch_api.get_stream_info, BROADCASTER_ID, config.TWITCH_CHAT_ACCESS_TOKEN
    )
    if stream_info:
        await _go_live(stream_info)


async def _viewer_sample_loop():
    """Schreibt die Zuschauerzahl während eines laufenden Streams mit. Twitch hat dafür
    kein EventSub-Event, das bleibt also Polling. Lief früher im Discord-Bot, hatte dort
    aber nichts zu suchen: er brauchte dafür BROADCASTER_ID und den Chat-Token aus
    platforms/twitch."""
    while True:
        await asyncio.sleep(timing("viewer_sample_interval", 60))
        if not _is_live or not BROADCASTER_ID:
            continue
        # Eine durchgereichte Exception würde den Loop zwar neu starten lassen
        # (_supervised), aber mit timings.task_restart_delay Verzögerung - für ein reines
        # Sampling ist Weitermachen die bessere Antwort.
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
    """Titel-/Kategoriewechsel mitten im Stream. Ohne das würde ein Stream für immer unter
    der Kategorie laufen, die beim Einschalten gesetzt war."""
    title = event.get("title") or ""
    game_name = event.get("category_name") or ""
    changed = any(await events.bus.publish(
        events.STREAM_SEGMENT, platform=NAME, title=title, category=game_name
    ))
    if changed:
        print(f"📝 Stream-Update: \"{title}\" ({game_name})")


async def handle_channel_ban(event):
    """Bans/Timeouts, die ein Mensch (oder ein anderer Bot) ausgelöst hat - die eigenen
    Aktionen protokolliert handle_twitch_violation bereits selbst. is_permanent
    unterscheidet Ban von Timeout."""
    user_name = event.get("user_name") or event.get("user_login") or "unbekannt"
    action = "ban" if event.get("is_permanent") else "timeout"
    reason = event.get("reason") or "manuell"
    await _publish_mod_action(user_name, reason, action)


async def handle_channel_unban(event):
    user_name = event.get("user_name") or event.get("user_login") or "unbekannt"
    await _publish_mod_action(user_name, "unban", "unban")


async def handle_hypetrain_end(event):
    """Das erreichte Endlevel ist verlässlicher als der letzte progress-Zwischenstand."""
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
    """Jeder Zwischenstand wird als eigenes Event mit dem Level als `amount` abgelegt -
    die Auswertung nimmt daraus das Maximum je Stream (siehe features/stats/store.py)."""
    level = int(event.get("level") or 0)
    await _publish_event("hypetrain", config.TWITCH_CHANNEL, level)


# subscription_type -> Handler, konsultiert im "notification"-Zweig von
# twitch_eventsub_listener. Jeder Handler bekommt nur das rohe event-Dict.
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
    # channel.hype_train.begin, channel.poll.end, channel.prediction.end und
    # channel.goal.end haben bewusst keinen Handler - sie werden über das Rohprotokoll
    # (record_eventsub_notification) vollständig erfasst und sind dort auswertbar.
}


async def twitch_eventsub_listener():
    """Hält eine EventSub-WebSocket-Verbindung offen, meldet nach jedem frischen
    session_welcome alle Abos aus _eventsub_subscriptions() an und dispatcht deren
    Benachrichtigungen über _EVENTSUB_HANDLERS. Bei session_reconnect wandern bestehende
    Abos laut Twitch automatisch mit auf die neue Session (kein Neu-Abo nötig); nach einem
    Verbindungsabbruch sind die alten Abos dagegen weg und müssen neu abonniert werden.
    Bleiben die session_keepalive-Nachrichten aus, gilt die Session als tot - ohne diese
    Prüfung konnte der Listener stumm an einer längst toten Session hängen."""
    url = EVENTSUB_WS_URL
    resubscribe = True
    loop = asyncio.get_event_loop()
    while True:
        try:
            async with websockets.connect(url) as ws:
                # Ein reconnect_url gilt nur für genau diesen einen Verbindungsaufbau.
                url = EVENTSUB_WS_URL
                keepalive_timeout = 30
                while True:
                    try:
                        deadline = keepalive_timeout + timing("eventsub_keepalive_grace", 10)
                        raw = await asyncio.wait_for(ws.recv(), timeout=deadline)
                    except asyncio.TimeoutError:
                        raise ConnectionError(f"kein Keepalive seit {deadline}s")

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
                        # Erst wegschreiben, dann verarbeiten: ein Fehler im Handler darf
                        # nicht dazu führen, dass das Ereignis nirgends dokumentiert ist.
                        await events.bus.publish(events.RAW_EVENT, platform=NAME, event_type=sub_type, payload=event)
                        handler = _EVENTSUB_HANDLERS.get(sub_type)
                        if handler:
                            try:
                                await handler(event)
                            except asyncio.CancelledError:
                                raise
                            except Exception as e:
                                # Ein kaputter Handler darf nicht die ganze EventSub-
                                # Session mitreißen - dann fielen alle anderen Events aus.
                                print(f"⚠️ Fehler im EventSub-Handler für {sub_type}: {e}")

                    elif msg_type == "revocation":
                        subscription = msg["payload"]["subscription"]
                        status = subscription.get("status")
                        print(f"⚠️ EventSub-Abo {subscription.get('type')} widerrufen ({status}).")
                        if status == "authorization_revoked":
                            # Token ungültig geworden: erst erneuern, dann mit frischer
                            # Session alles neu abonnieren. Ein bloßes Merken hätte hier
                            # nichts gebracht - diese Session liefert nichts mehr.
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
    """Loggt beim Start, welche Berechtigungen der Chat-Token tatsächlich hat und
    was der Bot damit tun kann - Klartext-Gegenstück zur manuellen Prüfung über
    https://id.twitch.tv/oauth2/validate."""
    if scopes is None:
        print("⚠️ Token-Scopes konnten nicht abgefragt werden (siehe Fehler oben).")
        return

    print(f"🔑 Twitch-Token hat {len(scopes)} Scope(s):")
    known = [s for s in scopes if s in twitch_scopes.CAPABILITIES]
    unknown = [s for s in scopes if s not in twitch_scopes.CAPABILITIES]
    for scope in known:
        print(f"   ✅ {scope} -> {twitch_scopes.CAPABILITIES[scope]}")
    if unknown:
        print(f"   ℹ️ weitere Scopes ohne Bot-Funktion: {', '.join(unknown)}")

    # Gegen dieselbe Liste, die get_token.py beim Erzeugen anfordert: kommt ein
    # Scope in config dazu, ohne dass der Token neu geholt wurde, sagt der Start es hier.
    missing = [s for s in twitch_scopes.REQUIRED if s not in scopes]
    if missing:
        print(f"   ⚠️ {len(missing)} benötigte(r) Scope(s) fehlen: {', '.join(missing)}")
        print("      -> Token mit 'python3 -m platforms.twitch.get_token' neu erzeugen "
              "(Twitch erweitert bestehende Tokens nicht nachträglich).")

    for scope, warning in twitch_scopes.DANGEROUS_UNNEEDED.items():
        if scope in scopes:
            print(f"   🚨 unnötig riskanter Scope vorhanden: {scope} ({warning}) - beim nächsten Token-Refresh entfernen")


async def twitch_token_refresh_loop():
    """Erneuert den Chat-Token, bevor er abläuft. Die Helix-Aufrufe refreshen zwar bei
    einem 401 selbst (siehe api._helix_request), aber genau die passieren im Leerlauf
    nicht: nach ein paar stillen Stunden wäre der Token sonst tot, der nächste
    IRC-Reconnect würde scheitern und die EventSub-Abos wären widerrufen."""
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(timing("token_check_interval", 1800))
        info = await loop.run_in_executor(None, twitch_api.validate_token_info, config.TWITCH_CHAT_ACCESS_TOKEN)
        expires_in = info.get("expires_in") if info else None
        # expires_in == 0 heißt bei Twitch "läuft nie ab" (langlebige Tokens mancher Apps),
        # nicht "gerade abgelaufen" - solche Tokens hier zu erneuern wäre nicht nur unnötig,
        # sondern lief vorher alle 30 Minuten in einen fehlgeschlagenen Refresh.
        if expires_in == 0:
            continue
        if expires_in is None or expires_in < timing("token_refresh_margin", 3600):
            await loop.run_in_executor(None, twitch_api.refresh_chat_token)


async def _supervised(name, coro_factory):
    """Hält einen der endlosen Hintergrund-Loops am Leben. Vorher wurde ein abgestürzter
    Task nur geloggt und blieb dann tot - der Bot lief weiter, aber z.B. ohne
    EventSub-Events oder ohne Token-Erneuerung, bis jemand ihn von Hand neu startete.
    Genau die Art stiller Teilausfall, die die Uptime auf dem Papier gut aussehen lässt."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            delay = timing("task_restart_delay", 10)
            print(f"⚠️ Hintergrundtask {name} abgestürzt: {e!r} - Neustart in {delay}s")
        else:
            delay = timing("task_restart_delay", 10)
            print(f"⚠️ Hintergrundtask {name} unerwartet beendet - Neustart in {delay}s")
        await asyncio.sleep(delay)


def _warn_if_task_died(task):
    """Letzte Absicherung: dass sogar der Supervisor endet, sollte nie passieren - wenn
    doch, darf es nicht stillschweigend geschehen."""
    if task.cancelled():
        return
    print(f"🚨 Twitch-Supervisor {task.get_name()} beendet: {task.exception()!r}")


async def start_twitch_bot():
    global BROADCASTER_ID, MODERATOR_ID
    global _listener_task, _eventsub_task, _token_task, _viewer_task, _reconcile_task
    loop = asyncio.get_event_loop()

    # Der Reader baut die Verbindung selbst auf (und immer wieder neu) - deshalb hier
    # zuerst starten: selbst wenn Twitch beim Start nicht erreichbar ist, versucht er
    # es weiter, statt den Twitch-Teil des Bots dauerhaft tot zurückzulassen.
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
            "⚠️ Broadcaster-/Moderator-ID nicht auflösbar - Twitch-Delete/Timeout sind deaktiviert. "
            "Prüfe, ob TWITCH_CHAT_ACCESS_TOKEN die Scopes moderator:manage:chat_messages und "
            "moderator:manage:banned_users besitzt und der Account im Kanal Moderator ist."
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

    # Läuft als eigener Task, weil er auf die Bereitschaft der übrigen Plattformen
    # wartet (bis zu timings.platform_ready_timeout). Inline würde eine Plattform, die nie
    # bereit wird, hier den kompletten Twitch-Start blockieren - inklusive der
    # Startmeldung im Chat.
    _reconcile_task = asyncio.create_task(_reconcile_live_status(), name="twitch-live-reconcile")

    try:
        await asyncio.wait_for(_connected.wait(), timeout=60)
        await send_twitch_chat(text("startup"))
    except asyncio.TimeoutError:
        print("⚠️ Twitch-IRC nach 60s noch nicht verbunden - Startmeldung übersprungen, der Reader versucht es weiter.")


async def close():
    """Beendet alle Hintergrundtasks (Chat-Reader, EventSub-Listener, Token-Wächter,
    Zuschauer-Sampling, Live-Abgleich, laufende Ansagen) und die IRC-Verbindung sauber -
    z.B. bei Strg+C oder wenn eine andere Plattform abstürzt."""
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
    """Führt das Urteil des Moderations-Features aus. Was ein Verstoß ist und ab wann ein
    Timeout fällig wird, entscheidet nicht mehr diese Funktion (das stand vorher wortgleich
    auch im Discord-Bot), sondern features/moderation - hier bleibt nur, wie man auf Twitch
    löscht und stummschaltet."""
    detail_suffix = f" ('{verdict.detail}')" if verdict.detail else ""
    loop = asyncio.get_event_loop()

    if verdict.delete and BROADCASTER_ID and MODERATOR_ID and msg_id:
        await loop.run_in_executor(
            None, twitch_api.delete_chat_message,
            BROADCASTER_ID, MODERATOR_ID, msg_id, config.TWITCH_CHAT_ACCESS_TOKEN,
        )

    print(
        f"🧹 Twitch-Nachricht gelöscht: {message.user_name} - {verdict.label}{detail_suffix} "
        f"(Verstoß #{verdict.violation_count})"
    )
    # Grund als Kategorie (label), nicht als Detail posten - sonst würde ein gelöschtes
    # Bannwort/ein gesperrter Link durch die eigene Bot-Nachricht erneut im Chat landen.
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
    """Löscht den Versuch eines Nicht-Moderators, einen Mod-Befehl zu nutzen, und
    postet eine kurze Ablehnung - vorher wurde das einfach stillschweigend ignoriert."""
    if BROADCASTER_ID and MODERATOR_ID and msg_id:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, twitch_api.delete_chat_message,
            BROADCASTER_ID, MODERATOR_ID, msg_id, config.TWITCH_CHAT_ACCESS_TOKEN,
        )
    print(f"🚫 Mod-Befehl {command_word} von {user_name} abgelehnt (keine Moderator-Rechte).")
    await send_twitch_chat(text("mod_only", user=user_name))


async def twitch_chat_listener():
    """Besitzt den kompletten Verbindungs-Lebenszyklus: verbinden, lesen, und nach jedem
    Abbruch mit wachsendem Backoff neu verbinden. Vorher wurde genau einmal verbunden und
    ein von Twitch geschlossener Socket (recv() -> b"") nur mit sleep(0.5) quittiert - der
    Bot lief dann endlos weiter, ohne je wieder eine Nachricht zu sehen."""
    backoff = 5
    print("👀 Twitch-Chat-Reader läuft im Hintergrund...")

    while True:
        try:
            await _connect_and_auth(config.TWITCH_CHAT_ACCESS_TOKEN)
            await _read_until_disconnect()
        except asyncio.CancelledError:
            raise
        except _AuthFailed:
            print("⚠️ Twitch-Login abgelehnt, erneuere Token und verbinde neu...")
            await asyncio.get_event_loop().run_in_executor(None, twitch_api.refresh_chat_token)
        except Exception as e:
            print(f"⚠️ Twitch-IRC-Verbindung verloren: {e}")

        # Nur nach einer Verbindung, die wirklich stand, sofort wieder schnell versuchen -
        # sonst hämmern wir bei kaputtem Token oder totem Netz gegen Twitch.
        backoff = 5 if _connected.is_set() else min(backoff * 2, timing("irc_reconnect_backoff_max", 300))
        await _close_connection()
        print(f"🔄 Nächster Twitch-IRC-Verbindungsversuch in {backoff}s...")
        await asyncio.sleep(backoff)


async def _read_until_disconnect():
    """Liest zeilenweise, bis die Verbindung tot ist, und wirft dann - der Aufrufer
    verbindet daraufhin neu. "Tot" heißt: EOF, Socket-Fehler, oder keine Antwort auf
    unser eigenes PING (der Fall, den eine still weggefallene Verbindung sonst
    unbemerkt lässt)."""
    reader = _reader
    awaiting_pong = False

    while True:
        try:
            ping_interval = timing("irc_ping_interval", 180)
            raw_line = await asyncio.wait_for(reader.readline(), timeout=ping_interval)
        except asyncio.TimeoutError:
            if awaiting_pong:
                raise ConnectionError(f"keine Antwort auf eigenes PING innerhalb von {ping_interval}s")
            await _send_raw("PING :tmi.twitch.tv")
            awaiting_pong = True
            continue

        if not raw_line:
            raise ConnectionError("Verbindung von Twitch geschlossen (EOF)")

        # Jedes eingehende Byte ist ein Lebenszeichen - ob PONG, Chat oder Systemzeile.
        awaiting_pong = False
        await _handle_irc_line(raw_line.decode("utf-8", "replace").rstrip("\r\n"))


async def _handle_irc_line(line):
    """Verarbeitet genau eine IRC-Zeile. PING/Login-Fehler zuerst, damit die
    Verbindungspflege nicht davon abhängt, was sonst noch im Chat passiert."""
    if line.startswith("PING"):
        await _send_raw(f"PONG {line[5:] or ':tmi.twitch.tv'}")
        return

    if "Login authentication failed" in line or "Improperly formatted auth" in line:
        raise _AuthFailed(line)

    # :tmi.twitch.tv 001 <nick> :Welcome, GLHF! - erst ab hier ist die Anmeldung durch.
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
            # Ein Fehler in der Nachrichtenverarbeitung darf die Verbindung nicht kosten.
            print(f"⚠️ Fehler bei der Verarbeitung einer Twitch-Nachricht: {e}")


async def _send_command_reply(reply):
    """Antwort eines Befehls in den Chat schicken. Feature-Befehle dürfen auch eine
    Announcement zurückgeben (Discord baut daraus ein Embed) - im IRC wird daraus eine
    Zeile. Zeilenumbrüche müssen dabei weg: eine IRC-Nachricht ist einzeilig, mehrzeilige
    Antworten (z.B. !top) kämen sonst abgeschnitten an."""
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

    # Zieht den reinen Usernamen sauber und ohne Müll heraus
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

    # Bewusst vor der Moderation: gerade die später gelöschten Nachrichten sind die, die
    # man im Nachhinein noch nachlesen können will. Wer daraus was macht (Mitschnitt nur
    # während eines Streams), entscheiden die Features - der Bot meldet nur.
    await events.bus.publish(events.MESSAGE, message=msg)

    # Moderation: das erste Feature, das etwas beanstandet, gewinnt. Ist keines geladen,
    # wird schlicht nicht moderiert - der Rest des Bots läuft unverändert weiter.
    for moderator in events.bus.features_with(feature_api.MODERATION):
        verdict = await moderator.review(msg, moderation_overrides())
        if verdict:
            await handle_twitch_violation(msg, tags.get("id"), verdict)
            return

    await events.bus.publish(events.MESSAGE_ACCEPTED, message=msg)

    # Befehle in fester Reihenfolge: erst die plattformeigenen (Helix-Aufrufe in
    # commands.py, Bot-Interna hier), dann die der Features, zuletzt die statischen
    # Text-Maps aus twitch.json. Die Plattform hat Vorrang - ein Feature soll einen
    # Befehl, den twitch.json ausdrücklich anders belegt, nicht überschreiben können.
    # Die vier Befehlstabellen jeweils durch die Umbenennungen aus twitch.json
    # ("command_names") - ein Befehl kann dadurch anders heißen, mehrere Namen haben oder
    # ganz fehlen, ohne dass hier etwas davon steht.
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
        # Nicht-Mods, die einen Mod-Befehl versuchen: Nachricht löschen
        # statt sie einfach zu ignorieren.
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
