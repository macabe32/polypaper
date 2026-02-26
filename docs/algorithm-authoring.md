# Algorithm Authoring Guide

This guide is for agent builders who want to discover and iterate on novel trading algorithms in `polytrader`.

## Design contract

- A **model** transforms market state into a directional idea.
- A **sizer** transforms that idea into risk-adjusted order size.
- The **fill engine** simulates execution against live orderbook depth.

### Model interface

`BaseModel.evaluate(MarketSnapshot) -> Signal | None`

Return `None` for pass/no-trade.

`Signal` should include:
- `side`: `buy_yes` or `buy_no`
- `token_id`
- `market_price` and `model_price`
- `edge`: cost-unadjusted expected edge
- `confidence`: normalized confidence score `[0, 1]`
- `metadata`: explainability payload for downstream agents

### Sizer interface

`BaseSizer.size(Signal, cash) -> SizedOrder | None`

Return `None` when no size should be placed.

`SizedOrder` should include:
- `order_usd`
- `side`, `token_id`
- `metadata`: risk context (caps, Kelly fraction, volatility bucket, etc.)

## Fast scaffold workflow

```bash
polytrader scaffold-model --name my_edge --class-name MyEdgeModel --output plugins/my_edge_model.py --json
polytrader scaffold-sizer --name my_risk --class-name MyRiskSizer --output plugins/my_risk_sizer.py --json
```

## Run custom algorithms

```bash
polytrader scan \
  --model plugins.my_edge_model:MyEdgeModel \
  --sizer plugins.my_risk_sizer:MyRiskSizer \
  --model-config '{"edge_threshold":0.02}' \
  --sizer-config '{"risk_fraction":0.02,"max_usd":100}' \
  --experiment-tag "exp-a1" \
  --json
```

## Agent Tooling

- `polytrader vars --json` exposes tunable variable metadata for search agents.
- `polytrader tournament --spec-file ... --json` executes many algorithm variants in one run.

## Multi-agent iteration pattern

1. **Hypothesis agent** proposes signal logic.
2. **Model agent** writes model code and config variants.
3. **Risk agent** writes sizer variants.
4. **Runner agent** executes `scan` over a fixed schedule.
5. **Evaluator agent** compares `runs`, `positions`, `history`, and `account`.
6. **Selector agent** promotes top variants and archives failures.

## Best practices

- Keep model and sizer logic independent.
- Emit rich `metadata` for explainability.
- Use `--experiment-tag` on every run for attribution.
- Compare only strategies run on similar market windows.
- Avoid hardcoding secrets in plugin files.
