# cheapquant-fixed-income

Interactive agent for **QuantLib** fixed-income analytics on government bonds.
Yield-curve inputs come from a read-only DuckDB or SQLite database (`ycs_data`);
QuantLib outputs are cached in SQLite via [framecache](https://github.com/hraoyama/FrameCache);
bond universes and historical analytics live in a separate **bond analytics** database;
and all three datasets are queryable through an LLM using [mcp-data](https://github.com/hraoyama/mcp_data).

Available as both a **terminal CLI** (`cqfi`) and a **GUI chat window** (`cqfi-gui`).

## Features

CheapQuant FI is built around **natural language**: you describe what you want,
the agent chooses tools and data sources, returns structured feedback, and (in
the GUI) renders tables and charts from the result.

![Example: 5Y CMT yields plotted from a natural-language query](resource/png/5ycmts.png)

### What you can do today

- **QuantLib pricing & analytics** — bootstrap yield curves (19 sovereign issuers,
  18 interpolation/fitting methods), price CMTs, and compute bond/CMT analytics:
  yield, duration, convexity, z-spread, par yield, curve zero rate, carry, and
  yield rolls (spot and forward at 1m/3m/6m/1y horizons).

- **Market context** — lazy-built `QuantlibMarketContext` objects keyed by
  valuation date and issuer. Curves are loaded from `ycs_data` on demand and
  cached in a process-wide singleton. Use `/mctx` in the CLI or the
  `check_market_context` LLM tool to verify/build curves.

- **Bond lookup** — look up individual bonds from `bond_universe` by
  `user_friendly_id` or `bond_id` via `/bond`, `@mention` syntax, or the
  `get_bond` LLM tool. Bonds deserialize into typed `Bond` objects via
  `BondManager`.

- **Bond analytics** — compute fixed-income metrics for any bond using the `/calc`
  command or `compute_bond_analytics` LLM tool. Results include yield, duration,
  convexity, z-spread, carry, roll-down analysis, and more. Trade date and curve
  label are optional (default to latest date and BOND_ZERO curve).

- **Three queryable datasets** (auto-routed by keyword, or forced with a prefix):

  | Prefix | Database | Typical questions |
  |--------|----------|-----------------|
  | `input:` | `ycs_data` | zero/par rates, FX, correlations, curve slopes |
  | `cache:` | active framecache | CMT prices, past QuantLib runs, calculation log |
  | `bond_analytics:` | `bond_analytics` | bond universe, stored analytics, CMT analytics |

- **Analytics cache** — every pricing call is stored in framecache SQLite.
  Save/load named sessions under `data/sessions/` to compare runs.

- **Mix and match** — pull a curve from `ycs_data`, ensure market context exists,
  price a bond, write results to cache, then ask “plot the 5Y CMTs we stored for
  Germany”. Data sources and QuantLib tools compose in one conversation.

### On the roadmap

- **Plug-in user tools** — register custom Python callables as agent tools
  alongside the built-in QuantLib and SQL paths.

- **Full analytics persistence** — write computed `bond_analytics` / `cmt_analytics`
  rows to the bond analytics DB on every run (schema and build script exist;
  runtime persistence is controlled by `settings.write_to_bond_analytics_db`).

### Interfaces

- **`cqfi` (CLI)** — interactive REPL with dataset prefixes, direct pricing
  commands, market-context and bond lookups, and session save/load.

- **`cqfi-gui` (GUI)** — PySide6 chat window: Markdown replies, sortable result
  tables, auto-generated plotnine charts, Download/Copy actions, and
  plot/table settings.

## Setup

```powershell
cd D:\Code\cheapquant-fixed-income
uv sync
copy .env.example .env   # optional — set ANTHROPIC_API_KEY for LLM mode
```

Paths are configured in `config/cqfi.yaml`, shared by both `cqfi` and
`cqfi-gui`. Override with `--config` or the `CQFI_CONFIG` environment variable.
Optional per-path overrides live in `.env` (see `.env.example`).

| Setting | Config key | Default |
|---------|------------|---------|
| YCS DB | `paths.ycs_db` | `D:/data/duckdb/ycs_data.duckdb` |
| YCS semantics | `paths.ycs_semantics` | `./semantics/ycs_data.yaml` |
| Bond analytics DB | `paths.bond_analytics_db` | `D:/data/duckdb/bond_analytics.duckdb` |
| Bond analytics semantics | `paths.bond_analytics_semantics` | `./semantics/bond_analytics.yaml` |
| Active cache | `paths.cache_db` | `./data/cache/active_cache.db` |
| Sessions | `paths.sessions_dir` | `./data/sessions/` |
| Cache semantics | `paths.cache_semantics_dir` | `./semantics` |
| Write analytics to bond DB | `settings.write_to_bond_analytics_db` | `true` |

Build or refresh the bond analytics database (schema + CSV seed data):

```powershell
uv run python -m cheapquant_fi.data.create_bond_analytics_db
```

## CLI usage (`cqfi`)

```powershell
uv run cqfi
uv run cqfi --config config/cqfi.yaml
uv run main.py        # IDE-friendly: auto-relaunches via .venv
```

### Dataset queries

```
cqfi> input: average 10Y zero rate for Germany in 2012
cqfi> cache: what 10Y CMT prices did we compute for USA?
cqfi> bond_analytics: show bonds for France maturing after 2030
```

Prefixes are optional when the question clearly targets one dataset (e.g.
“zero rate” → `input:`, “CMT price we computed” → `cache:`, “bond universe” →
`bond_analytics:`).

### Direct commands

#### Pricing commands

```
cqfi> price cmt USA 2020-01-02
cqfi> price cmt DEU 2019-06-14 --par
```

#### Market context

```
cqfi> /mctx 2024-02-15              # Check all curves for the date
cqfi> /mctx 2024-02-15 USA          # Check USA market for the date
cqfi> /mctx 2024-02-15 USA BOND_ZERO  # Check specific curve
cqfi> /mctx                          # Show /mctx help
```

`/mctx` verifies whether a `QuantlibMarketContext` exists for a given date
(optionally filtered by issuer and/or curve label) and builds it from `ycs_data`
if missing. Supported curve labels: `BOND_ZERO` (default) and `BOND_PAR`.

#### Bond lookup

```
cqfi> /bond usa10y001               # Look up by bond_id
cqfi> /bond fraapr029               # Look up by user_friendly_id
cqfi> @fraapr029                    # bare @mention shorthand
cqfi> /bond                         # Show /bond help
```

`/bond` loads a bond's details from `bond_universe` as JSON. The `@mention`
syntax (e.g., `@fraapr029`) works in natural-language queries to inject bond
context into the LLM's reasoning.

#### Bond analytics

```
cqfi> /calc fraapr029               # Calculate using latest date, BOND_ZERO curve
cqfi> /calc usa10y001 2024-02-15    # Specify trade date
cqfi> /calc fraapr029 2024-02-15 BOND_PAR  # Specify curve label
cqfi> /calc @fraapr029              # @ prefix optional
cqfi> /calc                         # Show /calc help
```

`/calc` computes fixed-income analytics for a bond under current market conditions.
Results include yield-to-maturity, duration, convexity, z-spread, par yield, carry
metrics, and roll-down analysis. If `trade_date` is omitted, the latest available
date for the issuer is used. Default curve label is `BOND_ZERO`.

#### Session management

```
cqfi> save my-run-001
cqfi> load my-run-001
cqfi> sessions                      # List saved sessions
cqfi> reset cache                   # Clear active cache
```

### LLM mode

Natural-language dataset questions require LLM mode:

```powershell
# Option 1: set API key in .env — cqfi auto-enables single-shot LLM
ANTHROPIC_API_KEY=sk-ant-...

# Option 2: explicit flags
uv run cqfi --llm
uv run cqfi --llm-single-shot
```

In LLM mode the agent can call SQL tools **and** fixed-income tools:

- `get_bond` — look up a bond and return its JSON representation
- `check_market_context` — ensure curves exist for a valuation date/issuer
- `compute_bond_analytics` — calculate bond analytics (yield, duration, convexity, carry, etc.)

Example prompts:

```
Is there a market for France on 2022-02-17?
Show bond usa10y001 as JSON
What was the 2s10s slope for Italy in 2019?
Calculate analytics for fraapr029
What is the duration of USA 10Y on 2024-02-15?
```

Without an API key, use rule syntax (works offline):

```
input: tables
input: schema zero_rates
input: sql: SELECT AVG(Y010p0) FROM zero_rates WHERE source='DEU'
bond_analytics: schema bond_universe
```

Force rule syntax even when an API key is set: `cqfi --rule`

LangSmith tracing is **off by default** — set `CQFI_LANGSMITH=1` in `.env` to
opt in (requires `LANGCHAIN_API_KEY`).

## GUI usage (`cqfi-gui`)

```powershell
uv run cqfi-gui
uv run cqfi-gui --config config/cqfi.yaml
```

`cqfi-gui` uses the same `config/cqfi.yaml` as the CLI by default.
Set `ANTHROPIC_API_KEY` in `.env` to enable LLM-powered queries.

## Bond analytics (Python API)

The analytics layer is split into typed inputs/outputs and a QuantLib backend.

```python
from datetime import date

from cheapquant_fi.analytics_input import BondAnalyticsInput
from cheapquant_fi.bond_manager import BondManager
from cheapquant_fi.numeric_term_structure import NumericTermStructure
from cheapquant_fi.quantlib.quantlib_analytics_calculator import QuantLibAnalyticsCalculator
from cheapquant_fi.quantlib.quantlib_market_context_manager import QuantlibMarketContextManager

# Ensure curves exist for the settlement date (loads from ycs_data on first use)
market = QuantlibMarketContextManager.instance().get(date(2024, 1, 15), "DEU")

# From bond_universe
bond = BondManager.instance().get("usa10y001")
request = BondAnalyticsInput.from_bond(
    bond,
    trade_date=date(2024, 1, 15),
    repo_term_structure=NumericTermStructure(
        {"1m": 5.25, "3m": 5.10, "6m": 4.95, "1y": 4.80},
        as_of=date(2024, 1, 15),
    ),
)

# Or specify directly
request = BondAnalyticsInput(
    issuer="DEU",
    coupon=2.5,
    maturity_date=date(2034, 1, 15),
    settlement_date=date(2024, 1, 17),
    issue_date=date(2024, 1, 15),
)

calc = QuantLibAnalyticsCalculator()
result = calc.compute_bond_analytics(request, market)

print(result.yield_to_maturity)   # percent
print(result.z_spread)            # basis points
print(result.roll_1y_spotyield)   # spot YTM minus 9y-equivalent YTM
print(result.roll_1y_fwdyield)    # spot YTM minus forward YTM in 1y
print(result.as_json())           # populated fields only
```

When a curve is available, analytics include:

| Field | Meaning |
|-------|---------|
| `yield_to_maturity`, `clean_price`, `dirty_price`, `accrued_interest` | Standard price/yield measures |
| `duration`, `convexity`, `dv01_sensitivity`, `gamma_sensitivity` | Risk metrics |
| `z_spread` | Z-spread to curve (bps) |
| `par_yield`, `zero_rate` | Par yield and curve zero at maturity (%) |
| `roll_*_spotyield` | Spot YTM minus YTM if maturity were shortened by the horizon |
| `roll_*_fwdyield` | Spot YTM minus YTM priced on the same curve at a forward date |
| `carry_*` | Yield minus repo rate from an optional `NumericTermStructure` |

CMT analytics use the same calculator with `CmtAnalyticsInput` (zero-coupon by
default; pass `coupon=` for a fixed-coupon synthetic).

## Tenor strings

Human-readable tenors are parsed by the `Tenor` class (`tenor.py`):

```python
from cheapquant_fi.tenor import Tenor

t = Tenor.parse("12y4M3w12d")   # order-independent
t.simplify()                    # carry units (60s→m, 7d→w, 12m→y, …)
t.add_to(date(2024, 1, 15))   # calendar-aware advance (issuer conventions)
t.days_tenor(date(2024, 1, 31))  # convert to day count from a start date
```

Units: `y`/`m`/`w`/`d`/`h` for year/month/week/day/hour; `` ` `` = minute;
```` `` = second.

`NumericTermStructure` maps tenor labels to numeric rates (e.g. repo curves) and
is used for carry calculations:

```python
from cheapquant_fi.numeric_term_structure import NumericTermStructure

repo = NumericTermStructure({"1m": 5.25, "3m": 5.10}, as_of=date(2024, 1, 15))
repo.to_dict()   # ordered by maturity
repo.filter({"1m", "3m", "6m", "1y"})
```

## Curve interpolation methods

Pass `interpolation=QLZeroInterp.<METHOD>` to `ql_build_zero_curve` /
`price_cmts_from_rates` (see `quantlib/quantlib_curve.py`).

| Family | Members | Rate type |
|--------|---------|-----------|
| `InterpolatedZeroCurve` | `LINEAR_ZERO`, `CUBIC_ZERO`\*, `NATURAL_CUBIC_ZERO`, `MONOTONE_CUBIC_ZERO` | ZERO |
| `PiecewiseYieldCurve` | `LINEAR_ZERO`\*\*, `CUBIC_ZERO`, `NATURAL_CUBIC_ZERO`, `KRUGER_ZERO`, `CONVEX_MONOTONE_ZERO`, `LOG_LINEAR_DISCOUNT`, `LOG_CUBIC_DISCOUNT`, `NATURAL_LOG_CUBIC_DISCOUNT`, `KRUGER_LOG_DISCOUNT`, `SPLINE_CUBIC_DISCOUNT`, `LINEAR_FORWARD`, `FLAT_FORWARD` | PAR |
| `FittedBondDiscountCurve` | `NELSON_SIEGEL`, `SVENSSON`, `EXPONENTIAL_SPLINES`, `SIMPLE_POLYNOMIAL`, `CUBIC_BSPLINES` | PAR |

\* default for ZERO rate inputs · \*\* default for PAR rate inputs

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         User (cqfi / cqfi-gui)                           │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          ▼                       ▼                       ▼
   Direct commands          LLM agent              Rule-based SQL
   (price cmt, /mctx,       (mcp-data +            (tables, schema,
    /bond, save/load)       extra tools)            sql: SELECT …)
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  ▼
                         ┌────────────────┐
                         │  Query router  │  input / cache / bond_analytics
                         └────────┬───────┘
                                  │
     ┌────────────────────────────┼────────────────────────────┐
     ▼                            ▼                            ▼
 ycs_data.duckdb            bond_analytics.duckdb         active_cache.db
 (zero/par/FX rates)        (bond_universe,               (framecache +
                             bond_analytics,                cmt_prices,
                             cmt_analytics)                 calculation_log)
     │                            │                            │
     └──────────────┬─────────────┴──────────────┬─────────────┘
                    ▼                            ▼
         QuantlibMarketContextManager    CacheManager (sessions)
                    │
                    ▼
         QuantLibAnalyticsCalculator
         (curves, CMT pricing, bond analytics)
```

**Key design points:**

- **`AppSettings`** (`config.py`) resolves all paths from `cqfi.yaml` + env
  overrides and registers MCP datasets with routing keywords.

- **`QuantlibMarketContextManager`** is a singleton. Calling `get(as_of, issuer)`
  lazily builds missing curves from `ycs_data` via `load_curve_rates` and
  `ql_build_zero_curve`.

- **`BondManager`** is a singleton lazy-loader over `bond_universe`.

- **`AnalyticsCalculator`** protocol (`analytics_calculator.py`) separates typed
  I/O (`BondAnalyticsInput`, `FixedIncomeAnalyticsOutput`) from the QuantLib
  implementation (`QuantLibAnalyticsCalculator`).

- **Semantics YAML** (`semantics/`) describes each database for mcp-data so the
  LLM can plan SQL without hard-coded schema knowledge.

Additional datasets can be registered in `cqfi.yaml` under a top-level
`datasets:` block — no code changes required.

## Project layout

```
src/cheapquant_fi/
  config.py                         — YAML path configuration, AppSettings
  issuers.py                        — 19 sovereign IssuerProfile conventions
  instruments.py                    — Bond dataclass (from bond_universe rows)
  bond_manager.py                   — singleton bond lookup cache
  tenor.py                          — Tenor parse/simplify/calendar math
  numeric_term_structure.py         — tenor → rate mappings (repo curves, etc.)
  ycs_tenors.py                     — YCS pillar column labels (6M, 1Y, …)
  analytics_input.py                — BondAnalyticsInput, CmtAnalyticsInput
  analytics_output.py               — FixedIncomeAnalyticsOutput
  analytics_calculator.py           — AnalyticsCalculator protocol
  cli_tools.py                      — get_bond, check_market_context, compute_bond_analytics LLM tools
  agent/
    cli.py                          — cqfi REPL entry point
    planner.py                      — rule/LLM query planning, /bond /mctx routing
  quantlib/
    quantlib_curve.py               — curve construction (QLZeroInterp enum)
    quantlib_market_context.py      — curve collections, FX, context builder
    quantlib_market_context_manager.py — singleton context registry
    quantlib_analytics_calculator.py   — QuantLib analytics implementation
    cmt.py                          — CMT pricing
  data/
    rates_loader.py                 — read zero/par rates from ycs_data
    create_bond_analytics_db.py     — build/populate bond_analytics DB
  cache/
    manager.py                      — framecache + session save/load
    registry.py                     — flattened SQL tables for LLM queries
  gui/
    app.py                          — cqfi-gui entry point
    chat_dialog.py                  — chat + result rendering
config/cqfi.yaml                    — shared path/settings config
semantics/
  ycs_data.yaml                     — ycs_data schema vocabulary
  bond_analytics.yaml               — bond analytics DB schema
  quant_cache.yaml                  — cache table schema
```

## Debug configurations (Cursor / VS Code)

Five launch profiles are defined in `.vscode/launch.json`:

| Name | What it runs |
|------|-------------|
| `cqfi` | CLI interactive REPL |
| `cqfi: one-shot query` | CLI with a single query argument (edit in `launch.json`) |
| `cqfi: price CMT` | CLI pricing smoke-run (`USA 2020-01-02`) |
| `cqfi-gui` | GUI window (uses `config/cqfi.yaml`) |
| `cqfi-gui: custom config` | GUI window with explicit `--config` flag |

## Dependencies

Local editable packages (via `pyproject.toml` `[tool.uv.sources]`):

- [framecache](https://github.com/FulgentMcGuffin/framecache) — SQLite-backed result caching
- [mcp-data](https://github.com/FulgentMcGuffin/mcp_data) — natural-language SQL planning
- [decorules](https://github.com/FulgentMcGuffin/decorules) — declarative validation decorators

PyPI: `QuantLib`, `polars`, `pyside6`, `plotnine`, `pyyaml`, `python-dotenv`,
`duckdb`, `anthropic`
