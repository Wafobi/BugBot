"""The companion feature: a small pond of per-chatter pets for a browser source in OBS.

A subscriber (or a mod/the broadcaster) who writes an accepted chat message gets a
companion, seeded from their name by default - the same DiceBear "sprouts" seed the
standalone vtubbi avatar uses, so a viewer's companion here and their own avatar (if they
run vtubbi themselves) look the same creature unless they have picked a custom one (see
!companion set below). Presence itself lives in RAM only, exactly like features/chat_panel's
history - it says who is around right now, not a record.

!companion <text> makes that person's companion talk. !companion set <hash> changes what it
looks like - "hash" is just DiceBear's seed string, so any text works, but the appeal is
finding one that renders well and keeping it. !vtubbi is a separate command and does not
take an argument - it just posts a link to the project behind the companions
(github.com/Wafobi/vtubbi).

Three gates stand in front of what !companion actually does:

  - a companion exists at all only for a subscriber, a mod or the broadcaster
    (message.is_subscriber / is_privileged) - everyone else's chat still works, they simply
    have nothing on screen to talk through.
  - !companion <text> goes through the MODERATION feature first, same as any chat message -
    but checked again here, deliberately, because a mod's or the broadcaster's own message
    never reaches moderation.review() at all (see features/moderation/feature.py:review),
    and their companion's speech bubble is exactly as public as anyone else's.
  - both !companion <text> and !companion set spend from one shared balance: what someone
    has cheered all-time (features/stats, event_type "cheer") minus everything they have
    already spent on either, via features/companion/store.py's companion_spend. Showing a
    message costs min_bits_to_speak, changing the look costs min_bits_to_set_seed, each
    time - deliberately one pot rather than two, or someone could spend their bits on
    messages and still get a free style change out of the same total (or the other way
    round). Below the price of whichever one was attempted, the companion still appears (or
    keeps its current look) and the command still runs, just with nothing deducted and
    nothing legible in a bubble, so chat is not spammed with a decline every time someone
    tries. Mods and the broadcaster are exempt from both prices (not from moderation) - they
    already run the stream, they should not have to donate to it to use a chat feature.

A custom seed from !companion set, and the shared spend ledger behind both commands, are
persisted (features/companion/store.py) precisely because they track bits actually spent -
unlike presence, losing either on a restart would mean charging for the same thing twice.
Without a loaded STORAGE feature both still work for the running process, just not across a
restart, same trade-off as the death counter in features/overlay.

MODERATION, STATS and STORAGE are all optional: without MODERATION nothing is filtered (as
everywhere else in the bot - "no feature, no moderation"), without STATS nobody has any bits
on record so !companion never shows or restyles anything until an operator adds one, and
without STORAGE a custom seed survives only until the next restart.
"""

import asyncio
import time

from core import events
from core import feature as feature_api
from core import runtime_config

from . import config as env
from .server import CompanionServer
from .store import CompanionStore

DEFAULTS = {
    "platforms": [],
    "min_bits_to_speak": 100,
    "min_bits_to_set_seed": 300,
    "speech_ttl_seconds": 8,
    "idle_minutes": 20,
}

# How often the presence sweep looks for companions nobody has heard from in a while. Short
# enough that "leave" arrives promptly after idle_minutes, long enough not to matter.
SWEEP_INTERVAL_SECONDS = 30


class CompanionFeature(feature_api.Feature):
    name = "companion"

    # Offers nothing, needs nothing to load at all - a companion pond without bits gating
    # (STATS), text filtering (MODERATION) or a persisted custom look (STORAGE) is still a
    # valid, if more trusting/forgetful, setup.
    provides = frozenset()
    optional = frozenset({feature_api.STATS, feature_api.MODERATION, feature_api.STORAGE})

    # Chat only - a companion is spoken into being, and only platforms with the CHAT
    # capability report messages at all.
    platform_capabilities = frozenset({feature_api.CHAT})

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        self._bus = None
        self._server = None
        self._stats = None
        self._moderation = None
        self._store = None
        # key -> {"key", "seed", "platform", "user_name", "last_seen"}. RAM only, like
        # chat_panel's history: this says who is around right now, not a record.
        self._companions = {}
        # key -> custom seed or None, cached from the store on first sight of that key -
        # see _seed_for. Kept even without a STORAGE feature (see __init__ comment on
        # `_store`), just lost on restart then.
        self._seed_overrides = {}
        # key -> bits already spent on !companion, all-time - see _spent_for/_spend. Same
        # RAM-cache-over-a-store shape as _seed_overrides, and the same fallback without
        # STORAGE.
        self._spend_cache = {}
        self._sweep_task = None

    # --- Lifecycle ------------------------------------------------------------------

    async def setup(self, bus):
        self._bus = bus
        self._stats = bus.feature_with(feature_api.STATS)
        self._moderation = bus.feature_with(feature_api.MODERATION)

        db = bus.feature_with(feature_api.STORAGE)
        if db is not None:
            self._store = CompanionStore(db)
            await asyncio.to_thread(self._store.init_schema)
        else:
            print("ℹ️  Companion without STORAGE: a custom look from !companion set "
                  "is forgotten on the next restart.")

        bus.subscribe(events.MESSAGE_ACCEPTED, self.on_message_accepted)
        self._sweep_task = asyncio.create_task(self._sweep_loop())

        if not env.COMPANION_TOKEN:
            print("ℹ️  No COMPANION_TOKEN set - no companion listener. "
                  "The !companion command still runs, just without anywhere to show it.")
            return

        self._server = CompanionServer(
            env.COMPANION_TOKEN, env.COMPANION_BIND, env.COMPANION_PORT,
            snapshot=lambda: [self._public(c) for c in self._companions.values()],
            on_error=lambda message: print(f"⚠️  {message}"),
        )
        await self._server.start()

    async def close(self):
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            self._sweep_task = None
        if self._server is not None:
            await self._server.close()
            self._server = None

    # --- Scope ------------------------------------------------------------------------

    def _in_scope(self, platform_name):
        if self._bus is None:
            return True  # without a bus no resolution - then better to show than to lose
        scope = self._bus.resolve_platforms(self.config.get("platforms", ()))
        return scope is None or platform_name in scope

    # --- Presence -----------------------------------------------------------------------

    @staticmethod
    def _key(message):
        """Per platform and person, the id where there is one - stable across a display
        name change, unlike the name itself. Same shape as
        features/moderation/feature.py:_user_key."""
        return f"{message.platform}:{message.user_id or message.user_name.lower()}"

    @staticmethod
    def _public(companion):
        """What a browser source gets to see of a companion: never the internal key beyond
        what it needs to remove the right element again, never last_seen."""
        return {
            "key": companion["key"],
            "seed": companion["seed"],
            "platform": companion["platform"],
            "user_name": companion["user_name"],
        }

    async def _seed_for(self, key, default_seed):
        """The custom seed for this key if one was ever set (!companion set), otherwise
        `default_seed` (their name). Loaded from the store at most once per key per process
        - afterwards the cache in self._seed_overrides answers, so presence (which runs on
        every accepted message) never costs a query beyond the first sighting."""
        if key not in self._seed_overrides:
            self._seed_overrides[key] = (
                await asyncio.to_thread(self._store.get_seed, key) if self._store is not None else None
            )
        return self._seed_overrides[key] or default_seed

    async def _spent_for(self, key):
        """Bits this key has spent on !companion so far, all-time. Same lazy-load-once-then-
        cache shape as _seed_for."""
        if key not in self._spend_cache:
            self._spend_cache[key] = (
                await asyncio.to_thread(self._store.get_spent, key) if self._store is not None else 0
            )
        return self._spend_cache[key]

    async def _spend(self, key, amount):
        """Books `amount` bits as spent on !companion for this key and updates the cache to
        match - called only after a speak attempt has already cleared the balance check, so
        this never has to reject anything itself."""
        if self._store is not None:
            total = await asyncio.to_thread(self._store.add_spent, key, amount)
        else:
            total = self._spend_cache.get(key, 0) + amount
        self._spend_cache[key] = total

    async def on_message_accepted(self, message):
        if not self._in_scope(message.platform):
            return
        # A companion is a perk, not a default for every chatter - subscribers get one, and
        # so do mods/the broadcaster (same exemption as the bits gates below: they already
        # run the stream).
        if not (message.is_subscriber or message.is_privileged):
            return
        key = self._key(message)
        is_new = key not in self._companions
        self._companions[key] = {
            "key": key,
            "seed": await self._seed_for(key, message.user_name),
            "platform": message.platform,
            "user_name": message.user_name,
            "last_seen": time.monotonic(),
        }
        # Only a genuinely new companion is worth a frame - every other message just
        # refreshes last_seen in RAM, or the pond would redraw itself on every chat line.
        if is_new and self._server is not None:
            await self._server.broadcast("join", self._public(self._companions[key]))

    async def _sweep_loop(self):
        try:
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
                await self._sweep()
        except asyncio.CancelledError:
            pass

    async def _sweep(self):
        idle_seconds = max(1, int(self.config.get("idle_minutes", 20))) * 60
        cutoff = time.monotonic() - idle_seconds
        stale = [key for key, c in self._companions.items() if c["last_seen"] < cutoff]
        for key in stale:
            del self._companions[key]
            if self._server is not None:
                await self._server.broadcast("leave", {"key": key})

    # --- Speaking -------------------------------------------------------------------

    async def _blocked_reason(self, message, text):
        """None when the text may be shown, otherwise the moderation Verdict that says why
        not. Deliberately does not reuse message.is_privileged/is_subscriber: this checks
        what will appear on a public overlay, not what may stay in a fast-scrolling chat -
        the two exemptions that make sense for the latter (mods, subscribers skipping the
        spam heuristics) do not carry over to a sentence sitting on screen for several
        seconds."""
        if self._moderation is None:
            return None
        probe = feature_api.Message(
            platform=message.platform, user_id=message.user_id, user_name=message.user_name,
            text=text, is_privileged=False, is_subscriber=False,
        )
        return await self._moderation.review(probe)

    async def _bits_total(self, user_name):
        if self._stats is None:
            return 0
        # "cheer" only, not "cheer_anon" - the same exclusion features/stats/store.py makes
        # for its leaderboards: an anonymous cheer cannot be matched back to whoever is
        # typing the command.
        return await self._stats.user_event_total("cheer", user_name)

    async def cmd_companion(self, message):
        # Same eligibility as presence itself (on_message_accepted) - speaking through, or
        # restyling, a companion that was never granted in the first place would be a way
        # around the subscriber gate rather than a use of it.
        if not (message.is_subscriber or message.is_privileged):
            return self.config.text("subs_only")

        text = (message.arg_text or "").strip()
        if not text:
            return self.config.text("usage")

        sub, _, rest = text.partition(" ")
        if sub.lower() == "set":
            return await self._cmd_companion_set(message, rest.strip())

        verdict = await self._blocked_reason(message, text)
        if verdict is not None:
            return self.config.text("blocked", label=verdict.label)

        key = self._key(message)
        min_bits = int(self.config.get("min_bits_to_speak", 100))
        # Mods and the broadcaster use this for free - the bits gate is about letting
        # regular chat earn a bubble, not about restricting the people already running the
        # stream. Unlike the moderation check above, this exemption *does* reuse
        # is_privileged: a mod's companion is still filtered, just never asked to pay.
        if not message.is_privileged:
            available = await self._bits_total(message.user_name) - await self._spent_for(key)
            if available < min_bits:
                # Presence only - the companion is on screen (or arrives now, via
                # on_message_accepted having already run for this same message), but
                # nothing legible goes into a bubble and nothing is deducted, and chat gets
                # the one reply that explains why.
                return self.config.text("needs_bits", have=available, need=min_bits)
            # Deduct *before* broadcasting, not after: a broadcast failure must not give a
            # free retry, and there is nothing here that would need to refund it anyway (the
            # server being briefly unavailable is not the speaker's problem to notice).
            await self._spend(key, min_bits)

        if self._server is not None:
            await self._server.broadcast("speak", {
                "key": key,
                "seed": await self._seed_for(key, message.user_name),
                "platform": message.platform,
                "user_name": message.user_name,
                "text": text,
                "ttl": int(self.config.get("speech_ttl_seconds", 8)),
            })
        return None

    async def _cmd_companion_set(self, message, seed):
        """!companion set <hash> - a custom DiceBear seed instead of the person's name.
        Costs min_bits_to_set_seed *per change*, from the same balance !companion <text>
        spends from (cheered all-time minus everything already spent on either) - not a
        separate pot, or someone could spend their bits on messages and still get a free
        style change out of the same total. The look itself is kept
        (features/companion/store.py) until the next !companion set changes it again; only
        the bits, not the look, are spent per use."""
        seed = seed.strip()
        if not seed:
            return self.config.text("set_usage")

        key = self._key(message)
        min_bits = int(self.config.get("min_bits_to_set_seed", 300))
        if not message.is_privileged:
            available = await self._bits_total(message.user_name) - await self._spent_for(key)
            if available < min_bits:
                return self.config.text("set_needs_bits", have=available, need=min_bits)
            await self._spend(key, min_bits)

        # DiceBear takes any string as a seed - the cap is only so a chat message stuffed
        # with a paragraph of text does not end up as a permanent DB row and a URL query
        # parameter of the same length.
        seed = seed[:64]
        self._seed_overrides[key] = seed
        if self._store is not None:
            await asyncio.to_thread(self._store.set_seed, key, seed)

        if key in self._companions:
            # Update the running presence in place and re-announce it as a "join" - the
            # companion page treats a join for a key it already knows as a restyle rather
            # than a duplicate (see client/companion.html:addPet), so the new look shows up
            # immediately instead of waiting for whatever chat line happens next.
            self._companions[key]["seed"] = seed
            if self._server is not None:
                await self._server.broadcast("join", self._public(self._companions[key]))
        return self.config.text("set_done", seed=seed)

    # --- Commands -------------------------------------------------------------------

    async def cmd_vtubbi(self, message):
        """!vtubbi is deliberately its own command, not an alias of !companion: it points at
        the project behind the companions (github.com/Wafobi/vtubbi) rather than making one
        talk. The link lives in companion.json ("vtubbi.link"), like every other line the
        bot says, so it can be repointed without a code change."""
        return self.config.text("vtubbi.link")

    def commands(self):
        min_bits = int(self.config.get("min_bits_to_speak", 100))
        min_bits_set = int(self.config.get("min_bits_to_set_seed", 300))
        return (
            feature_api.Command(
                name="!companion", handler=self.cmd_companion,
                help=self.config.text("help.companion", need=min_bits, set_need=min_bits_set),
            ),
            feature_api.Command(
                name="!vtubbi", handler=self.cmd_vtubbi,
                help=self.config.text("help.vtubbi"),
            ),
        )


def create_feature():
    return CompanionFeature()
