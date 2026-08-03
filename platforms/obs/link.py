# link.py
# Die Leitung zu OBS: der WebSocket-Lauscher des Bots und die obs-websocket-5-Sitzung, die
# über eine eingehende Verbindung läuft.
#
# Der Verbindungsaufbau geht andersherum als bei den übrigen Plattformen - nicht der Bot
# ruft OBS an, sondern OBS ruft den Bot an. Der Grund steht in config.py: obs-websocket
# lauscht auf dem Rechner des Streamers, und der soll dafür keinen Port ins Internet
# hängen müssen. Ein Relais auf dem OBS-PC (obs_bridge.py) verbindet sich lokal mit
# obs-websocket, wählt sich beim Bot ein und reicht die Frames unverändert durch.
#
# Für alles oberhalb dieser Datei ändert die gedrehte Richtung nichts: über die Leitung
# läuft ganz normales obs-websocket 5 - Hello/Identify beim Aufbau, danach Requests des
# Bots und Events von OBS. Auch die Anmeldung an obs-websocket macht weiterhin der Bot
# selbst (_identify), das Relais kennt OBS_PASSWORD gar nicht.
#
# Zwei Prüfungen bewachen den Port:
#   - der Token im WebSocket-Handschlag (_check_token). Passt er nicht, kommt gar keine
#     WebSocket-Verbindung zustande, sondern eine HTTP-401-Antwort.
#   - danach OBS selbst, per Challenge/Response gegen OBS_PASSWORD.
#
# Es gilt immer nur *eine* Sitzung: meldet sich ein zweites Relais, gewinnt das neue und
# das alte wird geschlossen. Sonst bliebe nach jedem OBS-Neustart eine Karteileiche
# stehen, und Anfragen gingen in die tote Verbindung.

import asyncio
import base64
import hashlib
import hmac
import http
import itertools
import json

import websockets

# --- Protokoll (obs-websocket 5.x) --------------------------------------------------
OP_HELLO = 0
OP_IDENTIFY = 1
OP_IDENTIFIED = 2
OP_EVENT = 5
OP_REQUEST = 6
OP_REQUEST_RESPONSE = 7

RPC_VERSION = 1

# Bitmaske der abonnierten Ereignis-Kategorien. Bits 0..10 sind "alles außer den
# Vielrednern" - InputVolumeMeters & Co. (ab Bit 16) sind bewusst nicht dabei: die feuern
# mehrfach pro Sekunde und hätten hier nichts zu melden, was sich mitzuschreiben lohnt.
EVENTS_ALL = (1 << 11) - 1

# Der Header, in dem das Relais den Token mitschickt. Zusätzlich wird "Authorization:
# Bearer <token>" akzeptiert, damit die Verbindung auch durch einen Reverse-Proxy oder
# einen anderen Client geführt werden kann, der nur damit umgehen kann.
TOKEN_HEADER = "X-BugBot-Token"

# Voreinstellungen; die tatsächlichen Werte kommen aus obs.json ("timings") und werden
# über den Rückruf `timings` hereingereicht - so bleibt diese Datei beim reinen Protokoll
# und weiß nichts von Konfigurationsdateien.
HANDSHAKE_TIMEOUT = 20
REQUEST_TIMEOUT = 10

# Schließcodes von obs-websocket, die kein Netzproblem sind, sondern eine Aussage.
CLOSE_REASONS = {
    4008: "OBS hat die Identify-Nachricht nicht akzeptiert",
    4009: "falsches Passwort - OBS_PASSWORD stimmt nicht mit den WebSocket-Servereinstellungen überein",
    4010: f"OBS spricht nicht RPC-Version {RPC_VERSION}",
    4011: "von OBS getrennt (Sitzung ausgeschlossen)",
}


class OBSError(RuntimeError):
    """Eine Anfrage an OBS ist fehlgeschlagen oder gar nicht erst losgeschickt worden.

    `code` ist der requestStatus.code von obs-websocket (0, wenn der Fehler vor der
    Antwort passiert ist - keine Verbindung, Zeitüberschreitung)."""

    def __init__(self, message, code=0):
        super().__init__(message)
        self.code = code


def auth_response(password, salt, challenge):
    """Challenge/Response von obs-websocket 5: base64(sha256(base64(sha256(pw+salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode("utf-8")).digest()).decode()


def describe(error):
    """Fehlermeldung mit Klartext, wo obs-websocket nur einen Schließcode schickt."""
    hint = CLOSE_REASONS.get(getattr(error, "code", None))
    return f"{hint} ({error!r})" if hint else repr(error)


class OBSLink:
    """Lauscht auf das Relais und hält die laufende obs-websocket-Sitzung.

    on_event(event_type, data)  wird für jedes Ereignis von OBS aufgerufen, jeweils als
                                eigener Task - ein Handler darf also selbst request()
                                benutzen, ohne den Leser zu blockieren (der die Antwort
                                erst zustellen müsste).
    on_connected()              nach jeder erfolgreichen Anmeldung, ebenfalls als Task.
    """

    def __init__(self, token, password="", on_event=None, on_connected=None,
                 bind="0.0.0.0", port=4456, timings=None):
        self._token = token
        # Rückruf, der {"request_timeout": ..., "handshake_timeout": ...} liefert - bei
        # jedem Zugriff neu, damit eine Änderung ohne Neustart wirkt.
        self._timings = timings or (lambda: {})
        self._password = password or ""
        self._on_event = on_event
        self._on_connected = on_connected
        self._bind = bind
        self._port = port

        self._server = None
        self._ws = None
        self._pending = {}
        self._ids = itertools.count(1)
        self._tasks = set()

        #: Version von obs-websocket auf der Gegenseite, sobald bekannt.
        self.version = ""
        #: Adresse des zuletzt verbundenen Relais - fürs Log und den Statusbefehl.
        self.peer = ""

    @property
    def connected(self):
        return self._ws is not None

    # --- Lauscher -------------------------------------------------------------------

    async def start(self):
        """Öffnet den Port und kehrt sofort zurück. Ob sich je ein Relais meldet, ist von
        hier aus offen - OBS läuft typischerweise erst Stunden später."""
        self._server = await websockets.serve(
            self._session, self._bind, self._port, process_request=self._check_token,
        )

    async def close(self):
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self._close_session()

    def _check_token(self, connection, request):
        """Läuft noch im HTTP-Handschlag, bevor die WebSocket-Verbindung steht. Gibt es
        eine Antwort zurück, wird der Aufbau damit abgelehnt; None heißt "durchlassen".

        Der Vergleich läuft über compare_digest, damit die Laufzeit nichts über den Token
        verrät. Ein offener Port im Internet wird gescannt - hier ist das die einzige
        Hürde vor der Fernsteuerung einer fremden OBS-Instanz."""
        header = request.headers.get(TOKEN_HEADER, "")
        if not header:
            bearer = request.headers.get("Authorization", "")
            header = bearer[7:] if bearer.lower().startswith("bearer ") else ""

        if not hmac.compare_digest(header, self._token):
            print(f"⛔ OBS-Relais von {connection.remote_address} abgewiesen: falscher/fehlender Token.")
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
        return None

    async def _session(self, connection):
        """Eine eingehende Relais-Verbindung: anmelden, dann bis zum Abbruch lesen."""
        peer = f"{connection.remote_address[0]}:{connection.remote_address[1]}"
        if self._ws is not None:
            # Neu schlägt alt: eine halb tote Vorgängerverbindung (OBS neu gestartet,
            # Leitung weggefallen, ohne dass ein FIN ankam) darf die frische nicht
            # blockieren.
            print(f"ℹ️ OBS-Relais {peer} übernimmt - vorherige Verbindung wird geschlossen.")
            await self._close_session()

        try:
            await self._identify(connection)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ OBS-Anmeldung über {peer} fehlgeschlagen: {describe(e)}")
            await connection.close()
            return

        self._ws = connection
        self.peer = peer
        print(f"🎛️ OBS verbunden über Relais {peer} (obs-websocket {self.version or '?'}).")
        if self._on_connected:
            self._spawn(self._on_connected())

        try:
            await self._read_loop(connection)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ OBS-Leitung ({peer}) gestört: {describe(e)}")
        finally:
            # Nur aufräumen, wenn diese Sitzung noch die aktuelle ist: hat inzwischen ein
            # neues Relais übernommen, gehört ihm der Zustand.
            if self._ws is connection:
                self._drop_session()
                print(f"🔌 OBS-Leitung zu {peer} beendet - warte auf neue Verbindung.")

    # --- obs-websocket-Anmeldung ----------------------------------------------------

    async def _identify(self, connection):
        """Hello -> Identify -> Identified. Die Frames kommen vom echten obs-websocket auf
        dem OBS-PC; das Relais reicht sie nur durch."""
        hello = await self._recv_json(connection)
        if hello.get("op") != OP_HELLO:
            raise OBSError(f"unerwartete Begrüßung (op={hello.get('op')}) - spricht die Gegenseite obs-websocket 5?")

        greeting = hello.get("d", {})
        payload = {"rpcVersion": RPC_VERSION, "eventSubscriptions": EVENTS_ALL}
        auth = greeting.get("authentication")
        if auth:
            if not self._password:
                raise OBSError("obs-websocket verlangt ein Passwort, OBS_PASSWORD ist aber leer")
            payload["authentication"] = auth_response(self._password, auth["salt"], auth["challenge"])
        elif self._password:
            print("ℹ️ OBS verlangt kein Passwort - OBS_PASSWORD bleibt ungenutzt.")

        await connection.send(json.dumps({"op": OP_IDENTIFY, "d": payload}))
        answer = await self._recv_json(connection)
        if answer.get("op") != OP_IDENTIFIED:
            raise OBSError(f"Anmeldung abgelehnt (op={answer.get('op')})")
        self.version = greeting.get("obsWebSocketVersion", "")

    def timing(self, key, default):
        value = self._timings().get(key, default)
        return value if isinstance(value, (int, float)) and value > 0 else default

    async def _recv_json(self, connection):
        timeout = self.timing("handshake_timeout", HANDSHAKE_TIMEOUT)
        return json.loads(await asyncio.wait_for(connection.recv(), timeout=timeout))

    # --- Lesen und Antworten zustellen ----------------------------------------------

    async def _read_loop(self, connection):
        async for raw in connection:
            message = json.loads(raw)
            op = message.get("op")
            data = message.get("d", {})
            if op == OP_EVENT:
                if self._on_event:
                    self._spawn(self._dispatch(data))
            elif op == OP_REQUEST_RESPONSE:
                self._settle(data)

    async def _dispatch(self, data):
        event_type = data.get("eventType", "")
        try:
            await self._on_event(event_type, data.get("eventData") or {})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Wie bei den EventSub-Handlern in platforms/twitch/bot.py: ein kaputter
            # Handler darf die Leitung nicht mitreißen.
            print(f"⚠️ Fehler im OBS-Handler für {event_type}: {e}")

    def _settle(self, data):
        future = self._pending.pop(data.get("requestId"), None)
        if future is None or future.done():
            return
        status = data.get("requestStatus", {})
        if status.get("result"):
            future.set_result(data.get("responseData") or {})
        else:
            comment = status.get("comment") or f"Fehlercode {status.get('code')}"
            future.set_exception(OBSError(f"{data.get('requestType')}: {comment}", code=status.get("code", 0)))

    # --- Anfragen -------------------------------------------------------------------

    async def request(self, request_type, data=None, timeout=None):
        """Schickt eine obs-websocket-Anfrage und gibt deren responseData zurück.
        Wirft OBSError - auch dann, wenn gerade gar kein Relais verbunden ist. Der
        Aufrufer muss also nie prüfen, ob OBS läuft, sondern nur den Fehler behandeln."""
        connection = self._ws
        if connection is None:
            raise OBSError("keine Verbindung zu OBS (Relais nicht eingewählt)")
        if timeout is None:
            timeout = self.timing("request_timeout", REQUEST_TIMEOUT)

        request_id = f"bugbot-{next(self._ids)}"
        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        try:
            await connection.send(json.dumps({"op": OP_REQUEST, "d": {
                "requestType": request_type,
                "requestId": request_id,
                "requestData": data or {},
            }}))
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise OBSError(f"{request_type}: keine Antwort von OBS nach {timeout}s")
        finally:
            self._pending.pop(request_id, None)

    # --- Zustand ---------------------------------------------------------------------

    def _drop_session(self):
        """Synchron, damit es auch in einem finally sicher ist: Verbindung vergessen und
        alle offenen Anfragen mit einem Fehler beenden, statt sie in ihren Timeout laufen
        zu lassen."""
        self._ws = None
        self.version = ""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(OBSError("Verbindung zu OBS verloren"))
        self._pending.clear()

    async def _close_session(self):
        connection, self._ws = self._ws, None
        self._drop_session()
        if connection is not None:
            await connection.close()

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
