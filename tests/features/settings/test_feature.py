"""The settings website's server side: which files it offers, what it refuses to save, and
that saving/resetting/undo go through the override layer (a temporary settings/ directory -
see tests/conftest.py), never into the repository's JSON."""

import json
import shutil

import pytest

from core import runtime_config
from features.settings.feature import SettingsFeature


@pytest.fixture
def setup(tmp_path):
    # The real tts.json and twitch.json - the override layer of the tests lives elsewhere.
    tts = runtime_config.LiveConfig(runtime_config.ROOT / "platforms/twitch/features/tts/tts.json",
                                    override=tmp_path / "tts.json")
    twitch = runtime_config.LiveConfig(runtime_config.ROOT / "platforms/twitch/twitch.json",
                                       override=tmp_path / "twitch.json")
    sql = runtime_config.LiveConfig(runtime_config.ROOT / "features/sql_db/sql_db.json",
                                    override=tmp_path / "sql_db.json")
    yield SettingsFeature(), tts, twitch, sql
    shutil.rmtree(tmp_path, ignore_errors=True)


async def ask(feature, **request):
    reply = await feature.handle(json.dumps({"id": 1, **request}))
    return reply


async def test_files_lists_the_configs_with_groups(setup):
    feature, *_ = setup
    files = {f["id"]: f for f in (await ask(feature, op="files"))["data"]}
    assert files["platforms/twitch/twitch.json"]["group"] == "Plattformen"
    assert files["platforms/twitch/features/tts/tts.json"]["group"] == "Features"
    assert files["features/sql_db/sql_db.json"]["read_only"] is True


async def test_text_with_known_placeholders_is_saved_as_override(setup):
    feature, tts, *_ = setup
    repo_before = tts.path.read_text()
    reply = await ask(feature, op="set", file="platforms/twitch/features/tts/tts.json",
                      path=["texts", "announce"], value="{user} schickt {bits} Bits: {message}")
    assert reply["ok"], reply
    assert tts.text("announce", user="A", bits=5, message="m") == "A schickt 5 Bits: m"
    assert tts.path.read_text() == repo_before
    assert reply["data"]["overrides"] == {"texts": {"announce": "{user} schickt {bits} Bits: {message}"}}


@pytest.mark.parametrize("path,value,error", [
    (["texts", "announce"], "{nutzer} hat {bits} Bits", "unbekannte Platzhalter"),
    (["texts", "announce"], "{user hat Bits", "ungültige Vorlage"),
    (["min_bits"], "hundert", "erwartet number"),
    (["include_anonymous"], 1, "erwartet bool"),
    (["texts", "brandneu"], "x", "keine neuen Einträge"),
    (["_comment"], "x", "Kommentare"),
    (["gibtsnicht", "x"], 1, "gibt es nicht"),
])
async def test_refused(setup, path, value, error):
    feature, tts, *_ = setup
    reply = await ask(feature, op="set", file="platforms/twitch/features/tts/tts.json", path=path, value=value)
    assert not reply["ok"] and error in reply["error"]
    assert tts.overrides == {}


async def test_new_static_command_may_be_added_and_reset(setup):
    feature, _, twitch, _ = setup
    file = "platforms/twitch/twitch.json"
    assert (await ask(feature, op="set", file=file, path=["commands", "!hallo"], value="Hallo {u}!"))["ok"]
    assert twitch.section("commands")["!hallo"] == "Hallo {u}!"
    assert (await ask(feature, op="reset", file=file, path=["commands", "!hallo"]))["ok"]
    assert "!hallo" not in twitch.section("commands") and twitch.overrides == {}


async def test_undo_restores_the_previous_version(setup):
    feature, tts, *_ = setup
    file = "platforms/twitch/features/tts/tts.json"
    await ask(feature, op="set", file=file, path=["min_bits"], value=200)
    state = (await ask(feature, op="set", file=file, path=["min_bits"], value=300))["data"]
    assert tts.get("min_bits") == 300
    await ask(feature, op="restore", file=file, version=state["history"][0])
    assert tts.get("min_bits") == 200


async def test_read_only_file_is_refused(setup):
    feature, *_ = setup
    reply = await ask(feature, op="set", file="features/sql_db/sql_db.json", path=["db_path"], value="/tmp/x.db")
    assert not reply["ok"] and "nur lesbar" in reply["error"]


async def test_garbage_is_answered_not_raised(setup):
    feature, *_ = setup
    assert (await feature.handle("{nope"))["ok"] is False
    assert (await ask(feature, op="get", file="../etc/passwd"))["ok"] is False
    assert (await ask(feature, op="explode", file="platforms/twitch/twitch.json"))["ok"] is False


class FakeTool:
    def settings_tool_info(self):
        return {"id": "fake", "title": "Fake", "config": "fake.json"}

    async def settings_tool(self, action, args):
        if action == "boom":
            raise ValueError("kaputt")
        return {"action": action, "args": args}


class ToolBus:
    def features_with(self, capability):
        return [FakeTool()]

    def feature_with(self, capability):
        return None


async def test_tool_calls_are_handed_through(setup):
    feature, *_ = setup
    await feature.setup(ToolBus())
    assert feature.tools() == [{"id": "fake", "title": "Fake", "config": "fake.json"}]
    reply = await ask(feature, op="tool", tool="fake", action="speak", args={"text": "hi"})
    assert reply["ok"] and reply["data"] == {"action": "speak", "args": {"text": "hi"}}
    assert (await ask(feature, op="tool", tool="fake", action="boom"))["error"] == "kaputt"
    assert not (await ask(feature, op="tool", tool="nope", action="x"))["ok"]
