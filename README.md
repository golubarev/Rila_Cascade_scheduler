\# Rila Cascade Scheduler



A tool for building hourly power-production schedules for the Rila hydropower

cascade (2 dams, 3 reservoirs, 4 plants, 7 turbine units).



Built in stages. \*\*Stage 1\*\* is the data foundation (a SQLite database plus a

loader and an incremental updater for the historical `ГРАФИК РАБОТЕН` workbooks).

\*\*Stage 2\*\* is the water-balance physics engine and its validation.



\## Project layout



```

src/                  # all the code (a Python package)

&#x20; columns.py          # documents where every value lives in the "Grafik" sheet

&#x20; database.py         # SQLite schema (tidy/long tables) + connection helpers

&#x20; grafik\_parser.py    # shared: turns one .xlsm file into DayBlock objects (no DB)

&#x20; db\_writer.py        # shared: writes/deletes one day in the database

&#x20; load\_grafik.py      # FULL build from all files (overlap resolution)

&#x20; update\_grafik.py    # INCREMENTAL top-up from a single file ("newest wins")

&#x20; physics.py          # water-balance engine (Reservoir / Cascade classes)

&#x20; validate\_physics.py # checks the engine against stored level\_calc values

data/                 # the data (kept out of git)

&#x20; grafik\_archive/     # the source .xlsm workbooks

&#x20; rila\_cascade.db     # the generated database

README.md

.gitignore

```



`load\_grafik.py` and `update\_grafik.py` both rely on `grafik\_parser.py` and

`db\_writer.py`, so parsing and storage are defined once and can never drift.



\## Commands



All commands are run from the project root.



\*\*Full rebuild\*\* of the database from every workbook in the archive (safe to

re-run; it rebuilds from scratch):



```

python -m src.load\_grafik --data-dir "data\\grafik\_archive" --db data\\rila\_cascade.db

```



\*\*Incremental top-up\*\* from a single new file (daily / weekly). For every day in

the file it replaces that day's rows - "newest wins" - refreshing recent days

and adding new ones, leaving all other days untouched:



```

python -m src.update\_grafik --db data\\rila\_cascade.db --file "data\\grafik\_archive\\<new-file>.xlsm"

```



\*\*Validate\*\* the physics engine against the stored levels (full report, or one

day in detail):



```

python -m src.validate\_physics --db data\\rila\_cascade.db

python -m src.validate\_physics --db data\\rila\_cascade.db --day 2025-11-01

```



\*\*One-time setup\*\* (install the one dependency):



```

pip install openpyxl

```



> Paths above use Windows separators. On macOS/Linux use forward slashes, e.g.

> `--data-dir data/grafik\_archive`.



\## How the loader resolves overlaps



The yearly archives deliberately overlap (each carries the previous Nov/Dec and

part of the next January). The loader scans every file's dates, then picks one

"winner" file per day - the exact-year file first, then the live file, then the

most recent year - so each day loads exactly once. Overlaps are reported, never

silently merged. DST days (23 or 25 hours) are handled without losing any hour.



\## Database tables (all keyed off `period`)



\- `period` - one row per scheduling hour (timestamp, date, hour, DST info)

\- `unit\_generation` - realized gross MWh per turbine unit

\- `plant\_output` - realized gross and net MWh per plant

\- `reservoir\_state` - level and inflow columns (calculated, measured, forecast)

\- `dayahead\_unit` - archived day-ahead schedule per unit

\- `cascade\_balance` - cascade totals, submitted schedule, imbalance, intraday

\- `daily\_params` - per-day `K` coefficient, target levels, status

\- `price` - IBEX day-ahead price (ready to be filled later)

\- `source\_file` - provenance for every loaded period



\## Stage 2: the water-balance physics engine



`physics.py` ports the spreadsheet's level calculation into `Reservoir` and

`Cascade` classes, using the coefficients decoded from the sheet (the 0.3 / 0.5

drop rates, Kamenitza's level-banded coefficient, the 1.12 Pastra->Rila

conversion, the 0.8 MW Kamenitza water-supply threshold and 0.1 factor, the K

source coefficient, and the level caps).



`validate\_physics.py` confirms the port: excluding the two known edge regimes (a

reservoir sitting at its spillway cap, and the daily re-seed where the operator

re-anchors to the measured level), the engine reproduces the spreadsheet for

\~99% of hours to the millimetre and \~99.5% within 5 mm.



\## Next stages (planned)



\- Teach the engine the spill / overflow regime so it stays faithful at the caps.

\- Turn it into a forward simulator (chain from a start level over any horizon).

\- Add the inflow forecast.

\- Add the price-following, headroom-aware schedule optimizer.

