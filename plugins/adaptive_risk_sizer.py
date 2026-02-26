from __future__ import annotations

from polytrader.sizers.base import BaseSizer
from polytrader.types import Signal, SizedOrder


class AdaptiveRiskSizer(BaseSizer):
    name = "adaptive_risk"

    def __init__(self, risk_fraction: float = 0.02, max_usd: float = 200.0):
        self.risk_fraction = risk_fraction
        self.max_usd = max_usd

    def size(self, signal: Signal, cash: float) -> SizedOrder | None:
        order_usd = min(max(cash, 0.0) * max(self.risk_fraction, 0.0), self.max_usd, max(cash, 0.0))
        if order_usd <= 0:
            return None
        return SizedOrder(
            sizer=self.name,
            side=signal.side,
            token_id=signal.token_id,
            order_usd=order_usd,
            metadata={"risk_fraction": self.risk_fraction},
        )
