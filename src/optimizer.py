"""
optimizer.py
============
Version 1 of the scheduler: an *interpretable* single-day optimiser that mirrors
how the operators actually work - follow the price, concentrate generation in
the high-price hours, spend only the water that is available, and respect every
unit and operator constraint.

It is deliberately transparent (you can read exactly why each hour was chosen)
rather than provably optimal.  It reuses everything we built:
  * units.py       - each unit's envelope and no-go bands,
  * constraints.py - the operator's per-run overrides and targets,
  * simulator.py   - to project the resulting reservoir levels and check them.

The strategy, in four steps
---------------------------
1. Daily energy budget per plant - how many MWh each plant may generate today,
   from the water it can draw (start level down to its target) plus inflows.
2. Price allocation - rank the 24 hours by price and pour each plant's budget
   into the highest-price hours first (this is the "clustering" behaviour).
3. Unit dispatch - split each plant's hourly MW across its units, honouring
   min/max, no-go bands, the Rila combined cap and island-mode rule, and any
   availability / power overrides from the constraints.
4. Simulate & check - run the schedule through the forward simulator, report the
   resulting levels, any limit violations, and the revenue.

Simplifications in v1 (documented, to refine later):
  * Revenue uses gross generation x price (net-of-losses comes later).
  * Kalin's daily budget is an operator-set number (the dams are not level-
    tracked here); the other plants' budgets come from their reservoirs.
  * Unit splitting is a sensible greedy pass, not an exhaustive optimum.
  * Level violations are reported, not auto-repaired.
"""

import datetime

from .units import PLANTS, get_plant
from .simulator import ForwardSimulator, SpillConfig

# Representative level-drop coefficient per plant's own reservoir, used to turn
# "metres of level room" into "MWh of generation".  (Kamenitza is banded; we use
# a mid-band value for the budget estimate only - the simulator uses the exact
# banded coefficient.)
PLANT_RESERVOIR = {"Rila": ("Rila", 0.30), "Pastra": ("Pastra", 0.50),
                   "Kamenitza": ("Kamenitza", 0.20)}

# Physical lower gauge limits (m) that always apply, independent of any operator
# level limit.  A reservoir should never be scheduled below these.
RESERVOIR_FLOORS = {"Kamenitza": 4.0, "Pastra": 2.115, "Rila": 1.5}


def synthetic_two_peak_prices(n=24, base=70.0, morning=140.0, evening=170.0):
    """A placeholder day-ahead price curve with a morning and an evening peak.

    Real IBEX prices will replace this via the database `price` table; the
    optimiser only needs a 24-value list, so swapping the source changes nothing.
    """
    prices = [base] * n
    for h in range(6, 10):      # morning peak 06:00-09:00
        prices[h] = morning
    for h in range(17, 22):     # evening peak 17:00-21:00
        prices[h] = evening
    return prices


class DayOptimizer:
    """Builds a single day's unit schedule by the heuristic above."""

    def __init__(self, cascade=None):
        self.sim = ForwardSimulator(cascade)

    # --- step 1: how much energy each plant may make today -----------------
    def plant_energy_budget(self, plant_name, start_level, target_level, inflows):
        """MWh a plant may generate today, ending near its target level.

        = water freed by drawing the reservoir from start down to target
          (in MWh via the drop coefficient) + the inflow arriving during the day.
        Never negative.
        """
        _res, coeff = PLANT_RESERVOIR[plant_name]
        drawdown_mwh = (start_level - target_level) / coeff
        return max(0.0, drawdown_mwh + sum(inflows))

    # --- step 2: pour a plant's budget into the best-priced hours ----------
    def allocate_by_price(self, budget_mwh, prices, plant_max, must_run_min=0.0):
        """Return an hourly plant-MW profile that spends the budget on peak prices.

        Every hour starts at ``must_run_min`` (0 unless the plant is must-run),
        then the remaining budget is filled into the highest-price hours up to
        ``plant_max`` each.
        """
        n = len(prices)
        alloc = [must_run_min] * n
        remaining = budget_mwh - must_run_min * n
        # highest price first
        for h in sorted(range(n), key=lambda i: prices[i], reverse=True):
            if remaining <= 0:
                break
            room = plant_max - alloc[h]
            add = min(room, remaining)
            if add > 0:
                alloc[h] += add
                remaining -= add
        return alloc

    # --- step 3: split a plant's hourly MW across its units ----------------
    def dispatch_units(self, plant_name, target_mw, ts, constraints):
        """Distribute ``target_mw`` across a plant's units for one hour.

        Honours each unit's availability/limits (from constraints), min/max and
        no-go bands (from units.py), the Rila simultaneous caps and combined cap,
        and the island-mode rule.  Returns {unit_no: mw}.
        """
        plant = get_plant(plant_name)

        # units that are available this hour, with their effective bounds
        avail = []
        for u in plant.units:
            lo, hi, ok = constraints.effective_unit_bounds(plant_name, u.unit_no, ts)
            if ok and hi > 0:
                avail.append((u, lo, hi))

        # respect the plant's combined cap (e.g. Rila 10 MW)
        cap_total = plant.combined_max_mw or sum(hi for _, _, hi in avail)
        target = min(target_mw, cap_total)

        dispatch = {u.unit_no: 0.0 for u in plant.units}
        remaining = target
        for u, lo, hi in avail:
            if remaining <= 1e-9:
                break
            # when several units co-operate, Rila lowers each unit's cap
            sim_cap = plant.simultaneous_caps.get(u.unit_no, hi)
            hi_eff = min(hi, sim_cap)
            want = min(hi_eff, remaining)
            if want < lo:                       # not enough left to reach this unit's min
                if remaining >= lo:
                    want = lo
                else:
                    continue
            want = u.clamp_to_feasible(want)    # step out of any no-go band
            dispatch[u.unit_no] = want
            remaining -= want

        # island-mode: if Rila Unit 1 ended up running alone, switch it off (safe v1 choice)
        viol = plant.companion_online({n: mw for n, mw in dispatch.items() if mw > 0})
        for uno in viol:
            dispatch[uno] = 0.0

        return dispatch

    # --- build the detailed schedule from plant MW profiles ----------------
    def _build_schedule(self, plant_profile, timestamps, constraints):
        """Turn per-plant hourly MW profiles into schedule + unit_schedule."""
        schedule, unit_schedule = [], []
        for h, ts in enumerate(timestamps):
            hour_units, hour_plants = {}, {}
            for name in ("Kamenitza", "Pastra", "Rila"):
                disp = self.dispatch_units(name, plant_profile[name][h], ts, constraints)
                for uno, mw in disp.items():
                    hour_units[(name, uno)] = mw
                hour_plants[name] = sum(disp.values())
            kdisp = self.dispatch_units("Kalin", plant_profile["Kalin"][h], ts, constraints)
            hour_units[("Kalin", 1)] = kdisp.get(1, 0.0)
            hour_plants["Kalin"] = kdisp.get(1, 0.0)
            unit_schedule.append(hour_units)
            schedule.append(hour_plants)
        return schedule, unit_schedule

    def _find_floor_violations(self, levels, constraints, timestamps):
        """List (reservoir, hour, deficit_m) where a level is below its floor.

        The effective floor is the stricter of the reservoir's physical gauge
        floor and any temporary operator limit.
        """
        out = []
        for h, ts in enumerate(timestamps):
            for r in ("Kamenitza", "Pastra", "Rila"):
                lo, _hi = constraints.level_bounds(r, ts)
                floor = RESERVOIR_FLOORS[r]
                if lo is not None:
                    floor = max(floor, lo)
                if levels[r][h] < floor - 1e-6:
                    out.append((r, h, floor - levels[r][h]))
        return out

    def _reduce_plant(self, profile, prices, up_to_hour, amount, floor_mw=0.0):
        """Cut ``amount`` MWh from a plant profile, in its cheapest hours first.

        Only hours 0..up_to_hour are touched (the water that caused the early
        violation was spent by then).  Generation is never taken below ``floor_mw``
        (used to keep Kamenitza's must-run minimum).  Returns MWh actually cut.
        """
        cut = 0.0
        for h in sorted(range(up_to_hour + 1), key=lambda i: prices[i]):
            if amount - cut <= 1e-9:
                break
            take = min(profile[h] - floor_mw, amount - cut)
            if take > 0:
                profile[h] -= take
                cut += take
        return cut

    # --- the full run ------------------------------------------------------
    def optimize(self, timestamps, prices, start_levels, targets, inflows,
                 constraints, kalin_budget_mwh=0.0, k=0.8, supply_min=0.5,
                 max_repairs=40):
        """Produce a day's schedule (price-following, floor-respecting).

        See the module docstring for the strategy.  After the initial price
        allocation the schedule is simulated and repaired: any reservoir that
        would drop below a temporary floor triggers a cut to that plant's
        cheapest hours, until the floors hold or the repair budget is spent.
        """
        n = len(timestamps)
        plant_max = {"Kamenitza": 3.06, "Pastra": 6.20, "Rila": 10.0, "Kalin": 3.90}

        # step 1+2: initial plant-level MW profiles from budgets + price ranking
        plant_profile = {}
        for name in ("Kamenitza", "Pastra", "Rila"):
            budget = self.plant_energy_budget(name, start_levels[name],
                                              targets[name], inflows[name])
            mrun = supply_min if name == "Kamenitza" else 0.0
            plant_profile[name] = self.allocate_by_price(budget, prices,
                                                         plant_max[name], mrun)
        plant_profile["Kalin"] = self.allocate_by_price(kalin_budget_mwh, prices,
                                                        plant_max["Kalin"], 0.0)

        reservoir_plant = {"Kamenitza": "Kamenitza", "Pastra": "Pastra", "Rila": "Rila"}

        # step 3+4: build, simulate, and repair floor violations
        for _ in range(max_repairs):
            schedule, unit_schedule = self._build_schedule(plant_profile, timestamps, constraints)
            res = self.sim.run(start_levels, schedule, inflows=inflows, k=k)
            viols = self._find_floor_violations(res.levels, constraints, timestamps)
            if not viols:
                break
            # repair the earliest violation first (it feeds the later ones)
            viols.sort(key=lambda v: v[1])
            r, h, deficit_m = viols[0]
            _res_name, coeff = PLANT_RESERVOIR[r]
            cut_mwh = deficit_m / coeff + 0.05           # small margin
            floor_mw = supply_min if r == "Kamenitza" else 0.0
            self._reduce_plant(plant_profile[reservoir_plant[r]], prices, h, cut_mwh, floor_mw)

        # final build + simulate (authoritative)
        schedule, unit_schedule = self._build_schedule(plant_profile, timestamps, constraints)
        res = self.sim.run(start_levels, schedule, inflows=inflows, k=k)

        revenue = sum(sum(hp.values()) * prices[h] for h, hp in enumerate(schedule))
        violations = []
        for h, ts in enumerate(timestamps):
            for r in ("Kamenitza", "Pastra", "Rila"):
                lo, hi = constraints.level_bounds(r, ts)
                lvl = res.levels[r][h]
                if lo is not None and lvl < lo - 1e-6:
                    violations.append((ts, r, "below", round(lvl, 3), lo))
                if hi is not None and lvl > hi + 1e-6:
                    violations.append((ts, r, "above", round(lvl, 3), hi))

        return {"schedule": schedule, "unit_schedule": unit_schedule,
                "levels": res.levels, "spilled": res.spilled,
                "revenue": revenue, "violations": violations,
                "plant_profile": plant_profile}


# ---------------------------------------------------------------------------
# Demo: optimise one day under the live scenario.
# ---------------------------------------------------------------------------
def _demo():
    from .constraints import Constraints

    day = datetime.datetime(2026, 8, 19, 0, 0)
    timestamps = [day + datetime.timedelta(hours=h) for h in range(24)]
    prices = synthetic_two_peak_prices()

    start_levels = {"Kamenitza": 9.5, "Pastra": 6.8, "Rila": 3.5}
    targets = {"Kamenitza": 9.0, "Pastra": 7.0, "Rila": 3.0}   # Pastra held high (construction)
    inflows = {"Kamenitza": [1.0] * 24, "Pastra": [1.2] * 24, "Rila": [1.0] * 24}

    c = Constraints()
    # Pastra Unit 2 out of service all day; hold Pastra near 7 m; supply min 0.5
    c.set_unit_unavailable("Pastra", 2, start=timestamps[0], end=timestamps[-1] + datetime.timedelta(hours=1))
    c.set_target("Pastra", level=7.0, stiffness=5.0)
    c.set_level_limit("Pastra", min_level=6.8)   # don't let construction pool drop

    opt = DayOptimizer()
    out = opt.optimize(timestamps, prices, start_levels, targets, inflows, c,
                       kalin_budget_mwh=20.0, supply_min=0.5)

    print("Single-day heuristic schedule (live scenario)")
    print("=" * 74)
    print(f"{'h':>2} {'price':>6} | {'Kam':>5} {'Pas':>5} {'Rila':>5} {'Kal':>5} "
          f"| {'Kam lvl':>7} {'Pas lvl':>7} {'Rila lvl':>8}")
    for h in range(24):
        sc = out["schedule"][h]
        print(f"{h:>2} {prices[h]:>6.0f} | {sc['Kamenitza']:>5.2f} {sc['Pastra']:>5.2f} "
              f"{sc['Rila']:>5.2f} {sc['Kalin']:>5.2f} | "
              f"{out['levels']['Kamenitza'][h]:>7.3f} {out['levels']['Pastra'][h]:>7.3f} "
              f"{out['levels']['Rila'][h]:>8.3f}")
    print(f"\nRevenue (gross x price proxy): {out['revenue']:.0f}")
    print(f"Level-limit violations: {len(out['violations'])}")
    for v in out["violations"][:5]:
        print("  ", v)
    # confirm Pastra Unit 2 stayed off
    u2 = [out["unit_schedule"][h][("Pastra", 2)] for h in range(24)]
    print(f"Pastra Unit 2 output all day (should be 0): max={max(u2)}")


if __name__ == "__main__":
    _demo()
