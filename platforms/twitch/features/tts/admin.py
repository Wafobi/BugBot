"""The TTS tool of the settings website: try voices, build the voice pool, tune how it is
spoken, read the record - and save it on the server.

Shown on the settings website (features/settings), as its "TTS – Stimmen & Pool" page: the
website reaches these methods through the SETTINGS_TOOL capability (TtsFeature.settings_tool),
behind SETTINGS_TOKEN - nothing TTS-related runs on the streamer's machine. Only /tts/count is
still a route of its own, for a Stream Deck label (TTS_TOKEN, like the buttons).

Every method takes the arguments as a dict of strings (the shape the old query-string API had)
and answers (HTTPStatus, JSON text, content type).

Saving never touches tts.json in the repository - it goes into the override layer,
settings/tts.json (see core/runtime_config.py, LiveConfig.save_overrides), which lies on top
of it. So `git pull` on the server never conflicts, and the change applies from the next
announcement, no restart.
"""

import asyncio
import base64
import http
import json
import logging

from . import config as env
from . import normalize
from . import synth

log = logging.getLogger(__name__)

MAX_TEXT = 1000
VOICE_PICKS = ("random", "per_user")
JSON = "application/json; charset=utf-8"


def _json(status, data):
    return status, json.dumps(data, ensure_ascii=False), JSON


def _error(message):
    return _json(http.HTTPStatus.BAD_REQUEST, {"error": message})


class TtsAdmin:
    """Handlers behind the settings website's TTS page. `feature` is the TtsFeature - for its config, its
    queue length and the same synthesis path the stream uses."""

    def __init__(self, feature):
        self._feature = feature

    @property
    def _config(self):
        return self._feature.config

    def routes(self):
        """The one route left on the overlay listener: the queue length for a Stream Deck
        label (Bitfocus Companion polls it into a custom variable, see docs/tts.md)."""
        return {"/tts/count": self.count}

    def actions(self):
        """What the settings website may call - see TtsFeature.settings_tool."""
        return {"state": self.state, "speak": self.speak, "save": self.save, "history": self.history}

    async def history(self, query):
        """Without ?session=: the streams that had TTS (newest first, with title/date from
        SESSIONS where available). With it: that stream's messages, oldest first."""
        feature = self._feature
        store = getattr(feature, "_store", None)
        if store is None:
            return _error("Ohne Datenbank (STORAGE) gibt es keinen Verlauf.")
        sessions = getattr(feature, "_sessions", None)
        if "session" not in query:
            streams = await asyncio.to_thread(store.sessions)
            for stream in streams:
                data = await sessions.session(stream["session_id"]) if sessions is not None else None
                stream["title"] = (data or {}).get("title") or ""
                stream["started_at"] = (data or {}).get("started_at") or stream["first_at"]
            return _json(http.HTTPStatus.OK, {"streams": streams})
        try:
            session = int(query["session"])
        except ValueError:
            return _error("session muss eine Zahl sein.")
        return _json(http.HTTPStatus.OK, {"messages": await asyncio.to_thread(store.history, session)})

    async def count(self, query):
        """The queue length as bare text, "3" - bytes so no trailing newline is added: it goes
        straight into a button label."""
        return http.HTTPStatus.OK, str(await self._feature.queue_length()).encode("ascii"), "text/plain; charset=utf-8"

    async def state(self, query):
        """Installed voices with their speakers and defaults, plus what tts.json says now."""
        voices = {}
        for model in synth.installed(env.TTS_VOICES_DIR):
            try:
                voices[model] = synth.model_info(env.TTS_VOICES_DIR / f"{model}.onnx")
            except (OSError, ValueError) as error:
                log.warning(f"TTS admin: {model} unreadable: {error}")
        configured = self._config.get("voice", synth.FALLBACK_VOICE)
        return _json(http.HTTPStatus.OK, {
            "voices": voices,
            "pool": configured if isinstance(configured, list) else [configured],
            "voice_pick": self._config.get("voice_pick", "random"),
            "speech": synth.speech_settings(self._config.section("speech")),
            "queue": len(self._feature._queue),
            "max_text": MAX_TEXT,
            "fallback": synth.FALLBACK_VOICE,
            "effects": synth.EFFECTS,
        })

    async def speak(self, query):
        """A sample, synthesized exactly like on stream: normalize.py with tts.json's
        replacements and english_words (unless prepare=0), the given voice and speech
        settings. Answers JSON with the spoken text and the WAV as base64."""
        text = " ".join(str(query.get("text") or "").split())[:MAX_TEXT]
        user = " ".join(str(query.get("user") or "").split())[:50]
        prepare = query.get("prepare", "1") != "0"
        try:
            voice = synth.resolve(query.get("voice") or "", env.TTS_VOICES_DIR)
            settings = {k: float(query[k]) for k in synth.SPEECH_SETTINGS if query.get(k)}
        except (synth.VoiceError, ValueError, OSError) as error:
            return _error(str(error))
        if prepare:
            text = self._feature._speakable(text)
        if user:
            text = self._config.text("announce_command", user=normalize.name(user) if prepare else user, message=text)
        if not text.strip():
            return _error("Kein Text (oder nach dem Aufbereiten nichts übrig).")
        english = self._config.get("english_words", []) if prepare else []
        try:
            wav = await asyncio.to_thread(synth.synthesize, text, voice, settings, english)
        except RuntimeError as error:
            return _json(http.HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
        return _json(http.HTTPStatus.OK, {"spoken": text, "audio": base64.b64encode(wav).decode("ascii")})

    async def save(self, query):
        """Writes voice (the pool), voice_pick and/or speech into tts.json - each only when
        given, each checked first: a pool entry that is not installed or a speaker out of
        range is refused rather than saved."""
        changes = {}
        try:
            if "pool" in query:
                pool = json.loads(query["pool"])
                if not isinstance(pool, list) or not pool or not all(isinstance(v, str) for v in pool):
                    return _error("Pool muss eine nicht-leere Liste von Stimmen sein.")
                for value in pool:
                    synth.resolve(value, env.TTS_VOICES_DIR)
                changes["voice"] = pool if len(pool) > 1 else pool[0]
            if "voice_pick" in query:
                if query["voice_pick"] not in VOICE_PICKS:
                    return _error(f"voice_pick muss eins von {VOICE_PICKS} sein.")
                changes["voice_pick"] = query["voice_pick"]
            if "speech" in query:
                speech = json.loads(query["speech"])
                if not isinstance(speech, dict):
                    return _error("speech muss ein Objekt sein.")
                clean = synth.speech_settings(speech)
                # Every key spelled out - null = the model's own default - so a value from
                # the repo's tts.json does not shine through a slider that was reset.
                changes["speech"] = {key: clean.get(key) for key in synth.SPEECH_SETTINGS}
        except (json.JSONDecodeError, synth.VoiceError, OSError, ValueError) as error:
            return _error(str(error))
        if not changes:
            return _error("Nichts zu speichern.")
        try:
            await asyncio.to_thread(self._config.save_overrides, changes)
        except OSError as error:
            return _json(http.HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"nicht speicherbar: {error}"})
        log.info(f"TTS admin: saved {', '.join(changes)} to {self._config.override_path}")
        return _json(http.HTTPStatus.OK, {"saved": sorted(changes)})
