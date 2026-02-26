# Autonomous Search Layer

This layer gives agents the primitives required to explore strategy space autonomously.

## Core commands

- `polytrader vars --json`  
  Machine-readable variable catalog for search agents.

- `polytrader mutate-spec --base-spec-file ... --search-space-file ... --output-file ... --json`  
  Expands search space into a tournament spec of concrete experiments.

- `polytrader tournament --spec-file ... --json`  
  Executes all experiments and returns a leaderboard.

- `polytrader rank --experiments-dir experiments --json`  
  Scores experiment DBs using a consistent formula.
- `polytrader rank --pareto --pareto-metrics ... --json`  
  Surfaces non-dominated experiment variants.
- `polytrader replay --db ... --start-ts ... --end-ts ... --json`  
  Replays recorded trade behavior in a time window.

## Minimal autonomous loop

1. Query variable catalog
2. Generate candidates (`mutate-spec`)
3. Execute candidates (`tournament`)
4. Evaluate candidates (`rank`, `runs`, `account`, `history`)
5. Narrow search space and repeat

## Example

```bash
polytrader vars --json > vars.json
polytrader mutate-spec \
  --base-spec-file tournament.spec.example.json \
  --search-space-file search_space.example.json \
  --output-file tournament.generated.json \
  --json
polytrader tournament --spec-file tournament.generated.json --json > tournament.out.json
polytrader rank --experiments-dir experiments --json > rank.out.json
```

## Notes

- All commands are non-interactive and agent-safe.
- Use `--json` for machine parsing.
- Exit code `2` indicates no opportunities/orders, not an execution error.
