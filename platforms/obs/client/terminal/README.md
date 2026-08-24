# Terminal — decorative OBS screens

Four browser sources in the same retro-terminal look as
[`features/overlay/client/terminal/`](../../../../features/overlay/client/terminal/) (VT323
display font, amber CRT glow), switched in OBS by hand (or by a Stream Deck / scene transition)
whenever the scene calls for them.

```
ad-break.html     small - a prompt line and a loading bar tracking the real ad break
                   (features/overlay's ad_break_started_at/ad_break_seconds)
stream-boot.html  a full-screen "booting" sequence - matrix-rain background always running,
                   but the log lines/chime/progress bar wait for the real "live", see below
stream-end.html   a full-screen end-of-stream recap - real follows/subs/bits/raids/viewers
                   for the stream that just ended, no progress bar, see below
stream-pause.html a "be right back" screen with a simulated countdown-style progress bar -
                   purely decorative, no connection

themes/           alternate palettes for all four at once - see themes/README.md
```

`ad-break.html`, `stream-boot.html` and `stream-end.html` open a WebSocket to the same listener
as [`../../../../features/overlay/client/bars.html`](../../../../features/overlay/client/bars.html)
— same `OVERLAY_TOKEN`, same port. `stream-pause.html` needs neither: every animated value in it
is simulated in the page's own script, there is nothing behind it worth being real.

`stream-boot.html`'s boot sequence (log lines, chime/blips, progress bar, ambient glitches)
deliberately does not start the moment the source loads: it sits in a quiet standby look - rain,
CRT flicker, a "STANDBY" badge - until the bot reports the stream as live. Three seconds later
the command after the shell prompt types itself out (keystroke sounds, the occasional typo
backspaced and corrected), and as soon as that finishes the badge flips to "LIVE" and everything
else kicks off at once. See the boot gate comment at the top of the file. It can also mix real
chat into the log next to the simulated lines (a pink "CHAT" tag, `?source=` to filter by
platform) - currently switched off (`CHAT_IN_LOG_ENABLED` in the file) pending another look at
the pacing between the two.

`stream-end.html` is the opposite direction: no wait-for-live gate to switch to it after -
whether the bot reports the stream live is already the first thing every connection learns, so
that alone decides what plays. Connecting is also what triggers `features/overlay` to re-read
`stream_recap` fresh from `STATS` for the running session (not on `STREAM_END`/`SESSION_ENDED`
- by the time those fire the stream has already stopped, and neither the audience nor the VOD
would ever see a recap that only appeared then; and not on every follow/sub/raid/cheer either -
those only update `STATS` itself, there is nothing for the overlay to do until someone actually
connects to look - see `snapshot()`/`_refresh_recap` in `features/overlay/feature.py`). Live:
it waits for that real `stream_recap`. Not live - whether that's genuinely offline or this
source just being set up/previewed while nothing streams - a sample recap plays instead, so
switching to this scene never finds an empty card. Either way: a typed command (same
keystrokes-and-typos treatment as the boot prompt), then the totals (follows, subs/resubs,
gift subs, bits, raids, chat activity), then every individual follow/sub/raid/cheer by name,
oldest first - same typewriter/plotter-sound
line-by-line reveal as the boot log. Meant to run alongside the Tubbi
(`../../../features/overlay/client/terminal/tubbi.html`) and chat
(`../../../features/overlay/client/terminal/chat.html`) sources in the same scene, as their own
sources — not reproduced inside this page.

## Setup

Add each as its own **Browser** source, sized to taste (`ad-break.html` is a small floating
card, not a full canvas; the other three are meant full-screen), URL:

```
file:///path/to/bugbot/platforms/obs/client/terminal/ad-break.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/platforms/obs/client/terminal/stream-boot.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/platforms/obs/client/terminal/stream-end.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/platforms/obs/client/terminal/stream-pause.html
```

Parameters on `ad-break.html`/`stream-boot.html`/`stream-end.html`: `token=`, `host=` (default
`127.0.0.1`), `port=` (default `4457`), `demo=1` (simulated/sample data instead of a connection
- on `stream-boot.html` this fires the whole sequence at once rather than waiting ten seconds,
for judging the layout without a bot running). `?theme=` recolours all four together — see
`themes/README.md`.
