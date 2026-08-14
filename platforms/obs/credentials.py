"""Checks the OBS credentials, as far as that is possible from here at all.

Called by check_credentials.py in the project root. The contract is a check() function
yielding (level, message) pairs; levels are "ok", "warn", "fail", "skip" and "detail"
(continuation line for the previous message).

The caveat right up front: OBS runs on the streamer's machine and calls *us* (see
docs/obs.md). From here it is therefore impossible to tell whether OBS is running, whether
the relay has dialled in, or whether OBS_PASSWORD is right - only the running bot knows
that, and `!obs` in chat says so. What can be checked is our own side: are the secrets set,
is the port free, does the configuration add up.
"""

import os
import socket

DEFAULT_PORT = 4456
DEFAULT_BIND = "0.0.0.0"


def _port_is_free(bind, port):
    """Can the bot open this port at all? Occupied does not mean broken - most of the time
    it means the bot is already running."""
    family = socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind, port))
            return True
        except OSError:
            return False


def check():
    token = os.environ.get("OBS_BRIDGE_TOKEN", "").strip()
    password = os.environ.get("OBS_PASSWORD", "").strip()
    raw_port = os.environ.get("OBS_BRIDGE_PORT", "").strip()
    bind = os.environ.get("OBS_BRIDGE_BIND", "").strip() or DEFAULT_BIND

    if not token:
        yield "skip", ("OBS_BRIDGE_TOKEN is not set - the OBS platform will not be loaded. "
                       "That is how you run the bot without OBS.")
        return

    # --- The shared secret -----------------------------------------------------------
    if len(token) < 32:
        yield "warn", (f"OBS_BRIDGE_TOKEN is only {len(token)} characters long - it is the "
                       f"only password guarding remote control of a live broadcast. "
                       f"Generate a fresh one with: openssl rand -hex 32")
    else:
        yield "ok", f"OBS_BRIDGE_TOKEN set ({len(token)} characters)."

    if not password:
        yield "warn", ("OBS_PASSWORD is empty - correct only when authentication is off in "
                       "OBS under Tools -> WebSocket Server Settings.")
    else:
        yield "ok", "OBS_PASSWORD set."

    # --- The port ----------------------------------------------------------------------
    if raw_port and not raw_port.isdigit():
        yield "fail", f"OBS_BRIDGE_PORT is not a number: {raw_port!r}"
        return
    port = int(raw_port) if raw_port else DEFAULT_PORT

    if _port_is_free(bind, port):
        yield "ok", f"Port {bind}:{port} is free."
    else:
        yield "warn", (f"Port {bind}:{port} is occupied - normal when the bot is running. "
                       f"Otherwise something else is listening there.")

    if bind == "0.0.0.0":
        yield "detail", ("Binding to 0.0.0.0 is correct in a container: the restriction to "
                         "loopback sits on the host side there (PublishPort in "
                         "bugbot.container). Without a container 127.0.0.1 belongs here.")

    yield "detail", ("whether OBS is running, the relay has dialled in and OBS_PASSWORD is "
                     "right is only answered by `!obs` in chat - see docs/obs.md.")
