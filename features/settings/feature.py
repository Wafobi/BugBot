"""The settings website: every JSON setting of the bot, editable in a browser.

Served on the overlay listener (OVERLAY capability) - no port of its own, same SSH tunnel as
everything on screen:

    http://127.0.0.1:4457/settings?token=<SETTINGS_TOKEN>

The page is a route there (GET /settings), and everything it reads and saves goes through a
WebSocket on the same port (?role=settings, behind SETTINGS_TOKEN - see
features/overlay/server.py, add_role): whole texts and lists do not fit safely into a URL.

What it edits is whatever core/runtime_config.py has loaded - every LiveConfig of this
checkout (all_configs()), so a new feature's JSON shows up without touching this file. It
never writes a file in the repository: a change goes into the override layer,
settings/<name>.json (LiveConfig.set_override), which lies on top - `git pull` never
conflicts, "back to default" is removing the override, and every save keeps the previous
version for undo (LiveConfig.history/restore). Secrets (.env) are out of reach by design.

Checked before anything is saved:
  - the key must exist in the shipped file, or be a new entry in a section that is a plain
    name -> text table (static commands, replacements); "_comment" keys are not editable;
  - the type must match the shipped value (a switch stays a switch, a number a number);
  - a text may only use {placeholders} the shipped text uses, and must be a valid template -
    otherwise the bot would print it raw or fall back.
"""

import asyncio
import json
import logging
from pathlib import Path

from core import feature as feature_api
from core import runtime_config

from . import config as env

log = logging.getLogger(__name__)

ROLE = "settings"
ROUTE = "/settings"
PAGE = Path(__file__).parent / "client" / "settings.html"

# Files that must not be changed at runtime: where the database lives is decided once, at
# startup - pointing it elsewhere mid-run would only split the data.
READ_ONLY = {"sql_db.json"}


def _type_name(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "null"


def _get(data, path):
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None, False
        node = node[key]
    return node, True


class SettingsError(ValueError):
    pass


class SettingsFeature(feature_api.Feature):
    name = "settings"
    optional = frozenset({feature_api.OVERLAY})

    async def setup(self, bus):
        overlay = bus.feature_with(feature_api.OVERLAY)
        if not env.SETTINGS_TOKEN:
            log.info("No SETTINGS_TOKEN set - no settings website.")
            return
        if overlay is None or not overlay.listening:
            log.info("Settings website needs the overlay listener (overlay feature + OVERLAY_TOKEN) - not offered.")
            return
        overlay.add_route(ROUTE, self.page, env.SETTINGS_TOKEN)
        overlay.add_role(ROLE, lambda: [("files", self.files())], token=env.SETTINGS_TOKEN, on_message=self.handle)
        log.info(f"Settings website on the overlay listener: {ROUTE}")

    async def page(self, query):
        return 200, PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8"

    # --- Files ----------------------------------------------------------------------------

    @staticmethod
    def _id(config):
        return config.path.resolve().relative_to(runtime_config.ROOT).as_posix()

    def _config(self, file_id):
        for config in runtime_config.all_configs():
            if self._id(config) == file_id:
                return config
        raise SettingsError(f"unbekannte Datei {file_id!r}")

    def files(self):
        """Every editable file: id (path in the repo), group, and whether it is overridden."""
        result = []
        for config in runtime_config.all_configs():
            file_id = self._id(config)
            parts = file_id.split("/")
            result.append({
                "id": file_id,
                "name": config.path.stem,
                "group": "Plattformen" if parts[0] == "platforms" and parts[2] != "features" else "Features",
                "owner": parts[1] if parts[0] == "platforms" else "",
                "overridden": bool(config.overrides),
                "read_only": config.path.name in READ_ONLY,
            })
        return result

    def _state(self, config):
        return {
            "file": self._id(config),
            "base": config.base,
            "overrides": config.overrides,
            "history": config.history()[:20],
            "read_only": config.path.name in READ_ONLY,
        }

    # --- Checks ---------------------------------------------------------------------------

    def _check(self, config, path, value):
        if config.path.name in READ_ONLY:
            raise SettingsError(f"{config.path.name} ist nur lesbar")
        if not path or not all(isinstance(k, str) and k for k in path):
            raise SettingsError("ungültiger Pfad")
        if any(k.startswith("_") for k in path):
            raise SettingsError("Kommentare sind nicht änderbar")
        base = config.base
        shipped, exists = _get(base, path)
        if not exists:
            parent, parent_exists = _get(base, path[:-1])
            if not parent_exists or not isinstance(parent, dict):
                raise SettingsError(f"{'.'.join(path)} gibt es nicht")
            entries = [v for k, v in parent.items() if not k.startswith("_")]
            if path[-2:-1] == ["texts"] or not entries or not all(isinstance(v, str) for v in entries):
                raise SettingsError(f"in {'.'.join(path[:-1])} können keine neuen Einträge angelegt werden")
            shipped = ""
        if shipped is not None and _type_name(shipped) != _type_name(value):
            raise SettingsError(f"erwartet {_type_name(shipped)}, bekommen {_type_name(value)}")
        if isinstance(value, str) and isinstance(shipped, str):
            self._check_template(path, shipped, value)

    @staticmethod
    def _check_template(path, shipped, value):
        """A text may only use placeholders the shipped one does, and must parse. Static
        commands (no placeholders shipped, free to use the variables) are only checked for
        parsing."""
        try:
            used = runtime_config.placeholders(value)
        except ValueError as error:
            raise SettingsError(f"ungültige Vorlage: {error}") from error
        if "texts" in path:
            unknown = used - runtime_config.placeholders(shipped)
            if unknown:
                raise SettingsError("unbekannte Platzhalter: " + ", ".join("{" + p + "}" for p in sorted(unknown)))

    # --- Messages from the page -------------------------------------------------------------

    async def handle(self, raw):
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            return {"type": "reply", "ok": False, "error": "kein JSON"}
        reply = {"type": "reply", "id": request.get("id")}
        try:
            reply.update(ok=True, data=await asyncio.to_thread(self._dispatch, request))
        except (SettingsError, OSError, ValueError, KeyError) as error:
            reply.update(ok=False, error=str(error))
        return reply

    def _dispatch(self, request):
        op = request.get("op")
        if op == "files":
            return self.files()
        config = self._config(str(request.get("file") or ""))
        path = request.get("path") or []
        if op == "get":
            return self._state(config)
        if op == "set":
            value = request.get("value")
            self._check(config, path, value)
            config.set_override(path, value)
            log.info(f"Settings: {self._id(config)} {'.'.join(path)} changed")
        elif op == "reset":
            if config.path.name in READ_ONLY:
                raise SettingsError(f"{config.path.name} ist nur lesbar")
            config.reset_override(path)
            log.info(f"Settings: {self._id(config)} {'.'.join(path)} reset to default")
        elif op == "restore":
            config.restore(str(request.get("version") or ""))
            log.info(f"Settings: {self._id(config)} restored to {request.get('version')}")
        else:
            raise SettingsError(f"unbekannte Aktion {op!r}")
        return self._state(config)


def create_feature():
    return SettingsFeature()
