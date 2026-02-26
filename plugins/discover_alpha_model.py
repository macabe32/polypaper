from __future__ import annotations

from polytrader.models.base import BaseModel
from polytrader.types import MarketSnapshot, Signal


class DiscoverAlphaModel(BaseModel):
    name = "discover_alpha"

    def __init__(self, edge_threshold: float = 0.01):
        self.edge_threshold = edge_threshold

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        # Example placeholder logic:
        # Trade the cheaper side when YES+NO midpoint is below 1.0 by threshold.
        total = market.yes_mid + market.no_mid
        if (1.0 - total) < self.edge_threshold:
            return None
        if market.yes_mid <= market.no_mid:
            side = "buy_yes"
            token_id = market.yes_token_id
            market_px = market.yes_mid
            model_px = min(max(market.yes_mid + (1.0 - total), 0.0), 1.0)
        else:
            side = "buy_no"
            token_id = market.no_token_id
            market_px = market.no_mid
            model_px = min(max(market.no_mid + (1.0 - total), 0.0), 1.0)
        edge = model_px - market_px
        if edge <= 0:
            return None
        return Signal(
            model=self.name,
            side=side,
            token_id=token_id,
            market_price=market_px,
            model_price=model_px,
            edge=edge,
            confidence=min(max(edge / 0.1, 0.0), 1.0),
            metadata={"total_mid": total},
        )
