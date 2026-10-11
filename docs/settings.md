# Settings website

Every JSON setting of the bot, editable in a browser — rules, texts, timings, command names,
static commands, TTS voices, everything that otherwise means SSH and an editor. Lives in
[`features/settings`](../features/settings).

```
http://127.0.0.1:4457/settings?token=<SETTINGS_TOKEN>
```

Served by the bot itself, on the [overlay](overlay.md) listener: no port of its own, through the
same [SSH tunnel](tunnel.md) as everything on screen. The token is only needed once — the page
keeps it for the tab and removes it from the address bar.

## How it works

- **Built from the JSON files themselves.** Left the files (platforms, features), right a form:
  a switch for true/false, a number field, a text field or box, one line per entry for lists, a
  section per object. The `_comment` keys appear as help next to what they explain. A new setting
  in any file shows up without anyone touching the website.
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
