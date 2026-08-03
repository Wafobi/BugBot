"""OBS-Konfiguration aus der Umgebung/.env.

Gegenstück zu platforms/twitch/config.py und platforms/discord/config.py: core bleibt
plattformneutral, jede Plattform bringt ihre eigenen Zugangsdaten mit.

Der Unterschied zu den beiden anderen steckt in der Richtung: Discord und Twitch sind
Dienste im Netz, die der Bot anruft. OBS läuft auf dem Rechner des Streamers, der Bot auf
einem Server - und obs-websocket ist selbst ein Server, der dort *lokal* lauscht. Der Bot
käme also nur an ihn heran, wenn zu Hause ein Port aus dem Internet erreichbar wäre. Statt
dessen dreht ein Relais auf dem OBS-PC die Richtung um (platforms/obs/obs_bridge.py) und
wählt sich beim Bot ein; hier stehen deshalb keine Adressdaten von OBS, sondern die des
eigenen Lauschers.

Zwei Geheimnisse, zwei Strecken:
  OBS_BRIDGE_TOKEN  Bot <-> Relais. Wer sich beim Bot einwählt, muss es kennen.
  OBS_PASSWORD      Bot <-> obs-websocket. Wandert durch das Relais hindurch bis zu OBS
                    und ist genau das Passwort aus den WebSocket-Servereinstellungen.

Ohne OBS_BRIDGE_TOKEN gibt es keine OBS-Plattform: core/registry.py überspringt sie dann
mit einer Warnung. Das ist der Weg, den Bot ohne OBS zu betreiben - und zugleich die
Zusicherung, dass der Port nie ohne Geheimnis offensteht.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent und unabhängig von der Import-Reihenfolge - die anderen Plattformen laden
# dieselbe Datei, load_dotenv überschreibt bereits gesetzte Variablen aber nicht.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Gemeinsames Geheimnis mit dem Relais auf dem OBS-PC. Pflicht: siehe oben.
OBS_BRIDGE_TOKEN = os.environ["OBS_BRIDGE_TOKEN"]

# Port, auf dem der Bot auf das Relais wartet.
OBS_BRIDGE_PORT = int(os.environ.get("OBS_BRIDGE_PORT") or 4456)

# Bind-Adresse. Der Port gehört nicht ins offene Netz: vom OBS-Rechner führt ein SSH-Tunnel
# hierher (siehe README, Setup-Schritt 4), das Relais verbindet sich also mit seinem eigenen
# Loopback. Wo diese Beschränkung sitzt, hängt vom Betrieb ab:
#   im Container    0.0.0.0 (Default) - der Container hat einen eigenen Netzwerk-Namensraum,
#                   und Podmans Portweiterleitung erreicht dessen Loopback nicht. Beschränkt
#                   wird deshalb auf der Host-Seite: PublishPort=127.0.0.1:4456:4456.
#   ohne Container  127.0.0.1 - dann ist der Port von außen gar nicht erst zu sehen.
OBS_BRIDGE_BIND = os.environ.get("OBS_BRIDGE_BIND") or "0.0.0.0"

# Das Passwort aus den WebSocket-Servereinstellungen von OBS. Leer nur dann, wenn dort die
# Authentifizierung ausgeschaltet ist - was vertretbar ist, weil obs-websocket auf dem
# OBS-PC nur auf 127.0.0.1 lauschen muss: nach draußen geht ausschließlich das Relais.
OBS_PASSWORD = os.environ.get("OBS_PASSWORD", "")
