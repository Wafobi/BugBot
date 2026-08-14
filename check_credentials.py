#!/usr/bin/env python3
"""Checks the credentials of the platforms present - against the services themselves.

    python3 check_credentials.py            every platform present
    python3 check_credentials.py twitch     only this one

Meant for after filling in the .env, after a new token, and for the moment when the bot "is
running" but something quietly is not working. check_config.py checks the JSON files against
the code; this checks the .env against Twitch, Discord and the machine itself.

The bot itself skips a platform that fails to load, with a warning, and runs on with the rest -
rightly so, but it also means an expired token is only a line in the log. This asks
deliberately.

The tests are found the same way as the platforms themselves: platforms/<name>/credentials.py
with a check() function. This script names no platform, and a new one simply brings its test
along (see core/registry.py, docs/extending.md).

The contract of check(): yields (level, message) pairs.

    "ok"      works
    "warn"    works, but not as intended - or is needlessly risky
    "fail"    this is not going to work
    "skip"    not set up; the platform would not even load
    "detail"  continuation line for the previous message

Reading calls only, nothing is changed. Without a network it says so, rather than claiming
something is broken. Return value 1 as soon as a "fail" appears anywhere.
"""

import importlib
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Before importing the platform packages: they read their environment at import time.
load_dotenv(ROOT / ".env")

from core import registry  # noqa: E402  (erst nach load_dotenv sinnvoll)

MARKERS = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "skip": "⏭️ ", "detail": "  ↳"}


def checks_for(name):
    """A platform's check() function, or None - a platform need not bring a credentials test
    along."""
    try:
        module = importlib.import_module(f"platforms.{name}.credentials")
    except ModuleNotFoundError:
        return None
    return getattr(module, "check", None)


def run(name):
    """Runs a platform's test and returns (number of fails, number of warns)."""
    print(f"\n── {name} " + "─" * (60 - len(name)))

    check = checks_for(name)
    if check is None:
        print(f"{MARKERS['skip']} no credentials test present "
              f"(platforms/{name}/credentials.py is missing).")
        return 0, 0

    failed = warned = 0
    try:
        for level, message in check():
            print(f"{MARKERS.get(level, '  ')} {message}")
            failed += level == "fail"
            warned += level == "warn"
    except Exception as e:
        # A broken test must not take the remaining platforms with it - the same rule as when
        # loading the platforms themselves.
        print(f"{MARKERS['fail']} Test abgebrochen: {e!r}")
        failed += 1
    return failed, warned


def main(argv):
    wanted = [a.lower() for a in argv[1:] if not a.startswith("-")]
    if any(a in ("-h", "--help") for a in argv[1:]):
        print(__doc__)
        return 0

    available = registry.platform_names()
    if not available:
        print("❌ No platform found - is the script running in the project root?")
        return 1

    unknown = [name for name in wanted if name not in available]
    if unknown:
        print(f"❌ Unbekannte Plattform(en): {', '.join(unknown)}")
        print(f"   Vorhanden: {', '.join(available)}")
        return 1

    names = wanted or available
    failed = warned = 0
    for name in names:
        f, w = run(name)
        failed += f
        warned += w

    print("\n" + "─" * 62)
    if failed:
        print(f"❌ {failed} problem(s), {warned} warning(s). The bot will not run fully like "
              f"this.")
        return 1
    if warned:
        print(f"⚠️  Keine Fehler, aber {warned} Warnung(en) - lies sie, bevor du dich "
              f"wunderst.")
        return 0
    print("✅ Alle Zugangsdaten in Ordnung.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
