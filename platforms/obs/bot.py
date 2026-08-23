# bot.py
# The OBS logic. What happens here falls into four parts:
#
#   Reporting   OBS events (scene change, recording, stream output, replay) go onto the bus -
#               verbatim as RAW_EVENT and in the neutral language of core/events.py as
#               PLATFORM_EVENT. That way features/stats counts them and raw_log keeps them,
#               without either of the two having to know OBS.
#   Ad panel    Twitch reports the start of an ad break on the bus (AD_BREAK); OBS then shows
#               a source and hides it again when the break is over. The two platforms still
#               know nothing of each other - Twitch only publishes that ads are running, and
#               somebody here is listening.
#   Announcing  writing announcements from the bus (bug report, clip, stream start) into a
#               text source and showing it briefly - the platform's ANNOUNCE capability, see
#               platform.py.
#   Controlling the helpers platforms/obs/features/obs_control builds its commands from
#               (!obs, !scene, !rec, ...).
#
# What deliberately does *not* happen here: publishing STREAM_START/STREAM_END. OBS does know
# when the output is running, but the stream session belongs to Twitch (see
# platforms/twitch/features/stream_sessions) - two reporters would mean two sessions for the
# same stream. The state of the OBS output is reported as an ordinary PLATFORM_EVENT instead
# and can be seen in the !obs status.

import asyncio
import logging
from pathlib import Path

from core import events, runtime_config
from core import platform as platform_api

from . import config
from .link import OBSError, OBSLink

log = logging.getLogger(__name__)

# Platform name as it appears in every bus notification and in the DB. Has to match
# platform.py:OBSPlatform.name.
NAME = "obs"

# Ad panel, announcements and the raw-log switch come from obs.json and can be edited at
# runtime (see core/runtime_config.py) - no restart needed.
OBS_CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "obs.json")

# Running hide tasks per source (ad panel, announcement text).
_hide_tasks = {}

# Everywhere a source sits: source name -> ((scene, sceneItemId), ...). OBS has no request
# for "where is this source?", so it costs one request per scene - and showing and hiding
# always come as a pair. The cache is discarded as soon as OBS reports that something about
# the scenes or their contents has changed (see _CACHE_INVALIDATING).
_item_cache = {}

# Most recently reported program scene, for the status command.
_current_scene = ""

# OBS events after which the cached locations may be wrong.
_CACHE_INVALIDATING = frozenset({
    "SceneListChanged", "SceneCreated", "SceneRemoved", "SceneNameChanged",
    "SceneItemCreated", "SceneItemRemoved", "CurrentSceneCollectionChanged",
})

# outputState of the stream/record events -> our short name. The intermediate states
# (STARTING/STOPPING) are deliberately absent: they report an intention, not an event.
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
    """Shorthand for OBS_CONFIG.text - every sentence lives in obs.json."""
    return OBS_CONFIG.text(key, **values)


def ad_break_source():
    return _settings("ad_break").get("source", "")


def announce_text_source():
    return _settings("announce").get("text_source", "")


# --- Reporting ----------------------------------------------------------------------

def _neutral_event(event_type, data):
    """The name an OBS event is counted under as a PLATFORM_EVENT - or "" when it belongs
    in the raw log only. The platform column already says "obs", so the event name need not
    repeat it."""
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
    """Every event from OBS. Write it away first, evaluate afterwards - exactly as in
    Twitch's EventSub listener: an error further down must not result in the event being
    documented nowhere."""
    global _current_scene

    if OBS_CONFIG.get("raw_events", True):
        await events.bus.publish(events.RAW_EVENT, platform=NAME, event_type=event_type, payload=data)

    if event_type in _CACHE_INVALIDATING:
        _item_cache.clear()

    if event_type == "CurrentProgramSceneChanged":
        _current_scene = data.get("sceneName", "")

    counted = _neutral_event(event_type, data)
    if counted:
        log.info(f"OBS: {counted}" + (f" ({_current_scene})" if counted == "scene_changed" else ""))
        # user_name stays empty: OBS events have no originator in chat. The column is NOT
        # NULL, not "always a person" (see features/stats/store.py).
        await events.bus.publish(
            events.PLATFORM_EVENT, platform=NAME, event_type=counted, user_name="", amount=0,
        )


async def _on_connected():
    """Reconcile the state after every sign-in. The hiding matters most: if the line drops
    in the middle of an ad break (OBS restart, bot restart), the ad panel would otherwise
    stand on screen for the rest of the stream."""
    global _current_scene
    _item_cache.clear()
    try:
        scenes = await link.request("GetSceneList")
        _current_scene = scenes.get("currentProgramSceneName", "")
        log.info(f"OBS scene: '{_current_scene}' ({len(scenes.get('scenes', []))} scenes).")
    except OBSError as e:
        log.warning(f"OBS scene list not retrievable: {e}")

    if OBS_CONFIG.get("hide_on_connect", True):
        for source in {ad_break_source(), announce_text_source()} - {""}:
            await set_source_visible(source, False, quiet=True)

    await events.bus.publish(
        events.PLATFORM_EVENT, platform=NAME, event_type="connected", user_name="", amount=0,
    )


# --- Showing and hiding sources ------------------------------------------------------

async def _locate_source(source):
    """All locations of a source as ((scene, sceneItemId), ...). Sources inside groups count
    too: for them the group name is the "scene name" OBS lets you address them by. The result
    is cached (see _item_cache)."""
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
                # Groups need their own request; nested groups do not exist in OBS, so one
                # level is enough.
                group = item.get("sourceName", "")
                members = (await link.request("GetGroupSceneItemList", {"sceneName": group})).get("sceneItems", [])
                found += [(group, member["sceneItemId"]) for member in members if member.get("sourceName") == source]

    _item_cache[source] = tuple(found)
    return _item_cache[source]


async def set_source_visible(source, visible, quiet=False):
    """Shows or hides a source everywhere it sits. Returns the number of places where it
    worked (0 = the source does not exist, or OBS is gone)."""
    try:
        locations = await _locate_source(source)
    except OBSError as e:
        if not quiet:
            log.warning(f"OBS: source '{source}' not locatable: {e}")
        return 0

    if not locations:
        if not quiet:
            log.warning(f"OBS: source '{source}' is in no scene - nothing to show or hide.")
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
                log.warning(f"OBS: '{source}' in '{scene}' not switchable: {e}")
    return changed


async def show_source(source, hide_after=0):
    """Shows a source and (for hide_after > 0) hides it again after that many seconds. A
    hide task still running for the same source is replaced: otherwise the second
    announcement would be cleared away by the first one's clock."""
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
        log.warning(f"OBS: '{source}' could not be hidden: {e}")
    finally:
        # Remove only our own entry - a new task may be sitting there by now.
        if _hide_tasks.get(source) is asyncio.current_task():
            _hide_tasks.pop(source, None)


def _cancel_hide(source):
    task = _hide_tasks.pop(source, None)
    if task and not task.done():
        task.cancel()


# --- Ad panel ------------------------------------------------------------------------

async def _on_ad_break(platform, duration_seconds):
    """Ad break on the bus (reported by Twitch): show the panel for the duration of the
    ads. If no relay is running, simply nothing happens."""
    source = ad_break_source()
    if not source or not link.connected:
        return
    seconds = max(0, int(duration_seconds or 0)) + int(_settings("ad_break").get("extra_seconds", 0) or 0)
    if await show_source(source, seconds):
        log.info(f"OBS: '{source}' shown for {seconds}s (ad break).")


# --- Announcing ----------------------------------------------------------------------

async def show_announcement(announcement):
    """Fulfils core.platform.Platform.announce for OBS: write text into a text source and
    show it briefly. True when it actually made it on screen.

    Which kinds appear on stream at all is in obs.json under announce.kinds - the default is
    empty, for the same reason as on Twitch: what the chat already sees need not additionally
    go on screen."""
    settings = _settings("announce")
    if announcement.kind not in (settings.get("kinds") or []):
        return False

    source = settings.get("text_source", "")
    if not source or not link.connected:
        return False

    text = announcement.as_text(max_fields=int(settings.get("max_fields", 2)))
    try:
        # overlay=True: change only the text; all the source's other settings (font,
        # colour, wrapping) stay as the streamer set them up.
        await link.request("SetInputSettings", {
            "inputName": source, "inputSettings": {"text": text}, "overlay": True,
        })
    except OBSError as e:
        log.warning(f"OBS: text source '{source}' not writable: {e}")
        return False

    return await show_source(source, int(settings.get("hide_after_seconds", 20) or 0))


# --- Controlling (used by platforms/obs/features/obs_control) -------------------------

async def scene_list():
    """(current scene, [all scene names]) - OBS returns them in reverse order compared with
    its own display, so they are flipped once here."""
    global _current_scene
    data = await link.request("GetSceneList")
    _current_scene = data.get("currentProgramSceneName", "")
    names = [scene.get("sceneName", "") for scene in reversed(data.get("scenes", []))]
    return _current_scene, names


async def switch_scene(wanted):
    """Switches the program scene and returns the name actually switched to, or None when
    no scene matches. The search is forgiving: exact, then case-insensitively, then as a
    prefix, then as a substring - nobody in chat types "🎮 Gameplay (fullscreen)" without a
    mistake."""
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
    """The state of OBS as an Announcement - Discord turns it into an embed, Twitch into a
    chat line (see core/platform.py)."""
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
        # Bitrate from bytes and duration rather than from an instantaneous value -
        # obs-websocket provides none, and averaged over the whole output it is more
        # meaningful anyway.
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


# --- Start/stop ----------------------------------------------------------------------

async def start_obs():
    """Opens the port and returns immediately.

    Deliberately no waiting for the relay: the OBS machine is usually off when the bot
    starts. For the same reason platform.py does not override wait_ready() either - otherwise
    Twitch's live reconciliation would hang for up to two minutes on a machine that only
    comes on in the evening."""
    await link.start()
    log.info(
        f"OBS link ready: waiting on {config.OBS_BRIDGE_BIND}:{config.OBS_BRIDGE_PORT} "
        f"for the relay from the OBS machine (platforms/obs/client/obs_bridge.py)."
    )


async def close():
    for source in list(_hide_tasks):
        _cancel_hide(source)
    await link.close()
    log.info("OBS listener closed.")


# --- Wiring --------------------------------------------------------------------------
# Sits at the end, because the line needs the handlers above. The subscription is set at
# import time - the same place as in platforms/discord/bot.py.

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
