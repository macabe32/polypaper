 # polytrader

Agent-first modular paper trading framework for Polymarket prediction markets.

`polytrader` is built for autonomous agents that need end-to-end, non-interactive workflows:
- JSON-friendly command outputs (`--json`)
- deterministic command semantics
- persistent state in SQLite
- pluggable strategy components (models + risk engines/sizers + fill engine)

## Mission

Treat Polymarket paper trading like an algorithm discovery lab:
- discover markets from public APIs
- invent and evaluate interchangeable models
- invent and evaluate interchangeable risk engines/sizers
- simulate realistic order-book fills
- persist account, positions, and trade history for agent reasoning loops
- enable many agents to propose, test, compare, and evolve new algorithms

This project is intentionally not "preset strategy only." Built-ins are examples, not constraints.
The primary purpose is to let agents discover novel algorithms.

## Install

```bash
cd polymarket-cli
python3 -m venv .venv
./.venv/bin/pip install -e .
```

Then:

```bash
./.venv/bin/polytrader --help
```

## Agent Workflow

```bash
polytrader init --bankroll 10000 --json
polytrader markets --query bitcoin --limit 20 --json
polytrader scan --model kelly_gbm --sizer kelly --query bitcoin --json
polytrader account --json
polytrader positions --json
polytrader resolve --slug some-market-slug --outcome yes --json
polytrader history --json
polytrader models --json
```

## Multi-Agent Discovery Workflow

Typical discovery loop for a team of agents:

1. **Research agent** identifies a market subset and hypotheses.
2. **Model agent** generates/updates a custom `BaseModel`.
3. **Risk agent** generates/updates a custom `BaseSizer`.
4. **Execution agent** runs `scan` and captures JSON artifacts.
5. **Evaluator agent** compares outcomes via `account`, `positions`, `history`.
6. **Coordinator agent** promotes or rejects algorithm variants.

Because models/sizers are swappable, agents can mix-and-match components and iterate quickly.

## Commands

- `polytrader init` – initialize SQLite state + paper account
- `polytrader scan` – market fetch -> model -> sizer -> fill simulation -> persist
- `polytrader vars` – machine-readable variable catalog for agent parameter search
- `polytrader account` – cash, equity, open exposure, PnL
- `polytrader positions` – open positions marked to midpoint
- `polytrader resolve` – settle a market manually (`yes`/`no`)
- `polytrader history` – realized trade performance
- `polytrader runs` – inspect prior scan metadata for experiment comparison
- `polytrader tournament` – run many experiment specs and rank results
- `polytrader mutate-spec` – generate many experiment variants from a search space
- `polytrader rank` – score existing experiment DBs
- `polytrader replay` – time-window replay from recorded paper trades
- `polytrader markets` – discover live markets without placing paper trades
- `polytrader models` – list built-in models and sizers
- `polytrader scaffold-model` – generate a custom model template
- `polytrader scaffold-sizer` – generate a custom sizer template

Exit codes:
- `0`: success
- `1`: error
- `2`: scan completed with zero placed paper orders

## Core Abstractions

### Model
`BaseModel.evaluate(MarketSnapshot) -> Signal | None`

Built-ins:
- `kelly_gbm` – GBM-style probability for BTC price-target markets
- `always_pass` – null model for pipeline testing

### Sizer
`BaseSizer.size(Signal, cash) -> SizedOrder | None`

Built-ins:
- `kelly` – fractional Kelly sizing
- `fixed` – fixed USD sizing
- `equal_weight` – cash split by slots

You can replace these entirely with your own risk engines.

### Fill Engine
`FillEngine.simulate_buy(book, usd_amount) -> FillResult | None`

Walks ask levels to simulate slippage and partial depth consumption.

## Plugin Pattern

Pass import paths instead of built-ins. This is the core extension mechanism:

```bash
polytrader scan \
  --model mypkg.custom_model:MyModel \
  --sizer mypkg.custom_sizer:MySizer \
  --model-config '{"alpha":0.3}' \
  --sizer-config '{"risk_cap":0.05}' \
  --json
```

Any agent can create a new algorithm module and immediately use it without forking `polytrader`.
That includes:
- new signal-generation models
- new sizing/risk logic
- custom parameter schemas via `--model-config` / `--sizer-config`

Quick scaffolding:

```bash
polytrader scaffold-model --name my_edge --class-name MyEdgeModel --output plugins/my_edge_model.py --json
polytrader scaffold-sizer --name my_risk --class-name MyRiskSizer --output plugins/my_risk_sizer.py --json
```

Then load:

```bash
polytrader scan \
  --model plugins.my_edge_model:MyEdgeModel \
  --sizer plugins.my_risk_sizer:MyRiskSizer \
  --model-config '{"edge_threshold":0.015}' \
  --sizer-config '{"risk_fraction":0.03,"max_usd":150}' \
  --experiment-tag "exp-2026-02-25-a" \
  --json
```

## Variables For Agent Search

Agents can query a structured variable catalog:

```bash
polytrader vars --json
```

This returns default search space metadata for:
- scan-level knobs (`limit`, `min_liquidity`, `min_volume`, etc.)
- built-in model configs
- built-in sizer configs
- plugin import format

## Tournament Mode

Run a set of experiments from one JSON file:

```bash
polytrader tournament --spec-file tournament.spec.example.json --json
```

Each experiment can define:
- its own model + sizer
- query and market filters
- model/sizer configs
- its own DB file (optional) and bankroll

See `tournament.spec.example.json` for the schema.

## Autonomous Search Layer

Agent primitives for discovery:

```bash
polytrader vars --json
polytrader mutate-spec \
  --base-spec-file tournament.spec.example.json \
  --search-space-file search_space.example.json \
  --output-file tournament.generated.json \
  --json
polytrader tournament --spec-file tournament.generated.json --json
polytrader rank --experiments-dir experiments --json
polytrader replay --db experiments/exp-kelly-gbm.sqlite3 --start-ts 2026-02-25T00:00:00+00:00 --json
```

Artifacts:
- `tournament.spec.example.json`
- `search_space.example.json`
- `docs/autonomous-search-layer.md`

Ranking options:

```bash
polytrader rank --experiments-dir experiments --by score --json
polytrader rank --experiments-dir experiments --by realized_pnl --json
polytrader rank --experiments-dir experiments --pareto --pareto-metrics realized_pnl,unrealized_pnl,win_rate,signal_count --json
```

## Moltbook Launch Pack

Copy-paste prompts for agent builders:

```text
You are a strategy-search agent. Use polytrader vars --json to discover tunable variables.
Then generate a search_space.json that explores at least 50 variants while keeping risk conservative.
Use polytrader mutate-spec, tournament, and rank. Return top 10 candidates with rationale.
```

```text
You are a risk agent. Given ranked experiment outputs, tighten sizing and liquidity filters to reduce drawdown risk.
Propose a new search_space.json and run another tournament iteration.
```

```text
You are an evaluator agent. Compare the last two tournament runs by win_rate, realized_pnl,
signal_count, and replay trajectories. Recommend which experiment tags should be promoted.
```

## Data Sources (Public, No Auth)

- Gamma API: market discovery
- CLOB API: midpoint + order book depth

No private credentials are needed for paper trading.

## Secrets Safety

`.env` is git-ignored. Keep private credentials out of logs/output.
For this paper-trading framework, credentials are not required by default.

## Project Layout

```
polytrader/
  cli.py
  db.py
  engine.py
  market_data.py
  fill_engine.py
  registry.py
  types.py
  models/
  sizers/
```

## Docs

- `docs/algorithm-authoring.md` – templates and workflow for agent-generated models/sizers

## License

MIT
