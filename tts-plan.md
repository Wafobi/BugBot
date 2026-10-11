# TTS-Feature: Plan (Stand 2026-10-11)

Bits/Cheer-getriggerte Text-to-Speech-Ansage, serverseitig synthetisiert (Piper), in eine
Warteschlange gestellt und per Stream-Deck-Knopfdruck manuell freigegeben. Wird über eine
Browser-Source in OBS abgespielt.

## Die Regel für Zuschauer

> **Cheer ab 100 Bits mit Nachricht → wird vorgelesen.**
> **Cheer ohne Nachricht → Guthaben. Damit kannst du später `!tts <text>` (vorlesen lassen)
> oder `!companion <text>` nutzen.**

Zwei Wege zum selben Ziel: der direkte (Cheer mit Text, kein Stichwort, die Konvention, die
Twitch-Zuschauer von anderen Kanälen kennen) und der über das Guthaben (erst cheeren, dann
per Befehl ausgeben - genau wie beim Companion schon heute). Diese zwei Sätze kommen in die
Info-Box unter dem Stream und in die Antwort von `!tts` ohne Text (siehe Befehle).

"Ohne Nachricht" heißt: nach dem Entfernen aller Cheermotes (`Cheer100`, `Kappa50`, …) und
Leerzeichen bleibt nichts übrig.

## Entschiedene Eckpunkte

| Frage | Entscheidung |
|---|---|
| Auslöser | (a) Twitch-Cheer ab `min_bits` **mit Nachricht**, oder (b) `!tts <text>` im Chat, bezahlt mit `min_bits` aus dem gemeinsamen Guthaben |
| Guthaben | **Ein Topf** für `!companion` und `!tts` - dasselbe Guthaben wie heute beim Companion (alle Cheers minus `companion_spend`), keine zweite Kasse |
| Trennung von Companion | Ein vorgelesener Cheer wird **komplett** (alle Bits, nicht nur `min_bits`) im Companion-Ausgabenkonto (`companion_spend`) gebucht - die Bits zählen nur einmal |
| Engine | **Piper** (neuronale TTS), CLI-Aufruf |
| Wo läuft Piper | Im **bestehenden** `bugbot`-Image gebacken (Dockerfile), Aufruf per `subprocess` aus dem Feature heraus - **kein** eigener Pod/Container |
| Wiedergabe | Nicht `speechSynthesis` im Browser (verworfen, unzuverlässig in CEF) - Bot synthetisiert WAV serverseitig, Browser-Source spielt es über ein `<audio>`-Element ab |
| Abspiel-Zeitpunkt | Nicht automatisch - landet in einer Queue, du gibst per Stream-Deck-Knopf den jeweils nächsten frei |
| Moderation | Jeder Cheer-Text läuft vor dem Sprechen durch `ModerationFeature.review()`; bei Treffer wird nur der Nachrichtenteil verworfen, die Bits-Ansage bleibt |

### Trennung von Companion im Detail

Das Companion-Guthaben ist "alle Cheers aller Zeiten (features/stats) minus
`companion_spend`" (siehe `features/companion/feature.py`). Ohne Trennung würde ein
vorgelesener Cheer zusätzlich das Companion-Guthaben aufladen.

| Fall | Vorgelesen? | Companion-Guthaben |
|---|---|---|
| Cheer ≥ `min_bits` mit Text | ja (in die Queue) | Bits werden als ausgegeben gebucht → kein Zuwachs |
| Cheer ≥ `min_bits` mit Text, Text von Moderation verworfen | nur Bits-Ansage | trotzdem gebucht (die Ansage lief ja) |
| Cheer ≥ `min_bits` ohne Text | nein | wächst wie bisher |
| Cheer < `min_bits` (mit oder ohne Text) | nein | wächst wie bisher |
| Anonymer Cheer | nein (`include_anonymous: false`) | zählt schon heute nicht (`cheer_anon`) |

Gebucht wird beim Einreihen in die Queue, nicht beim Abspielen - ob du einen Eintrag
überspringst (`/skip`), ändert am Guthaben nichts.

`!tts <text>` bucht dagegen wie `!companion <text>` genau `min_bits` vom Guthaben ab - und
nur, wenn genug da ist. Sonst: eine Chat-Antwort mit aktuellem Guthaben, nichts abgebucht,
nichts in der Queue (wie `needs_bits` beim Companion).

Umsetzung: `features/companion` bekommt eine neue Capability (z.B. `BITS_LEDGER` in
`core/feature.py`) mit `balance(key, user_name)` und `spend(key, amount)`, die auf die
bestehenden `_bits_total()`/`_spent_for()`/`_spend()` gehen. Das TTS-Feature listet sie als
`optional` - ohne geladenes Companion-Feature wird beim Cheer nichts gebucht und `!tts
<text>` antwortet nur mit der Regel. Schlüssel ist derselbe wie in `CompanionFeature._key`:
`twitch:<user_id>` (aus dem Cheer-Payload bzw. der Chat-Nachricht).

## Befehle

### Für Zuschauer (Chat)

| Befehl | Was er tut |
|---|---|
| `!tts` | Erklärt die Regel: "Cheer ab {min_bits} Bits mit Nachricht wird vorgelesen. Ohne Nachricht geht es aufs Guthaben - einlösbar mit !tts <text> oder !companion <text>. Dein Guthaben: {have} Bits." |
| `!tts <text>` | Kostet `min_bits` aus dem Guthaben; Text läuft durch Kappen + Moderation und kommt in dieselbe Queue wie Cheers. Zu wenig Guthaben → Antwort mit Stand, nichts abgebucht. Moderation schlägt an → Antwort "nicht vorgelesen ({label})", nichts abgebucht |

Für `!tts <text>` gilt dieselbe Ansage-Vorlage ohne Bits-Zahl ("{user} sagt: ..."), damit es
nicht nach einem frischen Cheer klingt. Umbenennbar/abschaltbar über `command_names` in
`tts.json`, wie bei den anderen Features.

Mods und du nutzen `!tts <text>` kostenlos (wie beim Companion) - nicht von der Moderation
ausgenommen, nur vom Preis. Du gibst ohnehin jede Ansage selbst frei.

### Für dich (Stream Deck, "Website"-Aktion, HTTP-GET)

| Adresse | Was sie tut |
|---|---|
| `http://127.0.0.1:4458/next?token=…` | Nächsten Eintrag aus der Queue synthetisieren und abspielen |
| `http://127.0.0.1:4458/skip?token=…` | Nächsten Eintrag verwerfen, ohne ihn abzuspielen |

Beide antworten mit einem kurzen Text (z.B. "ok, noch 2 in der Queue" / "Queue leer"), damit
der Knopf Erfolg/Fehler anzeigen kann.

## Architektur

```
Twitch EventSub                    Bot (Server, ein Container)                 OBS-Rechner
channel.cheer ──► platforms/twitch/features/tts/feature.py ◄── !tts <text> (Chat)
                    · !tts: Guthaben prüfen, min_bits abbuchen (BITS_LEDGER)
                    · anonym? → ignorieren (außer include_anonymous)
                    · Cheermotes entfernen → kein Text übrig? → ignorieren
                    · min_bits prüfen
                    · Text kappen (max_chars) VOR der Moderationsprüfung
                    · ModerationFeature.review() (fail-closed: Fehler = verwerfen)
                    · Bits im Companion-Ausgabenkonto buchen (BITS_LEDGER, optional)
                    · Text zusammenbauen ("{user} hat {bits} Bits dagelassen: ...")
                    · in Queue (deque) einreihen - NICHT sofort sprechen
                        │
        Stream-Deck-Knopf ── HTTP GET /next|/skip?token=TTS_TOKEN ──►  server.py
                    · Queue-Kopf poppen
                    · /next: Piper per subprocess synthetisieren (asyncio.to_thread)
                    · WAV als base64 in "speak"-Frame
                        │
                        └── WS-Broadcast ── SSH-Tunnel ──► client/tts.html
                                                             new Audio("data:audio/wav;base64,...").play()
```

**Ein Listener, ein Token, ein Port** (neu: `TTS_TOKEN`/`TTS_PORT`(4458)/`TTS_BIND`), wie bei
`features/overlay` - zwei Zugänge darauf:
- WebSocket (`/`) für die Browser-Source, empfängt `speak`-Frames
- Reiner HTTP-GET (`/next`, `/skip`) für den Stream-Deck-Knopf, via `process_request`-Hook
  direkt beantwortet (kein WS-Upgrade nötig) - gleicher Token-Check-Mechanismus wie in
  `features/overlay/server.py`

Warum `RAW_EVENT` statt `PLATFORM_EVENT`: `_publish_event()` in `platforms/twitch/bot.py`
trägt aktuell keinen Freitext. Statt den geteilten Event-Vertrag für einen Konsumenten zu
erweitern, hört das Feature direkt auf `events.RAW_EVENT` mit `event_type ==
"channel.cheer"` und liest `bits`/`user_id`/`user_name`/`message`/`is_anonymous` aus dem
rohen Payload. Keine Änderung an `bot.py` oder `core/events.py` nötig.

Warum unter `platforms/twitch/features/tts/` und nicht `features/tts/`: Bits/Cheer ist ein
reines Twitch-Konzept - wie `stream_sessions`/`raw_log` auch dort verortet.

Warum kein eigener Pod für Piper: kein dauerhafter Dienst, sondern ein CLI-Aufruf pro Text -
`subprocess` im bestehenden Container ist die kleinere, konsistentere Lösung (kein zweites
Quadlet-Unit, kein Container-zu-Container-Netzwerk).

## Sicherheit (Text als Daten, nie als Code)

1. **Moderation ist Pflicht, fail-closed.** `review()` in `try/except` - schlägt die Prüfung
   selbst fehl, wird verworfen statt ungeprüft durchgereicht.
2. **Länge wird gekappt, bevor irgendetwas mit dem Text passiert** - direkt nach dem Lesen
   aus `RAW_EVENT`, noch vor der Moderationsprüfung (schützt auch vor absichtlich
   pathologischem Text, der die Prüfung selbst belastet).
3. **Die Browser-Source interpretiert den Text nie als Markup.** Kein `innerHTML` mit
   Zuschauertext, kein SSML. Falls je eine visuelle Anzeige des gesprochenen Texts dazukommt:
   nur über `textContent`/`escapeHtml()`, wie in `chat.html` bereits gehandhabt.
4. **Kanal bleibt einseitig.** Der WS-Server sendet nur, liest von der Verbindung nichts
   Verwertbares - kein Rückweg von der Browser-Source zum Bot.
5. **Flood-Schutz** für die Browser-Source: Mindestabstand zwischen zwei `speak`-Frames plus
   Obergrenze wartender Utterances/Audios auf Client-Seite.

## Neue / geänderte Dateien

```
platforms/twitch/features/tts/
  feature.py     RAW_EVENT-Subscription, Filter, Moderation, Buchung, Queue, Piper, broadcast(), !tts [<text>]
  server.py      WS-Listener + HTTP-/next- und /skip-Endpoint (Token-Check wie overlay)
  config.py      TTS_TOKEN / TTS_PORT / TTS_BIND aus .env
  tts.json       min_bits, include_anonymous, max_chars, voice, voices, command_names,
                 texts (announce/announce_no_message/announce_command/help.tts/needs_bits/blocked)
  tools/
    voice_samples.py  Stimmen probehören (lokal, nicht auf dem Server)
  client/
    tts.html     unsichtbare Browser-Source, verbindet sich, spielt "speak"-Frames als Audio ab

core/feature.py              neue Capability BITS_LEDGER
features/companion/feature.py  provides BITS_LEDGER: balance(key, user_name), spend(key, amount)
features/companion/companion.json  help.companion-Text: Guthaben gilt auch für !tts
```

## Deployment-Ergänzungen (für später, Server ist read-only für mich)

- `.env`: `TTS_TOKEN`, `TTS_PORT=4458`, `TTS_BIND=0.0.0.0`
- `bugbot.container`: `PublishPort=127.0.0.1:4458:4458`
- `Dockerfile`: Piper-Binary + alle Stimmen aus `voices` (siehe unten) installieren
- Tunnel-Setup (`platforms/obs/client/setup-tunnel.sh`): dritter Forward `-L
  4458:127.0.0.1:4458`
- Stream Deck: zwei "Website"-Aktionen (System-Kategorie) → `/next` und `/skip`
- Neue Browser-Source in OBS: `client/tts.html?token=...`
- Info-Box unter dem Stream: die Regel von oben

## Werte (bestätigt 2026-10-11)

| Wert | Festgelegt | Grund |
|---|---|---|
| `min_bits` | 100 | ~1 $, üblich; gleich wie `min_bits_to_speak` beim Companion. Gilt für Cheer und `!tts <text>` |
| `include_anonymous` | `false` | anonym = nicht sperrbar; zählt auch nicht fürs Companion-Guthaben |
| `max_chars` | 255 | ~15-18 s Sprache |
| `voice` | auswählbar (Modell + optional Sprecher), Standard `de_DE-thorsten-medium` | siehe unten |
| Mods/Broadcaster bei `!tts <text>` | kostenlos | wie beim Companion |
| Queue-Anzeige im Bild | nein | Cheers stehen ohnehin im Chat |

### Stimme auswählbar

Engine bleibt **Piper** - komplett lokal im Bot-Container, nur CPU, kein Internet zur
Laufzeit. Alle deutschen Piper-Modelle (Stand `rhasspy/piper-voices/voices.json`, geprüft
2026-10-11):

| Modell | Qualität | Sprecher |
|---|---|---|
| `de_DE-thorsten-low` / `-medium` / `-high` | low / medium / high | 1 |
| `de_DE-thorsten_emotional-medium` | medium | 8 (Thorsten in verschiedenen Emotionen) |
| `de_DE-mls-medium` | medium | **236** (verschiedene Sprecher*innen) |
| `de_DE-kerstin-low` | low | 1 |
| `de_DE-ramona-low` | low | 1 |
| `de_DE-karlsson-low` | low | 1 |
| `de_DE-pavoque-low` | low | 1 |
| `de_DE-eva_k-x_low` | x_low | 1 |

Eine **Stimme = Modell + optional Sprecher**, geschrieben als `modell#sprecher`. Ohne `#`
spricht Sprecher 0. Piper bekommt das als `--model … --speaker <n>`. Die Sprecher-Nummer
wird nur bei Mehrsprecher-Modellen akzeptiert und gegen `num_speakers` aus der
`.onnx.json` des Modells geprüft.

In `tts.json`, per Hot-Reload ohne Neustart umstellbar (wie alle `*.json`-Werte, siehe
`core/runtime_config.py`):

```json
"voice": "de_DE-thorsten-medium",
"voices": ["de_DE-thorsten-low", "de_DE-thorsten-medium", "de_DE-thorsten-high",
           "de_DE-thorsten_emotional-medium", "de_DE-mls-medium",
           "de_DE-kerstin-low", "de_DE-ramona-low", "de_DE-karlsson-low",
           "de_DE-pavoque-low", "de_DE-eva_k-x_low"]
```

- `voices` = welche **Modelle** ins Image kommen (das Dockerfile liest dieselbe Liste).
  Standard: alle zehn - zusammen grob einige hundert MB im Image; wer Platz sparen will,
  streicht Modelle aus der Liste. Exakte Größen bei der Umsetzung prüfen.
- `voice` = welche Stimme gerade spricht, z.B. `"de_DE-mls-medium#42"` oder
  `"de_DE-thorsten_emotional-medium#3"`. Modell nicht in `voices` oder Sprecher-Nummer
  außerhalb des Bereichs → Warnung im Log, Fallback auf `de_DE-thorsten-medium`.
- Bewusst **nicht** durch Zuschauer wählbar - hält die Regel für sie einfach. Lässt sich
  später als `!tts <stimme>: <text>` nachrüsten, falls gewünscht.

#### Stimmen probehören

236 Sprecher*innen in `mls-medium` kann niemand aus einer Liste aussuchen. Dazu ein kleines
Skript `platforms/twitch/features/tts/tools/voice_samples.py`: rendert einen festen
Beispielsatz für jede Stimme/jeden Sprecher (oder einen Ausschnitt, z.B. `--model
de_DE-mls-medium --speakers 0-49`) als WAV in einen Ordner, Dateiname = der fertige
`voice`-Wert (`de_DE-mls-medium#42.wav`). Läuft lokal auf deinem Rechner (Piper per `pip
install piper-tts`), nicht auf dem Server.

#### Später, falls Piper zu robotisch klingt

**Pocket TTS** (Kyutai): ~100M Parameter, läuft auf CPU schneller als Echtzeit, kann seit
April 2026 Deutsch, klingt deutlich natürlicher und kann Stimmen aus einer WAV-Datei klonen.
Code MIT, Gewichte CC BY 4.0 (Klon-Repo laut einer Quelle zugangsbeschränkt). Größerer
Schritt (PyTorch im Image, mehr CPU-Last auf dem Server) - daher nicht im ersten Wurf, aber
die Engine-Anbindung in `feature.py` hinter einer kleinen `synthesize(text, voice) -> wav`-
Funktion kapseln, damit ein zweiter Engine-Typ später nur dort andockt.

Verworfen: XTTS-v2 und Voxtral TTS (nicht-kommerzielle Lizenz - Twitch-Bits sind
Einnahmen), Chatterbox Multilingual (braucht GPU), Kokoro (kein bestätigtes Deutsch).

## Nächster Schritt

Plan steht - als Nächstes implementiere ich die Dateien oben plus die
Dockerfile-/Container-/Tunnel-Ergänzungen.
