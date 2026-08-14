"""BugBot relay as an OBS script - the convenient half of obs_bridge.py.

Like obs_bridge.py this belongs on the machine with OBS and not on the server: copy both
files together into one folder, then add this one in OBS under Tools -> Scripts. The server
address and token then appear as fields in the OBS interface, and the relay runs as long as
OBS runs - no console window, no autostart entry, no forgotten start after a reboot.

The relay itself is still obs_bridge.py. This script only starts it as a separate process and
keeps it alive. Why not directly in the script: OBS runs scripts in its own embedded Python,
with a thread that must not hang on shutdown, and with `websockets` as an additional
dependency in exactly that place. As a separate process the networking stays where it is
tested, a crash cannot take OBS with it, and stopping is a matter of one signal.

Prerequisites on this machine:
  - Python 3.9+ with `pip install websockets` (enter that same Python below as "Python
    interpreter" if it is not found on its own),
  - the SSH tunnel to the server, see obs_bridge.py.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading

import obspython as obs

# How often it is checked whether the relay process is still alive (and whether there are
# new log lines). Both run on OBS timers, i.e. in the main thread - the obs_* functions are
# not meant to be called from a thread of your own.
CHECK_INTERVAL_MS = 2000
RESTART_DELAY_MS = 5000

_settings = {
    "server": "",
    "token": "",
    "obs_url": "",
    "relay_path": "",
    "python_path": "",
    "enabled": True,
}

_process = None
_log_queue = queue.Queue()
_reader = None
_restart_pending = False


# --- OBS script API ------------------------------------------------------------------

def script_description():
    return (
        "<b>BugBot relay</b><br>"
        "Connects this OBS to the BugBot on the server: the relay talks to obs-websocket "
        "locally and dials in to the bot.<br><br>"
        "Prerequisites: obs-websocket enabled (Tools → WebSocket Server Settings), a running "
        "SSH tunnel to the server, and <code>obs_bridge.py</code> in the same folder as this "
        "script.<br><br>"
        "Messages from the relay appear in the script log below."
    )


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "server", "ws://127.0.0.1:4456")
    obs.obs_data_set_default_string(settings, "obs_url", "ws://127.0.0.1:4455")
    obs.obs_data_set_default_string(settings, "relay_path", _default_relay_path())
    obs.obs_data_set_default_bool(settings, "enabled", True)


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "Relay active")
    obs.obs_properties_add_text(
        props, "server", "BugBot address (tunnel end)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(
        props, "token", "Token (OBS_BRIDGE_TOKEN)", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(
        props, "obs_url", "Local obs-websocket", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_path(
        props, "relay_path", "Relay script (obs_bridge.py)", obs.OBS_PATH_FILE,
        "Python (*.py)", os.path.dirname(os.path.abspath(__file__)))
    obs.obs_properties_add_path(
        props, "python_path", "Python interpreter (empty = automatic)", obs.OBS_PATH_FILE,
        "*.*", None)
    obs.obs_properties_add_button(props, "restart", "Restart relay", _on_restart_clicked)
    return props


def script_load(settings):
    script_update(settings)
    obs.timer_add(_tick, CHECK_INTERVAL_MS)


def script_update(settings):
    """Runs on every change in the interface. A restart only happens when the values really
    changed - otherwise every keystroke in the token field would tear the connection down."""
    changed = _read_settings(settings)
    if not _settings["enabled"]:
        _stop_relay("Relay disabled.")
        return
    if changed or _process is None:
        _restart_relay()


def script_unload():
    obs.timer_remove(_tick)
    _stop_relay("OBS is shutting down.")


# --- Settings ------------------------------------------------------------------------

def _default_relay_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "obs_bridge.py")


def _read_settings(settings):
    """Adopts the values from the interface. True when something changed."""
    new = {
        "server": obs.obs_data_get_string(settings, "server").strip(),
        "token": obs.obs_data_get_string(settings, "token").strip(),
        "obs_url": obs.obs_data_get_string(settings, "obs_url").strip(),
        "relay_path": obs.obs_data_get_string(settings, "relay_path").strip() or _default_relay_path(),
        "python_path": obs.obs_data_get_string(settings, "python_path").strip(),
        "enabled": obs.obs_data_get_bool(settings, "enabled"),
    }
    changed = new != _settings
    _settings.update(new)
    return changed


def _find_python():
    """A Python that can run the relay.

    Inside OBS, sys.executable is usually OBS itself and is therefore no good - first check
    whether there really is a Python behind it, otherwise search the PATH."""
    if _settings["python_path"]:
        return _settings["python_path"]
    executable = sys.executable or ""
    if os.path.basename(executable).lower().startswith("python"):
        return executable
    return shutil.which("python3") or shutil.which("python") or ""


# --- Relay process -------------------------------------------------------------------

def _restart_relay():
    _stop_relay()
    global _process, _reader

    if not _settings["server"] or not _settings["token"]:
        _log("⚠️ Address or token missing - relay not started.")
        return
    if not os.path.isfile(_settings["relay_path"]):
        _log(f"⚠️ obs_bridge.py not found: {_settings['relay_path']}")
        return
    python = _find_python()
    if not python:
        _log("⚠️ No Python found - enter the path under 'Python interpreter' "
             "(it needs 'websockets' installed too).")
        return

    # The token goes through the environment, not the command line: arguments appear in the
    # process list and would be readable by anyone on the machine.
    env = dict(os.environ)
    env["BUGBOT_TOKEN"] = _settings["token"]
    # The relay's output is UTF-8 (emoji); without this a redirected output stream would
    # land in the default code page on Windows and break on the first symbol.
    env["PYTHONIOENCODING"] = "utf-8"

    command = [
        python, "-u", _settings["relay_path"],
        "--server", _settings["server"],
        "--obs", _settings["obs_url"] or "ws://127.0.0.1:4455",
    ]

    # On Windows this otherwise means one console window per start - exactly what this
    # script is meant to avoid.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        _process = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=creationflags,
        )
    except OSError as e:
        _process = None
        _log(f"⚠️ Relay could not be started: {e}")
        return

    _reader = threading.Thread(target=_pump_output, args=(_process,), daemon=True)
    _reader.start()
    _log(f"▶️ Relay started ({_settings['obs_url']} → {_settings['server']}).")


def _stop_relay(reason=""):
    global _process, _reader
    process, _process = _process, None
    _reader = None
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
    if reason:
        _log(f"⏹️ {reason}")


def _pump_output(process):
    """Runs in a thread of its own and only collects the output - logging happens in the
    main thread (see _tick), because that is where the obs_* functions belong."""
    try:
        for line in process.stdout:
            _log_queue.put(line.rstrip())
    except Exception:
        pass
    finally:
        _log_queue.put(None)  # the process closed its output


def _tick():
    """OBS timer: print log lines and watch the process."""
    global _restart_pending
    while True:
        try:
            line = _log_queue.get_nowait()
        except queue.Empty:
            break
        if line:
            _log(line)

    if not _settings["enabled"] or _process is None or _restart_pending:
        return
    code = _process.poll()
    if code is not None:
        # obs_bridge.py runs on endlessly by itself - if it does end, something went
        # fundamentally wrong (missing websockets, wrong path). Try again after a short
        # pause, rather than staying quietly dead.
        _log(f"⚠️ Relay ended (code {code}) - restarting in {RESTART_DELAY_MS // 1000}s.")
        _restart_pending = True
        obs.timer_add(_delayed_restart, RESTART_DELAY_MS)


def _delayed_restart():
    global _restart_pending
    obs.timer_remove(_delayed_restart)
    _restart_pending = False
    if _settings["enabled"]:
        _restart_relay()


def _on_restart_clicked(props, prop):
    _log("🔄 Restart requested.")
    _restart_relay()
    return True


def _log(message):
    obs.script_log(obs.LOG_INFO, message)
