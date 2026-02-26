from __future__ import annotations

from typing import Any

from . import SCHEMA_VERSION
from . import db as db_mod
from .fill_engine import FillEngine
from .market_data import build_market_snapshot, fetch_book, fetch_markets, fetch_midpoint
from .models.base import BaseModel
from .sizers.base import BaseSizer
from .types import MarketSnapshot, utc_now_iso


def scan_once(
    conn,
    model: BaseModel,
    sizer: BaseSizer,
    experiment_tag: str | None,
    query: str,
    limit: int,
    min_liquidity: float,
    min_volume: float,
) -> dict[str, Any]:
    account = db_mod.get_account(conn)
    cash = float(account["cash"])
    markets_raw = fetch_markets(query=query, limit=limit)
    fill_engine = FillEngine()
    snapshots: list[MarketSnapshot] = []
    for m in markets_raw:
        liq = float(m.get("liquidityNum") or 0.0)
        vol = float(m.get("volumeNum") or 0.0)
        if liq < min_liquidity or vol < min_volume:
            continue
        snap = build_market_snapshot(m)
        if snap is not None:
            snapshots.append(snap)

    opportunity_rows: list[dict[str, Any]] = []
    placed_rows: list[dict[str, Any]] = []
    for snap in snapshots:
        sig = model.evaluate(snap)
        if sig is None:
            continue
        opportunity_rows.append(
            {
                "slug": snap.slug,
                "question": snap.question,
                "side": sig.side,
                "token_id": sig.token_id,
                "market_price": sig.market_price,
                "model_price": sig.model_price,
                "edge": sig.edge,
                "confidence": sig.confidence,
                "metadata": sig.metadata,
            }
        )
        order = sizer.size(sig, cash=cash)
        if order is None:
            continue
        if order.order_usd > cash:
            continue
        book = fetch_book(order.token_id)
        if book is None:
            continue
        fill = fill_engine.simulate_buy(book, order.order_usd)
        if fill is None:
            continue
        cash -= fill.spent_usd
        db_mod.update_cash(conn, cash)
        placed_rows.append(
            {
                "market_id": snap.market_id,
                "market_slug": snap.slug,
                "question": snap.question,
                "side": order.side,
                "token_id": order.token_id,
                "entry_price": fill.avg_price,
                "shares": fill.shares,
                "spent_usd": fill.spent_usd,
                "model_price": sig.model_price,
                "edge": sig.edge,
                "confidence": sig.confidence,
                "slippage_bps": fill.slippage_bps,
                "fill_levels": fill.levels_used,
                "sizer_meta": order.metadata,
                "signal_meta": sig.metadata,
            }
        )

    run_id = db_mod.insert_run(
        conn,
        model=model.name,
        sizer=sizer.name,
        experiment_tag=experiment_tag,
        query=query,
        markets_scanned=len(snapshots),
        opportunities=len(opportunity_rows),
        signals=len(placed_rows),
        params={
            "limit": limit,
            "min_liquidity": min_liquidity,
            "min_volume": min_volume,
            "model": model.name,
            "sizer": sizer.name,
        },
    )

    trade_ids = []
    for row in placed_rows:
        trade_id = db_mod.insert_trade(
            conn=conn,
            run_id=run_id,
            market_id=row["market_id"],
            market_slug=row["market_slug"],
            question=row["question"],
            side=row["side"],
            token_id=row["token_id"],
            entry_price=row["entry_price"],
            shares=row["shares"],
            notional_usd=row["spent_usd"],
            model_price=row["model_price"],
            edge=row["edge"],
            confidence=row["confidence"],
            notes={
                "slippage_bps": row["slippage_bps"],
                "fill_levels": row["fill_levels"],
                "sizer_meta": row["sizer_meta"],
                "signal_meta": row["signal_meta"],
            },
        )
        trade_ids.append(trade_id)

    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": utc_now_iso(),
        "run_id": run_id,
        "experiment_tag": experiment_tag,
        "markets_scanned": len(snapshots),
        "opportunities": opportunity_rows,
        "orders_placed": placed_rows,
        "trade_ids": trade_ids,
        "cash_after": cash,
    }


def mark_open_positions(conn) -> list[dict[str, Any]]:
    rows = db_mod.list_open_trades(conn)
    out = []
    for r in rows:
        mid = fetch_midpoint(str(r["token_id"]))
        if mid is None:
            continue
        shares = float(r["shares"])
        current_value = shares * mid
        notional = float(r["notional_usd"])
        out.append(
            {
                **r,
                "mark_price": mid,
                "mark_value": current_value,
                "unrealized_pnl": current_value - notional,
                "unrealized_pnl_pct": (current_value - notional) / max(notional, 1e-9),
            }
        )
    return out
