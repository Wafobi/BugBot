# link.py
# The line to OBS: the bot's WebSocket listener and the obs-websocket 5 session running over
# an incoming connection.
#
# The connection is established the other way round than on the other platforms - the bot
# does not call OBS, OBS calls the bot. The reason is in config.py: obs-websocket listens on
# the streamer's machine, and they should not have to hang a port into the internet for it. A
# relay on the OBS machine (obs_bridge.py) connects locally to obs-websocket, dials in to the
# bot and passes the frames through unchanged.
#
# For everything above this file the reversed direction changes nothing: what runs over the
# line is perfectly ordinary obs-websocket 5 - Hello/Identify on setup, then the bot's
# requests and OBS's events. The sign-in to obs-websocket is still done by the bot itself
# (_identify); the relay does not know OBS_PASSWORD at all.
#
# Two checks guard the port:
#   - the token in the WebSocket handshake (_check_token). If it does not match, no WebSocket
#     connection comes about at all, but an HTTP 401 response.
#   - then OBS itself, by challenge/response against OBS_PASSWORD.
#
# There is only ever *one* session: if a second relay signs in, the new one wins and the old
# one is closed. Otherwise a dead record would stay standing after every OBS restart, and
# requests would go into the dead connection.

import asyncio
import base64
import hashlib
import hmac
import http
import itertools
import json
import logging

import websockets

log = logging.getLogger(__name__)

# --- Protocol (obs-websocket 5.x) ---------------------------------------------------
OP_HELLO = 0
OP_IDENTIFY = 1
OP_IDENTIFIED = 2
OP_EVENT = 5
OP_REQUEST = 6
OP_REQUEST_RESPONSE = 7

RPC_VERSION = 1

# Bitmask of the subscribed event categories. Bits 0..10 are "everything except the
# chatterboxes" - InputVolumeMeters and friends (from bit 16) are deliberately not included:
# they fire several times per second and would have nothing to report here worth recording.
EVENTS_ALL = (1 << 11) - 1

# The header the relay sends the token in. "Authorization: Bearer <token>" is accepted as
# well, so the connection can also be led through a reverse proxy or another client that can
# only handle that.
TOKEN_HEADER = "X-BugBot-Token"

# Defaults; the actual values come from obs.json ("timings") and are passed in through the
# `timings` callback - which keeps this file on the pure protocol and ignorant of
# configuration files.
HANDSHAKE_TIMEOUT = 20
REQUEST_TIMEOUT = 10

# Close codes from obs-websocket that are not a network problem but a statement.
CLOSE_REASONS = {
    4008: "OBS did not accept the Identify message",
    4009: "wrong password - OBS_PASSWORD does not match the WebSocket server settings",
    4010: f"OBS does not speak RPC version {RPC_VERSION}",
    4011: "disconnected by OBS (session kicked)",
}


class OBSError(RuntimeError):
    """A request to OBS failed, or was never sent in the first place.

    `code` is the requestStatus.code from obs-websocket (0 when the error happened before the
    response - no connection, timeout)."""

    def __init__(self, message, code=0):
        super().__init__(message)
        self.code = code


def auth_response(password, salt, challenge):
    """Challenge/response of obs-websocket 5: base64(sha256(base64(sha256(pw+salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode("utf-8")).digest()).decode()


def describe(error):
    """Error message in plain words where obs-websocket only sends a close code."""
    hint = CLOSE_REASONS.get(getattr(error, "code", None))
    return f"{hint} ({error!r})" if hint else repr(error)


class OBSLink:
    """Listens for the relay and holds the running obs-websocket session.

    on_event(event_type, data)  called for every event from OBS, each as its own task - so a
                                handler may use request() itself without blocking the reader
                                (which would have to deliver the response first).
    on_connected()              after every successful sign-in, likewise as a task.
    """

    def __init__(self, token, password="", on_event=None, on_connected=None,
                 bind="0.0.0.0", port=4456, timings=None):
        self._token = token
        # Callback returning {"request_timeout": ..., "handshake_timeout": ...} - afresh on
        # every access, so a change takes effect without a restart.
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

        #: Version of obs-websocket on the far side, as soon as it is known.
        self.version = ""
        #: Address of the most recently connected relay - for the log and the status command.
        self.peer = ""

    @property
    def connected(self):
        return self._ws is not None

    # --- Listener -------------------------------------------------------------------

    async def start(self):
        """Opens the port and returns immediately. Whether a relay ever signs in is an open
        question from here - OBS typically only runs hours later."""
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
        """Still runs during the HTTP handshake, before the WebSocket connection stands.
        Returning a response rejects the setup; None means "let through".

        The comparison goes through compare_digest, so the running time gives nothing away
        about the token. An open port on the internet gets scanned - here this is the only
        hurdle in front of remote-controlling somebody else's OBS instance. Encoded first,
        because compare_digest on str only accepts ASCII and would otherwise raise TypeError
        on a non-ASCII token instead of just rejecting it - and this handshake is exactly
        where an internet scanner throws arbitrary bytes at us."""
        header = request.headers.get(TOKEN_HEADER, "")
        if not header:
            bearer = request.headers.get("Authorization", "")
            header = bearer[7:] if bearer.lower().startswith("bearer ") else ""

        if not hmac.compare_digest(header.encode("utf-8", "ignore"), self._token.encode("utf-8")):
            log.warning(f"OBS relay from {connection.remote_address} rejected: wrong/missing token.")
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
        return None

    async def _session(self, connection):
        """One incoming relay connection: sign in, then read until it breaks."""
        peer = f"{connection.remote_address[0]}:{connection.remote_address[1]}"
        if self._ws is not None:
            # New beats old: a half-dead previous connection (OBS restarted, line dropped
            # without a FIN arriving) must not block the fresh one.
            log.info(f"OBS relay {peer} is taking over - closing the previous connection.")
            await self._close_session()

        try:
            await self._identify(connection)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"OBS sign-in via {peer} failed: {describe(e)}")
            await connection.close()
            return

        self._ws = connection
        self.peer = peer
        log.info(f"OBS connected via relay {peer} (obs-websocket {self.version or '?'}).")
        if self._on_connected:
            self._spawn(self._on_connected())

        try:
            await self._read_loop(connection)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"OBS line ({peer}) disturbed: {describe(e)}")
        finally:
            # Only clean up when this session is still the current one: if a new relay has
            # taken over in the meantime, the state belongs to it.
            if self._ws is connection:
                self._drop_session()
                log.info(f"OBS line to {peer} ended - waiting for a new connection.")

    # --- obs-websocket sign-in ------------------------------------------------------

    async def _identify(self, connection):
        """Hello -> Identify -> Identified. The frames come from the real obs-websocket on
        the OBS machine; the relay only passes them through."""
        hello = await self._recv_json(connection)
        if hello.get("op") != OP_HELLO:
            raise OBSError(f"unexpected greeting (op={hello.get('op')}) - does the far side speak obs-websocket 5?")

        greeting = hello.get("d", {})
        payload = {"rpcVersion": RPC_VERSION, "eventSubscriptions": EVENTS_ALL}
        auth = greeting.get("authentication")
        if auth:
            if not self._password:
                raise OBSError("obs-websocket demands a password, but OBS_PASSWORD is empty")
            payload["authentication"] = auth_response(self._password, auth["salt"], auth["challenge"])
        elif self._password:
            log.info("OBS demands no password - OBS_PASSWORD stays unused.")

        await connection.send(json.dumps({"op": OP_IDENTIFY, "d": payload}))
        answer = await self._recv_json(connection)
        if answer.get("op") != OP_IDENTIFIED:
            raise OBSError(f"sign-in rejected (op={answer.get('op')})")
        self.version = greeting.get("obsWebSocketVersion", "")

    def timing(self, key, default):
        value = self._timings().get(key, default)
        return value if isinstance(value, (int, float)) and value > 0 else default

    async def _recv_json(self, connection):
        timeout = self.timing("handshake_timeout", HANDSHAKE_TIMEOUT)
        return json.loads(await asyncio.wait_for(connection.recv(), timeout=timeout))

    # --- Reading and delivering responses -------------------------------------------

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
            # As with the EventSub handlers in platforms/twitch/bot.py: a broken handler
            # must not drag the line down with it.
            log.warning(f"Error in the OBS handler for {event_type}: {e}")

    def _settle(self, data):
        future = self._pending.pop(data.get("requestId"), None)
        if future is None or future.done():
            return
        status = data.get("requestStatus", {})
        if status.get("result"):
            future.set_result(data.get("responseData") or {})
        else:
            comment = status.get("comment") or f"error code {status.get('code')}"
            future.set_exception(OBSError(f"{data.get('requestType')}: {comment}", code=status.get("code", 0)))

    # --- Requests -------------------------------------------------------------------

    async def request(self, request_type, data=None, timeout=None):
        """Sends an obs-websocket request and returns its responseData. Raises OBSError -
        including when no relay is connected at all. So the caller never has to check whether
        OBS is running, only to handle the error."""
        connection = self._ws
        if connection is None:
            raise OBSError("no connection to OBS (relay not dialled in)")
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
            raise OBSError(f"{request_type}: no answer from OBS after {timeout}s")
        finally:
            self._pending.pop(request_id, None)

    # --- State -----------------------------------------------------------------------

    def _drop_session(self):
        """Synchronous, so it is safe inside a finally too: forget the connection and end
        every open request with an error, rather than letting them run into their timeout."""
        self._ws = None
        self.version = ""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(OBSError("lost the connection to OBS"))
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
