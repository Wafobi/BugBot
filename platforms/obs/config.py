"""OBS configuration from the environment/.env.

Counterpart to platforms/twitch/config.py and platforms/discord/config.py: core stays
platform-neutral, and every platform brings its own credentials.

The difference from the other two is in the direction: Discord and Twitch are services on
the network that the bot dials. OBS runs on the streamer's machine, the bot on a server -
and obs-websocket is itself a server listening *locally* there. So the bot could only reach
it if a port at home were reachable from the internet. Instead a relay on the OBS machine
reverses the direction (platforms/obs/client/obs_bridge.py) and dials in to the bot; which
is why there are no OBS addresses here, but those of our own listener.

Two secrets, two legs:
  OBS_BRIDGE_TOKEN  bot <-> relay. Whoever dials in to the bot has to know it.
  OBS_PASSWORD      bot <-> obs-websocket. Travels through the relay all the way to OBS and
                    is exactly the password from the WebSocket server settings.

Without OBS_BRIDGE_TOKEN there is no OBS platform: core/registry.py then skips it with a
warning. That is how you run the bot without OBS - and at the same time the guarantee that
the port never stands open without a secret.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent and independent of import order - the other platforms load the same file, but
# load_dotenv does not overwrite variables that are already set.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Shared secret with the relay on the OBS machine. Mandatory: see above.
OBS_BRIDGE_TOKEN = os.environ["OBS_BRIDGE_TOKEN"]

# Port on which the bot waits for the relay.
OBS_BRIDGE_PORT = int(os.environ.get("OBS_BRIDGE_PORT") or 4456)

# Bind address. The port does not belong in the open network: an SSH tunnel leads here from
# the OBS machine (see docs/tunnel.md), so the relay connects to its own loopback. Where that
# restriction sits depends on how it is run:
#   in a container   0.0.0.0 (default) - the container has its own network namespace, and
#                    Podman's port forwarding does not reach its loopback. The restriction
#                    therefore happens on the host side: PublishPort=127.0.0.1:4456:4456.
#   without one      127.0.0.1 - the port is then not visible from outside in the first place.
OBS_BRIDGE_BIND = os.environ.get("OBS_BRIDGE_BIND") or "0.0.0.0"

# The password from the WebSocket server settings in OBS. Empty only when authentication is
# switched off there - which is defensible, because obs-websocket on the OBS machine only
# has to listen on 127.0.0.1: the only thing going outwards is the relay.
OBS_PASSWORD = os.environ.get("OBS_PASSWORD", "")
