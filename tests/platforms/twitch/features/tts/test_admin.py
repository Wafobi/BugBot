"""TtsAdmin: the admin page's routes - state, and saving the pool/speech into the override
layer (settings/tts.json - a temporary one here), never into tts.json itself."""

import http
import json
import shutil
from pathlib import Path

import pytest

from core import runtime_config
from platforms.twitch.features.tts import admin as admin_module
from platforms.twitch.features.tts.admin import TtsAdmin
from platforms.twitch.features.tts.feature import DEFAULTS

REAL_JSON = Path(admin_module.__file__).parent / "tts.json"


class FakeFeature:
    def __init__(self, config):
        self.config = config
        self._queue = []

    def _speakable(self, text):
        return text

    async def queue_length(self):
        return len(self._queue)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    voices.mkdir()
    for model, speakers in (("de_DE-a-medium", 1), ("de_DE-b-medium", 8)):
        (voices / f"{model}.onnx").write_bytes(b"")
        (voices / f"{model}.onnx.json").write_text(json.dumps({
            "num_speakers": speakers, "speaker_id_map": {"amused": 0, "angry": 1},
            "inference": {"length_scale": 1, "noise_scale": 0.5, "noise_w": 0.6},
        }))
    monkeypatch.setattr(admin_module.env, "TTS_VOICES_DIR", voices)
    package = tmp_path / "tts"
    package.mkdir()
    shutil.copy(REAL_JSON, package / "tts.json")
    config = runtime_config.LiveConfig(package / "tts.json", DEFAULTS, override=tmp_path / "settings" / "tts.json")
    return TtsAdmin(FakeFeature(config)), package / "tts.json"


def body(result):
    return json.loads(result[1])


async def test_state_lists_voices_with_speaker_labels(setup):
    admin, _ = setup
    status, *_ = result = await admin.state({})
    data = body(result)
    assert status == http.HTTPStatus.OK
    assert set(data["voices"]) == {"de_DE-a-medium", "de_DE-b-medium"}
    assert data["voices"]["de_DE-b-medium"]["speakers"]["1"] == "angry"
    assert data["voices"]["de_DE-b-medium"]["speakers"]["5"] == "5"
    assert data["voices"]["de_DE-b-medium"]["defaults"]["noise_w_scale"] == 0.6


async def test_save_pool_goes_to_the_override_not_the_repo_file(setup):
    admin, path = setup
    before = path.read_text()
    result = await admin.save({"pool": json.dumps(["de_DE-a-medium", "de_DE-b-medium#3"]), "voice_pick": "per_user"})
    assert result[0] == http.HTTPStatus.OK
    assert path.read_text() == before  # the repo's tts.json is never written
    config = admin._config
    assert json.loads(config.override_path.read_text()) == {
        "voice": ["de_DE-a-medium", "de_DE-b-medium#3"], "voice_pick": "per_user",
    }
    assert config.get("voice") == ["de_DE-a-medium", "de_DE-b-medium#3"]
    assert config.text("announce_resub", user="x", months=1, message="m")  # the rest still comes from tts.json


async def test_save_refuses_unknown_voice_and_saves_nothing(setup):
    admin, path = setup
    for pool in (["de_DE-missing-low"], ["de_DE-b-medium#8"], [], "x"):
        result = await admin.save({"pool": json.dumps(pool)})
        assert result[0] == http.HTTPStatus.BAD_REQUEST
    assert (await admin.save({"voice_pick": "loud"}))[0] == http.HTTPStatus.BAD_REQUEST
    assert not admin._config.override_path.exists()


async def test_save_speech_sets_only_given_values_rest_null(setup):
    admin, path = setup
    await admin.save({"speech": json.dumps({"length_scale": 1.2, "noise_scale": 9})})
    speech = admin._config.section("speech")
    assert speech["length_scale"] == 1.2
    assert speech["noise_scale"] == 2.0  # clamped
    assert speech["noise_w_scale"] is None and speech["sentence_silence"] is None
    assert "_comment" in speech  # from the repo file underneath


async def test_speak_rejects_a_bad_voice(setup):
    admin, _ = setup
    assert (await admin.speak({"voice": "../x", "text": "hi"}))[0] == http.HTTPStatus.BAD_REQUEST


async def test_count_is_the_bare_number(setup):
    admin, _ = setup
    admin._feature._queue = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    assert (await admin.count({}))[:2] == (http.HTTPStatus.OK, b"3")
