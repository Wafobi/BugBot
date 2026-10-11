"""The listener the overlays - and the chat mirrored alongside them - hang on.

The same reversed direction as with the OBS relay, and for the same reason: the browser
source runs in OBS on the streamer's machine, the bot on a server. So an overlay does not
*fetch* its data but dials in and gets it sent - which puts a follower on screen the moment
they arrive, not at the next polling tick.

The port belongs in the open network as little as the relay's does: an SSH tunnel leads here
from the OBS machine (see docs/overlay.md). The only hurdle in front of it is the same token
comparison as in platforms/obs/link.py - deliberately kept identical, so there is nothing
new to check here.

One listener now carries both halves of what used to be two features (overlay and
chat_panel): the state/patch frames for the numbers, and the history/message/clear frames
for the chat, all through the one connection a browser source opens. A client that only
understands one half simply ignores the frame types it does not recognise - see
features/overlay/client/chat.html.

Other features can ride on the same listener instead of opening a port of their own (see
OverlayFeature's OVERLAY capability):

  - a browser source may name a role on connect (?role=tts), and broadcast(..., role=...)
    then reaches only connections with that role - so one feature's frames (a TTS clip,
    hundreds of KB) do not go to every stat bar and chat on screen;
  - add_role() makes a role a page of its own (the companion pond): on connect it gets that
    role's snapshot instead of the overlay's state/history, and the overlay's own broadcasts
    (role=None) skip it - it only ever sees its own frames. A role may bring its own token
    (then OVERLAY_TOKEN does *not* open it) and a message handler - the one way anything
    comes back from a browser source (the settings website saving a value);
  - add_route() answers a plain HTTP GET on its own path (/tts/next) in the handshake hook,
    without upgrading to a WebSocket - for a Stream Deck button, or a small page of its own
    (HTML, audio). Every route brings its own token: OVERLAY_TOKEN sits in the scene
    collection and only ever grants reading, a route that *does* something must not hang on
    it. GET only - the handshake hook never sees anything else.

This file knows only connections and JSON frames. What goes into the frames is decided by
features/overlay/feature.py and whoever registered a route or role.
"""

import asyncio
import dataclasses
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

# ?role=... on connect - see the module docstring.
ROLE_QUERY = "role"


def _token_from(request):
    """Header, then "Authorization: Bearer", then ?token= - see TOKEN_HEADER/TOKEN_QUERY."""
    header = request.headers.get(TOKEN_HEADER, "")
    if not header:
        bearer = request.headers.get("Authorization", "")
        header = bearer[7:] if bearer.lower().startswith("bearer ") else ""
    if not header:
        query = parse_qs(urlsplit(request.path).query)
        header = (query.get(TOKEN_QUERY) or [""])[0]
    return header


def _token_matches(given, expected):
    """compare_digest, so that the running time gives nothing away about the token; encoded
    first, because compare_digest on str only accepts ASCII and raises TypeError otherwise."""
    return bool(expected) and hmac.compare_digest(given.encode("utf-8", "ignore"), expected.encode("utf-8"))


async def _empty_snapshot():
    return {}


class OverlayServer:
    """WebSocket server for the overlays. Holds the open connections and sends each of them
    the same thing: a complete state on connect, only changes afterwards."""

    def __init__(self, token, bind="0.0.0.0", port=4457, snapshot=None, history=None, on_error=None):
        self._token = token
        self._bind = bind
        self._port = port
        # Async callback without arguments returning the initial state as a dict. Asked on
        # every new connection - an overlay reloading mid-stream then sees the same as one
        # that was there from the start. Async rather than a plain callable so that a fresh
        # connection can itself be the moment something is looked up (see OverlayFeature.
        # snapshot(): a stream_recap query, only while live).
        self._snapshot = snapshot or _empty_snapshot
        # Same idea, for the chat: the recent messages as a list. A client that only shows
        # the stat bars simply never looks at the frame this fills.
        self._history = history or (lambda: [])
        self._on_error = on_error or (lambda message: None)
        self._server = None
        self._clients = set()
        # connection -> role named on connect ("" without one), see ROLE_QUERY.
        self._roles = {}
        # path -> (async handler returning (HTTPStatus, text), token), see add_route.
        self._routes = {}
        # role -> callable returning the [(type, data), ...] frames for a fresh connection
        # with that role, see add_role.
        self._role_snapshots = {}
        # role -> its own token, role -> async handler for what the page sends; see add_role.
        self._role_tokens = {}
        self._role_handlers = {}

    @property
    def client_count(self):
        return len(self._clients)

    def clients_with_role(self, role):
        return sum(1 for connection in self._clients if self._roles.get(connection) == role)

    def add_role(self, role, snapshot, token=None, on_message=None):
        """A role that is a page of its own - see the module docstring. `snapshot` is a
        callable (sync or async) returning the frames a new connection with this role gets,
        as [(type, data), ...]. With `token`, only that token opens this role - not
        OVERLAY_TOKEN. With `on_message`, every text frame the page sends is handed to it
        (async, gets the raw text, returns a dict to send back or None)."""
        self._role_snapshots[role] = snapshot
        if token is not None:
            self._role_tokens[role] = token
        if on_message is not None:
            self._role_handlers[role] = on_message

    def add_route(self, path, handler, token):
        """Answers GET <path> with `await handler(query)` - query being the parsed query
        string ({name: first value}, token included) - which returns (HTTPStatus, body) or
        (HTTPStatus, body, content type); body str (sent as UTF-8, text/plain by default) or
        bytes. Only with `token`, never with OVERLAY_TOKEN; an empty token disables the
        route rather than opening it."""
        self._routes[path] = (handler, token)

    async def start(self):
        self._server = await websockets.serve(
            self._session, self._bind, self._port, process_request=self._process_request,
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

    async def _process_request(self, connection, request):
        """The handshake hook: a registered route is answered right here (with its own
        token), everything else goes through the overlay token check."""
        path = urlsplit(request.path).path
        if path in self._routes:
            handler, token = self._routes[path]
            if not _token_matches(_token_from(request), token):
                log.warning(f"Overlay route {path} from {connection.remote_address} rejected: wrong/missing token.")
                return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
            query = {k: v[0] for k, v in parse_qs(urlsplit(request.path).query).items()}
            try:
                status, body, *rest = await handler(query)
                content_type = rest[0] if rest else "text/plain; charset=utf-8"
            except Exception as error:  # a failing button must not take the listener with it
                self._on_error(f"overlay route {path}: {error}")
                status, body, content_type = http.HTTPStatus.INTERNAL_SERVER_ERROR, "error", "text/plain; charset=utf-8"
            return self._response(connection, status, body, content_type)
        role = (parse_qs(urlsplit(request.path).query).get(ROLE_QUERY) or [""])[0]
        if role in self._role_tokens:
            if not _token_matches(_token_from(request), self._role_tokens[role]):
                log.warning(f"Overlay role {role} from {connection.remote_address} rejected: wrong/missing token.")
                return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
            return None
        return self._check_token(connection, request)

    @staticmethod
    def _response(connection, status, body, content_type):
        """connection.respond() only does plain text - this swaps in any body and type."""
        if isinstance(body, str):
            if content_type.startswith("text/plain") and not body.endswith("\n"):
                body += "\n"  # a curl in a terminal ends on its own line
            body = body.encode("utf-8")
        response = connection.respond(status, "")
        for name in ("Content-Type", "Content-Length"):
            del response.headers[name]
        response.headers["Content-Type"] = content_type
        response.headers["Content-Length"] = str(len(body))
        response.headers["Cache-Control"] = "no-store"
        return dataclasses.replace(response, body=body)

    def _check_token(self, connection, request):
        """Still runs during the HTTP handshake, before the WebSocket connection stands. A
        response rejects the setup, None lets it through.

        compare_digest, so that the running time gives nothing away about the token - as in
        platforms/obs/link.py._check_token. The third route (query) does not appear there,
        because the relay can set headers and a browser source cannot (see TOKEN_QUERY).

        compare_digest on str only accepts ASCII and raises TypeError otherwise - encoding
        first turns a non-ASCII token into a normal rejection instead of an unhandled
        exception in the handshake."""
        if not _token_matches(_token_from(request), self._token):
            log.warning(f"Overlay from {connection.remote_address} rejected: wrong/missing token.")
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid token\n")
        return None

    async def _session(self, connection):
        """One overlay connection: send state, then hold it open.

        Unlike with the OBS relay, several may hang here at once - one browser source per
        scene is the normal case, and the preview in the browser is added while setting
        things up."""
        self._clients.add(connection)
        query = parse_qs(urlsplit(connection.request.path).query)
        self._roles[connection] = (query.get(ROLE_QUERY) or [""])[0]
        peer = f"{connection.remote_address[0]}:{connection.remote_address[1]}"
        log.info(f"Overlay connected: {peer} ({len(self._clients)} open)")
        try:
            snapshot = self._role_snapshots.get(self._roles[connection])
            if snapshot is not None:
                frames = snapshot()
                if asyncio.iscoroutine(frames):
                    frames = await frames
                for type_, data in frames:
                    await connection.send(json.dumps({"type": type_, "data": data}))
            else:
                await connection.send(json.dumps({"type": "state", "data": await self._snapshot()}))
                await connection.send(json.dumps({"type": "history", "data": self._history()}))
            # Overlays send nothing; reading runs anyway, because it is the route by which a
            # dropped connection arrives here. A role with a handler (add_role) gets its
            # page's frames, and the handler's answer goes back to that page only.
            handler = self._role_handlers.get(self._roles[connection])
            async for raw in connection:
                if handler is None or not isinstance(raw, str):
                    continue
                reply = await handler(raw)
                if reply is not None:
                    await connection.send(json.dumps(reply))
        except websockets.ConnectionClosed:
            pass
        except Exception as error:  # a broken connection must not take the bot with it
            self._on_error(f"overlay session {peer}: {error}")
        finally:
            self._clients.discard(connection)
            self._roles.pop(connection, None)
            log.info(f"Overlay disconnected: {peer} ({len(self._clients)} open)")

    async def broadcast(self, type_, data, role=None):
        """One message to all open overlays - or, with `role`, only to those that named it
        on connect. Without a role, connections of a role registered with add_role are
        skipped: those are pages of their own. Failures of individual connections are
        swallowed: an overlay that happens to be reloading is not the bot's problem."""
        if role is None:
            targets = [c for c in list(self._clients) if self._roles.get(c) not in self._role_snapshots]
        else:
            targets = [c for c in list(self._clients) if self._roles.get(c) == role]
        if not targets:
            return
        message = json.dumps({"type": type_, "data": data})
        results = await asyncio.gather(
            *(connection.send(message) for connection in targets),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, websockets.ConnectionClosed):
                self._on_error(f"overlay broadcast: {result}")
