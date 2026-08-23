"""The listener the overlays hang on.

The same reversed direction as with the OBS relay, and for the same reason: the browser
source runs in OBS on the streamer's machine, the bot on a server. So an overlay does not
*fetch* its data but dials in and gets it sent - which puts a follower on screen the moment
they arrive, not at the next polling tick.

The port belongs in the open network as little as the relay's does: an SSH tunnel leads here
from the OBS machine (see docs/overlay.md). The only hurdle in front of it is the same token
comparison as in platforms/obs/link.py - deliberately kept identical, so there is nothing
new to check here.

This file knows only connections and JSON frames. What goes into the frames is decided by
features/overlay/feature.py.
"""

import asyncio
import hmac
import http
import json
import logging
from urllib.parse import urlsplit, parse_qs

import websockets

log = logging.getLogger(__name__)

# As with the relay: the token goes in this header, alternatively as "Authorization: Bearer
# <token>", so the connection can also be led through a reverse proxy that can only handle
# that.
TOKEN_HEADER = "X-BugBot-Token"

# And additionally in the address: ?token=...
#
# That is not a convenience here but the only way. The far end is a browser source, and the
# WebSocket API in the browser can set *no* headers of its own during its handshake - unlike
# the relay, which is a Python process. If it stayed with the header, no overlay could ever
# sign in.
#
# The price is that the token stands in the source URL and therefore in the scene collection.
# Acceptable, because this port is not on the network: it is reachable only through the SSH
# tunnel from the OBS machine. Whoever can read the scene collection is sitting at the
# machine OBS runs on anyway.
TOKEN_QUERY = "token"


class OverlayServer:
    """WebSocket server for the overlays. Holds the open connections and sends each of them
    the same thing: a complete state on connect, only changes afterwards."""

    def __init__(self, token, bind="0.0.0.0", port=4457, snapshot=None, on_error=None):
        self._token = token
        self._bind = bind
        self._port = port
        # Callback without arguments returning the initial state as a dict. Asked on every
        # new connection - an overlay reloading mid-stream then sees the same as one that
        # was there from the start.
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
        log.info(f"Overlay listener on {self._bind}:{self._port}")

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
        response rejects the setup, None lets it through.

        compare_digest, so that the running time gives nothing away about the token - as in
        platforms/obs/link.py._check_token. The third route (query) does not appear there,
        because the relay can set headers and a browser source cannot (see TOKEN_QUERY).

        compare_digest on str only accepts ASCII and raises TypeError otherwise - encoding
        first turns a non-ASCII token into a normal rejection instead of an unhandled
        exception in the handshake."""
        header = request.headers.get(TOKEN_HEADER, "")
        if not header:
            bearer = request.headers.get("Authorization", "")
            header = bearer[7:] if bearer.lower().startswith("bearer ") else ""
        if not header:
            query = parse_qs(urlsplit(request.path).query)
            header = (query.get(TOKEN_QUERY) or [""])[0]

        if not hmac.compare_digest(header.encode("utf-8", "ignore"), self._token.encode("utf-8")):
            log.warning(f"Overlay from {connection.remote_address} rejected: wrong/missing token.")
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
        return None

    async def _session(self, connection):
        """One overlay connection: send state, then hold it open.

        Unlike with the OBS relay, several may hang here at once - one browser source per
        scene is the normal case, and the preview in the browser is added while setting
        things up."""
        self._clients.add(connection)
        peer = f"{connection.remote_address[0]}:{connection.remote_address[1]}"
        log.info(f"Overlay connected: {peer} ({len(self._clients)} open)")
        try:
            await connection.send(json.dumps({"type": "state", "data": self._snapshot()}))
            # We expect nothing from the far side. Reading runs anyway, because it is the
            # route by which a dropped connection arrives here.
            async for _ in connection:
                pass
        except websockets.ConnectionClosed:
            pass
        except Exception as error:  # a broken connection must not take the bot with it
            self._on_error(f"overlay session {peer}: {error}")
        finally:
            self._clients.discard(connection)
            log.info(f"Overlay disconnected: {peer} ({len(self._clients)} open)")

    async def broadcast(self, type_, data):
        """One message to all open overlays. Failures of individual connections are
        swallowed: an overlay that happens to be reloading is not the bot's problem."""
        if not self._clients:
            return
        message = json.dumps({"type": type_, "data": data})
        results = await asyncio.gather(
            *(connection.send(message) for connection in list(self._clients)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, websockets.ConnectionClosed):
                self._on_error(f"overlay broadcast: {result}")
