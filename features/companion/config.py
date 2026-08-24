"""Credentials of the companion listener, from the environment/.env.

Counterpart to features/overlay/config.py, and separate from companion.json for the same
reason: the JSON file is what you look into when adapting things, and secrets have no
business being there.

Without COMPANION_TOKEN the feature opens no port. It still loads and still tracks who is
in chat - that is at the same time the guarantee that the port never stands open without a
secret.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent and independent of import order - the platforms load the same file, but
# load_dotenv does not overwrite variables that are already set.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Shared secret with the browser source. Empty = no listener, see above.
COMPANION_TOKEN = os.environ.get("COMPANION_TOKEN", "")

# Port on which the bot waits for the companion page. Next free one after the relay (4456)
# and the overlay (4457, which also carries the chat).
COMPANION_PORT = int(os.environ.get("COMPANION_PORT") or 4459)

# Bind address. Same consideration as OBS_BRIDGE_BIND/OVERLAY_BIND: 0.0.0.0
# in the container, because Podman's port forwarding does not reach the container's
# loopback - the restriction then happens on the host side via
# PublishPort=127.0.0.1:4459:4459 in bugbot.container. Without a container 127.0.0.1 is the
# right value, and the port is then not visible from outside at all.
COMPANION_BIND = os.environ.get("COMPANION_BIND") or "0.0.0.0"
