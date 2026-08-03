#!/usr/bin/env python3
"""Prüft die Zugangsdaten der vorhandenen Plattformen - gegen die Dienste selbst.

    python3 check_credentials.py            alle vorhandenen Plattformen
    python3 check_credentials.py twitch     nur diese

Gedacht für nach dem Ausfüllen der .env, nach einem neuen Token, und für den Moment, in
dem der Bot "läuft", aber etwas still nicht tut. check_config.py prüft die JSON-Dateien
gegen den Code; das hier prüft die .env gegen Twitch, Discord und die eigene Maschine.

Der Bot selbst überspringt eine Plattform, die sich nicht laden lässt, mit einer Warnung
und läuft mit den übrigen weiter - richtig so, aber es heißt auch, dass ein abgelaufener
Token nur eine Zeile im Log ist. Das hier fragt gezielt nach.

Gefunden werden die Tests wie die Plattformen selbst: platforms/<name>/credentials.py mit
einer Funktion check(). Dieses Skript nennt keine Plattform beim Namen, und eine neue
bringt ihren Test einfach mit (siehe core/registry.py, docs/extending.md).

Der Vertrag von check(): liefert (Stufe, Meldung)-Paare.

    "ok"      läuft
    "warn"    funktioniert, aber nicht wie gedacht - oder ist unnötig riskant
    "fail"    so wird das nichts
    "skip"    nicht eingerichtet; die Plattform würde gar nicht erst laden
    "detail"  Fortsetzungszeile zur vorherigen Meldung

Nur lesende Aufrufe, nichts wird geändert. Ohne Netz meldet es das, statt zu behaupten,
etwas sei kaputt. Rückgabewert 1, sobald irgendwo ein "fail" steht.
"""

import importlib
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Vor dem Import der Plattform-Pakete: die lesen ihre Umgebung beim Import.
load_dotenv(ROOT / ".env")

from core import registry  # noqa: E402  (erst nach load_dotenv sinnvoll)

MARKERS = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "skip": "⏭️ ", "detail": "  ↳"}


def checks_for(name):
    """Die check()-Funktion einer Plattform, oder None - eine Plattform muss keinen
    Zugangsdaten-Test mitbringen."""
    try:
        module = importlib.import_module(f"platforms.{name}.credentials")
    except ModuleNotFoundError:
        return None
    return getattr(module, "check", None)


def run(name):
    """Führt den Test einer Plattform aus und gibt (Anzahl fail, Anzahl warn) zurück."""
    print(f"\n── {name} " + "─" * (60 - len(name)))

    check = checks_for(name)
    if check is None:
        print(f"{MARKERS['skip']} kein Zugangsdaten-Test vorhanden "
              f"(platforms/{name}/credentials.py fehlt).")
        return 0, 0

    failed = warned = 0
    try:
        for level, message in check():
            print(f"{MARKERS.get(level, '  ')} {message}")
            failed += level == "fail"
            warned += level == "warn"
    except Exception as e:
        # Ein kaputter Test darf die übrigen Plattformen nicht mitnehmen - dieselbe
        # Regel wie beim Laden der Plattformen selbst.
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
        print("❌ Keine Plattform gefunden - läuft das Skript im Projektstamm?")
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
        print(f"❌ {failed} Problem(e), {warned} Warnung(en). Der Bot läuft so nicht "
              f"vollständig.")
        return 1
    if warned:
        print(f"⚠️  Keine Fehler, aber {warned} Warnung(en) - lies sie, bevor du dich "
              f"wunderst.")
        return 0
    print("✅ Alle Zugangsdaten in Ordnung.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
