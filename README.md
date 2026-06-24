# cheapquant-fixed-income

Interactive agent for **QuantLib** fixed-income analytics on government bonds,
starting with US Treasuries and German Bunds. Yield-curve inputs come from a
read-only SQLite database; QuantLib outputs are cached in SQLite via
[framecache](https://github.com/hraoyama/FrameCache) and queryable through an
LLM using [mcp-data](https://github.com/hraoyama/mcp_data).

## Features

- **CMT pricing** — bootstrap a curve from zero/par pillars and price
  constant-maturity zero-coupon bonds per issuer
- **Dual-dataset agent** — one REPL for read-only `input_data.db` questions
  and cached analytics (`quant_cache`)
- **Session persistence** — save/load cache snapshots by session id
- **Modular issuers** — USA and DEU today; GBR and JPN profiles ready to extend

## Setup

```powershell
cd D:\Code\cheapquant-fixed-income
uv sync
copy .env.example .env   # optional — set ANTHROPIC_API_KEY for --llm
```

Paths are configured in `config/cqfi.yaml` (loaded automatically at startup).
Override with `--config path/to/cqfi.yaml` or the `CQFI_CONFIG` environment variable.

Default values:

| Setting | Config key | Default |
|---------|------------|---------|
| Input DB | `paths.input_db` | `C:\data\sqlitedb\input_data.db` |
| Input semantics | `paths.input_semantics` | `D:\Code\mcp_data\semantics\input_data.yaml` |
| Active cache | `paths.cache_db` | `./data/cache/active_cache.db` |
| Sessions | `paths.sessions_dir` | `./data/sessions/` (files named `{session_id}.db`) |
| Cache semantics | `paths.cache_semantics_dir` | `./semantics` |

## Usage

```powershell
uv run cqfi
uv run main.py
uv run cqfi --config config/cqfi.yaml
```

Use `uv run` (or activate `.venv` after `uv sync`) — the system Python outside the
project venv does not have `cheapquant_fi` or its dependencies installed.
Running `main.py` from an IDE will auto-relaunch via `.venv` when it exists.

```
cqfi> price cmt USA 2020-01-02
cqfi> price cmt DEU 2019-06-14
cqfi> input: average 10Y zero rate for Germany in 2012
cqfi> cache: what 10Y CMT prices did we compute for USA?
cqfi> save my-run-001
cqfi> load my-run-001
```

### LLM mode

Natural-language dataset questions (e.g. `input: average 10Y zero for Germany in 2017`)
require LLM mode — the same as `db-mcp-client --llm`:

```powershell
# Option 1: set API key in .env — cqfi auto-uses single-shot LLM for dataset queries
ANTHROPIC_API_KEY=sk-ant-...

# Option 2: explicit flags
uv run cqfi --llm
uv run cqfi --llm-single-shot
```

Without an API key, use rule syntax (works offline):

```
input: tables
input: schema zero_rates
input: sql: SELECT AVG(Y010p0) FROM zero_rates WHERE source='DEU' AND date BETWEEN '2017-01-01' AND '2017-12-31'
```

LangSmith tracing is disabled by default (see `CQFI_LANGSMITH` in `.env.example`) to
avoid 403 errors when a global `LANGCHAIN_TRACING_V2` setting is active without a
valid LangSmith API key.

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
```

## Project layout

```
src/cheapquant_fi/
  config.py          — YAML path configuration (config/cqfi.yaml)
  issuers.py         — sovereign conventions (USA, DEU, …)
  tenors.py          — pillar column mapping
  data/rates_loader  — read zero/par rates from input_data.db
  quantlib/curve.py  — yield curve construction
  quantlib/cmt.py    — CMT pricing
  cache/manager.py   — framecache + session save/load
  cache/registry.py  — flattened SQL tables for LLM queries
  agent/cli.py       — unified REPL (cqfi)
config/cqfi.yaml           — default path configuration
semantics/quant_cache.yaml — semantic profile for cached results
```

## Dependencies

Local editable packages (via `pyproject.toml` `[tool.uv.sources]`):

- `../framecache`
- `../mcp_data`

PyPI: `QuantLib`, `polars`, `pyyaml`, `python-dotenv`
