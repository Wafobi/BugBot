"""Credentials of the overlay listener, from the environment/.env.

Counterpart to platforms/obs/config.py, and separate from overlay.json for the same reason:
the JSON files are what you look into when adapting things, and secrets have no business
being there.

Without OVERLAY_TOKEN the feature opens no port. It still loads - its commands (death
counter) work without an overlay too. That is at the same time the guarantee that the port
never stands open without a secret.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent and independent of import order - the platforms load the same file, but
# load_dotenv does not overwrite variables that are already set.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Shared secret with the browser sources. Empty = no listener, see above.
OVERLAY_TOKEN = os.environ.get("OVERLAY_TOKEN", "")

# Port on which the bot waits for the overlays.
OVERLAY_PORT = int(os.environ.get("OVERLAY_PORT") or 4457)

# Bind address. Same consideration as for OBS_BRIDGE_BIND: 0.0.0.0 in the container,
# because Podman's port forwarding does not reach the container's loopback - the
# restriction then happens on the host side via PublishPort=127.0.0.1:4457:4457 in
# bugbot.container. Without a container 127.0.0.1 is the right value, and the port is then
# not visible from outside at all.
OVERLAY_BIND = os.environ.get("OVERLAY_BIND") or "0.0.0.0"
