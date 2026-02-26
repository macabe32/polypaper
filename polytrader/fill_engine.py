from __future__ import annotations

from typing import Any

from .types import FillResult


class FillEngine:
    def simulate_buy(self, book: dict[str, Any], usd_amount: float) -> FillResult | None:
        asks = book.get("asks") if isinstance(book, dict) else None
        if not isinstance(asks, list) or usd_amount <= 0:
            return None
        remaining_usd = float(usd_amount)
        spent = 0.0
        shares = 0.0
        levels = 0
        for lvl in asks:
            try:
                px = float(lvl["price"])
                sz = float(lvl["size"])
            except Exception:
                continue
            if px <= 0 or sz <= 0:
                continue
            level_capacity_usd = px * sz
            take_usd = min(remaining_usd, level_capacity_usd)
            take_shares = take_usd / px
            spent += take_usd
            shares += take_shares
            remaining_usd -= take_usd
            levels += 1
            if remaining_usd <= 1e-9:
                break
        if shares <= 0 or spent <= 0:
            return None
        avg_price = spent / shares
        top_ask = None
        try:
            top_ask = float(asks[0]["price"])
        except Exception:
            top_ask = avg_price
        slippage_bps = ((avg_price / max(top_ask, 1e-9)) - 1.0) * 10000.0
        return FillResult(
            avg_price=avg_price,
            shares=shares,
            spent_usd=spent,
            levels_used=levels,
            slippage_bps=slippage_bps,
        )
