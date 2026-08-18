"""
database.py
===========
Creates (or opens) the SQLite database for the Rila Cascade project and defines
its schema.

Why SQLite?
-----------
It is a single file, needs no server, ships inside Python, and comfortably
handles the ~55,000 hourly rows we have.  Backing up the whole database is just
copying one file, and pandas can query it directly.

Design choice: "tidy / long" tables
------------------------------------
Instead of copying the spreadsheet's ~66 awkward columns one-to-one, we split
the data into a handful of clean tables.  Generation and reservoir data are
stored in *long* form (one row per unit / per reservoir per hour) so that
adding or retiring a unit is a data change, not a schema change - and so the
tables map naturally onto the Unit / Reservoir objects we will build later.

Every table hangs off ``period`` via ``period_id`` so there is one and only one
definition of "which hour is this".
"""

import sqlite3

# The full schema as one script.  ``IF NOT EXISTS`` makes it safe to run again.
SCHEMA_SQL = """
-- Provenance: which source Excel file each period was loaded from -----------
CREATE TABLE IF NOT EXISTS source_file (
    id          INTEGER PRIMARY KEY,
    filename    TEXT UNIQUE NOT NULL,
    file_year   INTEGER,            -- year parsed from the file name (NULL for the live ADMIN file)
    is_admin    INTEGER NOT NULL,   -- 1 = the current live "ADMIN" file, 0 = a yearly archive
    imported_at TEXT NOT NULL
);

-- One row per scheduling hour ----------------------------------------------
-- We key uniqueness on (date, seq) rather than on the timestamp, because on
-- the autumn DST day one clock-hour occurs twice; seq (the running position of
-- the hourly row within its day) keeps those two hours distinct and loses no data.
CREATE TABLE IF NOT EXISTS period (
    id              INTEGER PRIMARY KEY,
    ts_eet          TEXT NOT NULL,     -- 'YYYY-MM-DD HH:00' in Eastern European Time (the sheet's clock)
    date            TEXT NOT NULL,     -- 'YYYY-MM-DD'
    hour            INTEGER NOT NULL,  -- clock start-hour taken from the period label (0..23)
    seq             INTEGER NOT NULL,  -- 0-based order of this hour within its day (handles 23/25h DST days)
    n_hours_in_day  INTEGER,           -- 23, 24 or 25
    source_file_id  INTEGER REFERENCES source_file(id),
    UNIQUE (date, seq)
);

-- Realized GROSS generation, one row per turbine unit per hour ---------------
CREATE TABLE IF NOT EXISTS unit_generation (
    period_id INTEGER NOT NULL REFERENCES period(id),
    plant     TEXT NOT NULL,           -- 'Rila' | 'Pastra' | 'Kamenitza' | 'Kalin'
    unit_no   INTEGER NOT NULL,        -- 1..3
    gross_mwh REAL,
    PRIMARY KEY (period_id, plant, unit_no)
);

-- Realized plant-level gross and NET (net is only tracked per plant) ---------
CREATE TABLE IF NOT EXISTS plant_output (
    period_id INTEGER NOT NULL REFERENCES period(id),
    plant     TEXT NOT NULL,
    gross_mwh REAL,
    net_mwh   REAL,
    PRIMARY KEY (period_id, plant)
);

-- Reservoir water-balance state, one row per reservoir per hour --------------
CREATE TABLE IF NOT EXISTS reservoir_state (
    period_id       INTEGER NOT NULL REFERENCES period(id),
    reservoir       TEXT NOT NULL,     -- 'Kamenitza' | 'Pastra' | 'Rila'
    level_calc      REAL,              -- level computed from the water balance
    level_measured  REAL,              -- level read from the gauge (may be blank)
    inflow_calc     REAL,              -- hand-seeded / carried-forward inflow (energy MWh units)
    inflow_measured REAL,              -- inflow back-calculated from the measured level
    inflow_delta    REAL,              -- measured-derived minus calculated (the correction signal)
    inflow_forecast REAL,              -- "new forecast" inflow actually used going forward
    level_forecast  REAL,              -- level rebuilt from the forecast inflow
    PRIMARY KEY (period_id, reservoir)
);

-- Archived DAY-AHEAD schedule per unit (spreadsheet columns BC:BI) -----------
CREATE TABLE IF NOT EXISTS dayahead_unit (
    period_id INTEGER NOT NULL REFERENCES period(id),
    plant     TEXT NOT NULL,
    unit_no   INTEGER NOT NULL,
    gross_mwh REAL,
    PRIMARY KEY (period_id, plant, unit_no)
);

-- Cascade totals, submitted schedule, imbalance and intraday -----------------
CREATE TABLE IF NOT EXISTS cascade_balance (
    period_id       INTEGER PRIMARY KEY REFERENCES period(id),
    actual_gross    REAL,
    actual_net      REAL,
    submitted_gross REAL,   -- schedule as submitted, later changed by intraday (AU)
    submitted_net   REAL,   -- (AV)
    dayahead_gross  REAL,   -- untouched day-ahead (AX)
    dayahead_net    REAL,   -- (AY)
    imbalance_gross REAL,   -- actual vs schedule (AQ)
    imbalance_net   REAL,   -- (AP)
    intraday_mwh    REAL    -- traded on the intraday market (BN)
);

-- Per-day header parameters (one row per calendar day) -----------------------
CREATE TABLE IF NOT EXISTS daily_params (
    date             TEXT PRIMARY KEY,
    k_source_coeff   REAL,   -- AH2: Kalin/Karagyol source coefficient for PSHPP Kalin -> Kamenitza inflow
    target_rila      REAL,   -- hand-set target level for Rila reservoir
    target_pastra    REAL,   -- hand-set target level for Pastra reservoir
    target_kamenitza REAL,   -- hand-set target level for Kamenitza reservoir
    status           TEXT,   -- finalized/submitted stamp, e.g. "ПРИКЛЮЧЕН - ПОДАДЕН"
    source_file_id   INTEGER REFERENCES source_file(id)
);

-- IBEX day-ahead price, ready to be filled later -----------------------------
CREATE TABLE IF NOT EXISTS price (
    period_id    INTEGER PRIMARY KEY REFERENCES period(id),
    ibex_eur_mwh REAL
);

-- Helpful indexes for the queries we will run most -------------------------
CREATE INDEX IF NOT EXISTS ix_period_date ON period(date);
CREATE INDEX IF NOT EXISTS ix_unit_gen_plant ON unit_generation(plant, unit_no);
CREATE INDEX IF NOT EXISTS ix_res_state_res ON reservoir_state(reservoir);
"""


def connect(db_path):
    """Open a connection to the database file (creating the file if needed)."""
    # ``foreign_keys=ON`` makes SQLite actually enforce the REFERENCES above.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_schema(conn):
    """Create every table and index if they do not already exist."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()