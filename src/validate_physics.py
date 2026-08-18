"""
validate_physics.py
===================
Checks the physics engine (physics.py) against the ``level_calc`` values already
stored in the database - i.e. does our Python reproduce the spreadsheet?

Two views:

1. Sample day - print, hour by hour, our predicted level next to the stored one
   for all three reservoirs, so the logic can be eyeballed.

2. Full history - a "one-step" test over every hour: predict this hour's level
   from the *stored* previous level and compare to the stored current level.
   Predicting one step at a time isolates the physics (no accumulated rounding),
   so it is the cleanest pass/fail on whether the engine matches the sheet.

The "previous level" is:
  * within a day: the previous hour's ``level_calc``;
  * at the first hour of a day: the previous day's last ``level_forecast``
    (the sheet re-anchors the calculated series to reality each day).

Run
---
    python -m src.validate_physics --db data/rila_cascade.db
    python -m src.validate_physics --db data/rila_cascade.db --day 2025-11-01
"""

import argparse
import sqlite3

from .physics import Cascade


def load_rows(conn):
    """Load every hour with the inputs the balance needs, ordered in time.

    Returns a list of dict rows with generation (per plant), each reservoir's
    side inflow / stored level_calc / stored level_forecast, and the day's K.
    """
    sql = """
    SELECT p.id, p.date, p.seq,
           MAX(CASE WHEN po.plant='Rila'      THEN po.gross_mwh END) AS rila_gen,
           MAX(CASE WHEN po.plant='Pastra'    THEN po.gross_mwh END) AS pastra_gen,
           MAX(CASE WHEN po.plant='Kamenitza' THEN po.gross_mwh END) AS kam_gen,
           MAX(CASE WHEN po.plant='Kalin'     THEN po.gross_mwh END) AS kalin_gen,
           MAX(CASE WHEN r.reservoir='Rila'      THEN r.inflow_calc END)     AS rila_inf,
           MAX(CASE WHEN r.reservoir='Pastra'    THEN r.inflow_calc END)     AS pastra_inf,
           MAX(CASE WHEN r.reservoir='Kamenitza' THEN r.inflow_calc END)     AS kam_inf,
           MAX(CASE WHEN r.reservoir='Rila'      THEN r.level_calc END)      AS rila_lvl,
           MAX(CASE WHEN r.reservoir='Pastra'    THEN r.level_calc END)      AS pastra_lvl,
           MAX(CASE WHEN r.reservoir='Kamenitza' THEN r.level_calc END)      AS kam_lvl,
           MAX(CASE WHEN r.reservoir='Rila'      THEN r.level_forecast END)  AS rila_fc,
           MAX(CASE WHEN r.reservoir='Pastra'    THEN r.level_forecast END)  AS pastra_fc,
           MAX(CASE WHEN r.reservoir='Kamenitza' THEN r.level_forecast END)  AS kam_fc,
           dp.k_source_coeff AS k
    FROM period p
    LEFT JOIN plant_output po   ON po.period_id = p.id
    LEFT JOIN reservoir_state r ON r.period_id = p.id
    LEFT JOIN daily_params dp   ON dp.date = p.date
    GROUP BY p.id
    ORDER BY p.date, p.seq
    """
    cols = None
    rows = []
    for rec in conn.execute(sql):
        if cols is None:
            cols = [d[0] for d in conn.execute(sql).description]
        rows.append(dict(zip(
            ["id", "date", "seq", "rila_gen", "pastra_gen", "kam_gen", "kalin_gen",
             "rila_inf", "pastra_inf", "kam_inf", "rila_lvl", "pastra_lvl", "kam_lvl",
             "rila_fc", "pastra_fc", "kam_fc", "k"], rec)))
    return rows


def g(x):
    """Treat missing generation/inflow as 0.0 (an idle plant contributes nothing)."""
    return 0.0 if x is None else x


def predicted_levels(prev, row, casc):
    """Predict all three levels for one hour from the previous levels in ``prev``.

    ``prev`` is a dict with 'Kamenitza'/'Pastra'/'Rila' previous levels.
    """
    kam = casc.step_kamenitza(prev["Kamenitza"], g(row["kam_inf"]),
                              g(row["kam_gen"]), g(row["kalin_gen"]), g(row["k"]))
    pas = casc.step_pastra(prev["Pastra"], g(row["pastra_inf"]),
                           g(row["pastra_gen"]), g(row["kam_gen"]))
    ril = casc.step_rila(prev["Rila"], g(row["rila_inf"]),
                         g(row["rila_gen"]), g(row["pastra_gen"]))
    return {"Kamenitza": kam, "Pastra": pas, "Rila": ril}


def sample_day(rows, day):
    """Print predicted vs stored levels, hour by hour, for one day."""
    casc = Cascade()
    # index rows by (date, seq) so we can find the seed (previous day's last forecast)
    by_key = {(r["date"], r["seq"]): r for r in rows}
    day_rows = sorted([r for r in rows if r["date"] == day], key=lambda r: r["seq"])
    if not day_rows:
        print(f"(no rows for {day})")
        return

    # seed = previous day's last forecast level, per reservoir
    prev_day_rows = sorted([r for r in rows if r["date"] < day], key=lambda r: (r["date"], r["seq"]))
    if prev_day_rows:
        last = prev_day_rows[-1]
        prev = {"Kamenitza": g(last["kam_fc"]), "Pastra": g(last["pastra_fc"]),
                "Rila": g(last["rila_fc"])}
    else:
        first = day_rows[0]
        prev = {"Kamenitza": g(first["kam_fc"]), "Pastra": g(first["pastra_fc"]),
                "Rila": g(first["rila_fc"])}

    print(f"\nSample day {day}  (predicted | stored | diff)")
    print(f"{'h':>3}  {'Kamenitza':^24} {'Pastra':^24} {'Rila':^24}")
    for r in day_rows:
        pred = predicted_levels(prev, r, casc)
        def cell(res, stored):
            p = pred[res]; s = stored
            d = (p - s) if s is not None else None
            return f"{p:6.3f}|{(s if s is not None else float('nan')):6.3f}|{(d if d is not None else 0):+.3f}"
        print(f"{r['seq']:>3}  {cell('Kamenitza', r['kam_lvl'])}  "
              f"{cell('Pastra', r['pastra_lvl'])}  {cell('Rila', r['rila_lvl'])}")
        # chain forward on OUR predicted values (full-day chaining view)
        prev = pred


def full_validation(rows):
    """One-step test across all hours; report error stats per reservoir."""
    casc = Cascade()
    # For each hour we need the previous level.  Build quick lookups.
    by_key = {(r["date"], r["seq"]): r for r in rows}
    # ordered dates to find "previous day"
    dates = sorted({r["date"] for r in rows})
    prev_date = {dates[i]: dates[i - 1] for i in range(1, len(dates))}
    # last seq per date
    last_seq = {}
    for r in rows:
        last_seq[r["date"]] = max(last_seq.get(r["date"], -1), r["seq"])

    stats = {res: {"n": 0, "sum_abs": 0.0, "max_abs": 0.0, "le1mm": 0, "le5mm": 0,
                   "worst": None}
             for res in ("Kamenitza", "Pastra", "Rila")}

    for r in rows:
        # Determine the previous level per reservoir (stored values).
        if r["seq"] > 0:
            prev_row = by_key.get((r["date"], r["seq"] - 1))
            prev = {"Kamenitza": prev_row["kam_lvl"], "Pastra": prev_row["pastra_lvl"],
                    "Rila": prev_row["rila_lvl"]}
        else:
            pd = prev_date.get(r["date"])
            if pd is None:
                continue  # very first day has no seed; skip its hour 0
            prev_row = by_key.get((pd, last_seq[pd]))
            prev = {"Kamenitza": prev_row["kam_fc"], "Pastra": prev_row["pastra_fc"],
                    "Rila": prev_row["rila_fc"]}

        # Skip if any needed previous level is missing.
        if any(prev[res] is None for res in prev):
            continue

        pred = predicted_levels(prev, r, casc)
        stored = {"Kamenitza": r["kam_lvl"], "Pastra": r["pastra_lvl"], "Rila": r["rila_lvl"]}
        for res in ("Kamenitza", "Pastra", "Rila"):
            if stored[res] is None:
                continue
            err = abs(pred[res] - stored[res])
            s = stats[res]
            s["n"] += 1
            s["sum_abs"] += err
            if err > s["max_abs"]:
                s["max_abs"] = err
                s["worst"] = (r["date"], r["seq"], pred[res], stored[res])
            if err <= 0.001:
                s["le1mm"] += 1
            if err <= 0.005:
                s["le5mm"] += 1

    print("\nFull one-step validation (predict each hour from the stored previous level)")
    print(f"{'reservoir':<11} {'hours':>7} {'mean|err|':>10} {'max|err|':>9} "
          f"{'<=1mm':>7} {'<=5mm':>7}  worst")
    for res in ("Kamenitza", "Pastra", "Rila"):
        s = stats[res]
        if s["n"] == 0:
            continue
        mean = s["sum_abs"] / s["n"]
        p1 = 100.0 * s["le1mm"] / s["n"]
        p5 = 100.0 * s["le5mm"] / s["n"]
        w = s["worst"]
        wtxt = f"{w[0]} h{w[1]} pred={w[2]:.3f} stored={w[3]:.3f}" if w else ""
        print(f"{res:<11} {s['n']:>7} {mean:>10.5f} {s['max_abs']:>9.3f} "
              f"{p1:>6.2f}% {p5:>6.2f}%  {wtxt}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate the physics engine vs stored levels.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--day", default="2025-11-01", help="day to show in detail (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    rows = load_rows(conn)
    conn.close()

    sample_day(rows, args.day)
    full_validation(rows)


if __name__ == "__main__":
    main()
