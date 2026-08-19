"""
constraints.py
==============
The operator's per-run instructions to the scheduler - everything about the real
world for a particular planning run that isn't part of the fixed unit roster.

It sits ON TOP of units.py: units.py gives each unit's base envelope, and the
constraints here temporarily narrow it (or steer the reservoirs) for one run.
The optimiser reads these to know what is legal and what to aim for.

Five kinds of instruction, each over a time window:

  * Unit availability   - a unit is out of service for a period (forced off).
  * Unit power limit    - a temporary min/max on a unit (thermal cap, or the
                          Kamenitza water-supply minimum).
  * Level limit         - a temporary hard floor/ceiling on a reservoir level
                          (distinct from the permanent spillway/floor).
  * Target level        - a level to steer a reservoir toward, with a stiffness
                          (how hard to hold it, traded against revenue).
  * Catchment state     - a named water catchment switched on/off, with its
                          estimated inflow contribution, so toggling it predicts
                          an inflow change (and later explains an observed one).

Times are plain datetimes.  A window with start=None is "from the beginning";
end=None is "with no end" - handy for standing instructions.
"""

import datetime

from .units import get_unit


class TimeWindow:
    """A half-open time interval [start, end).  None on either side means open."""

    __slots__ = ("start", "end")

    def __init__(self, start=None, end=None):
        self.start = start
        self.end = end

    def contains(self, ts):
        if self.start is not None and ts < self.start:
            return False
        if self.end is not None and ts >= self.end:
            return False
        return True

    def __repr__(self):
        return f"[{self.start} .. {self.end})"


# --- the individual instruction records ------------------------------------
class UnitAvailability:
    def __init__(self, plant, unit_no, window, available=False):
        self.plant, self.unit_no, self.window, self.available = plant, unit_no, window, available


class UnitPowerLimit:
    def __init__(self, plant, unit_no, window, min_mw=None, max_mw=None):
        self.plant, self.unit_no, self.window = plant, unit_no, window
        self.min_mw, self.max_mw = min_mw, max_mw


class LevelLimit:
    def __init__(self, reservoir, window, min_level=None, max_level=None):
        self.reservoir, self.window = reservoir, window
        self.min_level, self.max_level = min_level, max_level


class TargetLevel:
    def __init__(self, reservoir, window, level, stiffness=1.0):
        self.reservoir, self.window, self.level, self.stiffness = reservoir, window, level, stiffness


class CatchmentState:
    def __init__(self, name, reservoir, window, on=True, mwh_contribution=0.0):
        self.name, self.reservoir, self.window = name, reservoir, window
        self.on, self.mwh_contribution = on, mwh_contribution


class Constraints:
    """A collection of the operator's instructions, with query methods.

    Build one per planning run, add instructions with the ``set_*`` helpers, then
    the optimiser/simulator query it per hour.
    """

    def __init__(self):
        self.unit_availability = []
        self.unit_power_limits = []
        self.level_limits = []
        self.targets = []
        self.catchments = []

    # --- builders ---------------------------------------------------------
    def set_unit_unavailable(self, plant, unit_no, start, end):
        """Mark a unit out of service between ``start`` and ``end``."""
        self.unit_availability.append(
            UnitAvailability(plant, unit_no, TimeWindow(start, end), available=False))
        return self

    def set_unit_power(self, plant, unit_no, min_mw=None, max_mw=None,
                       start=None, end=None):
        """Temporarily cap or floor a unit's output (e.g. thermal limit, supply min)."""
        self.unit_power_limits.append(
            UnitPowerLimit(plant, unit_no, TimeWindow(start, end), min_mw, max_mw))
        return self

    def set_level_limit(self, reservoir, min_level=None, max_level=None,
                        start=None, end=None):
        """Temporary hard floor/ceiling on a reservoir's level."""
        self.level_limits.append(
            LevelLimit(reservoir, TimeWindow(start, end), min_level, max_level))
        return self

    def set_target(self, reservoir, level, stiffness=1.0, start=None, end=None):
        """A level to steer a reservoir toward (soft), with a stiffness weight."""
        self.targets.append(
            TargetLevel(reservoir, TimeWindow(start, end), level, stiffness))
        return self

    def set_catchment(self, name, reservoir, on, mwh_contribution,
                      start=None, end=None):
        """Record a catchment's on/off state and its estimated inflow contribution."""
        self.catchments.append(
            CatchmentState(name, reservoir, TimeWindow(start, end), on, mwh_contribution))
        return self

    # --- queries (used by the optimiser / simulator) ----------------------
    def effective_unit_bounds(self, plant, unit_no, ts):
        """Return ``(min_mw, max_mw, available)`` for a unit at time ``ts``.

        Starts from the unit's base envelope, applies any availability window
        (unavailable -> forced off) and any temporary power limits (tightening
        min up and max down).  The optimiser uses this as the unit's allowed
        range for that hour (no-go bands still come from the Unit itself).
        """
        u = get_unit(plant, unit_no)
        lo, hi = u.min_mw, u.max_mw

        for a in self.unit_availability:
            if a.plant == plant and a.unit_no == unit_no and a.window.contains(ts):
                if not a.available:
                    return (0.0, 0.0, False)

        for pl in self.unit_power_limits:
            if pl.plant == plant and pl.unit_no == unit_no and pl.window.contains(ts):
                if pl.min_mw is not None:
                    lo = max(lo, pl.min_mw)
                if pl.max_mw is not None:
                    hi = min(hi, pl.max_mw)

        if hi < lo:                       # a cap tighter than the floor -> off
            return (0.0, 0.0, False)
        return (lo, hi, True)

    def level_bounds(self, reservoir, ts):
        """Return ``(min_level, max_level)`` temporary limits, or (None, None)."""
        lo = hi = None
        for lim in self.level_limits:
            if lim.reservoir == reservoir and lim.window.contains(ts):
                if lim.min_level is not None:
                    lo = lim.min_level if lo is None else max(lo, lim.min_level)
                if lim.max_level is not None:
                    hi = lim.max_level if hi is None else min(hi, lim.max_level)
        return (lo, hi)

    def target_at(self, reservoir, ts):
        """Return ``(level, stiffness)`` if a target applies at ``ts``, else None.

        If several overlap, the stiffest wins (the firmest requirement).
        """
        best = None
        for t in self.targets:
            if t.reservoir == reservoir and t.window.contains(ts):
                if best is None or t.stiffness > best[1]:
                    best = (t.level, t.stiffness)
        return best

    def inflow_adjustment(self, reservoir, ts):
        """MWh-equivalent inflow change at ``ts`` from catchments that are OFF.

        A catchment switched off subtracts its contribution; the result is
        negative (or zero).  This is how toggling a catchment predicts an inflow
        change - and later helps attribute an observed one.
        """
        delta = 0.0
        for c in self.catchments:
            if c.reservoir == reservoir and c.window.contains(ts) and not c.on:
                delta -= c.mwh_contribution
        return delta


def apply_catchments(inflows, constraints, timestamps):
    """Return a copy of an inflow dict with catchment on/off adjustments applied.

    ``inflows`` maps reservoir -> hourly series; ``timestamps`` is the matching
    list of datetimes.  Off catchments reduce the affected reservoir's inflow.
    """
    out = {}
    for reservoir, series in inflows.items():
        adjusted = []
        for h, base in enumerate(series):
            ts = timestamps[h]
            adjusted.append((base or 0.0) + constraints.inflow_adjustment(reservoir, ts))
        out[reservoir] = adjusted
    return out


# ---------------------------------------------------------------------------
# Demo: express the current live situation and query it.
#   * Pastra Unit 2 out of service 08:00 today -> 08:00 tomorrow.
#   * Hold Pastra reservoir near 7.0 m for construction (stiff target).
#   * Kamenitza water-supply minimum 0.5 MW (standing).
#   * One catchment switched off, to show inflow attribution.
# ---------------------------------------------------------------------------
def _demo():
    today = datetime.datetime(2026, 8, 19, 8, 0)
    tomorrow = today + datetime.timedelta(days=1)

    c = Constraints()
    c.set_unit_unavailable("Pastra", 2, start=today, end=tomorrow)
    c.set_target("Pastra", level=7.0, stiffness=5.0, start=today, end=tomorrow)
    c.set_unit_power("Kamenitza", 1, min_mw=0.5)                      # standing supply min
    c.set_catchment("Kalin river", "Pastra", on=False, mwh_contribution=0.4,
                    start=today, end=tomorrow)

    inside = today + datetime.timedelta(hours=3)     # 11:00 today (in the window)
    outside = tomorrow + datetime.timedelta(hours=3)  # 11:00 tomorrow (after it)

    print("Live scenario checks")
    print("=" * 60)
    print("Pastra Unit 2 bounds  inside window:",
          c.effective_unit_bounds("Pastra", 2, inside))
    print("Pastra Unit 2 bounds outside window:",
          c.effective_unit_bounds("Pastra", 2, outside))
    print("Pastra Unit 1 bounds  inside window:",
          c.effective_unit_bounds("Pastra", 1, inside), "(unaffected)")
    print("Kamenitza U1 bounds (supply min):",
          c.effective_unit_bounds("Kamenitza", 1, inside))
    print("Pastra target  inside window:", c.target_at("Pastra", inside))
    print("Pastra target outside window:", c.target_at("Pastra", outside))
    print("Pastra inflow adj inside window:", c.inflow_adjustment("Pastra", inside),
          "(Kalin-river catchment off)")
    print("Pastra inflow adj outside window:", c.inflow_adjustment("Pastra", outside))


if __name__ == "__main__":
    _demo()
