# Rila Cascade Scheduler

A tool for building hourly power-production schedules for the Rila hydropower
cascade (2 dams, 3 reservoirs, 4 plants, 7 turbine units).

Built in stages. **Stage 1: the data foundation** - a SQLite database plus a
loader and an incremental updater for the historical `ГРАФИК РАБОТЕН` workbooks.

## Files

```
columns.py        # documents where every value lives in the "Grafik" sheet
database.py       # SQLite schema (tidy/long tables) + connection helpers
grafik_parser.py  # shared: turns one .xlsm file into DayBlock objects (no DB)
db_writer.py      # shared: writes/deletes one day in the database
load_grafik.py    # FULL build from all files (winner-file overlap resolution)
update_grafik.py  # INCREMENTAL top-up from a single file ("newest wins")
```

`load_grafik.py` and `update_grafik.py` both rely on `grafik_parser.py` and
`db_writer.py`, so parsing and storage are defined once and can never drift.

## Building the database (first time, or a clean rebuild)

```
python -m <pkg>.load_grafik --data-dir <folder-with-xlsm> --db <path-to.db>
```

Discovers every `*ГРАФИК*.xlsm` file, resolves the overlaps between the yearly
archives (each day is loaded once, from its "owner" file), and reports the
result. Rebuilds from scratch every run, so it is always safe to re-run.

## Topping up from a new file (the everyday case)

When you get a fresh file (daily, or start of the week):

```
python -m <pkg>.update_grafik --db <path-to.db> --file "<one-file.xlsm>"
```

For every day in that file it deletes the day's existing rows and re-inserts
them from the file - **newest wins**. This refreshes recent days (forecast rows
that now carry actual measured levels, intraday trades) and adds brand-new
future days, in one pass. Every other day in the database is left untouched.

It does not create the schema: if the database does not exist yet, it tells you
to run the full builder first.

> Note: the updater trusts the file you name for *all* days in it. Point it at a
> current/newer file - pointing it at an old file would overwrite newer days
> with older data.

(Replace `<pkg>` with the package folder name, e.g. `cascade_data`.)

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

## Next stages (planned)

- Port the spreadsheet's water-balance physics to Python and validate the
  computed levels against the stored `level_calc` values.
- Add the inflow forecast.
- Add the price-following, headroom-aware schedule optimizer.
