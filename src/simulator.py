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


class SpillConfig:
    """Tunable parameters for routing spilled water downstream.

    None of these are measured (we cannot see real spill), so they are estimates
    you can adjust.  Each downstream link has:
      * a delay (whole hours) - how long spilled water takes to arrive below, and
      * a transfer coefficient - how much arrives, in the DOWNSTREAM reservoir's
        MWh-equivalent units per unit of upstream overflow.  This lumps together
        the unit conversion between reservoirs and the losses (soil absorption,
        partial capture) we cannot separate without data.

    Defaults mirror the turbined-path conversions we decoded from the sheet
    (Kamenitza->Pastra ~0.10, Pastra->Rila ~1.12).  Later, when weather / soil
    data exists, the delays and transfers can be made condition-dependent.
    """

    def __init__(self,
                 kam_to_pastra_delay=3, kam_to_pastra_transfer=0.10,
                 pastra_to_rila_delay=1, pastra_to_rila_transfer=1.12,
                 route=True):
        self.kam_to_pastra_delay = kam_to_pastra_delay
        self.kam_to_pastra_transfer = kam_to_pastra_transfer
        self.pastra_to_rila_delay = pastra_to_rila_delay
        self.pastra_to_rila_transfer = pastra_to_rila_transfer
        # route=False falls back to plain clamp-at-cap (no downstream routing).
        self.route = route


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
        # Water that overflowed each reservoir this hour (its own MWh-equiv units).
        self.spill_water = {r: [] for r in RESERVOIRS}
        # Total water that left the whole cascade via Rila's spill (accounting).
        self.spill_out_total = 0.0

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

    def run(self, start_levels, schedule, inflows=None, persist_inflow=None,
            k=0.8, spill=None):
        """Project levels forward. See module docstring for the parameter meanings.

        ``spill`` is a SpillConfig controlling how overflow is routed downstream;
        if None, a default SpillConfig is used.
        """
        horizon = len(schedule)
        casc = self.cascade
        cfg = spill or SpillConfig()
        result = SimulationResult()

        inflow = {r: self._inflow_series(r, horizon, inflows, persist_inflow)
                  for r in RESERVOIRS}
        prev = {r: start_levels[r] for r in RESERVOIRS}

        # Buffers holding spilled water that arrives at a downstream reservoir in
        # a FUTURE hour.  Index = hour of arrival; value = extra inflow to add
        # (already in the downstream reservoir's MWh-equivalent units).
        arrive_pastra = [0.0] * (horizon + cfg.kam_to_pastra_delay + 1)
        arrive_rila = [0.0] * (horizon + cfg.pastra_to_rila_delay + 1)

        for h in range(horizon):
            gen = schedule[h]
            rila_g = gen.get("Rila", 0.0) or 0.0
            pastra_g = gen.get("Pastra", 0.0) or 0.0
            kam_g = gen.get("Kamenitza", 0.0) or 0.0
            kalin_g = gen.get("Kalin", 0.0) or 0.0
            k_h = k[h] if isinstance(k, list) else k

            # --- Kamenitza (top of the modelled chain) ---------------------
            net = casc.kamenitza_net(inflow["Kamenitza"][h], kam_g, kalin_g, k_h)
            lvl_k, sp_k, over_k = casc.kamenitza.step_with_spill(prev["Kamenitza"], net)
            if cfg.route and over_k > 0:
                # schedule the overflow to arrive at Pastra after the delay
                arrive_pastra[h + cfg.kam_to_pastra_delay] += over_k * cfg.kam_to_pastra_transfer

            # --- Pastra (gets its own inflow + any Kamenitza spill arriving now) ---
            pastra_inflow = inflow["Pastra"][h] + arrive_pastra[h]
            net = casc.pastra_net(pastra_inflow, pastra_g, kam_g)
            lvl_p, sp_p, over_p = casc.pastra.step_with_spill(prev["Pastra"], net)
            if cfg.route and over_p > 0:
                arrive_rila[h + cfg.pastra_to_rila_delay] += over_p * cfg.pastra_to_rila_transfer

            # --- Rila (gets its own inflow + any Pastra spill arriving now) -------
            rila_inflow = inflow["Rila"][h] + arrive_rila[h]
            net = casc.rila_net(rila_inflow, rila_g, pastra_g)
            lvl_r, sp_r, over_r = casc.rila.step_with_spill(prev["Rila"], net)
            if cfg.route and over_r > 0:
                # Rila is the bottom: its overflow leaves the cascade.
                result.spill_out_total += over_r

            # record
            result.levels["Kamenitza"].append(lvl_k); result.spilled["Kamenitza"].append(sp_k)
            result.levels["Pastra"].append(lvl_p);    result.spilled["Pastra"].append(sp_p)
            result.levels["Rila"].append(lvl_r);      result.spilled["Rila"].append(sp_r)
            result.spill_water["Kamenitza"].append(over_k)
            result.spill_water["Pastra"].append(over_p)
            result.spill_water["Rila"].append(over_r)
            result.inflow_used["Kamenitza"].append(inflow["Kamenitza"][h])
            result.inflow_used["Pastra"].append(pastra_inflow)
            result.inflow_used["Rila"].append(rila_inflow)

            prev = {"Kamenitza": lvl_k, "Pastra": lvl_p, "Rila": lvl_r}

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
