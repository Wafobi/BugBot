# Terminal — an alternate overlay skin

A second look for the browser sources in [`features/overlay/client/`](../), built as separate
OBS sources rather than one canvas — a retro-terminal aesthetic (VT323 display font, amber CRT
glow) instead of `bars.html`'s indigo/cyan "channel art" look. Not a `?theme=` of
`bars.html`/`chat.html` — the layout itself is different (chat as its own card, the vtuber
avatar frame as its own card), so it lives in its own folder instead.

```
chat.html      the chat card - wired to the same listener as ../chat.html
tubbi.html     the avatar frame - a transparent cut-out for an Avatar source underneath
video.html     a window frame for a video - fills its source, transparent middle
background.html  the ground under it all, themeable, gradients only
video_preview.html  the overview page for both - live, scaled iframes of the real
               pages plus the parameter tables. Browser only

themes/        alternate palettes for all of them at once - see themes/README.md
```

There used to be another page here, `overlay.html` (a letterbox-bars-plus-clock chrome, with a
last-follower/sub/raid line added later) — removed again once it turned out `chat.html` and
`tubbi.html` were the only two of the game scene actually in use, stacked over the real,
unrestyled `../bars.html` for the stat bars. If a from-scratch letterbox chrome is ever wanted
again, `../bars.html` with `?only=` is worth trying first rather than rebuilding one here.

Grown out of a one-off design for a specific channel (`#wafobitv`, an avatar source named
"Avatar", a 290×250 frame sized for a particular vtuber setup) — treat it as a worked example to
copy and adapt rather than a second shipped default. The parts worth generalising if this grows
another user: the channel name and avatar frame size are hard-coded in `chat.html`/`tubbi.html`
rather than parameters.

## Setup

Same listener, same token as `../bars.html` — nothing new to open. Two separate **Browser**
sources, each `2560×1440`, position `(0,0)`:

```
file:///path/to/bugbot/features/overlay/client/terminal/chat.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/features/overlay/client/terminal/tubbi.html?token=YOUR_OVERLAY_TOKEN
```

Being two sources rather than one is deliberate, not incidental: each can be repositioned,
resized, or shown/hidden live with its own eye icon in the Sources dock — in particular
`chat.html`, which most nights you may want off entirely without touching a URL.

`tubbi.html` needs no real data (it's a transparent frame; the avatar itself is a separate OBS
source underneath it). `chat.html` opens a WebSocket to the same listener, same parameters as
[`../chat.html`](../chat.html) (`token=`, `host=`, `port=`, `source=`, `demo=1`).

`?theme=` works on both — see `themes/README.md`. `?t.<token>=` overrides any single token from
the `:root` block in each file, same convention as everywhere else in this repo.

## `video.html` and `background.html` — a frame for a video, and its ground

Two browser sources, deliberately separate, to be arranged in OBS rather than in a URL:

```
background.html   the ground - bottom of the scene list, under everything
                   (the capture)   ── the video itself, a window or screen capture
video.html        the frame - above the capture, transparent in the middle
```

`video.html` is the window `tubbi.html` puts around the avatar, put around a video instead. It
fills its browser source, so where the frame sits and how big it is comes from OBS's own
transform — drop the source over the capture and scale it. The picture shows through the
transparent middle. No token, no connection, no data: the chat stays `chat.html`'s job and the
avatar `tubbi.html`'s, each its own source with its own eye icon.

If you would rather place the capture by numbers than by eye, the cut-out sits inside the source
by `1px` left and right, `1px + 40px` at the top (border plus titlebar) and `1px + 34px` at the
bottom (border plus footer). `?titlebar=0` and `?footer=0` drop either one, and the inset with
it.

| | |
|---|---|
| `theme=` | palette, same files as `chat.html`/`tubbi.html` — see `themes/README.md` |
| `title=` `now=` `badge=` | titlebar text, footer text, top-right badge (off unless set) |
| `titlebar=0` `footer=0` | drop either bar |
| `crt=1` | scanlines and a vignette over the picture too. Off by default |
| `demo=1` | colour bars in the cut-out, its pixel size in the footer — for setting up |
| `t.NAME=` | any single token: `?t.tb-h=56px`, `?t.frame-radius=0px` |

`background.html` is the ground for a scene with nothing else behind it — built from gradients
only, so it costs no file and recolours with the same `?theme=`.

| | |
|---|---|
| `pattern=` | `grid` (default), `dots`, `lines`, `plain` |
| `scanlines=0` `vignette=0` | drop either overlay |
| `theme=` | as above |
| `t.NAME=` | `?t.grid=96px`, `?t.pattern-alpha=.28`, `?t.ground=%2307100e`, `?t.glow-size=70%` |

Both take the same `?t.<token>=` convention as everything else here, and both are plain files —
open them in a browser to look at them.
