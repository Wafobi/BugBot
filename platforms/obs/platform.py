# platform.py
# OBS as a Platform implementation (contract: core/platform.py).
#
# As with Twitch and Discord, only the shell - the logic is in bot.py, the protocol in
# link.py.
#
# ANNOUNCE alone is declared: OBS has no chat (no CHAT), moderates nothing (no MODERATE) and
# deliberately reports no stream start (no STREAM) - the stream session belongs to Twitch,
# and two reporters would mean two sessions for the same stream. What OBS knows about it
# goes onto the bus as an ordinary PLATFORM_EVENT (see bot.py).

from core import platform as platform_api

from . import bot


class OBSPlatform(platform_api.Platform):
    name = "obs"
    capabilities = frozenset({
        platform_api.ANNOUNCE,   # text source on screen, see announce() below
    })

    async def start(self):
        # Returns as soon as the port is open. Whether a relay ever calls is up to the
        # streaming PC.
        await bot.start_obs()

    async def close(self):
        await bot.close()

    # wait_ready() deliberately stays at the default (ready immediately): OBS is usually not
    # even running when the bot starts. If the connection were waited for here, Twitch's
    # live reconciliation would hang on a switched-off machine (see bot.start_obs).

    async def announce(self, announcement):
        """Shows the announcement as text on stream - but only the kinds listed in obs.json
        under announce.kinds. The default is empty: what the chat sees anyway need not
        additionally go on screen."""
        return await bot.show_announcement(announcement)


def create_platform():
    return OBSPlatform()
