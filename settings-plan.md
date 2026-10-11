# Settings-Website: Plan (Stand 2026-10-11)

> **Umgesetzt (2026-10-11)** – maßgeblich ist [docs/settings.md](docs/settings.md). Abweichung vom
> Plan: kein eigener Port 8080 – die Seite läuft auf dem Overlay-Listener
> (`http://127.0.0.1:4457/settings?token=<SETTINGS_TOKEN>`), damit es bei zwei Ports bleibt;
> Speichern über einen WebSocket auf demselben Port. Entschieden: Token in der URL (einmal, dann
> nur im Tab), nur Einstellungen (keine DB, keine Aktionen), nur ein Zugang. Die TTS-Seite ist
> verlinkt, aber (noch) eigenständig.

Eine Einstellungs-Website für bugbot, gehostet im Container, erreichbar über den SSH-Tunnel
unter **http://localhost:8080**. Darin soll man alles einstellen können, was heute in den
JSON-Dateien steht – ohne SSH, ohne Editor, ohne Neustart.

Reihenfolge: **erst TTS fertig machen, dann das hier.** Die Überschreib-Schicht (siehe unten)
entsteht schon mit TTS, weil die TTS-Admin-Seite sie braucht.

## Bestand (geprüft 2026-10-11)

12 JSON-Dateien, alle per Hot-Reload (`core/runtime_config.py`), alle einzeln in den
Container gemountet, Hilfetexte schon als `_comment`-Felder drin:

| Datei | Werte | davon Texte | Inhalt |
|---|---|---|---|
| `platforms/twitch/twitch.json` | 167 | 128 | Befehle, Mod-Befehle, Regeln, Timings, Moderation |
| `platforms/discord/discord.json` | 145 | 68 | Rollen, Kanäle, Reaction-Roles, Regeln |
| `platforms/obs/obs.json` | 65 | 48 | Werbepausen, Ansagen, Timings |
| `features/stats/stats.json` | 54 | 49 | Texte, Farben, Leaderboard |
| `platforms/twitch/features/tts/tts.json` | 48 | 16 | Stimmen, Pool, Sprechweise, Ersetzungen, englische Wörter |
| `features/overlay/overlay.json` | 29 | 14 | Chat, Event-Slots |
| `features/moderation/moderation.json` | 20 | 5 | Schwellen, verbotene Wörter |
| `features/companion/companion.json` | 17 | 9 | Bits-Preise, Timings |
| `features/variables/variables.json` | 17 | 0 | Zeitzone, Locale, Variablen |
| `platforms/discord/features/levels/levels.json` | 16 | 9 | XP |
| `features/chat_log/chat_log.json` | 5 | 0 | |
| `features/sql_db/sql_db.json` | 2 | 0 | DB-Pfad |

≈ 600 Einstellungen, ≈ 340 davon Texte. Platzhalter-Prüfung existiert schon
(`check_config.py`).

## Entschiedene Eckpunkte

| Frage | Entscheidung |
|---|---|
| Wo läuft es | Im bugbot-Prozess, eigener kleiner Listener (Feature `features/settings`) |
| Port | **8080**, `PublishPort=127.0.0.1:8080:8080` – nur Server-Loopback, Weg rein über den SSH-Tunnel → `http://localhost:8080` |
| Zugang | eigener Token (`SETTINGS_TOKEN`), nicht OVERLAY/TTS – diese Seite kann alles ändern |
| `.env` | **bleibt draußen** – Secrets gehören nicht auf eine Webseite |
| Speichern | **Überschreib-Schicht**, nie in die JSONs im Repo (siehe unten) |

## Überschreib-Schicht (kommt schon mit TTS)

Problem: Ändert eine Webseite die JSONs auf dem Server direkt, weicht die deployte Kopie vom
Git-Stand ab, und das nächste `git pull` (in `update.sh`-Ablauf) scheitert mit Konflikt.

Lösung – drei Schichten in `LiveConfig`, die obere gewinnt:

1. Defaults im Code (wie heute)
2. `<name>.json` im Repo – die mitgelieferten Standardwerte (wie heute)
3. **`settings/<name>.json`** – nur was über die Website geändert wurde; Verzeichnis außerhalb
   von Git (`.gitignore`), im Container als **Verzeichnis** gemountet
   (`Volume=…/settings:/app/settings:Z`) – kein Inode-Problem wie bei Einzeldatei-Mounts

Gemischt wird wie heute schon mit `deep_merge`: Dicts werden zusammengeführt, Listen ersetzt.
Damit:
- `git pull` geht immer glatt, eigene Einstellungen überleben jedes Update
- „Auf Standard zurücksetzen“ = Schlüssel aus der Override-Datei löschen
- Neue Standardwerte aus dem Repo kommen an, solange man sie nicht überschrieben hat
- Hand-Editieren auf dem Server bleibt möglich (in `settings/`, nicht im Repo)

Dateinamen in `settings/` = Dateiname der Config (`tts.json`, `twitch.json` …) – heute alle
eindeutig; `check_config.py` soll Doppelte melden.

## Die Website

### Allgemeiner Editor – baut sich aus den JSONs selbst
- Navigation: eine Seite pro Datei, gruppiert (Plattformen / Features)
- Typen aus dem Wert abgeleitet: bool → Schalter, Zahl → Zahlenfeld, String → Textfeld,
  Liste → Chips, Dict → aufklappbarer Abschnitt
- `_comment*`-Felder als Hilfetext neben dem, was sie beschreiben
- `texts`: eigene Ansicht mit Suche, Platzhalter-Anzeige und Prüfung beim Speichern
  (unbekannter/fehlender `{platzhalter}` → Warnung, nicht speichern)
- Pro Wert sichtbar: Standard oder geändert, mit „↺ Standard“
- `command_names`: Umbenennen/Aliase/Abschalten als eigene kleine Tabelle
- Neue Einstellungen erscheinen automatisch, ohne die Website anzufassen

### Eigene Seiten, wo ein Formular nicht reicht
- **TTS** – die heutige Admin-Seite (Probehören, Pool, Sprechweise) zieht hierher um
- später denkbar: verbotene Wörter (Moderation), Befehlsübersicht, Farben mit Picker

### Verlauf
- Jede Speicherung legt die vorherige Version der Override-Datei ab
  (`settings/.history/<name>/<zeitstempel>.json`, letzte N behalten)
- „Rückgängig“ / Version ansehen

## Offen / noch zu entscheiden

- Login: nur Token in der URL (wie Overlays) oder einmal eingeben → Cookie?
- Sollen `bugbot.db`-Inhalte (z. B. Statistik ansehen, Death-Counter setzen) auch rein, oder
  bewusst nur Einstellungen?
- Darf die Seite Dinge auslösen (Bot-Neustart, Testansage in den Chat) oder nur einstellen?
- Mehrere Personen (Mods) mit eigenem Zugang, oder nur du?

## Nächster Schritt

TTS abschließen (inkl. Überschreib-Schicht in `core/runtime_config.py`), committen – dann
diesen Plan durchgehen und die offenen Punkte entscheiden.
