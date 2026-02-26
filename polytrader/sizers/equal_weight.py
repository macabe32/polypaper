from __future__ import annotations

from ..types import Signal, SizedOrder
from .base import BaseSizer


class EqualWeightSizer(BaseSizer):
    name = "equal_weight"

    def __init__(self, slots: int = 10):
        self.slots = max(slots, 1)

    def size(self, signal: Signal, cash: float) -> SizedOrder | None:
        order_usd = max(cash, 0.0) / self.slots
        if order_usd <= 0:
            return None
        return SizedOrder(
            sizer=self.name,
            side=signal.side,
            token_id=signal.token_id,
            order_usd=order_usd,
            metadata={"slots": self.slots},
        )
