"""OverlayServer: roles (?role=) and extra routes - over a real socket, because both live in
the WebSocket handshake hook."""

import asyncio
import http
import json
import urllib.error
import urllib.request

import pytest
import websockets

from features.overlay.server import OverlayServer

PORT = 14571


def get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}") as response:
            return response.status, response.read().decode().strip()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode().strip()


@pytest.fixture
async def server():
    s = OverlayServer("overlay-token", "127.0.0.1", PORT)
    await s.start()
    yield s
    await s.close()


async def test_route_needs_its_own_token_not_the_overlay_token(server):
    async def handler(query):
        return http.HTTPStatus.OK, "done"
    server.add_route("/tts/next", handler, "route-token")
    assert await asyncio.to_thread(get, "/tts/next?token=route-token") == (200, "done")
    assert (await asyncio.to_thread(get, "/tts/next?token=overlay-token"))[0] == 401
    assert (await asyncio.to_thread(get, "/tts/next"))[0] == 401


async def test_route_with_empty_token_is_closed(server):
    async def handler(query):
        return http.HTTPStatus.OK, "done"
    server.add_route("/tts/next", handler, "")
    assert (await asyncio.to_thread(get, "/tts/next?token="))[0] == 401


async def test_failing_route_answers_500(server):
    async def handler(query):
        raise RuntimeError("boom")
    server.add_route("/x", handler, "t")
    assert (await asyncio.to_thread(get, "/x?token=t"))[0] == 500


async def test_role_broadcast_reaches_only_that_role(server):
    url = f"ws://127.0.0.1:{PORT}/?token=overlay-token"
    async with websockets.connect(url) as plain, websockets.connect(url + "&role=tts") as tts:
        for ws in (plain, tts):  # state + history on connect
            await ws.recv()
            await ws.recv()
        await asyncio.sleep(0.05)
        assert server.clients_with_role("tts") == 1
        await server.broadcast("speak", {"audio": "x"}, role="tts")
        await server.broadcast("patch", {"viewers": 1})
        assert json.loads(await tts.recv())["type"] == "speak"
        assert json.loads(await tts.recv())["type"] == "patch"
        assert json.loads(await plain.recv())["type"] == "patch"


async def test_route_gets_the_query_and_can_send_bytes_with_a_type(server):
    async def handler(query):
        return http.HTTPStatus.OK, query["text"].encode(), "audio/wav"
    server.add_route("/x", handler, "t")

    def fetch():
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/x?token=t&text=hallo") as response:
            return response.headers["Content-Type"], response.read()
    assert await asyncio.to_thread(fetch) == ("audio/wav", b"hallo")


async def test_page_role_gets_its_own_snapshot_and_only_its_own_frames(server):
    server.add_role("companion", lambda: [("state", [{"key": "twitch:1"}])])
    url = f"ws://127.0.0.1:{PORT}/?token=overlay-token"
    async with websockets.connect(url) as plain, websockets.connect(url + "&role=companion") as pond:
        assert json.loads(await pond.recv()) == {"type": "state", "data": [{"key": "twitch:1"}]}
        await plain.recv()  # overlay state
        await plain.recv()  # overlay history
        await asyncio.sleep(0.05)
        await server.broadcast("patch", {"viewers": 3})          # overlay frame: not for the pond
        await server.broadcast("join", {"key": "twitch:2"}, role="companion")
        assert json.loads(await pond.recv())["type"] == "join"
        assert json.loads(await plain.recv())["type"] == "patch"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(plain.recv(), 0.2)  # ...and the join never reached the overlay


async def test_page_role_snapshot_may_be_async(server):
    async def snapshot():
        return [("state", [])]
    server.add_role("companion", snapshot)
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/?role=companion&token=overlay-token") as pond:
        assert json.loads(await pond.recv()) == {"type": "state", "data": []}
