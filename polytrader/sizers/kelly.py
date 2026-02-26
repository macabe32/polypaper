from __future__ import annotations

from ..types import Signal, SizedOrder
from .base import BaseSizer


class KellySizer(BaseSizer):
    name = "kelly"

    def __init__(self, fraction: float = 0.25, max_usd: float = 250.0):
        self.fraction = min(max(fraction, 0.0), 1.0)
        self.max_usd = max(max_usd, 0.0)

    def size(self, signal: Signal, cash: float) -> SizedOrder | None:
        q = min(max(signal.market_price, 1e-6), 0.999999)
        p = min(max(signal.model_price, 0.0), 1.0)
        full_kelly = max(0.0, min(1.0, (p - q) / (1.0 - q)))
        scaled = self.fraction * full_kelly
        order_usd = min(max(cash, 0.0) * scaled, self.max_usd, max(cash, 0.0))
        if order_usd <= 0:
            return None
        return SizedOrder(
            sizer=self.name,
            side=signal.side,
            token_id=signal.token_id,
            order_usd=order_usd,
            metadata={"full_kelly": full_kelly, "fraction": self.fraction},
        )
