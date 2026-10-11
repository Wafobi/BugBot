# Settings website

Every JSON setting of the bot, editable in a browser — rules, texts, timings, command names,
static commands, TTS voices, everything that otherwise means SSH and an editor. Lives in
[`features/settings`](../features/settings).

```
http://127.0.0.1:4457/settings?token=<SETTINGS_TOKEN>
```

Served by the bot itself, on the [overlay](overlay.md) listener: no port of its own, through the
same [SSH tunnel](tunnel.md) as everything on screen. The token stays in the address, so
reloading and a bookmark work.

## How it works

**Laid out like the bot.** The navigation is the module tree, read live from the registry: each
platform with the features it owns underneath (twitch → raw_log, stream_sessions, tts; discord →
levels; obs → obs_control), then the neutral features. Packages present but not loaded are
listed greyed out. The website names no module — a new feature appears on its own.

Each module's page shows what the module says about itself — the first paragraph of its
docstring, whether it is loaded, what it can (`provides`/platform capabilities), needs
(`requires`) and uses if present (`optional`) — and has one tab per thing it brings along:

- **⚙ Einstellungen** — its JSON file (found next to its code), if it has one;
- one tab per **tool page** it offers (capability `SETTINGS_TOOL`) — e.g. tts →
  **🔊 TTS – Stimmen & Pool**.

The address remembers module and tab (`#feature:tts/tool:tts`), so reloading and bookmarks land
where you were.

**The settings tab**

- **Built from the JSON file itself:** a switch for true/false, a number field, a text field or
  box, one line per entry for lists, a section per object. The `_comment` keys appear as help next
  to what they explain. A new setting shows up without anyone touching the website.
- **Saves never touch the repository.** Every change goes into the override layer,
  `settings/<name>.json` ([Configuration → Overrides](configuration.md#overrides)), which lies
  on top of the shipped file. `git pull` never conflicts; new defaults from an update arrive
  wherever nothing is overridden. Changes apply immediately — no restart.
- **"geändert"** marks an overridden value, **↺** removes the override (back to the shipped
  value), **↶ Rückgängig** puts the previous version of the file's overrides back (the last 30
  are kept in `settings/.history/`).
- **Search** (Ctrl+K) filters every key, value and help text of the open file.
- **New entries** (`＋ hinzufügen`) only where the bot accepts them: plain name → text tables
  such as `commands` (static commands) or TTS `replacements`. Shipped keys cannot be deleted —
  only overridden.
- **Read-only** is declared by the module itself in its JSON, `"_settings": {"read_only": true}`
  (`sql_db.json` does — where the database lives is decided at startup).

## Style

Bottom left, **Stil**: the overlays' colour worlds — aurora (default, the stream's cyan/magenta),
amber, cyan, matrix, sunset, mono. The choice is itself a setting
(`features/settings/settings.json`, `theme`), so it is the same in every browser and can be reset
or undone like any other; tool pages (TTS) follow it.

## Tool pages

A feature that brings a page of its own provides `SETTINGS_TOOL`: `settings_tool_info()` (id,
title, icon) and `settings_tool(action, args)`, where action `page` returns its HTML. The website
shows that page in a frame on the module's tab and relays its calls (`window.parent.bugbotTool`)
through its own authenticated connection — the page needs no token and no connection of its own,
and the website knows nothing about it.

## Checked before saving

| Check | Example refused |
|---|---|
| the key exists in the shipped file (or is a new entry where allowed) | `texts.brandneu` |
| the type matches the shipped value | `"hundert"` for `min_bits` |
| a text uses only placeholders the shipped text uses | `{nutzer}` instead of `{user}` |
| a text is a valid template | `{user hat Bits` |
| `_comment` keys and `sql_db.json` (read-only) | — |

The error is shown next to the field; nothing is written.

## Setup

1. `.env` on the server: `SETTINGS_TOKEN=` a fresh `openssl rand -hex 32` — not one of the other
   tokens: this page can change everything. `OVERLAY_TOKEN` must be set too.
2. `git pull && ./update.sh` (or `systemctl --user restart bugbot.service` if only `.env`
   changed).
3. Open the URL above through the tunnel.

Out of scope on purpose: the `.env` (secrets), the database, and actions such as restarting the
bot — this page sets things, it does not run them.
