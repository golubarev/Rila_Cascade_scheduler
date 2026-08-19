"""
units.py
========
The static configuration of the cascade's seven turbine units and the four
plants: each unit's operating envelope (min / max MW, forbidden "no-go" bands),
and the plant-level rules (must-run, island-mode dependency, the combined-output
cap at HPP Rila).

This is the reference the scheduler constrains against.  It encodes what we
specified during design:

  * PSHPP Kalin  - 1 Pelton unit, generation-only.
  * HPP Kamenitza- 1 Pelton unit, must-run 24/7.
  * HPP Pastra   - 2 Francis units (Unit 2 has a no-go band).
  * HPP Rila     - 3 Francis units (no-go bands; Unit 1 has an island-mode
                   dependency; a 3+3+4 = 10 MW combined cap when all run).

Every limit here is a *default* and is meant to be adjustable - the operator's
per-run overrides (availability, temporary power caps) live separately in the
constraints layer and take precedence over these base values.

All Rila-cascade machines have horizontal turbine and generator shafts.
"""


class NoGoZone:
    """A forbidden operating band: the unit cannot run stably at lo < MW < hi."""

    __slots__ = ("lo", "hi")

    def __init__(self, lo, hi):
        self.lo = lo
        self.hi = hi

    def contains(self, mw, tol=1e-9):
        return (self.lo + tol) < mw < (self.hi - tol)

    def __repr__(self):
        return f"NoGo({self.lo}-{self.hi})"


class Unit:
    """One turbine unit and its operating envelope.

    Parameters
    ----------
    plant, unit_no : identity, e.g. ("Pastra", 2).
    turbine : "Pelton" | "Francis".
    min_mw, max_mw : technical output limits (MW).  A unit is either OFF (0 MW)
        or running somewhere in [min_mw, max_mw], minus any no-go band.
    no_go : list[NoGoZone] - forbidden bands the unit must skip over.
    warn_below : soft "don't run below this" level (allowed but discouraged);
        None if there is none.  Not a hard limit - the optimiser treats it as a
        preference, not a wall.
    practical_min : the value the operator normally floors this unit at when
        scheduling (e.g. Rila Unit 2 at 2.0 to keep down-room); informational.
    requires_companion : True if the unit cannot run alone and needs another
        unit of the same plant online (Rila Unit 1's island-mode rule).
    make, year, rpm, axis_masl : reference nameplate data.
    """

    def __init__(self, plant, unit_no, turbine, min_mw, max_mw,
                 no_go=None, warn_below=None, practical_min=None,
                 requires_companion=False, make="", year=None, rpm=None,
                 axis_masl=None, notes=""):
        self.plant = plant
        self.unit_no = unit_no
        self.turbine = turbine
        self.min_mw = min_mw
        self.max_mw = max_mw
        self.no_go = list(no_go) if no_go else []
        self.warn_below = warn_below
        self.practical_min = practical_min
        self.requires_companion = requires_companion
        self.make = make
        self.year = year
        self.rpm = rpm
        self.axis_masl = axis_masl
        self.notes = notes

    # --- identity ----------------------------------------------------------
    @property
    def key(self):
        return (self.plant, self.unit_no)

    def __repr__(self):
        return f"Unit({self.plant} U{self.unit_no}, {self.min_mw}-{self.max_mw} MW)"

    # --- feasibility -------------------------------------------------------
    def in_no_go(self, mw):
        """True if ``mw`` falls inside any forbidden band."""
        return any(z.contains(mw) for z in self.no_go)

    def is_feasible(self, mw, tol=1e-6):
        """Is running at ``mw`` allowed by this unit's base envelope?

        Off (0 MW) is always feasible here; must-run is enforced at the plant /
        constraints level, not per unit.  A running value must sit within
        [min_mw, max_mw] and outside every no-go band.
        """
        if abs(mw) <= tol:
            return True                      # unit is off
        if mw < self.min_mw - tol or mw > self.max_mw + tol:
            return False
        return not self.in_no_go(mw)

    def clamp_to_feasible(self, mw):
        """Snap ``mw`` to the nearest feasible output (for the optimiser).

        Below the minimum snaps to 0 or min (whichever is closer); above max
        snaps to max; inside a no-go band snaps to the nearer edge.
        """
        if mw <= 0:
            return 0.0
        if mw < self.min_mw:
            return 0.0 if mw < self.min_mw / 2 else self.min_mw
        if mw > self.max_mw:
            return self.max_mw
        for z in self.no_go:
            if z.contains(mw):
                # snap to whichever edge is closer
                return z.lo if (mw - z.lo) <= (z.hi - mw) else z.hi
        return mw

    # --- deliberate permanent changes (e.g. after a rehabilitation) --------
    # Every operating limit here is editable - none is a fixed constant.  A
    # rehabilitation can move a unit's min/max or no-go bands; use update() so
    # the change is explicit and validated.  Because the roster lives in code,
    # such a change is git-tracked - a permanent audit trail of the rehab.
    _EDITABLE = {"turbine", "min_mw", "max_mw", "no_go", "warn_below",
                 "practical_min", "requires_companion", "make", "year", "rpm",
                 "axis_masl", "notes"}

    def update(self, **changes):
        """Change base parameters. Unknown fields raise (to catch typos). Returns self."""
        for key, val in changes.items():
            if key not in self._EDITABLE:
                raise KeyError(f"Unit has no editable field {key!r}")
            setattr(self, key, val)
        return self


class Plant:
    """A plant and the rules that span its units.

    Attributes
    ----------
    must_run : the plant must generate every hour (Kamenitza).
    combined_max_mw : cap on the sum of the plant's units when several run
        together (Rila: 3+3+4 = 10, below the units' individual maxima).
    simultaneous_caps : dict unit_no -> cap when co-operating (Rila 3/3/4).
    island_units : units that need a companion online (Rila Unit 1).
    """

    def __init__(self, name, units, must_run=False, combined_max_mw=None,
                 simultaneous_caps=None, notes=""):
        self.name = name
        self.units = units
        self.must_run = must_run
        self.combined_max_mw = combined_max_mw
        self.simultaneous_caps = simultaneous_caps or {}
        self.notes = notes

    def unit(self, unit_no):
        for u in self.units:
            if u.unit_no == unit_no:
                return u
        raise KeyError(f"{self.name} has no unit {unit_no}")

    def companion_online(self, dispatch):
        """Given a dict {unit_no: mw}, is any island-mode unit's rule satisfied?

        Returns a list of (unit_no) that VIOLATE their island-mode rule - i.e.
        they are running while no companion unit is.  Empty list means OK.
        """
        violations = []
        running = {n for n, mw in dispatch.items() if mw and mw > 0}
        for u in self.units:
            if u.requires_companion and u.unit_no in running:
                others = running - {u.unit_no}
                if not others:
                    violations.append(u.unit_no)
        return violations


# ---------------------------------------------------------------------------
# The roster, exactly as specified during design.
# ---------------------------------------------------------------------------
PSHPP_KALIN = Plant("Kalin", [
    Unit("Kalin", 1, "Pelton", min_mw=0.070, max_mw=3.900,
         make="Ateliers des Charmilles", year=1949, rpm=1000, axis_masl=1574.40,
         notes="Generation-only (pumps non-operational since 1985); "
               "source Kalin or Karagyol dam."),
])

HPP_KAMENITZA = Plant("Kamenitza", [
    Unit("Kamenitza", 1, "Pelton", min_mw=0.080, max_mw=3.060,
         make="Ateliers des Charmilles & Ateliers de Constructions Mecaniques de Vevey",
         year=1939, rpm=1000, axis_masl=861.20,
         notes="Must-run 24/7. Water supply is skimmed after the turbine; only "
               "output above ~0.8 MW feeds Pastra. Supply-driven minimum "
               "(currently ~0.5 MW) is set in the constraints layer."),
], must_run=True)

HPP_PASTRA = Plant("Pastra", [
    Unit("Pastra", 1, "Francis", min_mw=0.180, max_mw=2.600, warn_below=1.300,
         make="Voith", year=1925, rpm=1000, axis_masl=693.70,
         notes="Francis good practice: avoid running below ~half power (soft)."),
    Unit("Pastra", 2, "Francis", min_mw=0.180, max_mw=3.600,
         no_go=[NoGoZone(1.350, 2.000)], warn_below=1.300,
         make="Voith", year=1926, rpm=750, axis_masl=693.78,
         notes="No-go 1.35-2.00 MW; lower band allowed but discouraged; "
               "upper band (>2.2) preferred."),
], notes="Sediment derate below 2.5 m reservoir level reduces achievable max "
         "power (handled with a tunable blockage coefficient at run time).")

HPP_RILA = Plant("Rila", [
    Unit("Rila", 1, "Francis", min_mw=0.500, max_mw=3.000,
         no_go=[NoGoZone(1.800, 2.150)], requires_companion=True,
         make="Voith", year=1928, rpm=750, axis_masl=517.50,
         notes="No regulating band; island-mode risk if run alone -> needs "
               "Unit 2 or Unit 3 online."),
    Unit("Rila", 2, "Francis", min_mw=0.200, max_mw=3.500,
         no_go=[NoGoZone(1.200, 1.500)], practical_min=2.000,
         make="Voith", year=1928, rpm=750, axis_masl=517.50,
         notes="Best regulator; normally floored at ~2.0 MW to keep down-room."),
    Unit("Rila", 3, "Francis", min_mw=0.500, max_mw=4.200,
         no_go=[NoGoZone(1.000, 3.400)], practical_min=3.800,
         make="Ateliers des Charmilles", year=1948, rpm=1000, axis_masl=517.58,
         notes="Normally set ~4.0 MW to keep up/down room."),
], combined_max_mw=10.0, simultaneous_caps={1: 3.0, 2: 3.0, 3: 4.0},
   notes="When all three run together the caps drop to 3+3+4 = 10 MW total "
         "(shared hydraulic limit). Rila is the grid gateway.")

PLANTS = {p.name: p for p in (PSHPP_KALIN, HPP_KAMENITZA, HPP_PASTRA, HPP_RILA)}
UNITS = {u.key: u for p in PLANTS.values() for u in p.units}


def get_unit(plant, unit_no):
    return UNITS[(plant, unit_no)]


def get_plant(name):
    return PLANTS[name]


# ---------------------------------------------------------------------------
# Small demo when run directly: print each unit's envelope and spot-check
# a few feasibility cases against the no-go bands.
# ---------------------------------------------------------------------------
def _demo():
    print("Rila Cascade unit roster")
    print("=" * 60)
    for name, plant in PLANTS.items():
        tag = " [must-run]" if plant.must_run else ""
        cap = f"  combined cap {plant.combined_max_mw} MW" if plant.combined_max_mw else ""
        print(f"\n{name}{tag}{cap}")
        for u in plant.units:
            ng = (" no-go " + ",".join(f"{z.lo}-{z.hi}" for z in u.no_go)) if u.no_go else ""
            comp = " requires-companion" if u.requires_companion else ""
            print(f"  U{u.unit_no} {u.turbine:<7} {u.min_mw:.3f}-{u.max_mw:.3f} MW"
                  f"  ({u.year}, {u.rpm} rpm){ng}{comp}")

    print("\nFeasibility spot-checks:")
    checks = [
        ("Pastra", 2, 1.6, False),   # inside no-go 1.35-2.00
        ("Pastra", 2, 2.5, True),    # above the no-go
        ("Rila", 3, 2.0, False),     # inside no-go 1.0-3.4
        ("Rila", 3, 4.0, True),      # in the operating band
        ("Rila", 2, 1.35, False),    # inside no-go 1.2-1.5
        ("Kalin", 1, 3.9, True),     # at max
        ("Kalin", 1, 4.5, False),    # above max
    ]
    for plant, uno, mw, expected in checks:
        got = get_unit(plant, uno).is_feasible(mw)
        flag = "OK" if got == expected else "MISMATCH!"
        print(f"  {plant} U{uno} @ {mw} MW -> feasible={got} (expected {expected}) {flag}")

    print("\nclamp_to_feasible examples:")
    for plant, uno, mw in [("Pastra", 2, 1.6), ("Rila", 3, 2.0), ("Rila", 2, 1.35)]:
        print(f"  {plant} U{uno}: {mw} -> {get_unit(plant, uno).clamp_to_feasible(mw)}")

    print("\nIsland-mode check (Rila):")
    for dispatch in ({1: 2.5}, {1: 2.5, 2: 2.0}, {2: 2.0, 3: 4.0}):
        v = HPP_RILA.companion_online(dispatch)
        print(f"  dispatch {dispatch} -> violations: {v if v else 'none'}")


if __name__ == "__main__":
    _demo()
