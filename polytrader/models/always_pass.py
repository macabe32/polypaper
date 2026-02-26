from __future__ import annotations

from ..types import MarketSnapshot, Signal
from .base import BaseModel


class AlwaysPassModel(BaseModel):
    name = "always_pass"

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        return None
