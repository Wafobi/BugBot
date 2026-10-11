"""synth: parsing and checking the "voice" setting against what is installed."""

import json

import pytest

from platforms.twitch.features.tts import synth


def install(directory, model, speakers=1):
    (directory / f"{model}.onnx").write_bytes(b"")
    (directory / f"{model}.onnx.json").write_text(json.dumps({"num_speakers": speakers}))


def test_parse_model_only():
    assert synth.parse("de_DE-thorsten-medium") == ("de_DE-thorsten-medium", None)


def test_parse_model_and_speaker():
    assert synth.parse(" de_DE-mls-medium # 42 ") == ("de_DE-mls-medium", 42)


@pytest.mark.parametrize("value", ["", "../etc/passwd", "a/b", "de_DE-mls-medium#x", "de_DE-mls-medium#-1"])
def test_parse_rejects_bad_values(value):
    with pytest.raises(synth.VoiceError):
        synth.parse(value)


def test_resolve_checks_installation_and_speaker_range(tmp_path):
    install(tmp_path, "de_DE-mls-medium", speakers=236)
    voice = synth.resolve("de_DE-mls-medium#235", tmp_path)
    assert voice.speaker == 235 and voice.name == "de_DE-mls-medium#235"
    with pytest.raises(synth.VoiceError):
        synth.resolve("de_DE-mls-medium#236", tmp_path)
    with pytest.raises(synth.VoiceError):
        synth.resolve("de_DE-kerstin-low", tmp_path)


def test_installed_lists_only_complete_models(tmp_path):
    install(tmp_path, "b-model")
    install(tmp_path, "a-model")
    (tmp_path / "half.onnx").write_bytes(b"")
    assert synth.installed(tmp_path) == ["a-model", "b-model"]
