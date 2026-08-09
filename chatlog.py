#!/usr/bin/env python3
"""Das Archiv der mitgeschnittenen Stream-Chats.

    python3 chatlog.py                    welche Streams es gibt
    python3 chatlog.py 7                  den Chat von Stream #7 lesen
    python3 chatlog.py 7 --suche tallneck nur Zeilen, die das enthalten
    python3 chatlog.py 7 --html           eine Seite zum Blättern, im Browser zu öffnen
    python3 chatlog.py --alle --html      jeden Stream als eigene Seite, plus Übersicht

Der Bot schreibt den Wortlaut mit (features/chat_log), gibt ihn aber nirgends wieder
heraus - er beantwortet nur zwei Abfragen für sich selbst. Ohne dieses Skript käme man an
das Archiv nur mit einer SQL-Abfrage von Hand, und das ist keine Antwort auf "was wurde
letzten Dienstag geschrieben".

Zwei Dinge nimmt es einem ab, die man sonst jedes Mal falsch macht:

  * Die Datenbank speichert UTC (SQLite datetime('now')). Hier stehen die Zeiten in der
    Zeitzone aus features/variables/variables.json - derselben, in der der Bot auch {time}
    ausgibt. Ein Stream, der laut Datenbank um 11:03 begann, war um 13:03.
  * Gelesen wird ausdrücklich nur (mode=ro). Das Skript läuft gefahrlos, während der Bot
    weiterschreibt - auch mitten im Stream.

Mitgeschnitten wird nur während eines Streams; außerhalb gibt es keine Session, und dann
steht auch nichts in der Tabelle (siehe features/chat_log/chat_log.json, _live_only).
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

ROOT = Path(__file__).resolve().parent


def database_path():
    """Dieselbe Reihenfolge wie in features/sql_db/feature.py: BUGBOT_DB, dann db_path aus
    sql_db.json, dann bugbot.db neben dem Code. Hier noch einmal und nicht importiert,
    damit das Skript ohne die Abhängigkeiten des Bots läuft - man will ein Archiv auch auf
    einem Rechner lesen können, auf dem discord.py nicht installiert ist."""
    from_env = os.environ.get("BUGBOT_DB")
    if from_env:
        return Path(from_env)
    try:
        configured = json.loads((ROOT / "features/sql_db/sql_db.json").read_text("utf-8")).get("db_path")
    except (OSError, json.JSONDecodeError):
        configured = None
    return Path(configured) if configured else ROOT / "bugbot.db"


def apply_locale():
    """Dieselbe Sprache wie im Chat: "locale" aus variables.json bestimmt, ob hier
    "Sonntag" oder "Sunday" steht. Ohne das richtete sich das Archiv nach dem Rechner, auf
    dem man es gerade erzeugt - derselbe Stream sähe je nach Login anders aus."""
    name = _setting("locale")
    if not name:
        return
    for candidate in (name, name.replace("-", "_"), name.split(".")[0]):
        try:
            locale.setlocale(locale.LC_TIME, candidate)
            return
        except locale.Error:
            continue
    print(f"⚠️ Locale {name!r} ist hier nicht verfügbar, Wochentage bleiben englisch.", file=sys.stderr)


def _setting(key):
    try:
        return json.loads((ROOT / "features/variables/variables.json").read_text("utf-8")).get(key)
    except (OSError, json.JSONDecodeError):
        return None


def local_zone():
    """Die Zeitzone aus variables.json - die, in der der Bot auch {time} ausgibt. Fehlt sie
    oder ist sie unbekannt, bleibt es bei der des Rechners; ein Archiv soll an einer
    kaputten Zeitzone nicht scheitern."""
    name = _setting("timezone")
    if name:
        try:
            return ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            print(f"⚠️ Zeitzone {name!r} unbekannt, nehme die dieses Rechners.", file=sys.stderr)
    return None


def to_local(stamp, zone):
    """"2026-08-09 11:03:49" (UTC, wie SQLite es schreibt) -> datetime in `zone`."""
    try:
        moment = datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return moment.astimezone(zone) if zone else moment.astimezone()


def connect(path):
    if not path.exists():
        sys.exit(f"❌ Keine Datenbank unter {path} - stimmt der Pfad (BUGBOT_DB, sql_db.json)?")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("SELECT 1 FROM chat_log LIMIT 1")
    except sqlite3.OperationalError:
        sys.exit("❌ In dieser Datenbank gibt es keine Tabelle chat_log - lief das Feature 'chat_log' je?")
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
    what = row["game_name"] or row["title"] or "ohne Kategorie"
    return when, what


# --- Ausgaben --------------------------------------------------------------------------

def print_sessions(rows, zone):
    if not rows:
        print("Noch kein Stream aufgezeichnet.")
        return
    print(f"{'#':>4}  {'Wann':<22} {'Was':<28} {'Zeilen':>7} {'Chatter':>8}")
    for row in rows:
        when, what = describe(row, zone)
        live = "" if row["ended_at"] else "  (läuft)"
        print(f"{row['id']:>4}  {when:<22} {what[:28]:<28} {row['messages']:>7} {row['chatters']:>8}{live}")
    print(f"\nEinen davon lesen: python3 {Path(__file__).name} <#>")


def print_messages(rows, zone):
    if not rows:
        print("Nichts gefunden.")
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
    """Eine Seite je Stream, ohne Fremdinhalte: kein Skript von außen, keine Schrift von
    außen, nichts, was beim Öffnen ins Netz greift. Ein Archiv soll in fünf Jahren noch
    genauso aussehen und auch offline funktionieren."""
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
<html lang="de"><head><meta charset="utf-8">
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
<header><h1>{html.escape(title)}</h1><em>{html.escape(what)} &middot; {len(rows)} Zeilen</em></header>
<input id="q" type="search" placeholder="Filtern…" autocomplete="off">
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
        f'<em>{html.escape(describe(session, zone)[1])} &middot; {session["messages"]} Zeilen</em></li>'
        for session, file in written
    )
    path.write_text(f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chat-Archiv</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.7 system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; }}
  h1 {{ font-size: 1.3rem; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: .35rem 0; border-bottom: 1px solid #8883; }}
  em {{ opacity: .6; font-style: normal; }}
</style></head><body>
<h1>Chat-Archiv</h1><ul>{items}</ul></body></html>
""", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Archiv der mitgeschnittenen Stream-Chats.",
        epilog="Ohne Argumente: die Liste der aufgezeichneten Streams.",
    )
    parser.add_argument("session", nargs="?", type=int, help="Nummer aus der Liste")
    parser.add_argument("--alle", action="store_true", help="alle Streams (nur mit --html sinnvoll)")
    parser.add_argument("--suche", metavar="TEXT", help="nur Zeilen mit diesem Text (auch im Namen)")
    parser.add_argument("--html", action="store_true", help="als Seite(n) schreiben statt auszugeben")
    parser.add_argument("--out", metavar="ORDNER", default="chat-archiv", type=Path,
                        help="wohin die Seiten sollen (Vorgabe: chat-archiv/)")
    args = parser.parse_args()

    apply_locale()
    zone = local_zone()
    connection = connect(database_path())
    available = sessions(connection)

    if not args.alle and args.session is None:
        print_sessions(available, zone)
        return 0

    wanted = available if args.alle else [row for row in available if row["id"] == args.session]
    if not wanted:
        print(f"❌ Kein Stream #{args.session}. Vorhanden:", file=sys.stderr)
        print_sessions(available, zone)
        return 1

    if not args.html:
        for session in wanted:
            when, what = describe(session, zone)
            print(f"=== Stream #{session['id']} - {when} - {what} ===")
            print_messages(messages(connection, session["id"], args.suche), zone)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    written = []
    for session in wanted:
        rows = messages(connection, session["id"], args.suche)
        if not rows:
            continue
        file = write_html(args.out / f"stream-{session['id']:04d}.html", session, rows, zone)
        written.append((session, file))
        print(f"📄 {file}  ({len(rows)} Zeilen)")
    if not written:
        print("Nichts zu schreiben - keine mitgeschnittenen Zeilen.")
        return 0
    index = write_index(args.out / "index.html", written, zone)
    print(f"\n✅ Archiv: {index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
