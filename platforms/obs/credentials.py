"""Prüft die OBS-Zugangsdaten, soweit das von hier aus überhaupt geht.

Aufgerufen von check_credentials.py im Projektstamm. Der Vertrag ist eine Funktion
check(), die (Stufe, Meldung)-Paare liefert; Stufen sind "ok", "warn", "fail", "skip"
und "detail" (Fortsetzungszeile zur vorherigen Meldung).

Der Vorbehalt gleich vorweg: OBS läuft auf dem Rechner des Streamers und ruft *uns* an
(siehe docs/obs.md). Von hier aus lässt sich deshalb nicht feststellen, ob OBS läuft, ob
das Relais eingewählt ist oder ob OBS_PASSWORD stimmt - das weiß erst der laufende Bot,
und `!obs` im Chat sagt es. Prüfbar ist nur die eigene Seite: sind die Geheimnisse
gesetzt, ist der Port frei, passt die Konfiguration zusammen.
"""

import os
import socket

DEFAULT_PORT = 4456
DEFAULT_BIND = "0.0.0.0"


def _port_is_free(bind, port):
    """Kann der Bot diesen Port überhaupt öffnen? Belegt heißt nicht kaputt - meistens
    heißt es, dass der Bot schon läuft."""
    family = socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind, port))
            return True
        except OSError:
            return False


def check():
    token = os.environ.get("OBS_BRIDGE_TOKEN", "").strip()
    password = os.environ.get("OBS_PASSWORD", "").strip()
    raw_port = os.environ.get("OBS_BRIDGE_PORT", "").strip()
    bind = os.environ.get("OBS_BRIDGE_BIND", "").strip() or DEFAULT_BIND

    if not token:
        yield "skip", ("OBS_BRIDGE_TOKEN ist nicht gesetzt - die OBS-Plattform wird nicht "
                       "geladen. So betreibt man den Bot ohne OBS.")
        return

    # --- Das gemeinsame Geheimnis ----------------------------------------------------
    if len(token) < 32:
        yield "warn", (f"OBS_BRIDGE_TOKEN ist nur {len(token)} Zeichen lang - er ist das "
                       f"einzige Passwort zur Fernsteuerung einer laufenden Sendung. "
                       f"Frisch erzeugen mit: openssl rand -hex 32")
    else:
        yield "ok", f"OBS_BRIDGE_TOKEN gesetzt ({len(token)} Zeichen)."

    if not password:
        yield "warn", ("OBS_PASSWORD ist leer - richtig nur, wenn in OBS unter "
                       "Werkzeuge -> WebSocket-Server die Authentifizierung aus ist.")
    else:
        yield "ok", "OBS_PASSWORD gesetzt."

    # --- Der Port --------------------------------------------------------------------
    if raw_port and not raw_port.isdigit():
        yield "fail", f"OBS_BRIDGE_PORT ist keine Zahl: {raw_port!r}"
        return
    port = int(raw_port) if raw_port else DEFAULT_PORT

    if _port_is_free(bind, port):
        yield "ok", f"Port {bind}:{port} ist frei."
    else:
        yield "warn", (f"Port {bind}:{port} ist belegt - normal, wenn der Bot gerade läuft. "
                       f"Sonst hört dort etwas anderes zu.")

    if bind == "0.0.0.0":
        yield "detail", ("Bind auf 0.0.0.0 ist im Container richtig: die Beschränkung auf "
                         "Loopback steht dort auf der Host-Seite (PublishPort in "
                         "bugbot.container). Ohne Container gehört hier 127.0.0.1 hin.")

    yield "detail", ("ob OBS läuft, das Relais eingewählt ist und OBS_PASSWORD stimmt, "
                     "beantwortet erst `!obs` im Chat - siehe docs/obs.md.")
