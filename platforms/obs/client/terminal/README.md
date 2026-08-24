# Terminal — decorative OBS screens

Three browser sources in the same retro-terminal look as
[`features/overlay/client/terminal/`](../../../../features/overlay/client/terminal/) (VT323
display font, amber CRT glow), switched in OBS by hand (or by a Stream Deck / scene transition)
whenever the scene calls for them.

```
ad-break.html     small - a prompt line and a loading bar tracking the real ad break
                   (features/overlay's ad_break_started_at/ad_break_seconds)
stream-boot.html  a full-screen "booting" sequence - matrix-rain background always running,
                   but the log lines/chime/progress bar wait for the real "live", see below
stream-pause.html a "be right back" screen with a simulated countdown-style progress bar -
                   purely decorative, no connection

themes/           alternate palettes for all three at once - see themes/README.md
```

`ad-break.html` and `stream-boot.html` open a WebSocket to the same listener as
[`../../../../features/overlay/client/bars.html`](../../../../features/overlay/client/bars.html)
— same `OVERLAY_TOKEN`, same port. `stream-pause.html` needs neither: every animated value in it
is simulated in the page's own script, there is nothing behind it worth being real.

`stream-boot.html`'s boot sequence (log lines, chime/blips, progress bar, ambient glitches)
deliberately does not start the moment the source loads: it sits in a quiet standby look - rain,
CRT flicker, a "STANDBY" badge - until the bot reports the stream as live. Three seconds later
the command after the shell prompt types itself out (keystroke sounds, the occasional typo
backspaced and corrected), and as soon as that finishes the badge flips to "LIVE" and everything
else kicks off at once. See the boot gate comment at the top of the file.

Real chat mixes into the log alongside the simulated boot lines once it has started (a pink
"CHAT" tag, `?source=` to filter by platform) - the same messages `../../../../features/overlay/client/chat.html`
shows, just with fake systemd-style output for company.

## Setup

Add each as its own **Browser** source, sized to taste (`ad-break.html` is a small floating
card, not a full canvas; `stream-boot.html`/`stream-pause.html` are meant full-screen), URL:

```
file:///path/to/bugbot/platforms/obs/client/terminal/ad-break.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/platforms/obs/client/terminal/stream-boot.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/platforms/obs/client/terminal/stream-pause.html
```

Parameters on `ad-break.html`/`stream-boot.html`: `token=`, `host=` (default `127.0.0.1`),
`port=` (default `4457`), `demo=1` (simulated/sample data instead of a connection - on
`stream-boot.html` this fires the whole sequence at once rather than waiting ten seconds, for
judging the layout without a bot running). `?theme=` recolours all three together — see
`themes/README.md`.
