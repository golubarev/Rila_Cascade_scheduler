"""
columns.py
==========
This module is a single, well-documented place that records *where* each piece
of information lives inside the "Grafik" worksheet of the
``ГРАФИК РАБОТЕН - ...`` Excel files.

Why keep this separate?
-----------------------
The spreadsheet has ~66 columns whose meaning is not obvious from the letters.
By naming every column here once, the loader (load_grafik.py) stays readable,
and if the layout ever changes we only edit this one file.

How to read the numbers below
-----------------------------
When openpyxl reads a row with ``values_only=True`` it hands us a plain Python
tuple.  In that tuple the first cell (column A) is at index 0, column B is at
index 1, and so on.  So every constant here is the *0-based tuple index*, i.e.
(Excel column number - 1).  Handy reference:

    A=0  B=1  C=2  D=3  E=4  F=5  G=6  H=7  I=8  J=9  K=10 L=11 M=12 N=13 O=14
    P=15 Q=16 R=17 S=18 T=19 U=20 V=21 W=22 X=23 Y=24 Z=25
    AA=26 AB=27 AC=28 AD=29 AE=30 AF=31 AG=32 AH=33 AI=34 AJ=35 AK=36 AL=37
    AM=38 AN=39 AP=41 AQ=42 AR=43 AU=46 AV=47 AX=49 AY=50
    BC=54 BD=55 BE=56 BF=57 BG=58 BH=59 BI=60 BL=63 BM=64 BN=65
"""

# --- The two "key" columns that identify a row -----------------------------
COL_DATE = 0    # A  - the schedule date (present once per daily block, on the block's header row)
COL_PERIOD = 1  # B  - the hourly period label, e.g. "00:00 - 01:00" (Eastern European Time)

# --- Realized GROSS generation, one entry per turbine unit -----------------
# Each tuple is (plant name, unit number, tuple-index of that unit's gross MWh).
UNIT_GROSS = [
    ("Rila", 1, 2),    # C  - HPP Rila, unit 1
    ("Rila", 2, 3),    # D  - HPP Rila, unit 2
    ("Rila", 3, 4),    # E  - HPP Rila, unit 3
    ("Pastra", 1, 15), # P  - HPP Pastra, unit 1
    ("Pastra", 2, 16), # Q  - HPP Pastra, unit 2
    ("Kamenitza", 1, 26),  # AA - HPP Kamenitza, single unit
    ("Kalin", 1, 35),      # AJ - PSHPP Kalin, single unit
]

# --- Realized plant-level totals (gross sum and NET after losses) ----------
# net is only tracked per plant in the sheet, not per unit.
# Each tuple is (plant, gross-index, net-index).
PLANT_OUTPUT = [
    ("Rila", 5, 14),        # F (gross)  / O (net)
    ("Pastra", 17, 25),     # R (gross)  / Z (net)
    ("Kamenitza", 26, 34),  # AA (gross, single unit) / AI (net)
    ("Kalin", 35, 36),      # AJ (gross, single unit) / AK (net)
]

# --- Reservoir water-balance columns, one block of fields per reservoir ----
# The sheet only tracks levels for the three downstream reservoirs (the two
# top dams are not level-tracked hourly here).  Fields, in order:
#   level_calc, level_measured, inflow_calc, inflow_measured,
#   inflow_delta, inflow_forecast, level_forecast
RESERVOIR_FIELDS = ["level_calc", "level_measured", "inflow_calc",
                    "inflow_measured", "inflow_delta", "inflow_forecast",
                    "level_forecast"]

RESERVOIR_COLS = {
    #             lvl_calc lvl_meas inf_calc inf_meas delta inf_fc lvl_fc
    "Rila":       [7,      8,       6,       9,       10,   11,    12],   # H I G J K L M
    "Pastra":     [19,     20,      18,      21,      22,   23,    24],   # T U S V W X Y
    "Kamenitza":  [28,     29,      27,      30,      31,   32,    33],   # AC AD AB AE AF AG AH
}

# --- Archived DAY-AHEAD schedule, per unit (columns BC:BI) -----------------
DAYAHEAD_UNIT = [
    ("Rila", 1, 54),       # BC
    ("Rila", 2, 55),       # BD
    ("Rila", 3, 56),       # BE
    ("Pastra", 1, 57),     # BF
    ("Pastra", 2, 58),     # BG
    ("Kamenitza", 1, 59),  # BH
    ("Kalin", 1, 60),      # BI
]

# --- Cascade-level totals, schedule, imbalance and intraday ----------------
CASCADE = {
    "actual_gross": 38,     # AM - realized gross for the whole cascade
    "actual_net": 39,       # AN - realized net
    "submitted_gross": 46,  # AU - schedule as submitted (later changed by intraday)
    "submitted_net": 47,    # AV
    "dayahead_gross": 49,   # AX - the untouched day-ahead schedule
    "dayahead_net": 50,     # AY
    "imbalance_gross": 42,  # AQ - actual-vs-schedule imbalance (gross)
    "imbalance_net": 41,    # AP - imbalance (net)
    "intraday_mwh": 65,     # BN - amount traded on the intraday market
}

# --- Per-DAY parameters that sit in the block header rows ------------------
# These are NOT on the hourly rows.  Their positions relative to the block's
# date-row (the row where column A holds the date) are:
#   * K source coefficient -> same date-row, column AH (index 33)
#   * target levels        -> the row *below* the date-row (date-row + 1):
#                             Rila=H(7), Pastra=T(19), Kamenitza=AC(28)
#   * status stamp         -> the row *above* the date-row (date-row - 1),
#                             column M (index 12), e.g. "ПРИКЛЮЧЕН - ПОДАДЕН"
COL_K_COEFF = 33          # AH on the date-row
COL_TARGET_RILA = 7       # H  on date-row + 1
COL_TARGET_PASTRA = 19    # T  on date-row + 1
COL_TARGET_KAMENITZA = 28 # AC on date-row + 1
COL_STATUS = 12           # M  on date-row - 1