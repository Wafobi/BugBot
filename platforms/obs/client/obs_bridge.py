#!/usr/bin/env python3
"""BugBot OBS relay - runs on the machine with OBS, not on the server.

This file is the only one in this folder that is *not* part of the bot: it is imported
nowhere, but copied to the streaming PC and started there. It also has no dependency on the
rest of the project - a single file plus `pip install websockets`.

Why it exists: obs-websocket is a server and listens on the streaming PC. The bot runs on a
server on the network and could only reach it if a port at home were reachable from the
internet. So this relay reverses the direction - it dials in to the bot and then passes both
directions through unchanged:

    OBS (obs-websocket, 127.0.0.1:4455)  <--local-->  relay  --dials in-->  BugBot

What runs through the line is perfectly ordinary obs-websocket 5. The relay does not read the
content and does not know the OBS password: the sign-in is done by the bot at the other end.

Setting it up
-------------
 1. In OBS: Tools -> WebSocket Server Settings -> enable the server, authentication on, note
    the password. Port 4455 stays as it is; it does *not* have to be opened in the firewall -
    only this relay talks to it.
 2. On the server: the same password as OBS_PASSWORD in the bot's .env, plus a fresh
    OBS_BRIDGE_TOKEN (e.g. `openssl rand -hex 32`). The bot's port stays closed while doing
    so - it listens only on the server's 127.0.0.1.
 3. Open an SSH tunnel there from here and keep it open:

        ssh -N -L 4456:127.0.0.1:4456 user@bugbot.example.org

    That turns the bot's port into local port 4456 of *this* machine. Nothing stands open on
    the internet for it, and the whole leg is encrypted - which obs-websocket is not by
    itself. For permanent use run setup-tunnel.sh next to this file: it installs the same
    forward as a systemd user service, with the keepalive that makes a dead link get noticed.
 4. Python 3.9+ and `pip install websockets`, then the relay against your own tunnel end:

        python3 obs_bridge.py --server ws://127.0.0.1:4456 --token <TOKEN>

    Instead of the arguments, the environment variables BUGBOT_SERVER, BUGBOT_TOKEN and
    OBS_WEBSOCKET work too.

More convenient is obs_bridge_script.py from the same folder: the same thing as an OBS script
(Tools -> Scripts). Address and token then live in the OBS interface, and the relay starts and
ends with OBS - which makes step 4 unnecessary.

The relay runs along permanently and reconnects by itself after every break - so OBS and the
tunnel may come and go at any time. Anyone running it without the OBS script sensibly starts
it with the machine (Windows: Task Scheduler or the startup folder; Linux: a systemd user
unit), together with the tunnel from step 3.
"""

import argparse
import asyncio
import os
import sys

try:
    import websockets
except ImportError:
    sys.exit("Missing: the 'websockets' package. Install it with:  pip install websockets")

# The header the bot expects the token in (see platforms/obs/link.py).
TOKEN_HEADER = "X-BugBot-Token"

OBS_RETRY_SECONDS = 5
SERVER_RETRY_START = 5
SERVER_RETRY_MAX = 60


async def _pipe(source, target):
    """Passes everything from one side to the other until one of them closes."""
    async for message in source:
        await target.send(message)


async def _session(server_url, token, obs_url):
    """One pass: connect OBS first, then the bot, and wire the two together.

    The order is deliberate: obs-websocket sends its greeting immediately after the connection
    is established. If the line to the bot stood first, its sign-in deadline would be running
    before anybody is even there to answer it."""
    async with websockets.connect(obs_url, max_size=None) as obs:
        print(f"✅ Connected to OBS ({obs_url}).")
        async with websockets.connect(
            server_url, additional_headers={TOKEN_HEADER: token}, max_size=None,
        ) as server:
            print(f"✅ Dialled in to the bot ({server_url}) - line is up.")
            # The first direction to end ends the connection: if one side drops away, the
            # other must not live on with a dead line.
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
                task.result()  # reports the reason instead of swallowing it


async def run(server_url, token, obs_url):
    delay = SERVER_RETRY_START
    while True:
        try:
            await _session(server_url, token, obs_url)
            print("ℹ️ Line ended - trying again.")
            delay = SERVER_RETRY_START
        except OSError as e:
            # The most common case day to day: OBS has not been started yet.
            print(f"⏳ No connection ({e}) - trying again in {OBS_RETRY_SECONDS}s.")
            await asyncio.sleep(OBS_RETRY_SECONDS)
            continue
        except websockets.InvalidStatus as e:
            status = e.response.status_code
            if status == 401:
                print("⛔ The bot rejected the token - does --token match OBS_BRIDGE_TOKEN?")
            else:
                print(f"⚠️ The bot answers with HTTP {status}.")
        except websockets.ConnectionClosed as e:
            print(f"ℹ️ Connection closed ({e.code}) - trying again.")
            delay = SERVER_RETRY_START
        except Exception as e:
            print(f"⚠️ Error: {e!r}")

        await asyncio.sleep(delay)
        delay = min(delay * 2, SERVER_RETRY_MAX)


def main():
    parser = argparse.ArgumentParser(description="BugBot OBS relay (runs on the OBS machine)")
    parser.add_argument("--server", default=os.environ.get("BUGBOT_SERVER", ""),
                        help="address of the bot, usually your own tunnel end: ws://127.0.0.1:4456")
    parser.add_argument("--token", default=os.environ.get("BUGBOT_TOKEN", ""),
                        help="shared secret, has to match the bot's OBS_BRIDGE_TOKEN")
    parser.add_argument("--obs", default=os.environ.get("OBS_WEBSOCKET", "ws://127.0.0.1:4455"),
                        help="local obs-websocket (default: ws://127.0.0.1:4455)")
    args = parser.parse_args()

    if not args.server or not args.token:
        parser.error("--server and --token are required (or set BUGBOT_SERVER/BUGBOT_TOKEN).")

    print(f"🔗 Relay: {args.obs}  <->  {args.server}")
    try:
        asyncio.run(run(args.server, args.token, args.obs))
    except KeyboardInterrupt:
        print("\n🛑 Relay stopped.")


if __name__ == "__main__":
    main()
