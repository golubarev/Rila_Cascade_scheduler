\# Rila Cascade Scheduler



A tool for building hourly power-production schedules for the Rila hydropower

cascade (2 dams, 3 reservoirs, 4 plants, 7 turbine units).



This is being built in stages. \*\*Stage 1 (this commit): the data foundation\*\* —

a SQLite database and a loader that ingests the historical `ГРАФИК РАБОТЕН`

Excel workbooks into clean, query-ready tables.



\## What is here so far



```

src/

&#x20; columns.py       # documents where every value lives in the "Grafik" sheet

&#x20; database.py      # SQLite schema (tidy/long tables) + connection helpers

&#x20; load\_grafik.py   # parses the .xlsm files and loads them into the database

data/

&#x20; rila\_cascade.db  # the built database (generated - not stored in git)

```



\## How the loader works



1\. Finds every `\*ГРАФИК\*.xlsm` file in a folder and tags each with its year

&#x20;  (or marks the live `ADMIN` file).

2\. Scans the dates in each file. The yearly files deliberately overlap, so many

&#x20;  days appear twice.

3\. Picks one "winner" file per day (exact-year file first, then the live ADMIN

&#x20;  file, then the most recent year) so each day is loaded exactly once.

&#x20;  Overlaps are reported, never silently merged.

4\. Streams each file and writes the hourly rows plus the per-day header

&#x20;  parameters (the Kalin/Karagyol `K` coefficient and the target levels).



DST is handled: the spring 23-hour and autumn 25-hour days are kept intact via a

per-day sequence index, so no hour is lost or duplicated.



\## Running it



From the project root:



```

python -m src.load\_grafik --data-dir /path/to/xlsm/folder --db data/rila\_cascade.db

```



It rebuilds the database from scratch each run, so it is always safe to re-run.



\## Database tables (all keyed off `period`)



\- `period` — one row per scheduling hour (timestamp, date, hour, DST info)

\- `unit\_generation` — realized gross MWh per turbine unit

\- `plant\_output` — realized gross and net MWh per plant

\- `reservoir\_state` — level and inflow columns (calculated, measured, forecast)

&#x20; for the three downstream reservoirs

\- `dayahead\_unit` — archived day-ahead schedule per unit

\- `cascade\_balance` — cascade totals, submitted schedule, imbalance, intraday

\- `daily\_params` — per-day `K` coefficient, target levels, status

\- `price` — IBEX day-ahead price (ready to be filled later)

\- `source\_file` — provenance for every loaded period



\## Next stages (planned)



\- Port the spreadsheet's water-balance physics to Python and validate the

&#x20; computed levels against the stored `level\_calc` values.

\- Add the inflow forecast.

\- Add the price-following, headroom-aware schedule optimizer.

