# Rila Cascade Scheduler

A tool for building hourly power-production schedules for the Rila hydropower
cascade (2 dams, 3 reservoirs, 4 plants, 7 turbine units).

Built in stages. **Stage 1** is the data foundation (a SQLite database plus a
loader and an incremental updater for the historical `ГРАФИК РАБОТЕН` workbooks).
**Stage 2** is the water-balance physics engine and its validation.

## Project layout

```
src/                  # all the code (a Python package)
  columns.py          # documents where every value lives in the "Grafik" sheet
  database.py         # SQLite schema (tidy/long tables) + connection helpers
  grafik_parser.py    # shared: turns one .xlsm file into DayBlock objects (no DB)
  db_writer.py        # shared: writes/deletes one day in the database
  load_grafik.py      # FULL build from all files (overlap resolution)
  update_grafik.py    # INCREMENTAL top-up from a single file ("newest wins")
  physics.py          # water-balance engine (Reservoir / Cascade classes)
  validate_physics.py # checks the engine against stored level_calc values
data/                 # the data (kept out of git)
  grafik_archive/     # the source .xlsm workbooks
  rila_cascade.db     # the generated database
README.md
.gitignore
```

`load_grafik.py` and `update_grafik.py` both rely on `grafik_parser.py` and
`db_writer.py`, so parsing and storage are defined once and can never drift.

## Commands

All commands are run from the project root.

**Full rebuild** of the database from every workbook in the archive (safe to
re-run; it rebuilds from scratch):

```
python -m src.load_grafik --data-dir "data\grafik_archive" --db data\rila_cascade.db
```

**Incremental top-up** from a single new file (daily / weekly). For every day in
the file it replaces that day's rows - "newest wins" - refreshing recent days
and adding new ones, leaving all other days untouched:

```
python -m src.update_grafik --db data\rila_cascade.db --file "data\grafik_archive\<new-file>.xlsm"
```

**Validate** the physics engine against the stored levels (full report, or one
day in detail):

```
python -m src.validate_physics --db data\rila_cascade.db
python -m src.validate_physics --db data\rila_cascade.db --day 2025-11-01
```

**One-time setup** (install the one dependency):

```
pip install openpyxl
```

> Paths above use Windows separators. On macOS/Linux use forward slashes, e.g.
> `--data-dir data/grafik_archive`.

## How the loader resolves overlaps

The yearly archives deliberately overlap (each carries the previous Nov/Dec and
part of the next January). The loader scans every file's dates, then picks one
"winner" file per day - the exact-year file first, then the live file, then the
most recent year - so each day loads exactly once. Overlaps are reported, never
silently merged. DST days (23 or 25 hours) are handled without losing any hour.

## Database tables (all keyed off `period`)

- `period` - one row per scheduling hour (timestamp, date, hour, DST info)
- `unit_generation` - realized gross MWh per turbine unit
- `plant_output` - realized gross and net MWh per plant
- `reservoir_state` - level and inflow columns (calculated, measured, forecast)
- `dayahead_unit` - archived day-ahead schedule per unit
- `cascade_balance` - cascade totals, submitted schedule, imbalance, intraday
- `daily_params` - per-day `K` coefficient, target levels, status
- `price` - IBEX day-ahead price (ready to be filled later)
- `source_file` - provenance for every loaded period

## Stage 2: the water-balance physics engine

`physics.py` ports the spreadsheet's level calculation into `Reservoir` and
`Cascade` classes, using the coefficients decoded from the sheet (the 0.3 / 0.5
drop rates, Kamenitza's level-banded coefficient, the 1.12 Pastra->Rila
conversion, the 0.8 MW Kamenitza water-supply threshold and 0.1 factor, the K
source coefficient, and the level caps).

`validate_physics.py` confirms the port: excluding the two known edge regimes (a
reservoir sitting at its spillway cap, and the daily re-seed where the operator
re-anchors to the measured level), the engine reproduces the spreadsheet for
~99% of hours to the millimetre and ~99.5% within 5 mm.

## Stage 3: the forward simulator

`simulator.py` projects reservoir levels forward from a **starting level** and a
**proposed schedule**, chaining on its own computed values (no stored data
needed). This is what the optimizer will use to ask "what would the levels do if
the plants ran like this?".

* **Inflows** come from an `InflowSource`: either a supplied hourly series, or
  persistence (hold the last good inflow, with an optional trend).
* **Spill / underflow**: each hour flags whether a reservoir would rise past its
  spillway (spill) or fall below empty (underflow = an infeasible, over-drawing
  schedule). For now levels are simply clamped at the spillway cap; proper spill
  routing (surplus passing downstream with the travel delay) is the next piece.

`simulate_demo.py` demonstrates it: it forward-simulates a real day and shows the
projected levels match the stored ones to a couple of millimetres, then shows a
short persistence projection.

```
python -m src.simulate_demo --db data/rila_cascade.db --day 2025-11-01
```

## Next stages (planned)

- Teach the engine the spill / overflow regime so it stays faithful at the caps.
- Turn it into a forward simulator (chain from a start level over any horizon).
- Add the inflow forecast.
- Add the price-following, headroom-aware schedule optimizer.

## Stage 3: the forward simulator

`simulator.py` projects reservoir levels forward from a starting level and a
proposed generation schedule, chaining on its *own* computed values (no stored
data during the run). This is what the optimizer will call to score a candidate
schedule: "if the plants ran like this, where do the levels go, and does
anything spill?"

- Inflows are supplied as a per-reservoir series; the `project_inflow` helper
  builds one from recent observations, either held flat (persistence) or along a
  simple trend, until the real forecast exists.
- Spill is interim: at the spillway cap the level is clamped and the hour is
  flagged `spilled`; routing the overflow downstream is the next planned step.

```
python -m src.simulator --db data/rila_cascade.db --day 2025-11-01
```

Validation: seeded from a day's true starting level, the simulator reproduces
the spreadsheet's levels to a few millimetres on the large majority of days
(median within-day drift ~4-6 mm; ~88% of days within 10 mm). The days that
diverge more are those with unusual manual intervention in the historical record
(hand-adjusted inflows or measured re-anchoring) - corrections a pure physics
projection legitimately cannot reproduce. In production the simulator projects
the future (no measurements) and is re-seeded from the latest measured level on
each run, which is exactly its intended use.
