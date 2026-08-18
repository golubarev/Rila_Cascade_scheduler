"""
physics.py
==========
The water-balance "physics engine" for the Rila cascade.

This is a faithful Python port of the level calculation that the operators'
spreadsheet performs (the H / T / AC columns).  Given a reservoir's previous
level and the water moving in and out during an hour, it returns the new level.

Everything is expressed in the same units the spreadsheet uses:

  * generation and inflows are in **MWh-equivalent** - i.e. water is measured by
    how much energy the plant *below* the reservoir would make from it.  This is
    why a reservoir's level change is simply (net MWh) x (a level-drop coefficient).
  * the level-drop coefficient is "metres of level per MWh".  For Rila and
    Pastra it is a single number; for Kamenitza it depends on the current level
    (the reservoir is narrower low down, so the same water moves the level more).

All coefficients below are taken directly from the spreadsheet.  They are the
values that produced years of real schedules, so we treat them as authoritative
now and keep them here in one place to recalibrate later against measured data.
"""


def kamenitza_coeff(level):
    """Level-banded drop coefficient for Kamenitza (metres per MWh).

    From the operators' measured "cm per MWh at 1 MW" table.  The band is chosen
    from the level at the *start* of the hour (the previous level), matching the
    spreadsheet's ``IF(prev_level > 11, ...)`` logic.
    """
    if level > 11:
        return 0.15
    if level > 10:
        return 0.17
    if level > 9:
        return 0.19
    if level > 8:
        return 0.22
    if level > 7:
        return 0.26
    return 0.30


class Reservoir:
    """One reservoir and its level<->water-balance behaviour.

    Parameters
    ----------
    name : str
    cap : float
        Upper clamp on the level (the spreadsheet caps the computed level here).
    drop_coeff : float or callable
        Metres of level change per MWh of net water.  A plain number for a flat
        coefficient (Rila, Pastra), or a function ``coeff(level)`` for the
        level-dependent Kamenitza band.
    """

    def __init__(self, name, cap, drop_coeff):
        self.name = name
        self.cap = cap
        self._drop_coeff = drop_coeff

    def drop_coeff(self, level):
        """Return the metres-per-MWh coefficient at a given level."""
        if callable(self._drop_coeff):
            return self._drop_coeff(level)
        return self._drop_coeff

    def next_level(self, prev_level, net_inflow_mwh):
        """Advance the level by one hour.

        ``net_inflow_mwh`` is the net water into the reservoir this hour, in
        MWh-equivalent: (side inflow + arrivals from upstream) - (own generation).
        Positive fills the reservoir, negative draws it down.

        The new level is ``prev_level + net_inflow * coeff(prev_level)``, clamped
        to the cap and rounded to 3 decimals exactly as the spreadsheet does.
        """
        new_level = prev_level + net_inflow_mwh * self.drop_coeff(prev_level)
        if new_level > self.cap:
            new_level = self.cap
        return round(new_level, 3)


class Cascade:
    """The four-plant cascade: turns an hour of generation + inflows into levels.

    Responsibilities:
      * hold the three downstream reservoirs, and
      * assemble each reservoir's *net balance* from that hour's plant
        generation, side inflow and the water arriving from the step above.

    Inter-step conversion coefficients (all from the spreadsheet):
    """

    # Pastra generation converted into Rila inflow (MWh-equivalent).
    PASTRA_TO_RILA = 1.12
    # Kamenitza generation above the water-supply threshold, converted into
    # Pastra inflow.  Below the threshold everything is skimmed for town supply
    # and nothing reaches Pastra by this route.
    KAM_TO_PASTRA = 0.10
    KAM_SUPPLY_THRESHOLD = 0.80  # MW

    def __init__(self):
        # Caps and coefficients as used by the spreadsheet.
        self.kamenitza = Reservoir("Kamenitza", cap=12.0, drop_coeff=kamenitza_coeff)
        self.pastra = Reservoir("Pastra", cap=7.63, drop_coeff=0.5)
        self.rila = Reservoir("Rila", cap=6.1, drop_coeff=0.3)

    # --- net balance (MWh-equivalent) for each reservoir -------------------

    def kamenitza_net(self, side_inflow, kamenitza_gen, kalin_gen, k_coeff):
        """Water from PSHPP Kalin depends on the Kalin/Karagyol source coeff K."""
        return side_inflow - kamenitza_gen + kalin_gen * k_coeff

    def pastra_net(self, side_inflow, pastra_gen, kamenitza_gen):
        """Kamenitza feeds Pastra only for output above the 0.8 MW supply threshold."""
        if kamenitza_gen > self.KAM_SUPPLY_THRESHOLD:
            from_kamenitza = (kamenitza_gen - self.KAM_SUPPLY_THRESHOLD) * self.KAM_TO_PASTRA
        else:
            from_kamenitza = 0.0
        return side_inflow - pastra_gen + from_kamenitza

    def rila_net(self, side_inflow, rila_gen, pastra_gen):
        """Rila receives Pastra's turbined water, converted by PASTRA_TO_RILA."""
        return side_inflow - rila_gen + pastra_gen * self.PASTRA_TO_RILA

    # --- one-hour step for each reservoir (balance + level update) ----------

    def step_kamenitza(self, prev_level, side_inflow, kamenitza_gen, kalin_gen, k_coeff):
        net = self.kamenitza_net(side_inflow, kamenitza_gen, kalin_gen, k_coeff)
        return self.kamenitza.next_level(prev_level, net)

    def step_pastra(self, prev_level, side_inflow, pastra_gen, kamenitza_gen):
        net = self.pastra_net(side_inflow, pastra_gen, kamenitza_gen)
        return self.pastra.next_level(prev_level, net)

    def step_rila(self, prev_level, side_inflow, rila_gen, pastra_gen):
        net = self.rila_net(side_inflow, rila_gen, pastra_gen)
        return self.rila.next_level(prev_level, net)
