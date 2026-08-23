"""platforms/discord/bot.py: is_discord_mod defensiveness and the DM early-return (H-3)."""

from platforms.discord import bot


class FakeUser:
    """A discord.User (DM sender) - no guild_permissions at all, unlike discord.Member."""
    def __init__(self, name="someone"):
        self.name = name


class FakeMember:
    def __init__(self, administrator=False, roles=()):
        self.guild_permissions = FakePermissions(administrator)
        self.roles = roles


class FakePermissions:
    def __init__(self, administrator):
        self.administrator = administrator


class FakeMessage:
    """Just enough of discord.Message for on_message's early guards - author and guild."""
    def __init__(self, author, guild=None):
        self.author = author
        self.guild = guild


# --- H-3: is_discord_mod must not raise for a DM sender / ex-member -----------------------

def test_is_discord_mod_true_for_administrator():
    assert bot.is_discord_mod(FakeMember(administrator=True)) is True


def test_is_discord_mod_false_for_plain_member():
    assert bot.is_discord_mod(FakeMember(administrator=False)) is False


def test_is_discord_mod_false_for_a_user_without_guild_permissions():
    # The actual H-3 regression: a discord.User (DM sender, or someone who left the guild)
    # has no .guild_permissions at all - the old code (member.guild_permissions.administrator
    # unconditionally) raised AttributeError here.
    assert bot.is_discord_mod(FakeUser()) is False


# --- H-3: on_message returns early on a DM instead of reaching is_discord_mod -------------

async def test_on_message_returns_early_for_a_dm():
    dm = FakeMessage(author=FakeUser("someone"), guild=None)
    # Must not raise - previously this fell through to honeypot/is_discord_mod handling
    # that assumes a guild context, and crashed on message.channel.name/guild_permissions.
    result = await bot.on_message(dm)
    assert result is None


async def test_on_message_from_the_bot_itself_returns_early():
    # message.author == bot.user - guarded before the guild check.
    self_message = FakeMessage(author=bot.bot.user, guild=None)
    result = await bot.on_message(self_message)
    assert result is None
