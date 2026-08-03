# Moderation

Moderation is a feature (capability `MODERATION`), and the only one used by **pull**: the
platform needs a verdict before it can go on with the message, so a fire-and-forget event won't
do.

The split is deliberate. The feature decides the *consequence*; the platform only carries it
out, because only it knows how to delete and mute on its own service. The escalation logic used
to sit word-for-word in both platform modules and now exists exactly once.

## The flow

```
message arrives
   │
   ├─ privileged (broadcaster / moderator / admin)?  → no verdict, done
   │
   ├─ build settings:  code defaults  →  moderation.json  →  the platform's "moderation" section
   │
   ├─ run the filters in order, first hit wins
   │
   ├─ no hit?  → no verdict, done
   │
   └─ hit → count this user's violations in the window
            → Verdict(delete=True, timeout_seconds = 0 or the configured length)
                → the platform deletes, and times out if the verdict says so
```

The exemption for privileged users lives in the feature, not in each platform — one rule, one
place.

## The filters

Checked in this order; the first hit wins and nothing after it runs.

| # | Filter | Trips when | Subscribers |
|---|---|---|---|
| 1 | **Banned words** | the text matches the word list | applies |
| 2 | **Link spam** | a link to a domain not on `allowed_link_domains` | applies |
| 3 | **Excessive caps** | ≥ `caps_min_length` letters and the uppercase share ≥ `caps_ratio_threshold` | exempt |
| 4 | **Symbol spam** | ≥ `symbol_min_length` characters and the non-alphanumeric share ≥ `symbol_ratio_threshold` | exempt |
| 5 | **Emote/word spam** | ≥ `emote_spam_min_tokens` tokens and one of them repeats ≥ `emote_spam_min_repeats` times | exempt |

Subscribers are passed `relaxed=True`, which skips the three pure spam heuristics. Banned words
and links still apply to everyone — the relaxation is about false positives on enthusiasm, not
about a licence.

Each hit maps to a reason, and each reason to a label shown in chat and the log:

| Reason | Default label |
|---|---|
| `banned_word` | unerlaubtes Wort |
| `link_spam` | nicht erlaubter Link |
| `excessive_caps` | zu viele Großbuchstaben |
| `symbol_spam` | Symbol-Spam |
| `emote_spam` | Wort-/Emote-Spam |

The labels are texts like any other — override them in `moderation.json` under `texts` with the
keys `reason.banned_word` and so on.

## Escalation

Every hit deletes the message. What decides a timeout is *how many* violations that user has
racked up inside a rolling window:

```
violations in the last `violation_window_minutes` (default 10)
   < timeout_threshold (default 3)  → delete only
   ≥ timeout_threshold              → delete + timeout for timeout_duration_seconds (default 60)
```

Two details worth knowing:

- **Violations are counted per user *and* platform**, keyed by user id where there is one. The id
  is stabler than the name across renames and spelling variants.
- **The counter lives in RAM only.** A restart forgives old violations by design — nobody should
  be carrying a grudge from before a deploy.

The window is rolling, not a fixed bucket: each new violation drops everything older than the
window before counting.

## The word list

Three inputs, combined at read time:

| Source | Key |
|---|---|
| the curated base list in `filters.py` | `banned_words.use_builtin_list` (default `true`) |
| additions | `banned_words.extra` |
| removals from the base list | `banned_words.remove` |

The base list is the original curation merged with the public
[LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words)
list (German + English), deduplicated. Two LDNOOBW entries are deliberately excluded: the phrases
"how to kill" and "how to murder" — in a gaming chat ("how to kill the boss") those are
guaranteed false positives rather than abuse.

`banned_words.remove` exists because that is the honest way to disagree with a curated list. Turn
the whole thing off with `use_builtin_list: false` and supply your own via `extra`.

The pattern is compiled through an `lru_cache` keyed on the actual word list, so rebuilding the
settings per message costs nothing after the first.

## Per-platform strictness

`build_settings()` layers three sources, each beating the one before:

1. `DEFAULT_MODERATION_SETTINGS` in `filters.py`
2. the `settings` section of `moderation.json` — the common ground for all platforms
3. the `moderation` section of the calling platform's own JSON

So Twitch can run stricter than Discord on emote spam while sharing everything else, and the
platform hands its section in **fresh on every message**, which is what keeps the hot-reload
working: edit `twitch.json`, save, and the next message is judged by the new number.

The per-platform section also takes `extra_banned_words` for words that should only be banned
there.

## Tuning notes

- **`caps_ratio_threshold`** at 0.7 with `caps_min_length` 10 means "at least ten letters and
  seven in ten uppercase". Lowering the length is what catches short shouting; lowering the ratio
  catches mixed-case shouting and produces more false positives.
- **`emote_spam_min_repeats`** counts repeats of the single most frequent token, so it catches
  `LUL LUL LUL LUL LUL LUL` regardless of what else is in the message.
- **`allowed_link_domains`** is a substring check against the lowercased message, so `twitch.tv`
  also permits `clips.twitch.tv`.
- **`timeout_duration_seconds`** is a flat length, not a doubling ladder. If you want escalating
  lengths, that is a change in `ModerationFeature.review()`.

## What gets recorded

Every action lands on the bus as `MOD_ACTION` with `platform`, `user_name`, `reason` and
`action` (`delete`, `timeout`, `ban`, `warn`, `unban`) — including actions taken by a *human*
moderator, which Twitch reports over EventSub. The `stats` feature writes them into
`moderation_actions`; see [Database](database.md).

## See also

- [Configuration](configuration.md#featuresmoderationmoderationjson) — every key and its default
- [Extending it](extending.md#verdicts) — the `Verdict` contract, if you want a second
  moderation feature
