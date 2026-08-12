"""Zugangsdaten des Overlay-Lauschers aus der Umgebung/.env.

Gegenstück zu platforms/obs/config.py, und aus demselben Grund getrennt von overlay.json:
in die JSON-Dateien schaut man beim Anpassen, Geheimnisse haben dort nichts verloren.

Ohne OVERLAY_TOKEN öffnet das Feature keinen Port. Es lädt trotzdem - seine Befehle
(Todeszähler) funktionieren auch ohne Overlay. Das ist zugleich die Zusicherung, dass der
Port nie ohne Geheimnis offensteht.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent und unabhängig von der Import-Reihenfolge - die Plattformen laden dieselbe
# Datei, load_dotenv überschreibt bereits gesetzte Variablen aber nicht.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Gemeinsames Geheimnis mit den Browser-Quellen. Leer = kein Lauscher, siehe oben.
OVERLAY_TOKEN = os.environ.get("OVERLAY_TOKEN", "")

# Port, auf dem der Bot auf die Overlays wartet.
OVERLAY_PORT = int(os.environ.get("OVERLAY_PORT") or 4457)

# Bind-Adresse. Dieselbe Überlegung wie bei OBS_BRIDGE_BIND: im Container 0.0.0.0, weil
# Podmans Portweiterleitung den Container-Loopback nicht erreicht - beschränkt wird dann
# auf der Host-Seite über PublishPort=127.0.0.1:4457:4457 in bugbot.container. Ohne
# Container ist 127.0.0.1 richtig, dann ist der Port von außen gar nicht zu sehen.
OVERLAY_BIND = os.environ.get("OVERLAY_BIND") or "0.0.0.0"
