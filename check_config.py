#!/usr/bin/env python3
"""Checks the JSON configurations against the code that reads them.

    python3 check_config.py

Meant for after changing one of the *.json files - and for whoever is adapting the bot to
their own server for the first time. The bot itself does not crash on a broken configuration
(missing texts fall back to the shipped version, unknown placeholders stay standing), but you
would only notice once the command looks odd in chat. This says so beforehand.

What is checked:
  * can every file be read at all (JSON syntax),
  * does every text("...") call in the code have a key in the corresponding file,
  * do a text's {placeholders} match what the caller passes in,
  * are there texts nobody uses any more,
  * does "command_names" name only commands that exist, and is none of them duplicated after,
  * does the running container see the same files - or are you editing into nothing.

The mapping code -> file follows the convention from core/runtime_config.py: one JSON per
package, named after the package. The exceptions are the platform-owned features, which share
their platform's file (obs_control), and the two Twitch modules, which read the same
twitch.json.
"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Muss zu ContainerName in bugbot.container passen - siehe check_live_mounts.
CONTAINER = "bugbot"

# Which configuration file belongs to which module. The first matching prefix wins, which is
# why the more specific paths are at the top.
CONFIG_FOR = [
    ("platforms/discord/features/levels", "platforms/discord/features/levels/levels.json"),
    ("platforms/obs", "platforms/obs/obs.json"),
    ("platforms/twitch", "platforms/twitch/twitch.json"),
    ("platforms/discord", "platforms/discord/discord.json"),
    ("features/stats", "features/stats/stats.json"),
    ("features/moderation", "features/moderation/moderation.json"),
    ("features/variables", "features/variables/variables.json"),
    ("features/chat_log", "features/chat_log/chat_log.json"),
    ("features/overlay", "features/overlay/overlay.json"),
    ("features/companion", "features/companion/companion.json"),
    ("features/sql_db", "features/sql_db/sql_db.json"),
]

# Files that do print texts but are not part of the bot (they run on the OBS machine).
SKIP = {"platforms/obs/client/obs_bridge.py", "platforms/obs/client/obs_bridge_script.py"}

problems = []
notes = []


def config_path_for(path):
    relative = path.relative_to(ROOT).as_posix()
    for prefix, config in CONFIG_FOR:
        if relative.startswith(prefix):
            return ROOT / config
    return None


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        problems.append(f"{path.relative_to(ROOT)}: kaputtes JSON - {e}")
    except OSError as e:
        problems.append(f"{path.relative_to(ROOT)}: not readable - {e}")
    return None


def placeholders(template):
    from string import Formatter
    try:
        return {name for _, name, _, _ in Formatter().parse(template) if name}
    except ValueError as e:
        return {f"<kaputte Vorlage: {e}>"}


def _literal_keys(node):
    """The literal keys of a text call. Usually exactly one; with text("a" if x else "b") there
    are two, and a composed key (f"reason.{reason}") has none - that one cannot be checked from
    outside."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literal_keys(node.body) + _literal_keys(node.orelse)
    return []


def text_calls(tree):
    """Every call that fetches a text: text("k", ...), CONFIG.text("k", ...),
    self.config.text("k", ...)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "text" or not node.args:
            continue
        given = {kw.arg for kw in node.keywords if kw.arg}
        for key in _literal_keys(node.args[0]):
            yield key, given, node.lineno


def string_constants(tree):
    """Every string in the module. For the question "is anybody still using this?": text keys
    do not always stand in the call itself, but sometimes in a table next to it
    (requests = {"start": ("StartRecord", "rec.started")}). As evidence of "is used" that is
    enough - for the stricter placeholder check above it is not."""
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def dynamic_key_shapes(tree):
    """(prefixes, suffixes) of the composed keys, e.g. f"highscore.{metric}" -> prefix
    "highscore.", f"{key}.value" -> suffix ".value". Anything matching those counts as used."""
    prefixes, suffixes = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or not node.values:
            continue
        first, last = node.values[0], node.values[-1]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value:
            prefixes.add(first.value)
        if isinstance(last, ast.Constant) and isinstance(last.value, str) and last.value:
            suffixes.add(last.value)
    return prefixes, suffixes


def check_texts():
    used = {}
    for path in sorted(ROOT.glob("**/*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in SKIP or relative.startswith((".", "__")) or "/__pycache__/" in f"/{relative}":
            continue
        config_path = config_path_for(path)
        if config_path is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            problems.append(f"{relative}: {e}")
            continue

        data = load(config_path) or {}
        texts = data.get("texts", {})
        seen = used.setdefault(config_path, set())
        seen |= {value for value in string_constants(tree) if value in texts}
        prefixes, suffixes = dynamic_key_shapes(tree)
        seen |= {
            key for key in texts
            if any(key.startswith(p) for p in prefixes) or any(key.endswith(x) for x in suffixes)
        }
        for key, given, line in text_calls(tree):
            seen.add(key)
            if key not in texts:
                problems.append(
                    f"{relative}:{line}: Text '{key}' fehlt in {config_path.relative_to(ROOT)}"
                )
                continue
            template = texts[key]
            if not isinstance(template, str):
                problems.append(f"{config_path.relative_to(ROOT)}: '{key}' is not text")
                continue
            needed = placeholders(template)
            missing = needed - given
            if missing:
                problems.append(
                    f"{config_path.relative_to(ROOT)}: '{key}' demands {{{', '.join(sorted(missing))}}}, "
                    f"but {relative}:{line} does not pass it"
                )
    return used


def check_unused(used):
    for _, config in CONFIG_FOR:
        path = ROOT / config
        data = load(path)
        if not data:
            continue
        texts = {k for k in data.get("texts", {}) if not k.startswith("_")}
        unused = texts - used.get(path, set())
        if unused:
            notes.append(f"{config}: {len(unused)} Text(e) benutzt niemand: {', '.join(sorted(unused))}")


def check_commands():
    """Check the renames: does "command_names" name only commands that exist, and does no name
    occur twice afterwards?"""
    sys.path.insert(0, str(ROOT))
    from core import registry, runtime_config

    declared = {}
    for package, name in registry.feature_sources():
        try:
            module = __import__(f"{package}.{name}.feature", fromlist=["create_feature"])
            feature = module.create_feature()
        except Exception as e:
            notes.append(f"feature '{name}' not loadable, commands unchecked: {e!r}")
            continue
        config = getattr(feature, "config", None)
        if config is None:
            continue
        declared.setdefault(config.path, set()).update(c.name for c in feature.commands())

    seen = {}
    for path, names in declared.items():
        config = runtime_config.LiveConfig(path)
        overrides = config.command_names()
        for default_name in overrides:
            if default_name not in names:
                problems.append(
                    f"{path.relative_to(ROOT)}: command_names names '{default_name}', "
                    f"which does not exist (present: {', '.join(sorted(names))})"
                )
        for name, command in config.resolve_commands({n: n for n in names}).items():
            if name in seen:
                problems.append(f"command '{name}' is assigned twice ({seen[name]} and {path.name})")
            seen[name] = path.name


def check_live_mounts():
    """Does the running bot see the files that are lying here at all?

    In the container every JSON is mounted in individually (see bugbot.container), and a bind
    mount onto a *file* hangs on its inode, not on its name. Whoever replaces it instead of
    overwriting it - `vim` with the default backupcopy, VS Code, `sed -i`, `mv`, any `git pull`
    that touches the file - gets a new file under the old name. The container keeps the old one
    and from then on sees *no* change any more, not even a later one: the hot-reload check in
    core/runtime_config.py is looking at a file nobody edits.

    From the inside this is undetectable - the file is there and appears unchanged. Which is
    why the check sits here, on the host side, where both states can be compared. Without
    Podman or without a running container there is simply nothing to check."""
    import shutil
    import subprocess

    if not shutil.which("podman"):
        return
    paths = [ROOT / config for _, config in CONFIG_FOR]
    remote = {f"/app/{p.relative_to(ROOT).as_posix()}": p for p in paths if p.exists()}
    if not remote:
        return
    try:
        result = subprocess.run(
            ["podman", "exec", CONTAINER, "stat", "-c", "%n %i %Y", *remote],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        notes.append(f"mounts of the running container unchecked: {e}")
        return
    if result.returncode != 0 and not result.stdout.strip():
        # No container named 'bugbot' - either the bot does not run in a container here, or it
        # is not running right now. Neither is a problem of this configuration.
        return

    for line in result.stdout.splitlines():
        name, _, rest = line.partition(" ")
        path = remote.get(name)
        if path is None:
            continue
        try:
            inode, mtime = (int(float(v)) for v in rest.split())
        except ValueError:
            continue
        host = path.stat()
        if inode != host.st_ino:
            problems.append(
                f"{path.relative_to(ROOT)}: the running container sees a different file than "
                f"this one (inode {inode} instead of {host.st_ino}). Something replaced it "
                f"instead of overwriting it (editor, `sed -i`, `git pull`); no change has taken "
                f"effect since. Fix: systemctl --user restart bugbot.service"
            )
        elif mtime != int(host.st_mtime):
            problems.append(
                f"{path.relative_to(ROOT)}: container and host disagree about the state of the "
                f"file (mtime {mtime} instead of {int(host.st_mtime)})"
            )


def main():
    used = check_texts()
    check_unused(used)
    check_commands()
    check_live_mounts()

    for note in notes:
        print(f"ℹ️ {note}")
    if problems:
        print()
        for problem in problems:
            print(f"❌ {problem}")
        print(f"\n{len(problems)} Problem(e) gefunden.")
        return 1
    print("✅ Configuration and code fit together.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
