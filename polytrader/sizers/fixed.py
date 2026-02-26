from __future__ import annotations

from ..types import Signal, SizedOrder
from .base import BaseSizer


class FixedSizer(BaseSizer):
    name = "fixed"

    def __init__(self, usd: float = 25.0):
        self.usd = usd

    def size(self, signal: Signal, cash: float) -> SizedOrder | None:
        order_usd = min(self.usd, max(cash, 0.0))
        if order_usd <= 0:
            return None
        return SizedOrder(
            sizer=self.name,
            side=signal.side,
            token_id=signal.token_id,
            order_usd=order_usd,
            metadata={"fixed_usd": self.usd},
        )
