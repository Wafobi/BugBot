# Cozy — ein gemütlicher Skin mit 10 Themes

Ein eigener Skin neben `../bars.html`/`../chat.html`, `../terminal/` und den Terminal-Szenen in
`platforms/obs/client/terminal/`. Die bleiben alle unverändert. Er nutzt denselben Listener,
denselben `OVERLAY_TOKEN` und dieselben Frames (`state`/`patch`/`history`/`message`/`clear`),
auf dem Server ist also nichts zu ändern.

Jede Seite gibt es einmal, das Aussehen kommt aus `?theme=`:

```
overlay.html    über dem Spiel: Spielinfo oben links, Danke-Zeile unten links, Chat unten rechts
avatar.html     Rahmen mit durchsichtiger Mitte für die Avatar-/Kamera-Quelle (wie terminal/tubbi.html)
start.html      "Gleich geht's los" - wechselt auf "Es geht los!", sobald der Bot live meldet
pause.html      "Bin gleich wieder da" mit Pausenzeit
end.html        "Danke fürs Vorbeischauen" mit Recap (stream_recap, wie terminal/stream-end.html)
ad-break.html   kleine Karte, Balken folgt der echten Werbepause (wie terminal/ad-break.html)
background.html der Szenen-Hintergrund mit Partikeln, ganz unten (wie terminal/background.html)

preview.html    alle Themes und alle Szenen im Demo-Modus - nur für den Browser
cozy.css        Grundlayout, cozy.js gemeinsame Logik (Theme laden, Motive, Partikel, Chat, Verbindung)
themes/         die 10 Themes, je eine Datei
```

| `theme=` | Name | Kurz |
|---|---|---|
| `kamin` (Standard) | Kaminabend | Creme-Papier mit Steppnaht, Kerze, Glut steigt auf |
| `teegarten` | Teegarten | Salbei, Pastell-Sprechblasen, Blätter |
| `regenfenster` | Regenfenster | Lo-Fi-Nacht, Milchglas, Lampe, Regen |
| `herbstlaub` | Herbstlaub | Kürbis und Rostrot, Karo-Hintergrund, fallendes Laub |
| `winterhuette` | Winterhütte | Holzrahmen mit Schneekappe, Schnee |
| `kaffeehaus` | Kaffeehaus | Mokka, dicke Konturen, Dampf |
| `sternennacht` | Sternennacht | Mitternachtslila, Mond, Sterne funkeln |
| `gluehwuermchen` | Glühwürmchen | Moos und Rinde, schwebende Leuchtpunkte |
| `bibliothek` | Bibliothek | Pergament, Serifen, Goldkante, Staub im Licht |
| `kirschbluete` | Kirschblüte | Rosa und Creme, Blütenblätter |

## Einrichten

Jede Seite ist eine eigene **Browser**-Quelle, damit jede ihr eigenes Auge im Quellen-Dock hat:

```
file:///path/to/bugbot/features/overlay/client/cozy/overlay.html?theme=kamin&token=YOUR_OVERLAY_TOKEN
```

- `overlay.html`, `avatar.html`: 2560×1440 an (0,0), über Spiel bzw. Avatar-Quelle. Transparent bis auf die Karten.
- `start.html`, `pause.html`, `end.html`: 2560×1440 als eigene Szene, deckend.
  Bei `pause.html` in OBS „Aktualisieren, wenn Szene aktiv wird“ anhaken, sonst zählt die Pausenzeit weiter.
- `ad-break.html`: zum Beispiel 1000×300, transparent. Die Karte füllt die Quelle.

## Parameter

| Parameter | Wo | Bedeutung |
|---|---|---|
| `theme=` | alle | siehe Tabelle oben |
| `token=` `host=` `port=` | alle außer avatar | wie bei `../chat.html` (`127.0.0.1`, `4457`) |
| `source=` | mit Chat | nur diese Plattform(en) im Chat, z. B. `source=twitch` |
| `demo=1` | alle | Beispieldaten, keine Verbindung |
| `ambient=0` | alle | keine Partikel (Glut, Regen, Schnee …) |
| `bars=` | overlay | Letterbox-Balken oben/unten in dieser Höhe (px), gebaut wie in `../bars.html`: durchscheinender Verlauf, Muster, laufende Zweifarb-Linie. Aussehen über die `--bars-*`-Tokens, `t.bars-bg=black` macht sie schlicht schwarz |
| `tab=x,w,h` | overlay | mit `bars=`: der obere Balken bekommt eine Lasche nach unten (ab x, w breit, bis y=h), als Grund unter dem Avatar. Fester Teil der Balken |
| `off=` / `only=` | overlay | Teile ausblenden bzw. nur diese zeigen: `info`, `thanks`, `chat` |
| `side=left` | mit Chat | Chat-Karte links statt rechts (bei `chat=extern` rückt der Inhalt nach rechts) |
| `chat=0` | start, pause, end | ohne Chat-Karte, der Inhalt rückt in die Mitte |
| `chat=extern` | start, pause, end | keine eigene Chat-Karte, aber Platz dafür - wenn eine separate Chat-Quelle darüber liegt |
| `at=top` | ad-break | volle 2560×1440-Quelle, Karte oben mittig (Höhe über `t.edge-y=`) |
| `text=` `sub=` | pause | eigene Überschrift / Unterzeile |
| `x=` `y=` `w=` `h=` | avatar | Position und Größe des Ausschnitts in px (Standard oben rechts, 560×316) |
| `round=1` `name=` | avatar | runder Ausschnitt; Namensschild darunter |
| `--hero-line: on` | Theme-Token | die laufende Zweifarb-Linie der Balken als Rand um die Karten von Start, Pause und Ende (Kamin: an) |
| `t.NAME=` | alle | ein Token aus `:root` überschreiben, z. B. `t.card-radius=12px`. Für das Layout: `edge-x`, `edge-y` (Abstand zum Rand), `chat-w`, `chat-h`, `hero-x` (Mitte der Szenen-Karte) |

## So läuft es in OBS (Szenensammlung „Wafobi 2K“)

Ein eigenständiger Chat ist `overlay.html?only=chat`. Damit bleibt er eine eigene Quelle mit
eigenem Auge, wie vorher `terminal/chat.html`:

```
Background    background.html?theme=kamin
Overlay       overlay.html?theme=kamin&token=…&off=info,thanks,chat&bars=184&tab=2094,322,282   (Balken mit Lasche unter dem Avatar)
Chat          overlay.html?theme=kamin&token=…&only=chat&side=left&source=twitch&t.edge-y=24px&t.chat-w=560px&t.chat-h=520px
Avatar-Frame  avatar.html?theme=kamin&x=2110&y=40&w=290&h=226     (hinter der Avatar-Quelle bei 1862,-228; in PC ausgeblendet, dort trägt die Lasche)
Boot          start.html?theme=kamin&token=…&source=twitch
Pause         pause.html?theme=kamin&chat=extern&side=left&t.hero-x=1360px&t.chat-w=560px&t.chat-h=520px
Werbepause    ad-break.html?theme=kamin&token=…&at=top&t.edge-y=210px
Stream End    end.html?theme=kamin&token=…&chat=extern&side=left&t.hero-x=1360px&t.chat-w=560px&t.chat-h=520px
```

## Ein Theme ändern oder hinzufügen

Ein Theme ist eine CSS-Datei, die Tokens aus `cozy.css` überschreibt: Farben, Kartenform,
`--ambient` (`embers`, `rain`, `snow`, `leaves`, `petals`, `steam`, `stars`, `fireflies`, `dust`,
`none`), `--motif` (`candle`, `teacup`, `lamp`, `leaf`, `cabin`, `mug`, `moon`, `jar`, `book`,
`blossom`) und alle Texte als `--label-*`. Wo ein Theme eine andere Form will (Sprechblasen im
Teegarten), stehen darunter ein paar eigene Regeln. Ein neues Theme kommt zusätzlich in
`THEMES` in `cozy.js`, damit `preview.html` es zeigt.

Schriften: Es werden keine externen Schriften geladen. Zuerst werden `Nunito`/`Quicksand` bzw.
`Lora` versucht, sonst greift die Systemschrift.
