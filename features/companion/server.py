"""The listener the companion browser source hangs on.

The same reversed direction as the OBS relay, the overlay and the chat panel, and for the
same reason: the browser source runs in OBS on the streamer's machine, the bot on a server.
So it does not *fetch* who is in chat but dials in and gets it sent - a companion appears
the moment its person writes their first message, not at the next poll.

The token check is deliberately the same shape as platforms/obs/link.py,
features/overlay/server.py and features/chat_panel/server.py: same header, same query
parameter, same compare_digest. Four small copies of it are cheaper than a shared
abstraction none of them may ever need to diverge from - see features/overlay/server.py for
why the query parameter exists at all (the browser's WebSocket API cannot set headers of its
own).

This file knows only connections and JSON frames. What goes into them is decided by
features/companion/feature.py.
"""

import asyncio
import hmac
import http
import json
import logging
from urllib.parse import urlsplit, parse_qs

import websockets

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-BugBot-Token"
TOKEN_QUERY = "token"


class CompanionServer:
    """WebSocket server for the companion page. Holds the open connections and sends each
    of them the same thing: who is currently in chat on connect, one frame per change
    afterwards."""

    def __init__(self, token, bind="0.0.0.0", port=4459, snapshot=None, on_error=None):
        self._token = token
        self._bind = bind
        self._port = port
        # Callback without arguments returning the current companions as a list - asked on
        # every new connection, so a browser source reloading mid-stream sees the same
        # pond as one that was there from the start.
        self._snapshot = snapshot or (lambda: [])
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
        log.info(f"Companion listener on {self._bind}:{self._port}")

    async def close(self):
        for connection in list(self._clients):
            await connection.close()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def _check_token(self, connection, request):
        """Still runs during the HTTP handshake, before the WebSocket connection stands. A
        response rejects the setup, None lets it through. compare_digest, so that the
        running time gives nothing away about the token; encoded first, because compare_digest
        on str only accepts ASCII and would otherwise raise TypeError on a non-ASCII token
        instead of just rejecting it."""
        header = request.headers.get(TOKEN_HEADER, "")
        if not header:
            bearer = request.headers.get("Authorization", "")
            header = bearer[7:] if bearer.lower().startswith("bearer ") else ""
        if not header:
            query = parse_qs(urlsplit(request.path).query)
            header = (query.get(TOKEN_QUERY) or [""])[0]

        if not hmac.compare_digest(header.encode("utf-8", "ignore"), self._token.encode("utf-8")):
            log.warning(f"Companion page from {connection.remote_address} rejected: wrong/missing token.")
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
        return None

    async def _session(self, connection):
        """One browser source: send the current pond, then hold the connection open.
        Several may hang here at once, same as with the overlay and the chat panel."""
        self._clients.add(connection)
        peer = f"{connection.remote_address[0]}:{connection.remote_address[1]}"
        log.info(f"Companion page connected: {peer} ({len(self._clients)} open)")
        try:
            await connection.send(json.dumps({"type": "state", "data": self._snapshot()}))
            # We expect nothing from the far side. Reading runs anyway, because it is the
            # route by which a dropped connection arrives here.
            async for _ in connection:
                pass
        except websockets.ConnectionClosed:
            pass
        except Exception as error:  # a broken connection must not take the bot with it
            self._on_error(f"companion session {peer}: {error}")
        finally:
            self._clients.discard(connection)
            log.info(f"Companion page disconnected: {peer} ({len(self._clients)} open)")

    async def broadcast(self, type_, data):
        """One message to all open companion pages. Failures of individual connections are
        swallowed: a page that happens to be reloading is not the bot's problem."""
        if not self._clients:
            return
        message = json.dumps({"type": type_, "data": data})
        results = await asyncio.gather(
            *(connection.send(message) for connection in list(self._clients)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, websockets.ConnectionClosed):
                self._on_error(f"companion broadcast: {result}")
