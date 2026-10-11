"""LiveConfig: defaults-underneath-file merging, hot reload, text()/render() robustness,
command_names resolution."""

import json
import os

import pytest

from core.runtime_config import LiveConfig, deep_merge


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def touch_newer(path):
    """Forces a distinct, later mtime - see the identical note in test_events.py."""
    newer = (path.stat().st_mtime or 0) + 5
    os.utime(path, (newer, newer))


# --- deep_merge --------------------------------------------------------------------------

def test_deep_merge_overrides_scalars_and_merges_nested_dicts():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    override = {"a": 2, "nested": {"y": 20}}
    merged = deep_merge(base, override)
    assert merged == {"a": 2, "nested": {"x": 1, "y": 20}}


def test_deep_merge_replaces_lists_wholesale():
    base = {"items": [1, 2, 3]}
    merged = deep_merge(base, {"items": [9]})
    # A list in the override replaces the default list entirely - otherwise nothing could
    # ever be removed from a default list via JSON.
    assert merged == {"items": [9]}


# --- get/section: defaults underneath the file --------------------------------------------

def test_get_falls_back_to_defaults_for_missing_key(tmp_path):
    path = tmp_path / "x.json"
    write(path, {})
    config = LiveConfig(path, defaults={"threshold": 5})
    assert config.get("threshold") == 5


def test_get_prefers_file_over_defaults(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"threshold": 9})
    config = LiveConfig(path, defaults={"threshold": 5})
    assert config.get("threshold") == 9


def test_section_returns_empty_dict_for_missing_or_wrong_type(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"settings": "not a dict"})
    config = LiveConfig(path)
    assert config.section("settings") == {}
    assert config.section("does_not_exist") == {}


def test_missing_file_falls_back_to_defaults_without_crashing(tmp_path):
    config = LiveConfig(tmp_path / "does_not_exist.json", defaults={"a": 1})
    assert config.get("a") == 1


def test_broken_json_keeps_previous_state_instead_of_crashing(tmp_path, caplog):
    path = tmp_path / "x.json"
    write(path, {"a": 1})
    config = LiveConfig(path, defaults={})
    assert config.get("a") == 1

    touch_newer(path)
    path.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert config.get("a") == 1  # unchanged, not crashed
    assert "x.json" in caplog.text


# --- hot reload ----------------------------------------------------------------------------

def test_reload_picks_up_a_changed_file(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"a": 1})
    config = LiveConfig(path)
    assert config.get("a") == 1
    version_before = config.version

    touch_newer(path)
    write(path, {"a": 2})
    assert config.get("a") == 2
    assert config.version == version_before + 1


def test_deleting_a_key_from_the_file_falls_back_to_default(tmp_path):
    # "the file is the truth" - a deleted line falls back to the code default, it does not
    # keep the old file value alive.
    path = tmp_path / "x.json"
    write(path, {"a": 99})
    config = LiveConfig(path, defaults={"a": 1})
    assert config.get("a") == 99

    touch_newer(path)
    write(path, {})
    assert config.get("a") == 1


# --- text(): robust against a missing/broken template --------------------------------------

def test_text_returns_key_when_nothing_is_stored(tmp_path, caplog):
    path = tmp_path / "x.json"
    write(path, {})
    config = LiveConfig(path)
    with caplog.at_level("WARNING"):
        assert config.text("greeting") == "greeting"
    assert "greeting" in caplog.text


def test_text_fills_known_placeholders(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"texts": {"hello": "Hi {user}!"}})
    config = LiveConfig(path)
    assert config.text("hello", user="jens") == "Hi jens!"


def test_text_with_unknown_placeholder_falls_back_without_raising(tmp_path, caplog):
    # format(**values) would raise KeyError on {oops} with no such kwarg - text() must
    # never propagate that into a command handler.
    path = tmp_path / "x.json"
    write(path, {"texts": {"hello": "Hi {oops}!"}})
    config = LiveConfig(path)
    with caplog.at_level("WARNING"):
        result = config.text("hello", user="jens")
    assert result is not None
    assert "hello" in caplog.text


def test_text_complains_only_once_per_key(tmp_path, caplog):
    path = tmp_path / "x.json"
    write(path, {})
    config = LiveConfig(path)
    with caplog.at_level("WARNING"):
        config.text("missing")
        config.text("missing")
    assert caplog.text.count("missing") == 1


# --- render(): unknown placeholders stay standing rather than raising ---------------------

@pytest.fixture
def blank_config(tmp_path):
    path = tmp_path / "x.json"
    write(path, {})
    return LiveConfig(path)


def test_render_fills_known_values(blank_config):
    assert blank_config.render("It is {time}, @{u}", time="10:00", u="jens") == "It is 10:00, @jens"


def test_render_leaves_unknown_placeholder_standing(blank_config, caplog):
    with caplog.at_level("WARNING"):
        result = blank_config.render("It is {time}, @{u}", time="10:00")
    assert result == "It is 10:00, @{u}"


def test_render_survives_a_malformed_template(blank_config, caplog):
    with caplog.at_level("WARNING"):
        result = blank_config.render("broken {", time="10:00")
    assert result == "broken {"


# --- resolve_commands: rename/alias/disable --------------------------------------------

def test_resolve_commands_without_overrides_keeps_declared_names(tmp_path):
    path = tmp_path / "x.json"
    write(path, {})
    config = LiveConfig(path)
    resolved = config.resolve_commands({"!uptime": "handler"})
    assert resolved == {"!uptime": "handler"}


def test_resolve_commands_rename(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"command_names": {"!uptime": "!live"}})
    config = LiveConfig(path)
    resolved = config.resolve_commands({"!uptime": "handler"})
    assert resolved == {"!live": "handler"}


def test_resolve_commands_aliases(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"command_names": {"!uptime": ["!live", "!howlong"]}})
    config = LiveConfig(path)
    resolved = config.resolve_commands({"!uptime": "handler"})
    assert resolved == {"!live": "handler", "!howlong": "handler"}


def test_resolve_commands_disable(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"command_names": {"!uptime": False}})
    config = LiveConfig(path)
    resolved = config.resolve_commands({"!uptime": "handler"})
    assert resolved == {}


def test_resolve_commands_dict_form_with_name_and_aliases(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"command_names": {"!uptime": {"name": "!live", "aliases": ["!howlong"]}}})
    config = LiveConfig(path)
    resolved = config.resolve_commands({"!uptime": "handler"})
    assert resolved == {"!live": "handler", "!howlong": "handler"}


def test_resolve_commands_underscore_keys_are_comments_not_commands(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"command_names": {"_comment": "explanation", "!uptime": "!live"}})
    config = LiveConfig(path)
    resolved = config.resolve_commands({"!uptime": "handler"})
    assert resolved == {"!live": "handler"}


# --- color() -------------------------------------------------------------------------------

def test_color_parses_hex_string(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"colors": {"bug": "#2ECC71"}})
    config = LiveConfig(path)
    assert config.color("bug") == 0x2ECC71


def test_color_accepts_plain_int(tmp_path):
    path = tmp_path / "x.json"
    write(path, {"colors": {"bug": 12345}})
    config = LiveConfig(path)
    assert config.color("bug") == 12345


def test_color_falls_back_to_default_on_garbage(tmp_path, caplog):
    path = tmp_path / "x.json"
    write(path, {"colors": {"bug": "not a color"}})
    config = LiveConfig(path)
    with caplog.at_level("WARNING"):
        assert config.color("bug", default=0x123456) == 0x123456


# --- override layer ------------------------------------------------------------------------

def test_override_lies_on_top_and_lists_replace(tmp_path):
    import json as _json
    path = tmp_path / "x.json"
    path.write_text(_json.dumps({"a": 1, "nested": {"k": 1, "j": 2}, "pool": ["x", "y"]}))
    override = tmp_path / "settings" / "x.json"
    config = LiveConfig(path, override=override)
    assert config.get("a") == 1 and config.overrides == {}
    config.save_overrides({"a": 5, "nested": {"k": 9}, "pool": ["z"]})
    assert config.get("a") == 5
    assert config.section("nested") == {"k": 9, "j": 2}
    assert config.get("pool") == ["z"]
    assert _json.loads(path.read_text())["a"] == 1  # the file underneath is never written
    config.save_overrides({"a": None})  # None = back to the file's value
    assert config.get("a") == 1


def test_broken_override_keeps_the_previous_one(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"a": 1}')
    override = tmp_path / "o.json"
    config = LiveConfig(path, override=override)
    config.save_overrides({"a": 2})
    import os as _os
    import time as _time
    override.write_text("{broken")
    _os.utime(override, (_time.time() + 5, _time.time() + 5))
    assert config.get("a") == 2


def test_files_outside_the_repo_have_no_override_by_default(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"a": 1}')
    assert LiveConfig(path).override_path is None


def test_nested_override_set_reset_and_history(tmp_path):
    import json as _json
    path = tmp_path / "x.json"
    path.write_text(_json.dumps({"texts": {"a": "A", "b": "B"}, "n": 1}))
    config = LiveConfig(path, override=tmp_path / "settings" / "x.json")
    config.set_override(["texts", "a"], "AA")
    config.set_override(["n"], 2)
    assert config.section("texts") == {"a": "AA", "b": "B"} and config.get("n") == 2
    config.reset_override(["texts", "a"])
    assert config.overrides == {"n": 2}  # the emptied "texts" dict is pruned
    assert len(config.history()) == 2
    config.restore(config.history()[0])  # back to before the reset
    assert config.section("texts")["a"] == "AA"
