"""Announcement.as_text(): the single-line rendering for text-only platforms (Twitch)."""

from core.platform import Announcement, Field


def test_as_text_joins_title_text_fields_url():
    a = Announcement(kind="k", title="Title", text="Body", url="http://x",
                      fields=(Field("A", "1"), Field("B", "2")))
    assert a.as_text() == "Title - Body - A: 1 - B: 2 - http://x"


def test_as_text_drops_empty_parts():
    a = Announcement(kind="k", title="Title", text="")
    assert a.as_text() == "Title"


def test_as_text_max_fields_limits_detail_fields():
    a = Announcement(kind="k", title="T", fields=(Field("A", "1"), Field("B", "2"), Field("C", "3")))
    assert a.as_text(max_fields=1) == "T - A: 1"
    assert a.as_text(max_fields=0) == "T"
    assert a.as_text() == "T - A: 1 - B: 2 - C: 3"


def test_as_text_collapses_embedded_newlines_and_crlf():
    # The K-1 regression: a field containing a line break must never produce more than one
    # logical line, or a caller writing it straight to an IRC socket could have it read as a
    # second, independent command.
    a = Announcement(kind="k", title="Bug", text="line one\r\nPRIVMSG #x :injected")
    text = a.as_text()
    assert "\n" not in text
    assert "\r" not in text
    assert text == "Bug - line one PRIVMSG #x :injected"


def test_as_text_collapses_tabs_and_repeated_whitespace():
    a = Announcement(kind="k", title="A\tB   C")
    assert a.as_text() == "A B C"
