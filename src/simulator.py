"""
simulator.py
============
The forward simulator: given a *starting level* for each reservoir and a
*proposed generation schedule*, it projects the reservoir levels forward over
any horizon - using only its own computed values, no stored data.

This is the piece the optimizer will lean on: to score a candidate schedule it
asks "if the plants ran like THIS, where do the levels go, and does anything
spill or run dry?"  The validation engine (validate_physics.py) predicts each
hour from the *stored* previous level; the simulator instead chains on its *own*
previous level, which is what a real forecast run must do.

Inputs it needs each hour
-------------------------
* the generation of each plant (the schedule we are testing), and
* the side inflow into each reservoir.

Because the inflow forecast is not built yet, inflows are supplied to the
simulator as a per-reservoir series.  The helper ``project_inflow`` builds such a
series from recent observed inflows - either held flat (persistence) or nudged
along a simple trend - matching the "use the trend, else hold the last good
value" rule we agreed on.

Spill / cap handling (interim)
------------------------------
When a reservoir would rise above its spillway cap, the level is clamped and the
hour is flagged as ``spilled``.  The overflow is NOT yet routed downstream - that
is the next planned step.  The flags make those hours explicit rather than
silently wrong.
"""

from .physics import Cascade

RESERVOIRS = ("Kamenitza", "Pastra", "Rila")


def project_inflow(recent_values, horizon, method="flat"):
    """Build a future inflow series from recently observed inflows.

    method "flat" holds the last observed value across the horizon (persistence);
    "trend" continues the average hour-to-hour change of the recent window.
    """
    if not recent_values:
        return [0.0] * horizon
    last = recent_values[-1]
    if method == "flat" or len(recent_values) < 2:
        return [last] * horizon
    deltas = [recent_values[i] - recent_values[i - 1]
              for i in range(1, len(recent_values))]
    slope = sum(deltas) / len(deltas)
    return [last + slope * (i + 1) for i in range(horizon)]


class SimulationResult:
    """Output of a simulation run: projected levels, spill flags, inflows used."""

    def __init__(self):
        self.levels = {r: [] for r in RESERVOIRS}
        self.spilled = {r: [] for r in RESERVOIRS}
        self.inflow_used = {r: [] for r in RESERVOIRS}

    def any_spill(self):
        return any(any(s) for s in self.spilled.values())

    def final_levels(self):
        return {r: (self.levels[r][-1] if self.levels[r] else None)
                for r in RESERVOIRS}


class ForwardSimulator:
    """Projects reservoir levels forward from a schedule and a starting level."""

    def __init__(self, cascade=None):
        self.cascade = cascade or Cascade()

    def _inflow_series(self, reservoir, horizon, inflows, persist_inflow):
        if inflows and inflows.get(reservoir) is not None:
            series = list(inflows[reservoir])
            if len(series) < horizon:
                series += [series[-1]] * (horizon - len(series))
            return series[:horizon]
        value = (persist_inflow or {}).get(reservoir, 0.0)
        return [value] * horizon

    def run(self, start_levels, schedule, inflows=None, persist_inflow=None, k=0.8):
        """Project levels forward. See module docstring for the parameter meanings."""
        horizon = len(schedule)
        casc = self.cascade
        result = SimulationResult()

        inflow = {r: self._inflow_series(r, horizon, inflows, persist_inflow)
                  for r in RESERVOIRS}
        prev = {r: start_levels[r] for r in RESERVOIRS}

        for h in range(horizon):
            gen = schedule[h]
            rila_g = gen.get("Rila", 0.0) or 0.0
            pastra_g = gen.get("Pastra", 0.0) or 0.0
            kam_g = gen.get("Kamenitza", 0.0) or 0.0
            kalin_g = gen.get("Kalin", 0.0) or 0.0
            k_h = k[h] if isinstance(k, list) else k

            net = casc.kamenitza_net(inflow["Kamenitza"][h], kam_g, kalin_g, k_h)
            lvl_k, sp_k = casc.kamenitza.next_level_detailed(prev["Kamenitza"], net)

            net = casc.pastra_net(inflow["Pastra"][h], pastra_g, kam_g)
            lvl_p, sp_p = casc.pastra.next_level_detailed(prev["Pastra"], net)

            net = casc.rila_net(inflow["Rila"][h], rila_g, pastra_g)
            lvl_r, sp_r = casc.rila.next_level_detailed(prev["Rila"], net)

            result.levels["Kamenitza"].append(lvl_k); result.spilled["Kamenitza"].append(sp_k)
            result.levels["Pastra"].append(lvl_p);    result.spilled["Pastra"].append(sp_p)
            result.levels["Rila"].append(lvl_r);      result.spilled["Rila"].append(sp_r)

            prev = {"Kamenitza": lvl_k, "Pastra": lvl_p, "Rila": lvl_r}
            for r in RESERVOIRS:
                result.inflow_used[r].append(inflow[r][h])

        return result


# --- Demo / sanity check on one historical day (also a usage example) -------
def _demo(db_path, day):
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT p.seq,
          MAX(CASE WHEN po.plant='Rila'      THEN po.gross_mwh END),
          MAX(CASE WHEN po.plant='Pastra'    THEN po.gross_mwh END),
          MAX(CASE WHEN po.plant='Kamenitza' THEN po.gross_mwh END),
          MAX(CASE WHEN po.plant='Kalin'     THEN po.gross_mwh END),
          MAX(CASE WHEN r.reservoir='Kamenitza' THEN r.inflow_calc END),
          MAX(CASE WHEN r.reservoir='Pastra'    THEN r.inflow_calc END),
          MAX(CASE WHEN r.reservoir='Rila'      THEN r.inflow_calc END),
          MAX(CASE WHEN r.reservoir='Kamenitza' THEN r.level_calc END),
          MAX(CASE WHEN r.reservoir='Pastra'    THEN r.level_calc END),
          MAX(CASE WHEN r.reservoir='Rila'      THEN r.level_calc END)
        FROM period p
        LEFT JOIN plant_output po   ON po.period_id=p.id
        LEFT JOIN reservoir_state r ON r.period_id=p.id
        WHERE p.date=? GROUP BY p.id ORDER BY p.seq""", (day,)).fetchall()
    if not rows:
        print(f"(no data for {day})"); conn.close(); return

    schedule, inflows = [], {"Kamenitza": [], "Pastra": [], "Rila": []}
    stored = {"Kamenitza": [], "Pastra": [], "Rila": []}
    for (_seq, rila, pas, kam, kal, ik, ip, ir, lk, lp, lr) in rows:
        schedule.append({"Rila": rila, "Pastra": pas, "Kamenitza": kam, "Kalin": kal})
        inflows["Kamenitza"].append(ik or 0.0)
        inflows["Pastra"].append(ip or 0.0)
        inflows["Rila"].append(ir or 0.0)
        stored["Kamenitza"].append(lk); stored["Pastra"].append(lp); stored["Rila"].append(lr)

    seed = conn.execute("""
        SELECT MAX(CASE WHEN r.reservoir='Kamenitza' THEN r.level_forecast END),
               MAX(CASE WHEN r.reservoir='Pastra'    THEN r.level_forecast END),
               MAX(CASE WHEN r.reservoir='Rila'      THEN r.level_forecast END)
        FROM period p JOIN reservoir_state r ON r.period_id=p.id
        WHERE p.date < ? GROUP BY p.id ORDER BY p.date DESC, p.seq DESC LIMIT 1""",
        (day,)).fetchall()
    krow = conn.execute("SELECT k_source_coeff FROM daily_params WHERE date=?", (day,)).fetchall()
    conn.close()

    start = {"Kamenitza": seed[0][0], "Pastra": seed[0][1], "Rila": seed[0][2]}
    kval = (krow[0][0] if krow and krow[0][0] is not None else 0.8)

    res = ForwardSimulator().run(start, schedule, inflows=inflows, k=kval)

    print(f"\nForward simulation of {day} (projected | stored | diff), seed={start}")
    print(f"{'h':>3}  {'Kamenitza':^22} {'Pastra':^22} {'Rila':^22}")
    for h in range(len(schedule)):
        def cell(r):
            p = res.levels[r][h]; s = stored[r][h]
            d = (p - s) if s is not None else 0.0
            sp = "*" if res.spilled[r][h] else " "
            sval = s if s is not None else float("nan")
            return f"{p:6.3f}{sp}|{sval:6.3f}|{d:+.3f}"
        print(f"{h:>3}  {cell('Kamenitza')}  {cell('Pastra')}  {cell('Rila')}")
    print(f"\nfinal levels: {res.final_levels()}   any spill: {res.any_spill()}")
    print("(* = hour flagged as spilling / at cap)")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Demo the forward simulator on one historical day.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--day", default="2025-11-01")
    args = ap.parse_args(argv)
    _demo(args.db, args.day)


if __name__ == "__main__":
    main()
