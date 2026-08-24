# Terminal — decorative OBS screens

Three static browser sources in the same retro-terminal look as
[`features/overlay/client/terminal/`](../../../../features/overlay/client/terminal/) (VT323
display font, amber CRT glow) — but these three open no connection to the bot at all. No
token, no port, nothing to configure beyond adding them as sources: they are pure decoration,
switched in OBS by hand (or by a Stream Deck / scene transition) whenever the scene calls for
them.

```
ad-break.html     small - a prompt line and a loading bar, for during an ad break
stream-boot.html  a full-screen "booting" sequence with a matrix-rain background and a
                   synthesized terminal sound, for right before going live
stream-pause.html a "be right back" screen with a countdown-style progress bar

themes/           alternate palettes for all three at once - see themes/README.md
```

Every animated value in them (the loading percentage, the log lines, the countdown) is
randomized/simulated in the page's own script — there is nothing behind these worth being real,
an ad break's actual remaining time is not something OBS or the bot exposes.

## Setup

Add each as its own **Browser** source, sized to taste (`ad-break.html` is a small floating
card, not a full canvas; `stream-boot.html`/`stream-pause.html` are meant full-screen), URL:

```
file:///path/to/bugbot/platforms/obs/client/terminal/ad-break.html
file:///path/to/bugbot/platforms/obs/client/terminal/stream-boot.html
file:///path/to/bugbot/platforms/obs/client/terminal/stream-pause.html
```

`?theme=` recolours all three together — see `themes/README.md`.
