"""The TTS feature: cheers and !tts read out loud, one at a time, when the streamer says so.

Three ways in, one queue:

  - A cheer of at least min_bits *with a message* (cheermotes like "Cheer100" do not count
    as one). Its bits are booked as spent in the BITS_LEDGER (features/companion's balance)
    - otherwise the same bits would also top up the balance that !companion and !tts spend
    from, and count twice. A cheer without a message is never read out and simply stays in
    that balance.
  - A resub with a message (Twitch's "share your resub" text) - free, the sub is the
    payment; "resub_messages" in tts.json switches it off.
  - !tts <text> in chat, paid with min_bits out of that same balance. Mods and the
    broadcaster use it for free (not exempt from moderation, only from the price), the same
    exemption as !companion.

Nothing is spoken on arrival. Every entry waits in the queue until a Stream Deck button
calls /tts/next - only then is it synthesized (synth.py, Piper - on the server, in the bot's
container) and sent to the browser source as a WAV. /tts/skip stops what is playing and
nothing else. Bits are booked when an entry is queued, not when it is played.

Why RAW_EVENT and not PLATFORM_EVENT: the latter carries no free text (see
platforms/twitch/bot.py:_publish_event). Rather than widen the shared event contract for a
single consumer, this listens to the raw "channel.cheer" notification directly.

Text is data throughout: capped to max_chars before anything else looks at it, moderated
fail-closed (an error in the check drops the text rather than letting it through), handed to
Piper through stdin, and never sent to the browser source at all - only the audio is.

What gets spoken is not quite what was typed: normalize.py makes names and chat text
speakable (no emojis read out by name, no digits at the end of a name, English chat words
expanded - "replacements" in tts.json), and English chat words are pronounced in English
("english_words", see piper_runner.py). Moderation sees the original. How it is spoken
(tempo, intonation, pauses) is "speech" in tts.json, see synth.SPEECH_SETTINGS.

No port of its own: the browser source (client/tts.html, ?role=tts) and the two Stream Deck
routes ride on features/overlay's listener (OVERLAY) - same port, same SSH tunnel. The routes
are guarded by TTS_TOKEN, not by OVERLAY_TOKEN, which sits in the scene collection and only
ever grants reading.

MODERATION and BITS_LEDGER are optional: without MODERATION nothing is filtered (as
everywhere else in the bot), without BITS_LEDGER a cheer is read out without being booked
anywhere and !tts <text> is open to mods and the broadcaster only.
"""

import asyncio
import base64
import http
import io
import logging
import random
import re
import time
import wave
import zlib
from collections import deque

from core import events
from core import feature as feature_api
from core import runtime_config

from . import config as env
from . import normalize
from .admin import TtsAdmin
from . import synth

log = logging.getLogger(__name__)

# The browser source connects to the overlay listener with ?role=tts (client/tts.html) - only
# those get the audio frames. The Stream Deck buttons are routes on that same listener.
ROLE = "tts"
ROUTE_NEXT = "/tts/next"
ROUTE_SKIP = "/tts/skip"

DEFAULTS = {
    "min_bits": 100,
    "include_anonymous": False,
    "max_chars": 255,
    "max_queue": 50,
    "voice": synth.FALLBACK_VOICE,
    "voice_pick": "random",
}

# A cheermote is a word of letters directly followed by a number: Cheer100, Kappa50,
# PogChamp1000. Twitch only counts it as one when the prefix is a real cheermote, but the
# exact list is per channel and changes - stripping every such token is the cheap, safe
# approximation. Costs a viewer's "abc123" now and then, never lets a "Cheer100" be read out.
_CHEERMOTE = re.compile(r"^[A-Za-z]+\d+$")


def strip_cheermotes(text):
    return " ".join(word for word in str(text or "").split() if not _CHEERMOTE.match(word))


class TtsFeature(feature_api.Feature):
    name = "tts"

    optional = frozenset({feature_api.MODERATION, feature_api.BITS_LEDGER, feature_api.OVERLAY})

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        self._bus = None
        self._moderation = None
        self._ledger = None
        self._overlay = None
        # Entries waiting for /next: {"text": the full sentence to speak}. Nothing else is
        # kept - who sent it is already in the sentence, and in chat.
        self._queue = deque()
        # One /next at a time: a double press waits for the first synthesis instead of
        # starting a second Piper process next to it.
        self._release_lock = asyncio.Lock()
        # Until when (time.monotonic) the browser source is busy playing what was released -
        # the clips' own lengths, back to back. While it is, /tts/skip interrupts instead of
        # dropping the next queued entry.
        self._playing_until = 0.0
        # Pool entries already reported as unusable - each is warned about once, not on
        # every press.
        self._voice_complaints = set()

    # --- Lifecycle ------------------------------------------------------------------

    async def setup(self, bus):
        self._bus = bus
        self._moderation = bus.feature_with(feature_api.MODERATION)
        self._ledger = bus.feature_with(feature_api.BITS_LEDGER)
        if self._ledger is None:
            log.info("TTS without BITS_LEDGER: cheers are read out without being booked, "
                     "!tts <text> only works for mods and the broadcaster.")

        bus.subscribe(events.RAW_EVENT, self.on_raw_event)

        installed = synth.installed(env.TTS_VOICES_DIR)
        if not installed:
            log.warning(f"TTS: no Piper voices in {env.TTS_VOICES_DIR} - /next will fail until some are installed.")

        # No port of its own: audio and buttons ride on the overlay listener (OVERLAY).
        self._overlay = bus.feature_with(feature_api.OVERLAY)
        if self._overlay is None or not self._overlay.listening:
            log.info("TTS without an overlay listener (no overlay feature or no OVERLAY_TOKEN) - "
                     "cheers and !tts still queue up, but nothing can release or play them.")
        elif not env.TTS_TOKEN:
            log.info("No TTS_TOKEN set - the Stream Deck routes stay off, nothing can release the queue.")
        else:
            self._overlay.add_route(ROUTE_NEXT, self.release_next, env.TTS_TOKEN)
            self._overlay.add_route(ROUTE_SKIP, self.skip_next, env.TTS_TOKEN)
            # The admin page (try voices, build the pool, save to tts.json) - see admin.py.
            for path, handler in TtsAdmin(self).routes().items():
                self._overlay.add_route(path, handler, env.TTS_TOKEN)

    # --- Shared helpers -------------------------------------------------------------

    @staticmethod
    def _key(platform, user_id, user_name):
        """Same shape as features/companion/feature.py:_key - the ledger is keyed by it."""
        return f"{platform}:{user_id or user_name.lower()}"

    async def _blocked_reason(self, platform, user_id, user_name, text):
        """None when the text may be read out, otherwise a label saying why not.

        Fail-closed: an error in the check itself counts as blocked. Checked as an ordinary
        viewer's message even for mods - what is read out on stream is exactly as public as
        anyone else's, same reasoning as features/companion:_blocked_reason."""
        if self._moderation is None:
            return None
        probe = feature_api.Message(
            platform=platform, user_id=user_id, user_name=user_name,
            text=text, is_privileged=False, is_subscriber=False,
        )
        try:
            verdict = await self._moderation.review(probe)
        except Exception as error:
            log.warning(f"TTS: moderation check failed, dropping the text: {error}")
            return "check failed"
        return verdict.label if verdict is not None else None

    def _speakable(self, text):
        return normalize.message(text, self.config.section("replacements"))

    def _cap(self, text):
        return text[:max(1, int(self.config.get("max_chars", 255)))].strip()

    async def _enqueue(self, sentence, user_key="", user_name=""):
        """False when the queue is full - the caller decides what that means. user_key is
        only there for voice_pick "per_user" (see _voice)."""
        if len(self._queue) >= max(1, int(self.config.get("max_queue", 50))):
            return False
        self._queue.append({"text": sentence, "user_key": user_key, "user_name": user_name})
        await self._report("tts_queued", user_name)
        return True

    async def _report(self, event_type, user_name):
        """tts_queued / tts_played as an ordinary PLATFORM_EVENT - features/stats counts them
        per stream like any other event (same route platforms/obs takes for its own), and
        from there they reach !streamstats, the Discord stream report and the end screen's
        recap without this feature knowing any of them."""
        if self._bus is not None:
            await self._bus.publish(
                events.PLATFORM_EVENT, platform=self.owner, event_type=event_type,
                user_name=user_name, amount=0,
            )

    # --- Cheers (Push) ----------------------------------------------------------------

    async def on_raw_event(self, platform, event_type, payload):
        if platform != self.owner:
            return
        if event_type == "channel.subscription.message":
            return await self._on_resub(platform, payload or {})
        if event_type != "channel.cheer":
            return
        payload = payload or {}
        bits = int(payload.get("bits") or 0)
        anonymous = bool(payload.get("is_anonymous"))
        if anonymous and not self.config.get("include_anonymous", False):
            return
        if bits < int(self.config.get("min_bits", 100)):
            return

        # Capped before anything else touches it, moderation included.
        text = self._cap(strip_cheermotes(str(payload.get("message") or "")[:2000]))
        if not text:
            return  # no message = no TTS, the bits stay in the balance (see module doc)

        user_id = "" if anonymous else str(payload.get("user_id") or "")
        user_name = "Anonym" if anonymous else (payload.get("user_name") or payload.get("user_login") or "?")

        if len(self._queue) >= max(1, int(self.config.get("max_queue", 50))):
            log.warning(f"TTS queue full - cheer from {user_name} ({bits} bits) not queued, not booked.")
            return

        blocked = await self._blocked_reason(platform, user_id, user_name, text)
        spoken = self._speakable(text) if blocked is None else ""
        if blocked is not None:
            log.info(f"TTS: message of {user_name}'s cheer not read out ({blocked}).")
        if spoken:
            sentence = self.config.text("announce", user=normalize.name(user_name), bits=bits, message=spoken)
        else:
            sentence = self.config.text("announce_no_message", user=normalize.name(user_name), bits=bits)

        # Booked even when moderation dropped the message: the bits announcement still runs.
        # Anonymous cheers never count towards the balance in the first place.
        if self._ledger is not None and not anonymous:
            await self._ledger.spend(self._key(platform, user_id, user_name), bits)
        await self._enqueue(sentence, "" if anonymous else self._key(platform, user_id, user_name), user_name)

    async def _on_resub(self, platform, payload):
        """A resub with a message: read out like a cheer, but free - the sub is the payment,
        nothing is booked. Twitch only lets a *resub* carry a message (a first sub has none),
        so this is the only sub event with something to say. Off with "resub_messages":
        false in tts.json."""
        if not self.config.get("resub_messages", True):
            return
        text = self._cap(str((payload.get("message") or {}).get("text") or "")[:2000])
        if not text:
            return
        user_id = str(payload.get("user_id") or "")
        user_name = payload.get("user_name") or payload.get("user_login") or "?"
        months = int(payload.get("cumulative_months") or 0)
        if len(self._queue) >= max(1, int(self.config.get("max_queue", 50))):
            log.warning(f"TTS queue full - resub message from {user_name} not queued.")
            return
        blocked = await self._blocked_reason(platform, user_id, user_name, text)
        if blocked is not None:
            log.info(f"TTS: resub message of {user_name} not read out ({blocked}).")
            return
        spoken = self._speakable(text)
        if not spoken:
            return
        sentence = self.config.text("announce_resub", user=normalize.name(user_name), months=months, message=spoken)
        await self._enqueue(sentence, self._key(platform, user_id, user_name), user_name)

    # --- !tts (chat) ------------------------------------------------------------------

    async def cmd_tts(self, message):
        # The balance behind this is Twitch bits - on another platform there is nothing to
        # pay with, and nothing to say.
        if message.platform != self.owner:
            return None

        min_bits = int(self.config.get("min_bits", 100))
        key = self._key(message.platform, message.user_id, message.user_name)
        text = self._cap((message.arg_text or "").strip())
        if not text:
            reply = self.config.text("help.tts", need=min_bits)
            if self._ledger is not None and not message.is_privileged:
                have = await self._ledger.balance(key, message.user_name)
                reply += self.config.text("help.balance", have=have)
            return reply

        if not message.is_privileged and self._ledger is None:
            return self.config.text("help.tts", need=min_bits)

        blocked = await self._blocked_reason(message.platform, message.user_id, message.user_name, text)
        if blocked is not None:
            return self.config.text("blocked", label=blocked)

        spoken = self._speakable(text)
        if not spoken:
            return self.config.text("nothing_to_say")

        if len(self._queue) >= max(1, int(self.config.get("max_queue", 50))):
            return self.config.text("queue_full")

        if not message.is_privileged:
            have = await self._ledger.balance(key, message.user_name)
            if have < min_bits:
                return self.config.text("needs_bits", have=have, need=min_bits)
            await self._ledger.spend(key, min_bits)

        await self._enqueue(
            self.config.text("announce_command", user=normalize.name(message.user_name), message=spoken),
            key, message.user_name,
        )
        # Deliberately no confirmation in chat - it was noise. Only the failures above answer,
        # since without them nobody could tell why nothing happens.
        return None

    def commands(self):
        return (
            feature_api.Command(
                name="!tts", handler=self.cmd_tts,
                help=self.config.text("help.tts", need=int(self.config.get("min_bits", 100))),
            ),
        )

    # --- Stream Deck (/next, /skip) ---------------------------------------------------

    def _voice(self, entry=None):
        """The voice for this entry. "voice" in tts.json is one voice or a list (a pool):

            voice_pick "random"    a fresh pick from the pool for every entry - against
                                   monotony, no two messages in a row need sound alike
            voice_pick "per_user"  the same pool voice for the same person every time, so a
                                   regular is recognisable by ear (stable hash of their key;
                                   changes only when the pool does)

        Unusable pool entries are skipped, each warned about once; with none left, the
        fallback voice speaks."""
        configured = self.config.get("voice", synth.FALLBACK_VOICE)
        pool = configured if isinstance(configured, list) else [configured]
        usable = []
        for value in pool:
            try:
                usable.append(synth.resolve(value, env.TTS_VOICES_DIR))
            except (synth.VoiceError, OSError, ValueError) as error:
                if str(value) not in self._voice_complaints:
                    self._voice_complaints.add(str(value))
                    log.warning(f"TTS voice {value!r} unusable ({error}) - skipped.")
        if not usable:
            return synth.resolve(synth.FALLBACK_VOICE, env.TTS_VOICES_DIR)
        user_key = (entry or {}).get("user_key")
        if self.config.get("voice_pick", "random") == "per_user" and user_key:
            return usable[zlib.crc32(user_key.encode("utf-8")) % len(usable)]
        return random.choice(usable)

    async def release_next(self, query=None):
        """Synthesizes the next entry and sends it to the browser source. On failure the
        entry goes back to the front - /skip is how to get rid of one that keeps failing."""
        async with self._release_lock:
            if not self._queue:
                return http.HTTPStatus.OK, self.config.text("http.empty")
            if self._overlay is None or self._overlay.listeners(ROLE) == 0:
                return http.HTTPStatus.SERVICE_UNAVAILABLE, self.config.text("http.no_client")
            entry = self._queue.popleft()
            try:
                voice = self._voice(entry)
                wav = await asyncio.to_thread(
                    synth.synthesize, entry["text"], voice, self.config.section("speech"),
                    self.config.get("english_words", []),
                )
            except Exception as error:
                self._queue.appendleft(entry)
                log.warning(f"TTS synthesis failed: {error}")
                return http.HTTPStatus.INTERNAL_SERVER_ERROR, self.config.text("http.error")
            await self._overlay.push("speak", {"audio": base64.b64encode(wav).decode("ascii")}, ROLE)
            self._playing_until = max(time.monotonic(), self._playing_until) + _duration(wav)
            await self._report("tts_played", entry.get("user_name", ""))
            return http.HTTPStatus.OK, self.config.text("http.played", remaining=len(self._queue))

    async def skip_next(self, query=None):
        """Stops what is playing, right now. Nothing else: with nothing playing it does
        nothing at all, and it never touches the queue - a stray press must not throw away a
        message somebody paid for."""
        if time.monotonic() >= self._playing_until:
            return http.HTTPStatus.OK, self.config.text("http.nothing_playing", remaining=len(self._queue))
        self._playing_until = 0.0
        if self._overlay is not None:
            await self._overlay.push("stop", None, ROLE)
        return http.HTTPStatus.OK, self.config.text("http.stopped", remaining=len(self._queue))


def _duration(wav):
    """Seconds of audio in a WAV, plus the browser source's pause between two clips
    (client/tts.html, GAP_MS). 0 when the header is unreadable - then skip just drops."""
    try:
        with wave.open(io.BytesIO(wav)) as reader:
            return reader.getnframes() / reader.getframerate() + 0.4
    except (wave.Error, EOFError, ZeroDivisionError):
        return 0.0


def create_feature():
    return TtsFeature()
