# bot.py
# Die OBS-Logik. Was hier passiert, zerfällt in vier Teile:
#
#   Melden      OBS-Ereignisse (Szenenwechsel, Aufnahme, Stream-Ausgabe, Replay) gehen auf
#               den Bus - als RAW_EVENT wortwörtlich und als PLATFORM_EVENT in der
#               neutralen Sprache aus core/events.py. Damit zählt features/stats sie mit
#               und raw_log hebt sie auf, ohne dass eines der beiden OBS kennen muss.
#   Werbepanel  Twitch meldet den Beginn einer Werbepause auf dem Bus (AD_BREAK); OBS
#               blendet daraufhin eine Quelle ein und nach Ablauf wieder aus. Die beiden
#               Plattformen wissen weiterhin nichts voneinander - Twitch publiziert nur,
#               dass Werbung läuft, und hier hört jemand zu.
#   Ankündigen  Ankündigungen aus dem Bus (Bug-Report, Clip, Streamstart) in eine
#               Text-Quelle schreiben und kurz einblenden - die ANNOUNCE-Fähigkeit der
#               Plattform, siehe platform.py.
#   Steuern     die Helfer, aus denen platforms/obs/features/obs_control seine Befehle
#               baut (!obs, !scene, !rec, ...).
#
# Was hier bewusst *nicht* passiert: STREAM_START/STREAM_END publizieren. OBS weiß zwar,
# wann die Ausgabe läuft, aber die Stream-Session gehört Twitch (siehe
# platforms/twitch/features/stream_sessions) - zwei Melder wären zwei Sessions für
# denselben Stream. Der Zustand der OBS-Ausgabe wird stattdessen als gewöhnliches
# PLATFORM_EVENT gemeldet und ist im !obs-Status zu sehen.

import asyncio
from pathlib import Path

from core import events, runtime_config
from core import platform as platform_api

from . import config
from .link import OBSError, OBSLink

# Plattformname, wie er in jeder Bus-Meldung und in der DB auftaucht. Muss zu
# platform.py:OBSPlatform.name passen.
NAME = "obs"

# Werbepanel, Ankündigungen und Rohprotokoll-Schalter kommen aus obs.json und können zur
# Laufzeit editiert werden (siehe core/runtime_config.py) - kein Neustart nötig.
OBS_CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "obs.json")

# Laufende Ausblend-Tasks je Quelle (Werbepanel, Ankündigungstext).
_hide_tasks = {}

# Wo eine Quelle überall liegt: Quellenname -> ((Szene, sceneItemId), ...). OBS hat keine
# Anfrage "wo steckt diese Quelle?", das kostet also eine Anfrage je Szene - und Ein- und
# Ausblenden kommen immer als Paar. Der Cache wird verworfen, sobald OBS meldet, dass sich
# an Szenen oder deren Inhalt etwas geändert hat (siehe _CACHE_INVALIDATING).
_item_cache = {}

# Zuletzt gemeldete Programm-Szene, für den Statusbefehl.
_current_scene = ""

# OBS-Ereignisse, nach denen die Fundorte im Cache falsch sein können.
_CACHE_INVALIDATING = frozenset({
    "SceneListChanged", "SceneCreated", "SceneRemoved", "SceneNameChanged",
    "SceneItemCreated", "SceneItemRemoved", "CurrentSceneCollectionChanged",
})

# outputState der Stream-/Aufnahme-Ereignisse -> unser Kurzname. Die Zwischenzustände
# (STARTING/STOPPING) sind bewusst nicht dabei: sie melden eine Absicht, kein Ereignis.
_OUTPUT_STATES = {
    "OBS_WEBSOCKET_OUTPUT_STARTED": "started",
    "OBS_WEBSOCKET_OUTPUT_STOPPED": "stopped",
    "OBS_WEBSOCKET_OUTPUT_RECONNECTING": "reconnecting",
    "OBS_WEBSOCKET_OUTPUT_PAUSED": "paused",
    "OBS_WEBSOCKET_OUTPUT_RESUMED": "resumed",
}


def _settings(section):
    return OBS_CONFIG.section(section)


def text(key, **values):
    """Kurzform für OBS_CONFIG.text - alle Sätze stehen in obs.json."""
    return OBS_CONFIG.text(key, **values)


def ad_break_source():
    return _settings("ad_break").get("source", "")


def announce_text_source():
    return _settings("announce").get("text_source", "")


# --- Melden -------------------------------------------------------------------------

def _neutral_event(event_type, data):
    """Der Name, unter dem ein OBS-Ereignis als PLATFORM_EVENT gezählt wird - oder "",
    wenn es nur ins Rohprotokoll gehört. Die Spalte platform sagt bereits "obs", der
    Ereignisname muss das nicht wiederholen."""
    if event_type == "CurrentProgramSceneChanged":
        return "scene_changed"
    if event_type == "StreamStateChanged":
        state = _OUTPUT_STATES.get(data.get("outputState", ""))
        return f"stream_{state}" if state else ""
    if event_type == "RecordStateChanged":
        state = _OUTPUT_STATES.get(data.get("outputState", ""))
        return f"record_{state}" if state else ""
    if event_type == "ReplayBufferSaved":
        return "replay_saved"
    if event_type == "VirtualcamStateChanged":
        state = _OUTPUT_STATES.get(data.get("outputState", ""))
        return f"virtualcam_{state}" if state else ""
    if event_type == "ExitStarted":
        return "obs_exit"
    return ""


async def _on_obs_event(event_type, data):
    """Jedes Ereignis von OBS. Erst wegschreiben, dann auswerten - genau wie im
    EventSub-Listener von Twitch: ein Fehler weiter unten darf nicht dazu führen, dass das
    Ereignis nirgends dokumentiert ist."""
    global _current_scene

    if OBS_CONFIG.get("raw_events", True):
        await events.bus.publish(events.RAW_EVENT, platform=NAME, event_type=event_type, payload=data)

    if event_type in _CACHE_INVALIDATING:
        _item_cache.clear()

    if event_type == "CurrentProgramSceneChanged":
        _current_scene = data.get("sceneName", "")

    counted = _neutral_event(event_type, data)
    if counted:
        print(f"🎛️ OBS: {counted}" + (f" ({_current_scene})" if counted == "scene_changed" else ""))
        # user_name bleibt leer: OBS-Ereignisse haben keinen Urheber im Chat. Die Spalte
        # ist NOT NULL, nicht "immer eine Person" (siehe features/stats/store.py).
        await events.bus.publish(
            events.PLATFORM_EVENT, platform=NAME, event_type=counted, user_name="", amount=0,
        )


async def _on_connected():
    """Nach jeder Anmeldung den Zustand angleichen. Wichtig ist vor allem das Ausblenden:
    fällt die Leitung mitten in einer Werbepause weg (OBS-Neustart, Bot-Neustart), stünde
    das Werbepanel sonst für den Rest des Streams im Bild."""
    global _current_scene
    _item_cache.clear()
    try:
        scenes = await link.request("GetSceneList")
        _current_scene = scenes.get("currentProgramSceneName", "")
        print(f"🎬 OBS-Szene: '{_current_scene}' ({len(scenes.get('scenes', []))} Szenen).")
    except OBSError as e:
        print(f"⚠️ OBS-Szenenliste nicht abrufbar: {e}")

    if OBS_CONFIG.get("hide_on_connect", True):
        for source in {ad_break_source(), announce_text_source()} - {""}:
            await set_source_visible(source, False, quiet=True)

    await events.bus.publish(
        events.PLATFORM_EVENT, platform=NAME, event_type="connected", user_name="", amount=0,
    )


# --- Quellen ein- und ausblenden -----------------------------------------------------

async def _locate_source(source):
    """Alle Fundorte einer Quelle als ((Szene, sceneItemId), ...). Quellen in Gruppen
    zählen mit: für sie ist der Gruppenname der "Szenenname", mit dem OBS sie ansprechen
    lässt. Ergebnis wird gecacht (siehe _item_cache)."""
    cached = _item_cache.get(source)
    if cached is not None:
        return cached

    found = []
    scenes = (await link.request("GetSceneList")).get("scenes", [])
    for scene in scenes:
        scene_name = scene.get("sceneName", "")
        items = (await link.request("GetSceneItemList", {"sceneName": scene_name})).get("sceneItems", [])
        for item in items:
            if item.get("sourceName") == source:
                found.append((scene_name, item["sceneItemId"]))
            elif item.get("isGroup"):
                # Gruppen brauchen ihre eigene Anfrage; verschachtelte Gruppen gibt es in
                # OBS nicht, eine Ebene reicht also.
                group = item.get("sourceName", "")
                members = (await link.request("GetGroupSceneItemList", {"sceneName": group})).get("sceneItems", [])
                found += [(group, member["sceneItemId"]) for member in members if member.get("sourceName") == source]

    _item_cache[source] = tuple(found)
    return _item_cache[source]


async def set_source_visible(source, visible, quiet=False):
    """Blendet eine Quelle überall ein oder aus, wo sie liegt. Rückgabe: Anzahl der
    Stellen, an denen es geklappt hat (0 = Quelle gibt es nicht oder OBS ist weg)."""
    try:
        locations = await _locate_source(source)
    except OBSError as e:
        if not quiet:
            print(f"⚠️ OBS: Quelle '{source}' nicht auffindbar: {e}")
        return 0

    if not locations:
        if not quiet:
            print(f"⚠️ OBS: Quelle '{source}' liegt in keiner Szene - nichts ein-/auszublenden.")
        return 0

    changed = 0
    for scene, item_id in locations:
        try:
            await link.request("SetSceneItemEnabled", {
                "sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": bool(visible),
            })
            changed += 1
        except OBSError as e:
            if not quiet:
                print(f"⚠️ OBS: '{source}' in '{scene}' nicht umschaltbar: {e}")
    return changed


async def show_source(source, hide_after=0):
    """Blendet eine Quelle ein und (bei hide_after > 0) nach so vielen Sekunden wieder
    aus. Ein noch laufender Ausblend-Task für dieselbe Quelle wird abgelöst: sonst würde
    die zweite Ankündigung von der Uhr der ersten wieder weggeräumt."""
    _cancel_hide(source)
    if not await set_source_visible(source, True):
        return False
    if hide_after > 0:
        _hide_tasks[source] = asyncio.create_task(_hide_later(source, hide_after), name=f"obs-hide-{source}")
    return True


async def _hide_later(source, delay):
    try:
        await asyncio.sleep(delay)
        await set_source_visible(source, False)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"⚠️ OBS: '{source}' konnte nicht ausgeblendet werden: {e}")
    finally:
        # Nur den eigenen Eintrag entfernen - inzwischen kann ein neuer Task dort stehen.
        if _hide_tasks.get(source) is asyncio.current_task():
            _hide_tasks.pop(source, None)


def _cancel_hide(source):
    task = _hide_tasks.pop(source, None)
    if task and not task.done():
        task.cancel()


# --- Werbepanel ----------------------------------------------------------------------

async def _on_ad_break(platform, duration_seconds):
    """Werbepause auf dem Bus (gemeldet von Twitch): Panel für die Dauer der Werbung
    einblenden. Läuft gerade kein Relais, passiert schlicht nichts."""
    source = ad_break_source()
    if not source or not link.connected:
        return
    seconds = max(0, int(duration_seconds or 0)) + int(_settings("ad_break").get("extra_seconds", 0) or 0)
    if await show_source(source, seconds):
        print(f"📺 OBS: '{source}' für {seconds}s eingeblendet (Werbepause).")


# --- Ankündigen ----------------------------------------------------------------------

async def show_announcement(announcement):
    """Erfüllt core.platform.Platform.announce für OBS: Text in eine Text-Quelle schreiben
    und sie kurz einblenden. True, wenn sie tatsächlich im Bild gelandet ist.

    Welche Arten überhaupt auf dem Stream erscheinen, steht in obs.json unter
    announce.kinds - Default ist leer, aus demselben Grund wie bei Twitch: was der Chat
    ohnehin schon sieht, muss nicht zusätzlich ins Bild."""
    settings = _settings("announce")
    if announcement.kind not in (settings.get("kinds") or []):
        return False

    source = settings.get("text_source", "")
    if not source or not link.connected:
        return False

    text = announcement.as_text(max_fields=int(settings.get("max_fields", 2)))
    try:
        # overlay=True: nur den Text ändern, alle übrigen Einstellungen der Quelle
        # (Schrift, Farbe, Umbruch) bleiben, wie der Streamer sie eingerichtet hat.
        await link.request("SetInputSettings", {
            "inputName": source, "inputSettings": {"text": text}, "overlay": True,
        })
    except OBSError as e:
        print(f"⚠️ OBS: Text-Quelle '{source}' nicht beschreibbar: {e}")
        return False

    return await show_source(source, int(settings.get("hide_after_seconds", 20) or 0))


# --- Steuern (benutzt von platforms/obs/features/obs_control) -------------------------

async def scene_list():
    """(aktuelle Szene, [alle Szenennamen]) - OBS liefert sie in umgekehrter Reihenfolge
    zur Anzeige in OBS, hier also einmal gedreht."""
    global _current_scene
    data = await link.request("GetSceneList")
    _current_scene = data.get("currentProgramSceneName", "")
    names = [scene.get("sceneName", "") for scene in reversed(data.get("scenes", []))]
    return _current_scene, names


async def switch_scene(wanted):
    """Wechselt die Programm-Szene und gibt den tatsächlich geschalteten Namen zurück,
    oder None, wenn keine Szene passt. Gesucht wird nachsichtig: exakt, dann ohne
    Groß-/Kleinschreibung, dann als Anfang, dann als Teilstück - im Chat tippt niemand
    "🎮 Gameplay (Vollbild)" fehlerfrei ab."""
    _, names = await scene_list()
    needle = wanted.strip()
    if not needle:
        return None

    lowered = needle.lower()
    for candidate in (
        [name for name in names if name == needle],
        [name for name in names if name.lower() == lowered],
        [name for name in names if name.lower().startswith(lowered)],
        [name for name in names if lowered in name.lower()],
    ):
        if candidate:
            await link.request("SetCurrentProgramScene", {"sceneName": candidate[0]})
            return candidate[0]
    return None


def _timecode(value):
    """"00:12:34.567" -> "00:12:34"."""
    return (value or "").split(".")[0]


async def status_announcement():
    """Der Zustand von OBS als Announcement - Discord macht daraus ein Embed, Twitch eine
    Chatzeile (siehe core/platform.py)."""
    if not link.connected:
        return platform_api.Announcement(
            kind=platform_api.STATUS, source=NAME,
            color=OBS_CONFIG.color("offline", 0xE74C3C),
            title=text("status.offline.title"),
            text=text("status.offline.text", port=config.OBS_BRIDGE_PORT),
        )

    stream = await link.request("GetStreamStatus")
    record = await link.request("GetRecordStatus")
    stats = await link.request("GetStats")
    scene, names = await scene_list()

    if stream.get("outputActive"):
        # Bitrate aus Bytes und Dauer statt aus einem Momentanwert - obs-websocket liefert
        # keinen, und über die ganze Ausgabe gemittelt ist er ohnehin aussagekräftiger.
        seconds = max(1, int(stream.get("outputDuration", 0)) // 1000)
        kbit = int(stream.get("outputBytes", 0)) * 8 / seconds / 1000
        streaming = text("status.stream.live",
                         duration=_timecode(stream.get("outputTimecode")), kbit=f"{kbit:.0f}")
    else:
        streaming = text("status.stream.off")

    if record.get("outputActive"):
        state = text("status.record.paused" if record.get("outputPaused") else "status.record.running")
        recording = text("status.record.value", state=state,
                         duration=_timecode(record.get("outputTimecode")))
    else:
        recording = text("status.record.off")

    dropped = int(stream.get("outputSkippedFrames", 0))
    total = int(stream.get("outputTotalFrames", 0))
    share = text("status.dropped.share", percent=f"{dropped / total * 100:.1f}") if total else ""

    return platform_api.Announcement(
        kind=platform_api.STATUS, source=NAME,
        color=OBS_CONFIG.color("status", 0x2ECC71),
        title=text("status.title"),
        text=text("status.scene", scene=scene or "?"),
        fields=(
            platform_api.Field(text("status.stream.name"), streaming, inline=True),
            platform_api.Field(text("status.record.name"), recording, inline=True),
            platform_api.Field(text("status.dropped.name"),
                               text("status.dropped.value", dropped=dropped, share=share), inline=True),
            platform_api.Field(
                text("status.performance.name"),
                text("status.performance.value",
                     fps=f"{stats.get('activeFps', 0):.0f}",
                     cpu=f"{stats.get('cpuUsage', 0):.0f}",
                     skipped=int(stats.get("renderSkippedFrames", 0))),
                inline=True,
            ),
            platform_api.Field(text("status.scenes.name"), str(len(names)), inline=True),
            platform_api.Field(
                text("status.bridge.name"),
                text("status.bridge.value", peer=link.peer, version=link.version or "?"),
                inline=True,
            ),
        ),
    )


# --- Start/Stop ----------------------------------------------------------------------

async def start_obs():
    """Öffnet den Port und kehrt sofort zurück.

    Bewusst kein Warten auf das Relais: der OBS-PC ist beim Start des Bots meistens aus.
    Aus demselben Grund überschreibt platform.py auch wait_ready() nicht - sonst hinge der
    Live-Abgleich von Twitch bis zu zwei Minuten an einem Rechner, der erst am Abend
    angeht."""
    await link.start()
    print(
        f"🎛️ OBS-Anbindung bereit: warte auf {config.OBS_BRIDGE_BIND}:{config.OBS_BRIDGE_PORT} "
        f"auf das Relais vom OBS-PC (platforms/obs/obs_bridge.py)."
    )


async def close():
    for source in list(_hide_tasks):
        _cancel_hide(source)
    await link.close()
    print("🔌 OBS-Lauscher geschlossen.")


# --- Verdrahtung ---------------------------------------------------------------------
# Steht am Ende, weil die Leitung die Handler oben braucht. Das Abonnement wird beim
# Import gesetzt - dieselbe Stelle wie in platforms/discord/bot.py.

link = OBSLink(
    token=config.OBS_BRIDGE_TOKEN,
    password=config.OBS_PASSWORD,
    on_event=_on_obs_event,
    on_connected=_on_connected,
    bind=config.OBS_BRIDGE_BIND,
    port=config.OBS_BRIDGE_PORT,
    timings=lambda: OBS_CONFIG.section("timings"),
)

events.bus.subscribe(events.AD_BREAK, _on_ad_break)
