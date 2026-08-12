"""Der Lauscher, an dem die Overlays hängen.

Dieselbe umgedrehte Richtung wie beim OBS-Relais und aus demselben Grund: die
Browser-Quelle läuft in OBS auf dem Rechner des Streamers, der Bot auf einem Server.
Ein Overlay *holt* seine Daten also nicht, sondern wählt sich ein und bekommt sie
geschickt - ein Follower steht damit in dem Moment im Bild, in dem er hereinkommt, und
nicht beim nächsten Abfragetakt.

Der Port gehört so wenig ins offene Netz wie der des Relais: vom OBS-Rechner führt ein
SSH-Tunnel hierher (siehe docs/overlay.md). Die einzige Hürde davor ist derselbe
Token-Vergleich wie in platforms/obs/link.py - bewusst gleich gehalten, damit es hier
nichts Neues zu prüfen gibt.

Diese Datei kennt nur Verbindungen und JSON-Rahmen. Was in den Rahmen steht, entscheidet
features/overlay/feature.py.
"""

import asyncio
import hmac
import http
import json
from urllib.parse import urlsplit, parse_qs

import websockets

# Wie beim Relais: der Token steht in diesem Header, ersatzweise als "Authorization:
# Bearer <token>", damit die Verbindung auch durch einen Reverse-Proxy geführt werden
# kann, der nur damit umgehen kann.
TOKEN_HEADER = "X-BugBot-Token"

# Und zusätzlich in der Adresse: ?token=...
#
# Das ist hier keine Bequemlichkeit, sondern der einzige Weg. Die Gegenstelle ist eine
# Browser-Quelle, und die WebSocket-API im Browser kann bei ihrem Handschlag *keine*
# eigenen Header setzen - anders als das Relais, das ein Python-Prozess ist. Bliebe es
# beim Header, könnte sich kein Overlay je anmelden.
#
# Der Preis ist, dass der Token in der Quellen-URL steht und damit in der Szenensammlung.
# Vertretbar, weil dieser Port nicht im Netz steht: er ist nur über den SSH-Tunnel vom
# OBS-Rechner erreichbar. Wer die Szenensammlung lesen kann, sitzt ohnehin an dem Rechner,
# auf dem OBS läuft.
TOKEN_QUERY = "token"


class OverlayServer:
    """WebSocket-Server für die Overlays. Hält die offenen Verbindungen und schickt
    jeder dasselbe: beim Verbinden einen vollständigen Zustand, danach nur noch
    Änderungen."""

    def __init__(self, token, bind="0.0.0.0", port=4457, snapshot=None, on_error=None):
        self._token = token
        self._bind = bind
        self._port = port
        # Rückruf ohne Argumente, der den Startzustand als dict liefert. Wird bei jeder
        # neuen Verbindung gefragt - ein Overlay, das mitten im Stream neu lädt, sieht
        # danach dasselbe wie eines, das von Anfang an dabei war.
        self._snapshot = snapshot or (lambda: {})
        self._on_error = on_error or (lambda message: None)
        self._server = None
        self._clients = set()

    @property
    def client_count(self):
        return len(self._clients)

    async def start(self):
        self._server = await websockets.serve(
            self._session, self._bind, self._port, process_request=self._check_token,
        )
        print(f"🖼  Overlay-Lauscher auf {self._bind}:{self._port}")

    async def close(self):
        for connection in list(self._clients):
            await connection.close()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def _check_token(self, connection, request):
        """Läuft noch im HTTP-Handschlag, bevor die WebSocket-Verbindung steht. Eine
        Antwort lehnt den Aufbau ab, None lässt durch.

        compare_digest, damit die Laufzeit nichts über den Token verrät - wie in
        platforms/obs/link.py._check_token. Der dritte Weg (Query) kommt dort nicht vor,
        weil das Relais Header setzen kann und eine Browser-Quelle nicht (siehe
        TOKEN_QUERY)."""
        header = request.headers.get(TOKEN_HEADER, "")
        if not header:
            bearer = request.headers.get("Authorization", "")
            header = bearer[7:] if bearer.lower().startswith("bearer ") else ""
        if not header:
            query = parse_qs(urlsplit(request.path).query)
            header = (query.get(TOKEN_QUERY) or [""])[0]

        if not hmac.compare_digest(header, self._token):
            print(f"⛔ Overlay von {connection.remote_address} abgewiesen: falscher/fehlender Token.")
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
        return None

    async def _session(self, connection):
        """Eine Overlay-Verbindung: Zustand schicken, dann offen halten.

        Anders als beim OBS-Relais dürfen hier mehrere gleichzeitig hängen - eine
        Browser-Quelle je Szene ist der Normalfall, und die Vorschau im Browser kommt
        beim Einrichten noch dazu."""
        self._clients.add(connection)
        peer = f"{connection.remote_address[0]}:{connection.remote_address[1]}"
        print(f"🖼  Overlay verbunden: {peer} ({len(self._clients)} offen)")
        try:
            await connection.send(json.dumps({"type": "state", "data": self._snapshot()}))
            # Wir erwarten nichts von der Gegenseite. Das Lesen läuft trotzdem, weil es
            # der Weg ist, auf dem ein Verbindungsabbruch hier ankommt.
            async for _ in connection:
                pass
        except websockets.ConnectionClosed:
            pass
        except Exception as error:  # eine kaputte Verbindung darf den Bot nicht mitnehmen
            self._on_error(f"Overlay-Sitzung {peer}: {error}")
        finally:
            self._clients.discard(connection)
            print(f"🖼  Overlay getrennt: {peer} ({len(self._clients)} offen)")

    async def broadcast(self, type_, data):
        """Eine Nachricht an alle offenen Overlays. Fehlschläge einzelner Verbindungen
        werden verschluckt: ein Overlay, das gerade neu lädt, ist kein Problem des Bots."""
        if not self._clients:
            return
        message = json.dumps({"type": type_, "data": data})
        results = await asyncio.gather(
            *(connection.send(message) for connection in list(self._clients)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, websockets.ConnectionClosed):
                self._on_error(f"Overlay-Broadcast: {result}")
