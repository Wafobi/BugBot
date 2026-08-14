"""Credentials of the chat panel listener, from the environment/.env.

Counterpart to platforms/obs/config.py and features/overlay/config.py, and separate from
chat_panel.json for the same reason: the JSON file is what you look into when adapting
things, and secrets have no business being there.

Without CHAT_PANEL_TOKEN the feature opens no port. It still loads and still records chat
into the in-memory history - that is at the same time the guarantee that the port never
stands open without a secret.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent and independent of import order - the platforms load the same file, but
# load_dotenv does not overwrite variables that are already set.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

# Shared secret with the browser source. Empty = no listener, see above.
CHAT_PANEL_TOKEN = os.environ.get("CHAT_PANEL_TOKEN", "")

# Port on which the bot waits for the chat panel.
CHAT_PANEL_PORT = int(os.environ.get("CHAT_PANEL_PORT") or 4458)

# Bind address. Same consideration as OBS_BRIDGE_BIND/OVERLAY_BIND: 0.0.0.0 in the
# container, because Podman's port forwarding does not reach the container's loopback - the
# restriction then happens on the host side via PublishPort=127.0.0.1:4458:4458 in
# bugbot.container. Without a container 127.0.0.1 is the right value, and the port is then
# not visible from outside at all.
CHAT_PANEL_BIND = os.environ.get("CHAT_PANEL_BIND") or "0.0.0.0"
