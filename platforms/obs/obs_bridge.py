#!/usr/bin/env python3
"""BugBot-OBS-Relais - läuft auf dem Rechner mit OBS, nicht auf dem Server.

Diese Datei gehört als einzige in diesem Ordner *nicht* zum Bot: sie wird nirgends
importiert, sondern auf den Streaming-PC kopiert und dort gestartet. Sie hat auch keine
Abhängigkeit zum übrigen Projekt - eine einzelne Datei plus `pip install websockets`.

Warum es sie gibt: obs-websocket ist ein Server und lauscht auf dem Streaming-PC. Der Bot
läuft auf einem Server im Netz und käme dort nur heran, wenn zu Hause ein Port aus dem
Internet erreichbar wäre. Also dreht dieses Relais die Richtung um - es wählt sich beim
Bot ein und reicht danach beide Richtungen unverändert durch:

    OBS (obs-websocket, 127.0.0.1:4455)  <--lokal-->  Relais  --wählt sich ein-->  BugBot

Durch die Leitung läuft ganz normales obs-websocket 5. Das Relais liest den Inhalt nicht
und kennt das OBS-Passwort nicht: die Anmeldung macht der Bot am anderen Ende selbst.

Einrichten
----------
 1. In OBS: Werkzeuge -> WebSocket-Servereinstellungen -> Server aktivieren,
    Authentifizierung an, Passwort merken. Port 4455 bleibt, wie er ist; er muss *nicht*
    in der Firewall freigegeben werden - nur dieses Relais spricht damit.
 2. Auf dem Server: dasselbe Passwort als OBS_PASSWORD in die .env des Bots, dazu ein
    frisches OBS_BRIDGE_TOKEN (z.B. `openssl rand -hex 32`). Der Port des Bots bleibt
    dabei geschlossen - er lauscht nur auf 127.0.0.1 des Servers.
 3. Hier einen SSH-Tunnel dorthin aufmachen und offen halten:

        ssh -N -L 4456:127.0.0.1:4456 benutzer@bugbot.example.org

    Damit wird der Port des Bots zum lokalen Port 4456 *dieses* Rechners. Nichts steht
    dafür im Internet offen, und die ganze Strecke ist verschlüsselt - was obs-websocket
    von sich aus nicht ist. Dauerhaft am besten mit autossh (oder `ssh -o
    ServerAliveInterval=30 -o ExitOnForwardFailure=yes` in einer Neustart-Schleife).
 4. Python 3.9+ und `pip install websockets`, dann das Relais gegen das eigene Tunnelende:

        python3 obs_bridge.py --server ws://127.0.0.1:4456 --token <TOKEN>

    Statt der Argumente gehen auch die Umgebungsvariablen BUGBOT_SERVER, BUGBOT_TOKEN
    und OBS_WEBSOCKET.

Bequemer geht es mit obs_bridge_script.py aus demselben Ordner: das ist dieselbe Sache als
OBS-Skript (Werkzeuge -> Skripte). Adresse und Token stehen dann in der OBS-Oberfläche, und
das Relais startet und endet mit OBS - Schritt 4 entfällt damit.

Das Relais läuft dauerhaft mit und verbindet sich nach jedem Abbruch von selbst neu - OBS
und der Tunnel dürfen also jederzeit zu- und aufgehen. Wer es ohne OBS-Skript betreibt,
startet es sinnvollerweise mit dem Rechner (Windows: Aufgabenplanung oder Autostart-Ordner;
Linux: eine systemd-User-Unit), zusammen mit dem Tunnel aus Schritt 3.
"""

import argparse
import asyncio
import os
import sys

try:
    import websockets
except ImportError:
    sys.exit("Fehlt: das Paket 'websockets'. Installieren mit:  pip install websockets")

# Der Header, in dem der Bot den Token erwartet (siehe platforms/obs/link.py).
TOKEN_HEADER = "X-BugBot-Token"

OBS_RETRY_SECONDS = 5
SERVER_RETRY_START = 5
SERVER_RETRY_MAX = 60


async def _pipe(source, target):
    """Reicht alles von einer Seite an die andere weiter, bis eine davon zumacht."""
    async for message in source:
        await target.send(message)


async def _session(server_url, token, obs_url):
    """Ein Durchgang: erst OBS, dann den Bot verbinden und beide zusammenschalten.

    Reihenfolge mit Absicht: obs-websocket schickt seine Begrüßung sofort nach dem
    Verbindungsaufbau. Stünde die Leitung zum Bot zuerst, liefe dessen Anmeldefrist,
    bevor überhaupt jemand da ist, der sie beantworten kann."""
    async with websockets.connect(obs_url, max_size=None) as obs:
        print(f"✅ Mit OBS verbunden ({obs_url}).")
        async with websockets.connect(
            server_url, additional_headers={TOKEN_HEADER: token}, max_size=None,
        ) as server:
            print(f"✅ Beim Bot eingewählt ({server_url}) - Leitung steht.")
            # Die erste Richtung, die endet, beendet die Verbindung: fällt eine Seite
            # weg, darf die andere nicht mit einer toten Leitung weiterleben.
            done, pending = await asyncio.wait(
                {
                    asyncio.create_task(_pipe(obs, server), name="obs->bot"),
                    asyncio.create_task(_pipe(server, obs), name="bot->obs"),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()  # meldet den Grund, statt ihn zu verschlucken


async def run(server_url, token, obs_url):
    delay = SERVER_RETRY_START
    while True:
        try:
            await _session(server_url, token, obs_url)
            print("ℹ️ Leitung beendet - neuer Versuch.")
            delay = SERVER_RETRY_START
        except OSError as e:
            # Häufigster Fall im Alltag: OBS ist noch nicht gestartet.
            print(f"⏳ Keine Verbindung ({e}) - neuer Versuch in {OBS_RETRY_SECONDS}s.")
            await asyncio.sleep(OBS_RETRY_SECONDS)
            continue
        except websockets.InvalidStatus as e:
            status = e.response.status_code
            if status == 401:
                print("⛔ Der Bot hat den Token abgelehnt - stimmt --token mit OBS_BRIDGE_TOKEN überein?")
            else:
                print(f"⚠️ Der Bot antwortet mit HTTP {status}.")
        except websockets.ConnectionClosed as e:
            print(f"ℹ️ Verbindung geschlossen ({e.code}) - neuer Versuch.")
            delay = SERVER_RETRY_START
        except Exception as e:
            print(f"⚠️ Fehler: {e!r}")

        await asyncio.sleep(delay)
        delay = min(delay * 2, SERVER_RETRY_MAX)


def main():
    parser = argparse.ArgumentParser(description="BugBot-OBS-Relais (läuft auf dem OBS-Rechner)")
    parser.add_argument("--server", default=os.environ.get("BUGBOT_SERVER", ""),
                        help="Adresse des Bots, in der Regel das eigene Tunnelende: ws://127.0.0.1:4456")
    parser.add_argument("--token", default=os.environ.get("BUGBOT_TOKEN", ""),
                        help="gemeinsames Geheimnis, muss OBS_BRIDGE_TOKEN des Bots entsprechen")
    parser.add_argument("--obs", default=os.environ.get("OBS_WEBSOCKET", "ws://127.0.0.1:4455"),
                        help="lokales obs-websocket (Standard: ws://127.0.0.1:4455)")
    args = parser.parse_args()

    if not args.server or not args.token:
        parser.error("--server und --token sind nötig (oder BUGBOT_SERVER/BUGBOT_TOKEN setzen).")

    print(f"🔗 Relais: {args.obs}  <->  {args.server}")
    try:
        asyncio.run(run(args.server, args.token, args.obs))
    except KeyboardInterrupt:
        print("\n🛑 Relais beendet.")


if __name__ == "__main__":
    main()
