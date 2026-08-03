"""BugBot-Relais als OBS-Skript - die bequeme Hälfte von obs_bridge.py.

Gehört wie obs_bridge.py auf den Rechner mit OBS und nicht auf den Server: beide Dateien
zusammen in einen Ordner kopieren, dann in OBS unter Werkzeuge -> Skripte diese hier
hinzufügen. Serveradresse und Token stehen danach als Felder in der OBS-Oberfläche, und
das Relais läuft, solange OBS läuft - kein Konsolenfenster, kein Autostart-Eintrag, kein
vergessener Start nach einem Neustart.

Das Relais selbst ist weiterhin obs_bridge.py. Dieses Skript startet es nur als eigenen
Prozess und hält es am Leben. Warum nicht direkt hier im Skript: OBS führt Skripte in
seinem eigenen eingebetteten Python aus, mit einem Thread, der beim Beenden nicht hängen
darf, und mit `websockets` als zusätzlicher Abhängigkeit genau dort. Als eigener Prozess
bleibt die Netzwerkseite dort, wo sie getestet ist, ein Absturz kann OBS nicht mitnehmen,
und für das Beenden reicht ein Signal.

Voraussetzungen auf diesem Rechner:
  - Python 3.9+ mit `pip install websockets` (dasselbe Python unten als "Python-Programm"
    eintragen, falls es nicht von allein gefunden wird),
  - der SSH-Tunnel zum Server, siehe obs_bridge.py.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading

import obspython as obs

# Wie oft nachgesehen wird, ob der Relais-Prozess noch lebt (und ob es neue Log-Zeilen
# gibt). Beides läuft über OBS-Timer, also im Hauptthread - die obs_*-Funktionen sind
# nicht dafür gedacht, aus einem eigenen Thread heraus aufgerufen zu werden.
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


# --- OBS-Skript-API ------------------------------------------------------------------

def script_description():
    return (
        "<b>BugBot-Relais</b><br>"
        "Verbindet dieses OBS mit dem BugBot auf dem Server: das Relais spricht lokal mit "
        "obs-websocket und wählt sich beim Bot ein.<br><br>"
        "Voraussetzungen: obs-websocket aktiviert (Werkzeuge → WebSocket-Servereinstellungen), "
        "ein laufender SSH-Tunnel zum Server, sowie <code>obs_bridge.py</code> im selben "
        "Ordner wie dieses Skript.<br><br>"
        "Meldungen des Relais stehen im Skript-Protokoll unten."
    )


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "server", "ws://127.0.0.1:4456")
    obs.obs_data_set_default_string(settings, "obs_url", "ws://127.0.0.1:4455")
    obs.obs_data_set_default_string(settings, "relay_path", _default_relay_path())
    obs.obs_data_set_default_bool(settings, "enabled", True)


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_bool(props, "enabled", "Relais aktiv")
    obs.obs_properties_add_text(
        props, "server", "BugBot-Adresse (Tunnelende)", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_text(
        props, "token", "Token (OBS_BRIDGE_TOKEN)", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_text(
        props, "obs_url", "Lokales obs-websocket", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_path(
        props, "relay_path", "Relais-Skript (obs_bridge.py)", obs.OBS_PATH_FILE,
        "Python (*.py)", os.path.dirname(os.path.abspath(__file__)))
    obs.obs_properties_add_path(
        props, "python_path", "Python-Programm (leer = automatisch)", obs.OBS_PATH_FILE,
        "*.*", None)
    obs.obs_properties_add_button(props, "restart", "Relais neu starten", _on_restart_clicked)
    return props


def script_load(settings):
    script_update(settings)
    obs.timer_add(_tick, CHECK_INTERVAL_MS)


def script_update(settings):
    """Läuft bei jeder Änderung in der Oberfläche. Neu gestartet wird nur, wenn sich an
    den Werten wirklich etwas geändert hat - sonst würde jeder Tastendruck im Token-Feld
    die Verbindung abreißen lassen."""
    changed = _read_settings(settings)
    if not _settings["enabled"]:
        _stop_relay("Relais deaktiviert.")
        return
    if changed or _process is None:
        _restart_relay()


def script_unload():
    obs.timer_remove(_tick)
    _stop_relay("OBS wird beendet.")


# --- Einstellungen -------------------------------------------------------------------

def _default_relay_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "obs_bridge.py")


def _read_settings(settings):
    """Übernimmt die Werte aus der Oberfläche. True, wenn sich etwas geändert hat."""
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
    """Ein Python, das das Relais ausführen kann.

    sys.executable ist innerhalb von OBS meist OBS selbst und taugt deshalb nicht - erst
    prüfen, ob wirklich ein Python dahintersteht, sonst im PATH suchen."""
    if _settings["python_path"]:
        return _settings["python_path"]
    executable = sys.executable or ""
    if os.path.basename(executable).lower().startswith("python"):
        return executable
    return shutil.which("python3") or shutil.which("python") or ""


# --- Relais-Prozess ------------------------------------------------------------------

def _restart_relay():
    _stop_relay()
    global _process, _reader

    if not _settings["server"] or not _settings["token"]:
        _log("⚠️ Adresse oder Token fehlt - Relais nicht gestartet.")
        return
    if not os.path.isfile(_settings["relay_path"]):
        _log(f"⚠️ obs_bridge.py nicht gefunden: {_settings['relay_path']}")
        return
    python = _find_python()
    if not python:
        _log("⚠️ Kein Python gefunden - Pfad unter 'Python-Programm' eintragen "
             "(dort muss auch 'websockets' installiert sein).")
        return

    # Der Token geht über die Umgebung, nicht über die Kommandozeile: Argumente stehen in
    # der Prozessliste und wären für jeden auf dem Rechner lesbar.
    env = dict(os.environ)
    env["BUGBOT_TOKEN"] = _settings["token"]
    # Die Ausgaben des Relais sind UTF-8 (Emoji); ohne das würde ein umgeleiteter
    # Ausgabestrom unter Windows in der Standard-Codepage landen und beim ersten Symbol
    # abbrechen.
    env["PYTHONIOENCODING"] = "utf-8"

    command = [
        python, "-u", _settings["relay_path"],
        "--server", _settings["server"],
        "--obs", _settings["obs_url"] or "ws://127.0.0.1:4455",
    ]

    # Unter Windows sonst ein Konsolenfenster pro Start - genau das, was dieses Skript
    # vermeiden soll.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        _process = subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=creationflags,
        )
    except OSError as e:
        _process = None
        _log(f"⚠️ Relais konnte nicht gestartet werden: {e}")
        return

    _reader = threading.Thread(target=_pump_output, args=(_process,), daemon=True)
    _reader.start()
    _log(f"▶️ Relais gestartet ({_settings['obs_url']} → {_settings['server']}).")


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
    """Läuft in einem eigenen Thread und sammelt die Ausgaben nur ein - geloggt wird im
    Hauptthread (siehe _tick), weil die obs_*-Funktionen dort hingehören."""
    try:
        for line in process.stdout:
            _log_queue.put(line.rstrip())
    except Exception:
        pass
    finally:
        _log_queue.put(None)  # Prozess hat seine Ausgabe geschlossen


def _tick():
    """OBS-Timer: Log-Zeilen ausgeben und den Prozess überwachen."""
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
        # obs_bridge.py läuft von sich aus endlos weiter - endet es doch, ist etwas
        # grundlegend schiefgelaufen (fehlendes websockets, falscher Pfad). Nach kurzer
        # Pause noch einmal versuchen, statt still tot zu bleiben.
        _log(f"⚠️ Relais beendet (Code {code}) - Neustart in {RESTART_DELAY_MS // 1000}s.")
        _restart_pending = True
        obs.timer_add(_delayed_restart, RESTART_DELAY_MS)


def _delayed_restart():
    global _restart_pending
    obs.timer_remove(_delayed_restart)
    _restart_pending = False
    if _settings["enabled"]:
        _restart_relay()


def _on_restart_clicked(props, prop):
    _log("🔄 Neustart angefordert.")
    _restart_relay()
    return True


def _log(message):
    obs.script_log(obs.LOG_INFO, message)
