# cheapquant-fixed-income

Interactive agent for **QuantLib** fixed-income analytics on government bonds.
Yield-curve inputs come from a read-only SQLite database; QuantLib outputs are
cached in SQLite via [framecache](https://github.com/hraoyama/FrameCache) and
queryable through an LLM using [mcp-data](https://github.com/hraoyama/mcp_data).

Available as both a **terminal CLI** (`cqfi`) and a **GUI chat window** (`cqfi-gui`).

## Features

- **CMT pricing** — bootstrap a yield curve from zero/par pillars and price
  constant-maturity zero-coupon bonds per issuer
- **19 sovereign issuers** — USA, DEU, GBR (with ex-dividend), JPN, and 15 more
  (AUS, AUT, BEL, BRA, CAN, CHE, CHN, ESP, FRA, GRC, IND, IRL, ITA, KOR, NLD,
  PRT, RUS) — all with real-life calendar and day-count conventions
- **Curve interpolation menu** — 18 methods across three QuantLib families
  (InterpolatedZeroCurve, PiecewiseYieldCurve, FittedBondDiscountCurve)
- **Dual-dataset agent** — one interface for read-only `input_data.db` questions
  and cached analytics (`quant_cache`)
- **Session persistence** — save/load cache snapshots by session id
- **GUI chat window** — `cqfi-gui` opens a PySide6 window with inline data
  tables, auto-generated plots, and Download/Copy buttons

## Setup

```powershell
cd D:\Code\cheapquant-fixed-income
uv sync
copy .env.example .env   # optional — set ANTHROPIC_API_KEY for LLM mode
```

Paths are configured in `config/cqfi.yaml` (CLI) and `config/cqfi_gui.yaml`
(GUI), both loaded automatically at startup. Override with `--config` or
the `CQFI_CONFIG` environment variable.

| Setting | Config key | Default |
|---------|------------|---------|
| Input DB | `paths.input_db` | `C:\data\sqlitedb\input_data.db` |
| Input semantics | `paths.input_semantics` | `D:\Code\mcp_data\semantics\input_data.yaml` |
| Active cache | `paths.cache_db` | `./data/cache/active_cache.db` |
| Sessions | `paths.sessions_dir` | `./data/sessions/` |
| Cache semantics | `paths.cache_semantics_dir` | `./semantics` |

## CLI usage (`cqfi`)

```powershell
uv run cqfi
uv run cqfi --config config/cqfi.yaml
uv run main.py        # IDE-friendly: auto-relaunches via .venv
```

```
cqfi> price cmt USA 2020-01-02
cqfi> price cmt DEU 2019-06-14 --par
cqfi> input: average 10Y zero rate for Germany in 2012
cqfi> cache: what 10Y CMT prices did we compute for USA?
cqfi> save my-run-001
cqfi> load my-run-001
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

Without an API key, use rule syntax (works offline):

```
input: tables
input: schema zero_rates
input: sql: SELECT AVG(Y010p0) FROM zero_rates WHERE source='DEU'
```

LangSmith tracing is **off by default** — set `CQFI_LANGSMITH=1` in `.env` to
opt in (requires `LANGCHAIN_API_KEY`).

## GUI usage (`cqfi-gui`)

```powershell
uv run cqfi-gui
uv run cqfi-gui --config config/cqfi_gui.yaml
```

The GUI window provides:

- **Chat panel** — type natural-language questions; responses rendered as
  formatted Markdown with syntax-highlighted code blocks
- **Results table** — last query result shown in a sortable, copyable table
- **Visualization panel** — auto-generated plot from the result data;
  date/time columns are used as the x-axis automatically
- **Markdown table detection** — if the LLM answer itself contains a
  date-indexed table the GUI displays it directly (no extra SQL round-trip)
- **Download / Copy Data / Copy Chart** action buttons
- **Settings** — UI theme, plot style, and download format/directory

`cqfi-gui` reads `config/cqfi_gui.yaml` first, falling back to `config/cqfi.yaml`.
Set `ANTHROPIC_API_KEY` in `.env` to enable LLM-powered queries.

## Curve interpolation methods

Pass `interpolation=ZeroInterp.<METHOD>` to `build_zero_curve` / `price_cmts_from_rates`.

| Family | Members | Rate type |
|--------|---------|-----------|
| `InterpolatedZeroCurve` | `LINEAR_ZERO`, `CUBIC_ZERO`\*, `NATURAL_CUBIC_ZERO`, `MONOTONE_CUBIC_ZERO` | ZERO |
| `PiecewiseYieldCurve` | `LINEAR_ZERO`\*\*, `CUBIC_ZERO`, `NATURAL_CUBIC_ZERO`, `KRUGER_ZERO`, `CONVEX_MONOTONE_ZERO`, `LOG_LINEAR_DISCOUNT`, `LOG_CUBIC_DISCOUNT`, `NATURAL_LOG_CUBIC_DISCOUNT`, `KRUGER_LOG_DISCOUNT`, `SPLINE_CUBIC_DISCOUNT`, `LINEAR_FORWARD`, `FLAT_FORWARD` | PAR |
| `FittedBondDiscountCurve` | `NELSON_SIEGEL`, `SVENSSON`, `EXPONENTIAL_SPLINES`, `SIMPLE_POLYNOMIAL`, `CUBIC_BSPLINES` | PAR |

\* default for ZERO rate inputs · \*\* default for PAR rate inputs

## Architecture

```
input_data.db (read-only)          QuantLib (CMT pricing)
        │                                    │
        │                                    ▼
        │                          framecache SQLiteBackend
        │                                    │
        └──────── mcp-data agent ────────────┤
                 (input: / cache:)           │
                                             ▼
                                    cmt_prices / calculation_log
                                    (LLM-queryable tables)
                                             │
                        ┌────────────────────┴────────────────────┐
                        ▼                                         ▼
                   cqfi (CLI)                             cqfi-gui (GUI)
                  terminal REPL                      PySide6 ChatDialog window
```

## Project layout

```
src/cheapquant_fi/
  config.py              — YAML path configuration
  issuers.py             — sovereign conventions (19 issuers)
  tenors.py              — pillar column mapping
  data/rates_loader.py   — read zero/par rates from input_data.db
  quantlib/curve.py      — yield curve construction (ZeroInterp enum)
  quantlib/cmt.py        — CMT pricing
  cache/manager.py       — framecache + session save/load
  cache/registry.py      — flattened SQL tables for LLM queries
  agent/cli.py           — CLI REPL entry point (cqfi)
  gui/app.py             — GUI entry point (cqfi-gui)
  gui/chat_dialog.py     — ChatDialog window (chat + table + plot)
config/cqfi.yaml           — CLI default path configuration
config/cqfi_gui.yaml       — GUI default path configuration
semantics/quant_cache.yaml — semantic profile for cached results
.vscode/launch.json        — Cursor/VS Code debug configurations
```

## Debug configurations (Cursor / VS Code)

Five launch profiles are defined in `.vscode/launch.json`:

| Name | What it runs |
|------|-------------|
| `cqfi` | CLI interactive REPL |
| `cqfi: one-shot query` | CLI with a single query argument (edit in `launch.json`) |
| `cqfi: price CMT` | CLI pricing smoke-run (`USA 2020-01-02`) |
| `cqfi-gui` | GUI window (uses `config/cqfi_gui.yaml`) |
| `cqfi-gui: custom config` | GUI window with explicit `--config` flag |

## Dependencies

Local editable packages (via `pyproject.toml` `[tool.uv.sources]`):

- `../framecache`
- `../mcp_data`

PyPI: `QuantLib`, `polars`, `pyside6`, `plotnine`, `pyyaml`, `python-dotenv`
