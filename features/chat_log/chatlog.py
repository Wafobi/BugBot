#!/usr/bin/env python3
"""The archive of the recorded stream chats.

Called from the repository root:

    python3 features/chat_log/chatlog.py                      which streams exist
    python3 features/chat_log/chatlog.py 7                    read the chat of stream #7
    python3 features/chat_log/chatlog.py 7 --search tallneck  only lines containing that
    python3 features/chat_log/chatlog.py 7 --html             a page to browse, opened in a browser
    python3 features/chat_log/chatlog.py --all --html         every stream as its own page, plus an index

The bot records the wording (features/chat_log) but hands it out nowhere - it answers only
two queries for itself. Without this script the archive would be reachable only through a
hand-written SQL query, and that is no answer to "what was written last Tuesday".

It takes two things off your hands that otherwise get done wrong every time:

  * The database stores UTC (SQLite datetime('now')). Here the times are in the timezone
    from features/variables/variables.json - the same one the bot prints {time} in. A stream
    that began at 11:03 according to the database began at 13:03.
  * Reading is explicitly read-only (mode=ro). The script runs safely while the bot keeps
    writing - mid-stream too.

Recording only happens during a stream; outside of one there is no session, and then there
is nothing in the table either (see features/chat_log/chat_log.json, _live_only).
"""

import argparse
import html
import json
import locale
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# features/chat_log/ -> repository root. The paths below are written from there, because
# they point at other features' files; so they do not follow the script when it moves.
ROOT = Path(__file__).resolve().parents[2]


def database_path():
    """The same order as in features/sql_db/feature.py: BUGBOT_DB, then db_path from
    sql_db.json, then bugbot.db next to the code. Repeated here rather than imported, so the
    script runs without the bot's dependencies - you want to be able to read an archive on a
    machine where discord.py is not installed."""
    from_env = os.environ.get("BUGBOT_DB")
    if from_env:
        return Path(from_env)
    try:
        configured = json.loads((ROOT / "features/sql_db/sql_db.json").read_text("utf-8")).get("db_path")
    except (OSError, json.JSONDecodeError):
        configured = None
    return Path(configured) if configured else ROOT / "features/sql_db/bugbot.db"


def apply_locale():
    """The same language as in chat: "locale" from variables.json decides whether this
    reads "Sonntag" or "Sunday". Without it the archive would follow the machine it is
    generated on - the same stream would look different depending on the login."""
    name = _setting("locale")
    if not name:
        return
    for candidate in (name, name.replace("-", "_"), name.split(".")[0]):
        try:
            locale.setlocale(locale.LC_TIME, candidate)
            return
        except locale.Error:
            continue
    print(f"⚠️ Locale {name!r} is not available here, weekdays stay English.", file=sys.stderr)


def _setting(key):
    try:
        return json.loads((ROOT / "features/variables/variables.json").read_text("utf-8")).get(key)
    except (OSError, json.JSONDecodeError):
        return None


def local_zone():
    """The timezone from variables.json - the one the bot prints {time} in as well. If it is
    missing or unknown, the machine's own is used; an archive should not fail on a broken
    timezone."""
    name = _setting("timezone")
    if name:
        try:
            return ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            print(f"⚠️ Timezone {name!r} unknown, using this machine's.", file=sys.stderr)
    return None


def to_local(stamp, zone):
    """"2026-08-09 11:03:49" (UTC, as SQLite writes it) -> datetime in `zone`."""
    try:
        moment = datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return moment.astimezone(zone) if zone else moment.astimezone()


def connect(path):
    if not path.exists():
        sys.exit(f"❌ No database at {path} - is the path right (BUGBOT_DB, sql_db.json)?")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("SELECT 1 FROM chat_log LIMIT 1")
    except sqlite3.OperationalError:
        sys.exit("❌ This database has no chat_log table - did the 'chat_log' feature ever run?")
    return connection


def sessions(connection):
    return connection.execute("""
        SELECT s.id, s.started_at, s.ended_at, s.title, s.game_name,
               (SELECT COUNT(*) FROM chat_log l WHERE l.stream_session_id = s.id) AS messages,
               (SELECT COUNT(DISTINCT COALESCE(l.user_id, l.user_name)) FROM chat_log l
                 WHERE l.stream_session_id = s.id) AS chatters
        FROM stream_sessions s ORDER BY s.id DESC
    """).fetchall()


def messages(connection, session_id, needle=None):
    rows = connection.execute("""
        SELECT ts, platform, user_name, message FROM chat_log
        WHERE stream_session_id = ? ORDER BY id
    """, (session_id,)).fetchall()
    if needle:
        lowered = needle.lower()
        rows = [row for row in rows if lowered in row["message"].lower()
                or lowered in row["user_name"].lower()]
    return rows


def describe(row, zone):
    started = to_local(row["started_at"], zone)
    when = started.strftime("%a %d.%m.%Y %H:%M") if started else str(row["started_at"])
    what = row["game_name"] or row["title"] or "no category"
    return when, what


# --- Output ----------------------------------------------------------------------------

def print_sessions(rows, zone):
    if not rows:
        print("No stream recorded yet.")
        return
    print(f"{'#':>4}  {'When':<22} {'What':<28} {'Lines':>7} {'Chatters':>8}")
    for row in rows:
        when, what = describe(row, zone)
        live = "" if row["ended_at"] else "  (running)"
        print(f"{row['id']:>4}  {when:<22} {what[:28]:<28} {row['messages']:>7} {row['chatters']:>8}{live}")
    # The path from the repository root, not just the file name: that is where you call it.
    print(f"\nRead one of them: python3 {Path(__file__).resolve().relative_to(ROOT)} <#>")


def print_messages(rows, zone):
    if not rows:
        print("Nothing found.")
        return
    day = None
    for row in rows:
        moment = to_local(row["ts"], zone)
        if moment and moment.date() != day:
            day = moment.date()
            print(f"\n--- {day.strftime('%A, %d.%m.%Y')} ---")
        clock = moment.strftime("%H:%M:%S") if moment else str(row["ts"])
        print(f"[{clock}] {row['user_name']}: {row['message']}")


def write_html(path, session, rows, zone):
    """One page per stream, without foreign content: no script from outside, no font from
    outside, nothing that reaches into the network when opened. An archive should still look
    the same in five years and work offline too."""
    when, what = describe(session, zone)
    title = f"Stream #{session['id']} - {when}"
    lines, day = [], None
    for row in rows:
        moment = to_local(row["ts"], zone)
        if moment and moment.date() != day:
            day = moment.date()
            lines.append(f'<h2>{html.escape(day.strftime("%A, %d.%m.%Y"))}</h2>')
        clock = moment.strftime("%H:%M:%S") if moment else str(row["ts"])
        lines.append(
            f'<p><time>{html.escape(clock)}</time>'
            f'<b>{html.escape(row["user_name"])}</b>'
            f'<span>{html.escape(row["message"])}</span></p>'
        )
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.6 system-ui, sans-serif; max-width: 54rem; margin: 2rem auto; padding: 0 1rem; }}
  header {{ border-bottom: 1px solid #8884; padding-bottom: .6rem; margin-bottom: 1rem; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 .2rem; }}
  header em {{ opacity: .7; font-style: normal; }}
  h2 {{ font-size: .85rem; text-transform: uppercase; letter-spacing: .06em;
        opacity: .6; margin: 1.6rem 0 .4rem; }}
  p {{ display: grid; grid-template-columns: 4.6rem 10rem 1fr; gap: .5rem; margin: 0; padding: .12rem 0; }}
  p:hover {{ background: #8881; }}
  time {{ opacity: .5; font-variant-numeric: tabular-nums; }}
  b {{ font-weight: 600; overflow-wrap: anywhere; }}
  span {{ overflow-wrap: anywhere; }}
  #q {{ width: 100%; padding: .45rem .6rem; margin-bottom: 1rem; font: inherit;
        border: 1px solid #8886; border-radius: .4rem; background: transparent; color: inherit; }}
  @media (max-width: 34rem) {{ p {{ grid-template-columns: 4.6rem 1fr; }} b {{ grid-column: 2; }}
                               span {{ grid-column: 2; }} }}
</style></head><body>
<header><h1>{html.escape(title)}</h1><em>{html.escape(what)} &middot; {len(rows)} lines</em></header>
<input id="q" type="search" placeholder="Filter…" autocomplete="off">
<main>{chr(10).join(lines)}</main>
<script>
  const rows = [...document.querySelectorAll('main p')];
  document.getElementById('q').addEventListener('input', e => {{
    const needle = e.target.value.toLowerCase();
    for (const row of rows) row.hidden = needle && !row.textContent.toLowerCase().includes(needle);
  }});
</script>
</body></html>
""", encoding="utf-8")
    return path


def write_index(path, written, zone):
    items = "".join(
        f'<li><a href="{html.escape(file.name)}">Stream #{session["id"]} &middot; '
        f'{html.escape(describe(session, zone)[0])}</a> '
        f'<em>{html.escape(describe(session, zone)[1])} &middot; {session["messages"]} lines</em></li>'
        for session, file in written
    )
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chat archive</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.7 system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; }}
  h1 {{ font-size: 1.3rem; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: .35rem 0; border-bottom: 1px solid #8883; }}
  em {{ opacity: .6; font-style: normal; }}
</style></head><body>
<h1>Chat archive</h1><ul>{items}</ul></body></html>
""", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Archive of the recorded stream chats.",
        epilog="Without arguments: the list of recorded streams.",
    )
    parser.add_argument("session", nargs="?", type=int, help="number from the list")
    parser.add_argument("--all", action="store_true", help="all streams (only useful with --html)")
    parser.add_argument("--search", metavar="TEXT", help="only lines containing this text (name included)")
    parser.add_argument("--html", action="store_true", help="write page(s) instead of printing")
    parser.add_argument("--out", metavar="FOLDER", default="chat-archive", type=Path,
                        help="where the pages should go (default: chat-archive/)")
    args = parser.parse_args()

    apply_locale()
    zone = local_zone()
    connection = connect(database_path())
    available = sessions(connection)

    if not args.all and args.session is None:
        print_sessions(available, zone)
        return 0

    wanted = available if args.all else [row for row in available if row["id"] == args.session]
    if not wanted:
        print(f"❌ No stream #{args.session}. Available:", file=sys.stderr)
        print_sessions(available, zone)
        return 1

    if not args.html:
        for session in wanted:
            when, what = describe(session, zone)
            print(f"=== Stream #{session['id']} - {when} - {what} ===")
            print_messages(messages(connection, session["id"], args.search), zone)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    written = []
    for session in wanted:
        rows = messages(connection, session["id"], args.search)
        if not rows:
            continue
        file = write_html(args.out / f"stream-{session['id']:04d}.html", session, rows, zone)
        written.append((session, file))
        print(f"📄 {file}  ({len(rows)} lines)")
    if not written:
        print("Nothing to write - no recorded lines.")
        return 0
    index = write_index(args.out / "index.html", written, zone)
    print(f"\n✅ Archive: {index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
