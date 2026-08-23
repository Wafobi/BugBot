"""VariablesFeature: eval() hardening (H-2 regression) and expression resolution."""

import json

import pytest

from core import runtime_config
from features.variables.feature import DEFAULTS, VariablesFeature, _SAFE_NAMES, _no_import


# --- H-2 regression: eval() must not be able to import anything ---------------------------

def test_no_import_allows_time_strftime_needs():
    # datetime.strftime's C implementation looks up __import__ on the calling frame's
    # builtins to lazily reach the stdlib "time" module for locale-aware directives (%A,
    # %B) - this is the one thing _no_import must still allow, or ordinary {time}/{date}
    # variables break.
    import time as time_module
    assert _no_import("time") is time_module


def test_no_import_blocks_everything_else():
    with pytest.raises(ImportError):
        _no_import("os")
    with pytest.raises(ImportError):
        _no_import("subprocess")
    with pytest.raises(ImportError):
        _no_import("sys")


def test_safe_names_eval_environment_has_no_dangerous_builtins():
    # __import__ is present (as the restricted _no_import), but open/eval/exec/compile must
    # not be reachable at all - not even indirectly, since there is no bare "__builtins__"
    # key left for Python to fall back to injecting the real ones.
    builtins_available = _SAFE_NAMES["__builtins__"]
    assert "open" not in builtins_available
    assert "eval" not in builtins_available
    assert "exec" not in builtins_available
    assert "compile" not in builtins_available
    assert builtins_available["__import__"] is _no_import


def test_eval_of_an_import_expression_is_blocked_end_to_end():
    # The actual exploit from H-2: __import__('os').system(...)/getcwd() as an *expression*,
    # not a statement - compile(..., "eval") alone does not stop this, only the explicit,
    # import-free __builtins__ does.
    compiled = compile("__import__('os').getcwd()", "<test>", "eval")
    with pytest.raises(ImportError):
        eval(compiled, {**_SAFE_NAMES})  # noqa: S307 - deliberately testing the guard itself


def test_eval_of_datetime_strftime_still_works_end_to_end():
    import datetime
    compiled = compile("now.strftime('%H:%M')", "<test>", "eval")
    env = {**_SAFE_NAMES, "now": datetime.datetime(2026, 1, 1, 9, 5)}
    assert eval(compiled, env) == "09:05"  # noqa: S307


# --- resolve(): end-to-end through a real VariablesFeature ---------------------------------

@pytest.fixture
def feature(tmp_path):
    f = VariablesFeature()
    path = tmp_path / "variables.json"
    path.write_text(json.dumps({
        "variables": {"static_one": "hello"},
        "python": {
            "double": "1 + 1",
            "greet": "'hi ' + u",
            "chain": "double + '!'",
            "exploit": "__import__('os').getcwd()",
            "circular_a": "circular_b",
            "circular_b": "circular_a",
        },
    }), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    return f


async def test_resolve_only_evaluates_placeholders_actually_used(feature):
    values = await feature.resolve("{static_one}")
    assert values == {"static_one": "hello"}
    assert "double" not in values  # never asked for, never evaluated


async def test_resolve_static_and_python_variables(feature):
    values = await feature.resolve("{static_one} {double}")
    assert values["static_one"] == "hello"
    assert values["double"] == "2"


async def test_resolve_python_variable_can_use_context(feature):
    values = await feature.resolve("{greet}", u="jens")
    assert values["greet"] == "hi jens"


async def test_resolve_python_variable_can_use_another_variable(feature):
    values = await feature.resolve("{chain}")
    assert values["chain"] == "2!"


async def test_resolve_circular_variable_stays_unresolved_instead_of_hanging(feature):
    values = await feature.resolve("{circular_a}")
    assert "circular_a" not in values


async def test_resolve_exploit_expression_stays_unresolved_not_raised(feature):
    # The end-to-end H-2 regression: an operator-authored (or, if variables.json were ever
    # writable by something less trusted, attacker-authored) __import__ expression must
    # never actually run - it fails silently into "placeholder stays standing", the same
    # outcome as any other broken expression, never an exception out of resolve().
    values = await feature.resolve("{exploit}")
    assert "exploit" not in values


async def test_resolve_python_variable_result_is_cached(feature):
    # _evaluate() only reaches _remember() on a successful evaluation - {double} qualifies,
    # so after one resolve() its result must sit in the cache under the config's current
    # version (see _cached()/_remember()).
    await feature.resolve("{double}")
    assert "double" in feature._cache
    version, _when, value = feature._cache["double"]
    assert version == feature.config.version
    assert value == "2"
