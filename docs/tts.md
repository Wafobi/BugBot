# TTS

Cheers with a message, and `!tts <text>`, read out loud on stream — one at a time, whenever the
streamer presses a Stream Deck button. Lives in
[`platforms/twitch/features/tts`](../platforms/twitch/features/tts), because bits are a Twitch
concept.

## The rule for viewers

> **Cheer of 100+ bits with a message → read out.**
> **Cheer without a message → balance, spendable later on `!tts <text>` or `!companion <text>`.**

| Case | Read out? | Balance |
|---|---|---|
| Cheer ≥ `min_bits` with a message | yes (queued) | the whole cheer is booked as spent — the bits count once |
| … message dropped by moderation | bits announcement only | still booked |
| Cheer ≥ `min_bits` without a message | no | grows as before |
| Cheer < `min_bits` | no | grows as before |
| Anonymous cheer | no (`include_anonymous: false`) | never counted anyway |
| `!tts <text>` | yes (queued) | `min_bits` spent; free for mods/broadcaster |
| Resub with a message | yes (queued, free) | untouched — the sub is the payment (`resub_messages: false` turns it off) |

"Without a message" means nothing is left once cheermotes (`Cheer100`, `Kappa50`, …) are
stripped. The balance is the companion's (see [Companion](companion.md)) — one pot, offered to
other features as the `BITS_LEDGER` capability.

## Commands

| Where | What |
|---|---|
| `!tts` | explains the rule, shows the caller's balance |
| `!tts <text>` | queues `<text>` for `min_bits` from the balance |
| `GET :4457/tts/next?token=<TTS_TOKEN>` | synthesizes and plays the next queued entry |
| `GET :4457/tts/skip?token=<TTS_TOKEN>` | drops the next queued entry unplayed |

Both HTTP routes answer with one line of text (e.g. `ok - noch 2 in der Warteschlange`). On the
Stream Deck: System → **Website**, with *GET request in background* ticked so no browser window
opens. Releasing deliberately does not go through chat.

### Stream Deck with Bitfocus Companion

Connection **Generic: HTTP Requests**, Base URL `http://127.0.0.1:4457`. Buttons: action `GET`,
URI `/tts/next?token=<TTS_TOKEN>` (and `/tts/skip?...`).

Queue length on a button: `GET /tts/count?token=<TTS_TOKEN>` answers the bare number (`3`).
Create a custom variable `tts_count`, add a trigger (event *Time interval*, 2 s) with the action
`GET /tts/count?token=…` and *JSON Response Data Variable* = `tts_count`, then put
`$(custom:tts_count)` into a button's text — optionally with an internal *Check boolean
expression* feedback `$(custom:tts_count) > 0` to colour it while something waits.

### OBS dock

`http://127.0.0.1:4457/tts/dock?token=<TTS_TOKEN>` as a custom browser dock (Docks → Custom
Browser Docks): the queue with each waiting sentence, ▶ play and ⏭ skip, and whether a player is
connected.

## Where it runs

Everything happens on the server, inside the bot's container: Piper makes the speech there.
Nothing TTS-related runs on the OBS machine except playing the finished audio.

No port of its own — it rides on the [overlay](overlay.md) listener (`4457`), which the SSH
tunnel already forwards:

- the browser source `client/tts.html` connects like `bars.html`/`chat.html` do, with
  `OVERLAY_TOKEN` and `?role=tts` — only connections with that role get the audio frames;
- the two buttons are routes on that listener, guarded by `TTS_TOKEN` — *not* by
  `OVERLAY_TOKEN`, which sits in the scene collection and only ever grants reading.

## Setup

1. `.env` on the server: `TTS_TOKEN` (fresh, `openssl rand -hex 32`). `OVERLAY_TOKEN` must be set
   too (it is, if the overlays already work).
2. `git pull && ./update.sh` — rebuilds the image (downloads Piper and every model in
   `voices.txt`, takes a while the first time) and refreshes the Quadlet unit (`tts.json` mount).
3. OBS: browser source with URL
   `file:///…/platforms/twitch/features/tts/client/tts.html?token=<OVERLAY_TOKEN>` (not as
   "local file", or the query string is lost), *Control audio via OBS* on, *Shutdown source when
   not visible* off.
4. Stream Deck: two Website buttons, `http://127.0.0.1:4457/tts/next?token=<TTS_TOKEN>` and
   `…/tts/skip?token=<TTS_TOKEN>`.

`/tts/next` with no TTS browser source connected answers `503` and leaves the queue alone.

## Statistics

Every queued entry and every played one is reported as a `PLATFORM_EVENT` (`tts_queued`,
`tts_played`), so [`features/stats`](../features/stats) counts them per stream like follows or
raids. They show up — only for streams that had any — as **🔊 TTS: `played` von `queued`
vorgelesen** in `!streamstats` and the Discord stream report, and as a line in the end screen's
recap (`platforms/obs/client/terminal/stream-end.html`).

## Voices

Engine is [Piper](https://github.com/OHF-Voice/piper1-gpl), run locally inside the container as
a separate process per utterance — CPU only, no network at runtime. The text goes in through
stdin, never as an argument.

`voice` in `tts.json` is `<model>` or `<model>#<speaker>` and is hot-reloaded:

| Model | Speakers |
|---|---|
| `de_DE-thorsten-low` / `-medium` / `-high` | 1 |
| `de_DE-thorsten_emotional-medium` | 8 |
| `de_DE-mls-medium` | 236 |
| `de_DE-kerstin-low`, `-ramona-low`, `-karlsson-low`, `-pavoque-low` | 1 each |
| `de_DE-eva_k-x_low` | 1 |

Append `@<effect>` for a machine voice — applied to the finished audio on the server
(`piper_runner.py`, numpy), mixable with natural voices in the pool:

| Effect | Sound |
|---|---|
| `@robot` | ring modulation, flat intonation — the classic sci-fi robot |
| `@retro` | 8-bit home computer / Speak & Spell — ~6 kHz, coarse resolution |
| `@funk` | radio / small terminal speaker — thin, slightly overdriven |

e.g. `"voice": ["de_DE-thorsten-medium", "de_DE-thorsten-medium@robot", "de_DE-mls-medium#42@retro"]`.

Which models exist in the image is decided by `voices.txt` (kept out of `tts.json` so switching
the voice does not invalidate the image's download layer). An unusable `voice` logs a warning
once and falls back to `de_DE-thorsten-medium`.

### Admin page

To pick voices by ear, build the pool and tune how it is spoken, open — through the tunnel —

    http://127.0.0.1:4457/tts/admin?token=<TTS_TOKEN>

Served by the bot itself ([`admin.py`](../platforms/twitch/features/tts/admin.py)), with the
server's Piper and the voices installed there: nothing runs on the OBS machine. Type a text
(optionally a name, to hear it as on stream), pick model and speaker (🎲 for a random one of the
236 mls speakers), listen. Add the good ones to the pool; **Pool speichern** and **Sprechweise
speichern** save `voice`/`voice_pick`/`speech` into `settings/tts.json` on the server — the
override layer on top of the repo's `tts.json` (see [Configuration](configuration.md#overrides)),
so `git pull` never conflicts. Effective from the next announcement, no restart.

## tts.json

| Key | Default | |
|---|---|---|
| `min_bits` | `100` | for cheers and `!tts <text>` |
| `include_anonymous` | `false` | |
| `max_chars` | `255` | text is capped before moderation sees it |
| `max_queue` | `50` | further cheers are neither queued nor booked |
| `voice` | `de_DE-thorsten-medium` | see above |
| `texts` | | everything said or spoken, including the HTTP replies |
