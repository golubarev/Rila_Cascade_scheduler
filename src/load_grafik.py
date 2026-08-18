"""
load_grafik.py
==============
Builds the SQLite database from scratch out of every Grafik .xlsm file in a
folder.  Use this once (or whenever you want a clean full rebuild).  For a quick
daily/weekly top-up from a single file, use update_grafik.py instead.

Steps
-----
1. Discover the source files and tag each with its year (or mark the live file).
2. First pass - scan each file's dates (the yearly files overlap heavily).
3. Pick one "winner" file per day so each day loads exactly once.  Overlaps are
   reported, never silently merged.
4. Second pass - parse each file and write the days it owns.

Parsing and writing are delegated to grafik_parser.py and db_writer.py, the same
modules the updater uses.

Run
---
    python -m src.load_grafik --data-dir /path/to/xlsm/folder --db data/rila_cascade.db
"""

import argparse
import datetime
import glob
import os
import re
import sys

from . import database as db
from . import db_writer
from . import grafik_parser as gp

YEAR_RE = re.compile(r"(20\d{2})")


def discover_files(data_dir):
    """Find Grafik files and tag each with (year, is_admin)."""
    paths = sorted(glob.glob(os.path.join(data_dir, "*ГРАФИК*.xlsm")))
    files = []
    for p in paths:
        name = os.path.basename(p)
        m = YEAR_RE.match(name)          # a leading year marks a yearly archive
        if m:
            year, is_admin = int(m.group(1)), False
        else:
            year, is_admin = None, True
        files.append({"path": p, "name": name, "year": year, "is_admin": is_admin})
    return files


def choose_winner(files_for_day, day):
    """Pick the file that should provide a given day's data.

    Priority: exact-year file, then the live ADMIN file, then most recent year.
    """
    def key(f):
        exact_year = 1 if (f["year"] == day.year) else 0
        admin = 1 if f["is_admin"] else 0
        year_rank = f["year"] if f["year"] is not None else -1
        return (exact_year, admin, year_rank)

    return max(files_for_day, key=key)


def load(data_dir, db_path):
    files = discover_files(data_dir)
    if not files:
        sys.exit(f"No Grafik files found in {data_dir}")

    print(f"Found {len(files)} source file(s):")
    for f in files:
        tag = "ADMIN (live)" if f["is_admin"] else f"year {f['year']}"
        print(f"  - {f['name']}  [{tag}]")

    # --- First pass: dates per file ---------------------------------------
    print("\nScanning dates (first pass)...")
    for f in files:
        f["dates"] = gp.scan_dates(f["path"])
        print(f"  {f['name']}: {len(f['dates'])} days")

    # winner file per day
    day_to_files = {}
    for f in files:
        for d in f["dates"]:
            day_to_files.setdefault(d, []).append(f)
    winner_of, overlaps = {}, 0
    for d, cands in day_to_files.items():
        if len(cands) > 1:
            overlaps += 1
        winner_of[d] = choose_winner(cands, d)
    print(f"\nTotal distinct days: {len(winner_of)}")
    print(f"Days present in more than one file (overlaps resolved): {overlaps}")

    # --- Fresh database ----------------------------------------------------
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = db.connect(db_path)
    db.create_schema(conn)
    conn.execute("PRAGMA synchronous = OFF;")
    conn.execute("PRAGMA journal_mode = MEMORY;")

    now = datetime.datetime.now().isoformat(timespec="seconds")
    for f in files:
        cur = conn.execute(
            "INSERT INTO source_file (filename, file_year, is_admin, imported_at) "
            "VALUES (?,?,?,?)",
            (f["name"], f["year"], 1 if f["is_admin"] else 0, now))
        f["id"] = cur.lastrowid
    conn.commit()

    # --- Second pass: parse each file, write the days it owns --------------
    print("\nLoading rows (second pass)...")
    next_id = 1
    total_periods, odd_days = 0, []
    for f in files:
        owned = 0
        for day in gp.parse_grafik(f["path"]):
            if winner_of.get(day.date) is not f:
                continue
            next_id = db_writer.write_day_block(conn, day, f["id"], next_id)
            owned += 1
            total_periods += len(day.hours)
            if len(day.hours) != 24:
                odd_days.append((day.date.isoformat(), len(day.hours)))
        conn.commit()
        print(f"  loaded from {f['name']}: {owned} owned days")

    conn.close()
    print("\nDone.")
    print(f"  hourly periods loaded: {total_periods}")
    print(f"  non-24h (DST) days: {len(odd_days)} -> "
          f"{sorted(odd_days)[:6]}{' ...' if len(odd_days) > 6 else ''}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the SQLite DB from all Grafik files.")
    ap.add_argument("--data-dir", required=True, help="folder holding the xlsm files")
    ap.add_argument("--db", required=True, help="path of the SQLite database to build")
    args = ap.parse_args(argv)
    load(args.data_dir, args.db)


if __name__ == "__main__":
    main()
