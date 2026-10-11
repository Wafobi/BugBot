"""TtsFeature: which cheers get read out, the booking that keeps their bits from counting
twice, !tts against the shared balance, and the /next//skip queue handling."""

import http

import pytest

from core import feature as feature_api
from platforms.twitch.features.tts import feature as tts_feature
from platforms.twitch.features.tts import synth
from platforms.twitch.features.tts.feature import TtsFeature, strip_cheermotes


class FakeLedger:
    def __init__(self, balances=None):
        self.balances = dict(balances or {})
        self.spent = []

    async def balance(self, key, user_name):
        return self.balances.get(key, 0)

    async def spend(self, key, amount):
        self.spent.append((key, amount))
        self.balances[key] = self.balances.get(key, 0) - amount


class FakeModeration:
    def __init__(self, block=None, fail=False):
        self.block = block
        self.fail = fail
        self.seen = []

    async def review(self, message):
        self.seen.append(message)
        if self.fail:
            raise RuntimeError("boom")
        if self.block and self.block in message.text:
            return feature_api.Verdict(reason="banned_word", label="unerlaubtes Wort")
        return None


class FakeOverlay:
    """Stands in for features/overlay's OVERLAY capability."""

    def __init__(self, clients=1):
        self.clients = clients
        self.sent = []

    def listeners(self, role):
        return self.clients if role == "tts" else 0

    async def push(self, type_, data, role):
        self.sent.append((type_, data, role))


def make_feature(ledger=None, moderation=None):
    f = TtsFeature()
    f.owner = "twitch"
    f._ledger = ledger
    f._moderation = moderation
    return f


def cheer(bits=100, message="Cheer100 Hallo Leute", anonymous=False, user_id="42", user_name="Viewer"):
    return {"bits": bits, "message": message, "is_anonymous": anonymous,
            "user_id": user_id, "user_name": user_name}


def chat(text, privileged=False, platform="twitch"):
    return feature_api.Message(platform=platform, user_id="42", user_name="Viewer",
                               arg_text=text, is_privileged=privileged)


# --- strip_cheermotes --------------------------------------------------------------------

def test_strip_cheermotes_keeps_only_the_message():
    assert strip_cheermotes("Cheer100 Hallo Kappa50 Leute PogChamp1000") == "Hallo Leute"


def test_strip_cheermotes_leaves_nothing_for_a_bare_cheer():
    assert strip_cheermotes("Cheer100 Cheer100") == ""


# --- cheers ------------------------------------------------------------------------------

async def test_cheer_with_message_is_queued_and_booked_in_full():
    ledger = FakeLedger()
    f = make_feature(ledger)
    await f.on_raw_event("twitch", "channel.cheer", cheer(bits=250))
    assert [e["text"] for e in f._queue] == ["Viewer hat 250 Bits dagelassen: Hallo Leute"]
    assert ledger.spent == [("twitch:42", 250)]


async def test_cheer_without_message_is_neither_queued_nor_booked():
    ledger = FakeLedger()
    f = make_feature(ledger)
    await f.on_raw_event("twitch", "channel.cheer", cheer(message="Cheer100"))
    assert not f._queue and not ledger.spent


async def test_cheer_below_min_bits_is_ignored():
    ledger = FakeLedger()
    f = make_feature(ledger)
    await f.on_raw_event("twitch", "channel.cheer", cheer(bits=99))
    assert not f._queue and not ledger.spent


async def test_anonymous_cheer_is_ignored_by_default():
    f = make_feature(FakeLedger())
    await f.on_raw_event("twitch", "channel.cheer", cheer(anonymous=True))
    assert not f._queue


async def test_other_events_and_platforms_are_ignored():
    f = make_feature(FakeLedger())
    await f.on_raw_event("twitch", "channel.follow", cheer())
    await f.on_raw_event("discord", "channel.cheer", cheer())
    assert not f._queue


async def test_moderated_cheer_keeps_the_announcement_and_the_booking():
    ledger = FakeLedger()
    f = make_feature(ledger, FakeModeration(block="Hallo"))
    await f.on_raw_event("twitch", "channel.cheer", cheer())
    assert [e["text"] for e in f._queue] == ["Viewer hat 100 Bits dagelassen."]
    assert ledger.spent == [("twitch:42", 100)]


async def test_moderation_failure_drops_the_text():
    f = make_feature(FakeLedger(), FakeModeration(fail=True))
    await f.on_raw_event("twitch", "channel.cheer", cheer())
    assert [e["text"] for e in f._queue] == ["Viewer hat 100 Bits dagelassen."]


async def test_text_is_capped_before_moderation_sees_it():
    moderation = FakeModeration()
    f = make_feature(FakeLedger(), moderation)
    await f.on_raw_event("twitch", "channel.cheer", cheer(message="Cheer100 " + "a" * 1000))
    assert len(moderation.seen[0].text) == 255


async def test_full_queue_neither_queues_nor_books():
    ledger = FakeLedger()
    f = make_feature(ledger)
    f.config.get = lambda key, default=None: 1 if key == "max_queue" else tts_feature.DEFAULTS.get(key, default)
    await f.on_raw_event("twitch", "channel.cheer", cheer())
    await f.on_raw_event("twitch", "channel.cheer", cheer(user_id="43"))
    assert len(f._queue) == 1
    assert ledger.spent == [("twitch:42", 100)]


# --- !tts ---------------------------------------------------------------------------------

async def test_tts_without_text_explains_and_shows_balance():
    f = make_feature(FakeLedger({"twitch:42": 300}))
    reply = await f.cmd_tts(chat(""))
    assert "mit Nachricht wird vorgelesen" in reply and "300 Bits" in reply


async def test_tts_spends_min_bits_from_the_balance():
    ledger = FakeLedger({"twitch:42": 150})
    f = make_feature(ledger)
    reply = await f.cmd_tts(chat("Hallo"))
    assert ledger.spent == [("twitch:42", 100)]
    assert [e["text"] for e in f._queue] == ["Viewer sagt: Hallo"]
    assert reply is None  # no confirmation in chat


async def test_tts_with_too_little_balance_spends_nothing():
    ledger = FakeLedger({"twitch:42": 50})
    f = make_feature(ledger)
    reply = await f.cmd_tts(chat("Hallo"))
    assert not ledger.spent and not f._queue
    assert "50" in reply


async def test_tts_is_free_for_mods_but_still_moderated():
    ledger = FakeLedger()
    f = make_feature(ledger, FakeModeration(block="böse"))
    await f.cmd_tts(chat("Hallo", privileged=True))
    assert not ledger.spent and len(f._queue) == 1
    reply = await f.cmd_tts(chat("böse", privileged=True))
    assert "nicht vorgelesen" in reply and len(f._queue) == 1


async def test_tts_blocked_text_spends_nothing():
    ledger = FakeLedger({"twitch:42": 500})
    f = make_feature(ledger, FakeModeration(block="böse"))
    await f.cmd_tts(chat("böse"))
    assert not ledger.spent and not f._queue


async def test_tts_is_silent_on_other_platforms():
    f = make_feature(FakeLedger({"discord:42": 500}))
    assert await f.cmd_tts(chat("Hallo", platform="discord")) is None


async def test_tts_without_ledger_is_mods_only():
    f = make_feature(ledger=None)
    await f.cmd_tts(chat("Hallo"))
    assert not f._queue
    await f.cmd_tts(chat("Hallo", privileged=True))
    assert len(f._queue) == 1


# --- /next, /skip -------------------------------------------------------------------------

@pytest.fixture
def fake_synth(monkeypatch):
    calls = []
    monkeypatch.setattr(TtsFeature, "_voice", lambda self, entry=None: "voice")

    def synthesize(text, voice, *args):
        calls.append(text)
        return b"RIFF"
    monkeypatch.setattr(synth, "synthesize", synthesize)
    return calls


async def test_next_plays_the_head_and_sends_only_audio(fake_synth):
    f = make_feature()
    f._overlay = FakeOverlay()
    await f._enqueue("eins")
    await f._enqueue("zwei")
    status, _ = await f.release_next()
    assert status == http.HTTPStatus.OK
    assert fake_synth == ["eins"]
    assert f._overlay.sent == [("speak", {"audio": "UklGRg=="}, "tts")]
    assert [e["text"] for e in f._queue] == ["zwei"]


async def test_next_without_browser_source_keeps_the_queue(fake_synth):
    f = make_feature()
    f._overlay = FakeOverlay(clients=0)
    await f._enqueue("eins")
    status, _ = await f.release_next()
    assert status == http.HTTPStatus.SERVICE_UNAVAILABLE
    assert len(f._queue) == 1 and not fake_synth


async def test_next_puts_the_entry_back_when_synthesis_fails(monkeypatch):
    f = make_feature()
    f._overlay = FakeOverlay()
    monkeypatch.setattr(TtsFeature, "_voice", lambda self, entry=None: "voice")

    def broken(text, voice, *args):
        raise RuntimeError("piper failed")
    monkeypatch.setattr(synth, "synthesize", broken)
    await f._enqueue("eins")
    status, _ = await f.release_next()
    assert status == http.HTTPStatus.INTERNAL_SERVER_ERROR
    assert [e["text"] for e in f._queue] == ["eins"]


async def test_skip_with_nothing_playing_leaves_the_queue_alone():
    f = make_feature()
    f._overlay = FakeOverlay()
    await f._enqueue("eins")
    status, text = await f.skip_next()
    assert status == http.HTTPStatus.OK and "läuft nichts" in text
    assert [e["text"] for e in f._queue] == ["eins"] and not f._overlay.sent


async def test_empty_queue_answers_ok():
    f = make_feature()
    f._overlay = FakeOverlay()
    assert (await f.release_next())[0] == http.HTTPStatus.OK
    assert (await f.skip_next())[0] == http.HTTPStatus.OK


# --- voice pool ---------------------------------------------------------------------------

@pytest.fixture
def voices_dir(tmp_path, monkeypatch):
    import json
    for model, speakers in (("de_DE-a-medium", 1), ("de_DE-b-medium", 10)):
        (tmp_path / f"{model}.onnx").write_bytes(b"")
        (tmp_path / f"{model}.onnx.json").write_text(json.dumps({"num_speakers": speakers}))
    (tmp_path / f"{synth.FALLBACK_VOICE}.onnx").write_bytes(b"")
    (tmp_path / f"{synth.FALLBACK_VOICE}.onnx.json").write_text("{}")
    monkeypatch.setattr(tts_feature.env, "TTS_VOICES_DIR", tmp_path)
    return tmp_path


def with_config(f, **values):
    f.config.get = lambda key, default=None: values.get(key, tts_feature.DEFAULTS.get(key, default))
    return f


def test_pool_skips_unusable_entries(voices_dir):
    f = with_config(make_feature(), voice=["de_DE-missing-low", "de_DE-b-medium#99", "de_DE-b-medium#3"])
    assert {f._voice({}).name for _ in range(20)} == {"de_DE-b-medium#3"}


def test_pool_random_uses_several_voices(voices_dir):
    f = with_config(make_feature(), voice=["de_DE-a-medium", "de_DE-b-medium#1", "de_DE-b-medium#2"])
    assert len({f._voice({}).name for _ in range(200)}) == 3


def test_pool_per_user_is_stable(voices_dir):
    f = with_config(make_feature(), voice=["de_DE-a-medium", "de_DE-b-medium#1", "de_DE-b-medium#2"],
                    voice_pick="per_user")
    assert len({f._voice({"user_key": "twitch:42"}).name for _ in range(50)}) == 1


def test_empty_pool_falls_back(voices_dir):
    f = with_config(make_feature(), voice=["de_DE-missing-low"])
    assert f._voice({}).name == synth.FALLBACK_VOICE


# --- reporting to the stream statistics -----------------------------------------------------

class FakeBus:
    def __init__(self):
        self.published = []

    async def publish(self, topic, **payload):
        self.published.append((topic, payload))
        return []


async def test_queued_and_played_are_reported_as_platform_events(fake_synth):
    from core import events
    f = make_feature(FakeLedger())
    f._bus = FakeBus()
    f._overlay = FakeOverlay()
    await f.on_raw_event("twitch", "channel.cheer", cheer())
    await f.release_next()
    assert [(t, p["event_type"], p["user_name"]) for t, p in f._bus.published] == [
        (events.PLATFORM_EVENT, "tts_queued", "Viewer"),
        (events.PLATFORM_EVENT, "tts_played", "Viewer"),
    ]


async def test_failed_synthesis_is_not_reported_as_played(monkeypatch):
    f = make_feature()
    f._bus = FakeBus()
    f._overlay = FakeOverlay()
    monkeypatch.setattr(TtsFeature, "_voice", lambda self, entry=None: "voice")

    def broken(*args):
        raise RuntimeError("piper failed")
    monkeypatch.setattr(synth, "synthesize", broken)
    await f._enqueue("eins")
    await f.release_next()
    assert [p["event_type"] for _, p in f._bus.published] == ["tts_queued"]


# --- resub messages -----------------------------------------------------------------------

def resub(text="Danke für alles!", months=7):
    return {"user_id": "42", "user_name": "Viewer", "cumulative_months": months, "message": {"text": text, "emotes": []}}


async def test_resub_message_is_queued_free():
    ledger = FakeLedger()
    f = make_feature(ledger)
    await f.on_raw_event("twitch", "channel.subscription.message", resub())
    assert [e["text"] for e in f._queue] == ["Viewer ist seit 7 Monaten dabei: Danke für alles!"]
    assert not ledger.spent


async def test_resub_without_text_or_blocked_is_skipped():
    f = make_feature(FakeLedger(), FakeModeration(block="böse"))
    await f.on_raw_event("twitch", "channel.subscription.message", resub(text=""))
    await f.on_raw_event("twitch", "channel.subscription.message", resub(text="böse"))
    assert not f._queue


async def test_resub_messages_can_be_switched_off():
    f = with_config(make_feature(), resub_messages=False)
    await f.on_raw_event("twitch", "channel.subscription.message", resub())
    assert not f._queue


# --- skip while playing ------------------------------------------------------------------

def make_wav(seconds):
    import io
    import wave
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(b"\0\0" * int(1000 * seconds))
    return buffer.getvalue()


async def test_skip_while_playing_interrupts_and_keeps_the_queue(monkeypatch):
    f = make_feature()
    f._overlay = FakeOverlay()
    monkeypatch.setattr(TtsFeature, "_voice", lambda self, entry=None: "voice")
    monkeypatch.setattr(synth, "synthesize", lambda *args: make_wav(5))
    await f._enqueue("eins")
    await f._enqueue("zwei")
    await f.release_next()
    status, text = await f.skip_next()
    assert "abgebrochen" in text
    assert f._overlay.sent[-1] == ("stop", None, "tts")
    assert [e["text"] for e in f._queue] == ["zwei"]
    # Nothing playing any more - the next skip does nothing.
    await f.skip_next()
    assert [e["text"] for e in f._queue] == ["zwei"]


# --- the settings website's TTS page (SETTINGS_TOOL) ---------------------------------------

async def test_settings_tool_serves_its_page_and_refuses_unknown_actions():
    f = make_feature()
    assert f.settings_tool_info()["id"] == "tts"
    page = await f.settings_tool("page", {})
    assert "bugbotTool" in page["html"]
    with pytest.raises(ValueError):
        await f.settings_tool("explode", {})
    with pytest.raises(ValueError):  # an action's own refusal comes back as ValueError too
        await f.settings_tool("speak", {"voice": "../x", "text": "hi"})


# --- its own part of the stream report (STREAM_REPORT) -------------------------------------

def test_stream_report_from_the_generic_event_counts():
    f = make_feature()
    report = f.stream_report({"event_counts": {"tts_queued": 9, "tts_played": 7}})
    assert "7" in report["fields"][0].value and "9" in report["fields"][0].value
    assert report["lines"] == ["TTS: 7 von 9 vorgelesen"]
    assert f.stream_report({"event_counts": {}}) == {}
