from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import MarketSnapshot, Signal


class BaseModel(ABC):
    name = "base"

    @abstractmethod
    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        raise NotImplementedError
