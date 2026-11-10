# Themes

`?theme=<name>` loads `<name>.css` from this folder. All three pages in `terminal/`
(`chat.html`, `tubbi.html`, `video.html`) build on the same four tokens for everything that
isn't plain ink/background —
`--amber`, `--amber-soft`, `--amber-dim`, `--glow` — so one theme file recolours titlebars,
badges, the connection dot and the glow across all of them at once, as long as the same
`?theme=` is on every source URL.

Shipped:

| `theme=` | Look |
|---|---|
| *(none)* | the shipped look — amber CRT |
| `matrix` | green phosphor terminal |
| `cyan` | cool cyan, tying back to bugbot's own default accent (`bars.html`'s `--accent`) |
| `aurora` | cyan and magenta together, echoing the hairline on `bars.html`'s own default look |
| `glut` | the terminal with the terminal taken out: same layout and positions, but warm honey/coral/cocoa, a rounded sans instead of monospace, plain rounded cards (no dots, file names or PIDs), no prompts or cursors, no rain, glitches, scanlines or ASCII art; boot loses its fake log, end keeps the recap without timestamps/levels. Restyles and hides elements beyond colour; pairs with `bars.html?theme=sunset` |
| `kaminfeuer` | `glut` mixed with the cozy skin's kamin look: imports glut.css, adds the running ember/gold rim around every card, a flickering flame before each heading, serif headings, and rising embers plus a firelight glow in the start/end scenes. Pairs with `bars.html?theme=sunset` |
| `polarlicht` | exactly `kaminfeuer` (imports it), only recoloured to the aurora palette: cyan and magenta on night blue - rim cyan<->magenta, cold cyan flame, cyan sparks, an aurora glow instead of firelight. Pairs with `bars.html?theme=polarlicht` |

Precedence, low to high: the shipped `:root` in each page → `?theme=` (this folder) →
`?t.<token>=` (always wins — inline style beats any stylesheet regardless of load order).

Adding one: copy an existing file, keep the token names, change the values.

### Chat window transparency (`glut`, `kaminfeuer`, `polarlicht`)

On the chat source's URL: `&t.chat-alpha=0.6` sets the window's opacity (`1` solid, the default,
`0` fully see-through), `&t.chat-blur=6px` blurs the game behind it. The text carries a soft
shadow so it stays readable at any setting.

**Switching it live (Stream Deck):** `chat.html` listens for an obs-browser event
`wafobi-chat-glass` and toggles between solid and "glass" (`--chat-glass-alpha`, default `.45`,
and `--chat-glass-blur`, default `6px` - both settable per URL with `t.chat-glass-alpha=` /
`t.chat-glass-blur=`). It remembers the state across reloads. In Bitfocus Companion, OBS module,
action **Custom Command**: request type `CallVendorRequest`, request data
`{"vendorName":"obs-browser","requestType":"emit_event","requestData":{"event_name":"wafobi-chat-glass","event_data":{"mode":"toggle"}}}`
(`mode` can also be `glass` or `solid`). Don't also set `t.chat-alpha` on that URL - an inline
value beats the toggle.
