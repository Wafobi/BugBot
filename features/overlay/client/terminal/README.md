# Terminal — an alternate overlay skin

A second, complete look for the browser sources in [`features/overlay/client/`](../), built as
three separate OBS sources rather than one canvas — a retro-terminal aesthetic (VT323 display
font, amber CRT glow) instead of `bars.html`'s indigo/cyan "channel art" look. Not a `?theme=`
of `bars.html`/`chat.html` — the layout itself is different (letterbox + clock only, chat as its
own card, the vtuber avatar frame as its own card), so it lives in its own folder instead.

```
overlay.html   the letterbox bars + clock - stack this one at the bottom
chat.html      the chat card - wired to the same listener as ../chat.html
tubbi.html     the avatar frame - a transparent cut-out for an Avatar source underneath

themes/        alternate palettes for all three at once - see themes/README.md
```

Grown out of a one-off design for a specific channel (`#wafobitv`, an avatar source named
"Avatar", a 220×190 frame sized for a particular vtuber setup) — treat it as a worked example to
copy and adapt rather than a second shipped default. The parts worth generalising if this grows
another user: the channel name and avatar frame size are hard-coded in `chat.html`/`tubbi.html`
rather than parameters.

## Setup

Same listener, same token as `../bars.html` — nothing new to open. Three separate **Browser**
sources, each `2560×1440`, position `(0,0)`, stacked in the order above (`overlay.html` lowest,
`tubbi.html`/`chat.html` on top):

```
file:///path/to/bugbot/features/overlay/client/terminal/overlay.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/features/overlay/client/terminal/chat.html?token=YOUR_OVERLAY_TOKEN
file:///path/to/bugbot/features/overlay/client/terminal/tubbi.html?token=YOUR_OVERLAY_TOKEN
```

Being three sources rather than one is deliberate, not incidental: each can be repositioned,
resized, or shown/hidden live with its own eye icon in the Sources dock — in particular
`chat.html`, which most nights you may want off entirely without touching a URL.

`chat.html` and `tubbi.html` need no real data of their own to render (`tubbi.html` is a
transparent frame; the avatar itself is a separate OBS source underneath it) — only `chat.html`
opens a WebSocket, same parameters as [`../chat.html`](../chat.html) (`token=`, `host=`, `port=`,
`source=`, `demo=1`). `overlay.html`'s clock is local wall-clock time, not fed by the bot.

`?theme=` works on all three — see `themes/README.md`. `?t.<token>=` overrides any single token
from the `:root` block in each file, same convention as everywhere else in this repo.
