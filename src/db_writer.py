"""
db_writer.py
============
The single, shared definition of *how a parsed day is written to (or removed
from) the database*.

Both the full loader and the incremental updater use these two functions, so
the way a day is stored is identical no matter which script runs.
"""

from . import columns as C

# Every table that holds per-hour rows keyed by period_id.  Order matters for
# deletion: children must go before the ``period`` rows they reference.
CHILD_TABLES = ["unit_generation", "plant_output", "reservoir_state",
                "dayahead_unit", "cascade_balance", "price"]


def delete_day(conn, date_iso):
    """Remove every stored row for one calendar day. Returns hours removed.

    Used by the updater's "newest wins" logic: wipe the day, then re-insert it
    from the new file.  Deleting per-day is safe and simple - a day is only a
    couple dozen rows across all tables.
    """
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM period WHERE date=?", (date_iso,))]
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    for table in CHILD_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE period_id IN ({placeholders})", ids)
    conn.execute("DELETE FROM period WHERE date=?", (date_iso,))
    conn.execute("DELETE FROM daily_params WHERE date=?", (date_iso,))
    return len(ids)


def write_day_block(conn, day, source_file_id, next_id):
    """Insert all rows for one DayBlock. Returns the next free period id.

    ``next_id`` is the first period id to use; we assign ids ourselves (rather
    than relying on auto-increment) so we can build every child row in one batch
    without a round-trip per row.
    """
    n_hours = len(day.hours)
    date_iso = day.date.isoformat()

    b_period, b_unit, b_plant = [], [], []
    b_res, b_da, b_casc = [], [], []

    for (seq, hour, ts, row) in day.hours:
        pid = next_id
        next_id += 1

        b_period.append((pid, ts, date_iso, hour, seq, n_hours, source_file_id))

        # unit-level gross generation
        for plant, unit_no, idx in C.UNIT_GROSS:
            b_unit.append((pid, plant, unit_no, _num(row, idx)))

        # plant-level gross + net
        for plant, gidx, nidx in C.PLANT_OUTPUT:
            b_plant.append((pid, plant, _num(row, gidx), _num(row, nidx)))

        # reservoir water-balance state
        for res, idxs in C.RESERVOIR_COLS.items():
            vals = [_num(row, i) for i in idxs]
            b_res.append((pid, res, *vals))

        # archived day-ahead schedule per unit
        for plant, unit_no, idx in C.DAYAHEAD_UNIT:
            b_da.append((pid, plant, unit_no, _num(row, idx)))

        # cascade totals / schedule / imbalance / intraday
        b_casc.append((
            pid,
            _num(row, C.CASCADE["actual_gross"]), _num(row, C.CASCADE["actual_net"]),
            _num(row, C.CASCADE["submitted_gross"]), _num(row, C.CASCADE["submitted_net"]),
            _num(row, C.CASCADE["dayahead_gross"]), _num(row, C.CASCADE["dayahead_net"]),
            _num(row, C.CASCADE["imbalance_gross"]), _num(row, C.CASCADE["imbalance_net"]),
            _num(row, C.CASCADE["intraday_mwh"]),
        ))

    conn.executemany(
        "INSERT OR IGNORE INTO period "
        "(id, ts_eet, date, hour, seq, n_hours_in_day, source_file_id) "
        "VALUES (?,?,?,?,?,?,?)", b_period)
    conn.executemany("INSERT OR IGNORE INTO unit_generation VALUES (?,?,?,?)", b_unit)
    conn.executemany("INSERT OR IGNORE INTO plant_output VALUES (?,?,?,?)", b_plant)
    conn.executemany("INSERT OR IGNORE INTO reservoir_state VALUES (?,?,?,?,?,?,?,?,?)", b_res)
    conn.executemany("INSERT OR IGNORE INTO dayahead_unit VALUES (?,?,?,?)", b_da)
    conn.executemany("INSERT OR IGNORE INTO cascade_balance VALUES (?,?,?,?,?,?,?,?,?,?)", b_casc)

    # One daily_params row.  REPLACE so an update refreshes it in place.
    conn.execute(
        "INSERT OR REPLACE INTO daily_params "
        "(date, k_source_coeff, target_rila, target_pastra, target_kamenitza, "
        " status, source_file_id) VALUES (?,?,?,?,?,?,?)",
        (date_iso, day.k_coeff, day.target_rila, day.target_pastra,
         day.target_kamenitza, day.status, source_file_id))

    return next_id


def _num(row, idx):
    """Read one value from a raw row tuple as a float (or None)."""
    # Imported here (not at top) to avoid a circular import surprise; the parser
    # already defines the canonical num().
    from .grafik_parser import num
    return num(row[idx])
