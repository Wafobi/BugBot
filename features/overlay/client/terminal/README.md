# Terminal — an alternate overlay skin

A second look for the browser sources in [`features/overlay/client/`](../), built as separate
OBS sources rather than one canvas — a retro-terminal aesthetic (VT323 display font, amber CRT
glow) instead of `bars.html`'s indigo/cyan "channel art" look. Not a `?theme=` of
`bars.html`/`chat.html` — the layout itself is different (chat as its own card, the vtuber
avatar frame as its own card), so it lives in its own folder instead.

```
chat.html      the chat card - wired to the same listener as ../chat.html
tubbi.html     the avatar frame - a transparent cut-out for an Avatar source underneath

themes/        alternate palettes for both at once - see themes/README.md
```

There used to be a third page here, `overlay.html` (a letterbox-bars-plus-clock chrome, with a
last-follower/sub/raid line added later) — removed again once it turned out `chat.html` and
`tubbi.html` were the only two actually in use, stacked over the real, unrestyled `../bars.html`
for the stat bars. If a from-scratch letterbox chrome is ever wanted again, `../bars.html` with
`?only=` is worth trying first rather than rebuilding one here.

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
