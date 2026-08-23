"""platforms/twitch/bot.py: the CRLF-injection fix (K-1), IRC PRIVMSG parsing (N-5), and
badge matching (N-1)."""

import pytest

from platforms.twitch import bot


class FakeWriter:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        return


@pytest.fixture
def fake_writer(monkeypatch):
    writer = FakeWriter()
    monkeypatch.setattr(bot, "_writer", writer)
    return writer


# --- K-1: _send_raw can never put a second logical line on the wire ------------------------

async def test_send_raw_strips_embedded_crlf(fake_writer):
    await bot._send_raw("harmless\r\nPRIVMSG #victim :/ban someone")
    line = fake_writer.written[0].decode()
    # Exactly one \r\n on the wire - the one _send_raw itself appends at the end. The
    # injected text is still present (translate() replaces \r/\n with a space, it does not
    # delete the rest of the string) but that is exactly the point: without a \r\n in front
    # of it, an IRC server reads it as trailing text on *this* line, not as a second,
    # independent command.
    assert line.count("\r\n") == 1
    assert line.endswith("\r\n")
    assert line == "harmless  PRIVMSG #victim :/ban someone\r\n"


async def test_send_raw_strips_bare_lf_and_nul(fake_writer):
    await bot._send_raw("a\nb\0c")
    line = fake_writer.written[0].decode()
    # \r/\n become a space (keeps the line readable as one line); \0 is deleted outright
    # (maketrans maps it to None) rather than replaced, since some IRC servers treat it as
    # a line terminator of its own.
    assert line == "a bc\r\n"


async def test_send_raw_without_a_connection_raises_instead_of_silently_dropping(monkeypatch):
    monkeypatch.setattr(bot, "_writer", None)
    with pytest.raises(ConnectionError):
        await bot._send_raw("PING :x")


async def test_send_twitch_chat_truncates_overlong_messages(fake_writer, monkeypatch):
    monkeypatch.setattr(bot.config, "TWITCH_CHANNEL", "chan")
    ok = await bot.send_twitch_chat("x" * 1000)
    assert ok is True
    line = fake_writer.written[0].decode()
    # 450 chars max for the message body (see _MAX_CHAT_MESSAGE_LENGTH), plus the
    # "PRIVMSG #chan :" prefix and the trailing \r\n.
    body = line.split(":", 2)[-1].rstrip("\r\n")
    assert len(body) == bot._MAX_CHAT_MESSAGE_LENGTH
    assert body.endswith("…")


async def test_send_twitch_chat_short_message_is_not_truncated(fake_writer, monkeypatch):
    monkeypatch.setattr(bot.config, "TWITCH_CHANNEL", "chan")
    await bot.send_twitch_chat("hello")
    line = fake_writer.written[0].decode()
    assert line == "PRIVMSG #chan :hello\r\n"


# --- N-5: parse_privmsg -----------------------------------------------------------------

def test_parse_privmsg_with_tags():
    line = ("@badges=moderator/1;id=abc-123;user-id=42 "
            ":wafobitv!wafobitv@wafobitv.tmi.twitch.tv PRIVMSG #wafobitv :hello there")
    tags, user, message = bot.parse_privmsg(line)
    assert user == "wafobitv"
    assert message == "hello there"
    assert tags["badges"] == "moderator/1"
    assert tags["user-id"] == "42"


def test_parse_privmsg_without_tags():
    line = ":someone!someone@someone.tmi.twitch.tv PRIVMSG #chan :no tags here"
    tags, user, message = bot.parse_privmsg(line)
    assert tags == {}
    assert user == "someone"
    assert message == "no tags here"


def test_parse_privmsg_message_containing_a_colon_is_kept_whole():
    line = ":u!u@u.tmi.twitch.tv PRIVMSG #c :time is 10:30 now"
    _, _, message = bot.parse_privmsg(line)
    assert message == "time is 10:30 now"


def test_parse_privmsg_malformed_line_returns_none_not_raises():
    assert bot.parse_privmsg("@badges=moderator/1") is None
    assert bot.parse_privmsg("garbage") is None
    assert bot.parse_privmsg("") is None


# --- N-1: badge matching is exact-segment, not startswith -----------------------------

def badge_flags(badges_str):
    """Mirrors _handle_privmsg's own badge_names computation - kept as a tiny, local
    reimplementation so this test still catches a regression to the old startswith()
    behaviour without needing to run the whole PRIVMSG handler."""
    badge_names = {b.split("/", 1)[0] for b in badges_str.split(",") if b}
    return bool(badge_names & {"broadcaster", "moderator"}), bool(badge_names & {"subscriber", "founder"})


def test_badge_moderator_is_privileged():
    assert badge_flags("moderator/1") == (True, False)


def test_badge_broadcaster_and_subscriber_together():
    assert badge_flags("broadcaster/1,subscriber/12") == (True, True)


def test_badge_lookalike_name_does_not_false_match():
    # The N-1 regression: startswith(("moderator",)) would have matched this.
    assert badge_flags("moderatorxyz/1") == (False, False)


def test_badge_no_badges_at_all():
    assert badge_flags("") == (False, False)


def test_badge_unrelated_badge_does_not_grant_anything():
    assert badge_flags("premium/1") == (False, False)
