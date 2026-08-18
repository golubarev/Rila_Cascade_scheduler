"""
update_grafik.py
================
Incrementally tops up an EXISTING database from a SINGLE Grafik .xlsm file.

Use this for the everyday case: you get a fresh file (daily, or at the start of
the week) and want to fold its days into the database without rebuilding the
whole thing from all seven archives.

Behaviour: "newest wins"
-------------------------
For every day found in the file, the day's existing rows (if any) are deleted
and re-inserted from this file.  So the file you point at becomes the truth for
the days it covers - which both:
  * refreshes recent days (yesterday's forecast row now carries actual measured
    levels, an intraday trade got recorded, ...), and
  * adds brand-new future/planning days,
in one pass.  Every other day already in the database is left untouched.

It does NOT create the schema.  If the database does not exist yet, it tells you
to run the full builder (load_grafik.py) first, rather than silently making an
empty database.

Run
---
    python -m src.update_grafik --db data/rila_cascade.db --file "path/to/one_file.xlsm"
"""

import argparse
import datetime
import os
import re
import sys

from . import database as db
from . import db_writer
from . import grafik_parser as gp

YEAR_RE = re.compile(r"(20\d{2})")


def _schema_present(conn):
    """True if the expected tables already exist (i.e. a real database)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='period'"
    ).fetchone()
    return row[0] == 1


def _register_source_file(conn, path):
    """Ensure the file has a row in source_file and return its id.

    If the file was seen before we refresh its imported_at; either way we return
    the id to stamp onto the day rows we write.
    """
    name = os.path.basename(path)
    m = YEAR_RE.match(name)
    year = int(m.group(1)) if m else None
    is_admin = 0 if m else 1
    now = datetime.datetime.now().isoformat(timespec="seconds")

    existing = conn.execute(
        "SELECT id FROM source_file WHERE filename=?", (name,)).fetchone()
    if existing:
        conn.execute("UPDATE source_file SET imported_at=? WHERE id=?",
                     (now, existing[0]))
        return existing[0]

    cur = conn.execute(
        "INSERT INTO source_file (filename, file_year, is_admin, imported_at) "
        "VALUES (?,?,?,?)", (name, year, is_admin, now))
    return cur.lastrowid


def update(db_path, file_path):
    # --- guard rails -------------------------------------------------------
    if not os.path.exists(db_path):
        sys.exit(f"Database not found: {db_path}\n"
                 f"Run the full builder first:\n"
                 f"    python -m src.load_grafik --data-dir <folder> --db {db_path}")
    if not os.path.exists(file_path):
        sys.exit(f"File not found: {file_path}")

    conn = db.connect(db_path)
    if not _schema_present(conn):
        sys.exit("This database has no schema yet - run load_grafik.py first.")

    conn.execute("PRAGMA synchronous = OFF;")
    conn.execute("PRAGMA journal_mode = MEMORY;")

    source_id = _register_source_file(conn, file_path)

    # New ids continue after the current maximum (deleting days never reuses ids).
    row = conn.execute("SELECT MAX(id) FROM period").fetchone()
    next_id = (row[0] or 0) + 1

    print(f"Updating {os.path.basename(db_path)} from "
          f"{os.path.basename(file_path)} ...")

    # --- parse the single file and apply each day --------------------------
    blocks = gp.parse_grafik(file_path)

    # A file should hold each day once; if not, keep the LAST block for a date
    # so we apply the most complete version.
    by_date = {}
    for b in blocks:
        by_date[b.date] = b

    replaced_days, added_days, rows_written = 0, 0, 0
    replaced_list, added_list = [], []

    for day in sorted(by_date.values(), key=lambda b: b.date):
        removed = db_writer.delete_day(conn, day.date.isoformat())
        next_id = db_writer.write_day_block(conn, day, source_id, next_id)
        rows_written += len(day.hours)
        if removed:
            replaced_days += 1
            replaced_list.append(day.date.isoformat())
        else:
            added_days += 1
            added_list.append(day.date.isoformat())

    conn.commit()
    conn.close()

    # --- report ------------------------------------------------------------
    print("\nDone.")
    print(f"  days in file: {len(by_date)}")
    print(f"  replaced (already existed): {replaced_days}")
    if replaced_list:
        print(f"     {replaced_list[0]} ... {replaced_list[-1]}")
    print(f"  added (new days): {added_days}")
    if added_list:
        print(f"     {added_list[0]} ... {added_list[-1]}")
    print(f"  hourly rows written: {rows_written}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Top up an existing SQLite DB from one Grafik file (newest wins).")
    ap.add_argument("--db", required=True, help="path of the existing SQLite database")
    ap.add_argument("--file", required=True, help="the single .xlsm file to load from")
    args = ap.parse_args(argv)
    update(args.db, args.file)


if __name__ == "__main__":
    main()
