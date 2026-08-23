# Tests

    pip install -r requirements-dev.txt
    pytest

One test package per source package, same name, same nesting - `core/events.py` is tested
by `tests/core/test_events.py`, `features/moderation/feature.py` by
`tests/features/moderation/test_feature.py`, and so on. The point of mirroring the layout
this closely: finding the tests for a piece of code (or noticing there are none) should
never need more than swapping `core/`/`features/`/`platforms/` for `tests/` in the path.

What's covered so far leans toward the highest-value spots rather than exhaustive coverage
of an ~11k-line project: the core contracts (`EventBus`, `LiveConfig`, `Announcement`), the
escalation/filtering logic in moderation (the part most likely to silently regress), and a
regression test for every finding fixed after the 2026-08-23 security review - a CRLF
smuggled onto the Twitch IRC connection, the link-filter host-matching bypass, the `eval()`
builtins leak, the badge `startswith` false-match, and so on. Extending this to
`platforms/discord`, `platforms/twitch/api.py` and the remaining `features/*` packages the
same way is the natural next step, not a closed set.

`EventBus` is deliberately instantiable on its own (see its docstring) exactly so tests
don't need the real bot running - most tests here build a fresh one (or a bare `Feature`
subclass, or a `LiveConfig` over a temp JSON file) rather than importing `bugbot.py`.
`platforms/twitch/*` and `platforms/discord/*` read required settings from the environment
at import time (`TWITCH_CHANNEL`, `DISCORD_TOKEN`, ...) - `tests/conftest.py` sets dummy
values for all of them before any such module is imported, so the test suite never needs a
real `.env`.
