# Hydro Cascade Scheduler — Build Plan

A step-by-step plan for replacing your `.xlsm` VBA scheduler with a Python + PostgreSQL
platform, packaged as a standalone executable. Tasks are numbered **Day 1, Day 2, …** and
sized at roughly 1–3 focused hours each. Do them in order, one at a time, whenever you have time.

---

## Working principles (read once)

- **Commit to git after every task.** Small, frequent commits = a safety net and a progress log.
- **Trust the Excel tool as your ground truth** until the Python version matches it. Every
  scheduling calculation you port gets validated against the spreadsheet's output.
- **One task = one thing that works.** If a "day" feels too big, split it. If it feels tiny, bundle two.
- **Keep secrets out of git** (API keys, DB passwords) — use a `.env` file that is git-ignored.
- **The output file is a hard interface.** SCADA reads the schedule from the original `.xlsm` layout, so the tool's *final* job is to write results back into that exact file format / cell layout — copy-paste ready. This is a fixed requirement, not a nice-to-have; design the `schedule` output so it maps cleanly onto those cells.
- **SQLAlchemy first.** You haven't gone through the SQLAlchemy course yet — do it before **Day 16**, where the ORM work begins. Days 4–5 only need a basic connection, so you can start now and learn the ORM in parallel.

## Recommended stack

| Layer | Choice | Note |
|---|---|---|
| Language | Python 3.11+ | |
| Database | PostgreSQL 15+ | runs as a server on your PC / network machine |
| DB access | SQLAlchemy 2.x + psycopg (v3) | ORM + engine |
| Data wrangling | pandas | |
| External data | Open-Meteo (weather, free/no key), IBEX day-ahead prices, optionally ENTSO-E API | |
| Scheduling logic | port your VBA first; optionally add LP/MILP via **Pyomo** or **PuLP** / OR-Tools later | |
| Charts | Plotly | |
| GUI / platform | **Dash** (recommended) — data-heavy, great charts, packages well. Alt: **PySide6** for a native desktop feel | |
| Packaging | **PyInstaller** (+ **pywebview** to wrap Dash as a desktop window) → single `.exe` | |
| Scheduling jobs | APScheduler, or Windows Task Scheduler | for daily data pulls |

---

## Phase 0 — Setup & orientation

- [ ] **Day 1** — Install Python 3.11+, create the project folder, run `git init`, add a `README.md`.
- [ ] **Day 2** — Create a virtual environment (`python -m venv .venv`), a `requirements.txt`, and a `.gitignore` (ignore `.venv/`, `.env`, `__pycache__/`, output files).
- [ ] **Day 3** — Install PostgreSQL locally + a GUI client (DBeaver or pgAdmin). Create an empty database, e.g. `hydro`.
- [ ] **Day 4** — From Python, connect with SQLAlchemy and print the Postgres version. Store the connection string in `.env`, load it with `python-dotenv`. Goal: one script that proves Python ↔ DB works.
- [ ] **Day 5** — Set up the folder structure and a basic logging config:
  ```
  src/
    config.py      # loads .env, settings
    db.py          # SQLAlchemy engine/session
    model/         # domain classes (plants, reservoirs…)
    orm/           # SQLAlchemy table models
    ingest/        # data source fetchers
    scheduler/     # the scheduling logic
    viz/           # charts
    app/           # the GUI platform
  tests/
  notebooks/       # scratch/exploration
  data/            # local sample files
  ```

## Phase 1 — Understand & document the existing scheduler

- [ ] **Day 6** — Open the `.xlsm`. Make an inventory of every sheet and label each as: input / parameter / intermediate calc / output.
- [ ] **Day 7** — List every **input variable**: name, units, source, and how often it updates.
- [ ] **Day 8** — Extract all VBA modules to text files (VBA editor → export, or copy each module). Skim and list every macro/function with a one-line purpose.
- [ ] **Day 9** — Write the **core scheduling algorithm in plain language** — a flowchart or pseudocode. This is the most important document in the project.
- [ ] **Day 10** — Collect all fixed **constants/coefficients**: reservoir min/max volumes, level–volume curves, turbine flow/power limits, efficiency curves, minimum environmental flows.
- [ ] **Day 11** — Document the **cascade topology**: which plant releases into which, and the water travel delay between them.
- [ ] **Day 12** — Write down the **objective** (what the schedule maximizes/minimizes) and the full list of **constraints**.
- [ ] **Day 13** — Save 2–3 real historical schedules (inputs + resulting output) as validation cases for later.
- [ ] **Day 13a** — **Map the exact output layout SCADA reads**: which sheet, which cells/columns, the row ordering, units, and number formatting. This becomes the contract your generated file must satisfy (used on Day 53).

## Phase 2 — Data model & PostgreSQL schema

- [ ] **Day 14** — Sketch an ER diagram. Separate **master/reference tables** (plants, units, reservoirs, cascade links) from **time-series tables** (prices, weather, inflows, levels, schedules, actuals).
- [ ] **Day 15** — Decide time granularity (hourly? 15-min?) and **store all timestamps in UTC**. Write this decision into the README.
- [ ] **Day 16** — Define master tables as SQLAlchemy models: `plant`, `turbine_unit`, `reservoir`, `cascade_link`. Create them in the DB.
- [ ] **Day 17** — Define time-series tables: `price`, `weather_forecast`, `inflow`, `water_level`, `schedule`, `actuals`. Add composite indexes on `(entity_id, timestamp)`.
- [ ] **Day 18** — Seed the master tables with your real 4 plants and their parameters from Phase 1.
- [ ] **Day 19** — Write a few test queries to sanity-check the schema and relationships.

## Phase 3 — Domain model (Python classes)

- [ ] **Day 20** — Design the class hierarchy: a `Facility` base → `HydroPlant`, `Reservoir`, `TurbineUnit`, plus a `Cascade` container.
- [ ] **Day 21** — Implement `Reservoir`: volume, level–volume curve, min/max, an `apply_balance(inflow, outflow, dt)` step.
- [ ] **Day 22** — Implement `TurbineUnit`: flow↔power conversion, efficiency curve, min/max flow, on/off state.
- [ ] **Day 23** — Implement `HydroPlant`: aggregates its units + reservoir, computes total power from total release.
- [ ] **Day 24** — Implement `Cascade`: plant ordering + water routing (upstream outflow becomes downstream inflow after the travel delay).
- [ ] **Day 25** — Add `Cascade.simulate(release_schedule, horizon)` that steps the whole system through time and returns power, levels, spill.
- [ ] **Day 26** — Add methods to load each object's parameters from the DB via SQLAlchemy.
- [ ] **Day 27** — Unit-test the water balance and power calc against a hand-computed example.

## Phase 4 — Data ingestion & automation

- [ ] **Day 28** — Write an importer that loads your existing input data (from the `.xlsm`/CSV) into Postgres via pandas, using **idempotent upserts** (safe to re-run).
- [ ] **Day 29** — Add validation on import: schema/units checks, gap detection; log rejected rows instead of failing silently.
- [ ] **Day 30** — Weather: connect to **Open-Meteo** (free, no API key), fetch forecast (precipitation, temperature) for your catchment coordinates, store in `weather_forecast`.
- [ ] **Day 31** — Prices: fetch **IBEX day-ahead prices** from ibex.bg, parse, store in `price`. (Inspect whether they offer a data file/API before scraping HTML.)
- [ ] **Day 32** — *(Optional)* Add **ENTSO-E Transparency Platform** as a source (free token) for richer prices/load/generation across the region.
- [ ] **Day 33** — Refactor sources into a common shape: each is a class with `fetch()` + `save()`, so adding a new feed is trivial.
- [ ] **Day 34** — Build a daily runner (APScheduler in-app, or a Windows Task Scheduler entry) that pulls all feeds automatically.
- [ ] **Day 35** — Add data-freshness checks: warn (log/email) when any feed is stale or missing.

## Phase 5 — Port the scheduling logic

- [ ] **Day 36** — Re-implement the VBA algorithm in Python for a **single plant, one day**. Compare to the Excel output.
- [ ] **Day 37** — Reconcile any differences until the single-plant result matches Excel within tolerance.
- [ ] **Day 38** — Add the **cascade coupling** (upstream → downstream with delay). Verify against Excel.
- [ ] **Day 39** — Produce the full **4-plant schedule**; compare to the historical validation cases from Day 13.
- [ ] **Day 40** — Reconcile discrepancies until the Python schedule reliably matches Excel.
- [ ] **Day 41** — Refactor into a clean `Scheduler` class: inputs in → schedule out. Save results to the `schedule` table, and make sure the output structure maps cleanly onto the SCADA output cells from Day 13a.
- [ ] **Day 42** — *(Optional, later)* Add an **optimization variant** (LP/MILP with Pyomo or PuLP) maximizing revenue = Σ(power × price) subject to your constraints. Compare against the heuristic.

## Phase 6 — Visualizations

- [ ] **Day 43** — Set up Plotly. Chart the price curve + power schedule (stacked per plant) over the horizon.
- [ ] **Day 44** — Chart reservoir levels/volumes over time.
- [ ] **Day 45** — Chart inflow vs outflow vs spill per plant.
- [ ] **Day 46** — Build a summary view: revenue, energy produced, utilization per plant.
- [ ] **Day 47** — Build a comparison view: planned vs actual (or scenario A vs B).

## Phase 7 — The platform / GUI

- [ ] **Day 48** — Scaffold a **Dash** app: layout with a date/horizon picker, plant selector, and a "Run" button. (Choose PySide6 instead here only if you want a native desktop UI.)
- [ ] **Day 49** — Screen 1 — **Data status dashboard**: are prices / weather / inflows loaded and fresh?
- [ ] **Day 50** — Screen 2 — **Inputs review/edit**: adjust assumptions before running.
- [ ] **Day 51** — Screen 3 — **Run scheduler** → show the schedule tables + the Phase 6 charts.
- [ ] **Day 52** — Screen 4 — **Scenarios**: save, load, and compare runs.
- [ ] **Day 53** — **Write results back into the original `.xlsm` layout** (using `openpyxl`) so SCADA reads it unchanged — matching the exact cells/format from Day 13a, copy-paste ready. This is the *primary* output; CSV/PDF are secondary.
- [ ] **Day 54** — Wire it all to the DB via SQLAlchemy; add error handling and clear user feedback on failures.

## Phase 8 — Standalone executable

- [ ] **Day 55** — Confirm the DB deployment model: Postgres as a local/network **server** (recommended), app connects as a client. Write connection settings into a config file.
- [ ] **Day 56** — Freeze dependencies; verify the app runs cleanly from a fresh virtual environment.
- [ ] **Day 57** — Package with **PyInstaller** (wrap the Dash app in **pywebview** so it opens as a desktop window) into a single `.exe`.
- [ ] **Day 58** — Handle bundled assets/config; **test on a clean machine that has no Python installed**.
- [ ] **Day 59** — Add a first-run setup screen for DB connection settings + a short user manual / README.

## Phase 9 — Validation & rollout

- [ ] **Day 60** — Run the new tool **in parallel** with the Excel scheduler; compare outputs daily.
- [ ] **Day 61** — Fix discrepancies and tune.
- [ ] **Day 62** — Add automated tests for the critical calculations; set up **DB backups**.
- [ ] **Day 63** — Document maintenance: how to add a data source, how to update turbine curves/coefficients.
- [ ] **Day 64** — Once confident, retire the Excel tool.

---

## Extra data sources worth considering

- **ENTSO-E Transparency Platform** — free (with token): day-ahead prices, load, cross-border flows, generation.
- **River discharge / hydrology** — national hydro-meteorological service gauge data for real inflow observations to validate/calibrate your inflow model.
- **Snowpack / precipitation history** — improves seasonal inflow forecasting for the catchment.
- **Balancing / imbalance prices** — if you also trade on the balancing market, not just day-ahead.

## Open questions to settle early (they shape several phases)

1. Is the current logic a **rule-based heuristic** or a **mathematical optimization**? (Determines how far Phase 5 goes.)
2. Time resolution: **hourly** (day-ahead) or **15-minute** (balancing)?
3. Who runs the final app — just you, or several colleagues on different machines? (Affects the DB-as-server decision.)
4. Any **environmental / regulatory constraints** (minimum flows, ramping limits) that must always hold?
