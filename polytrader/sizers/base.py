from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import Signal, SizedOrder


class BaseSizer(ABC):
    name = "base"

    @abstractmethod
    def size(self, signal: Signal, cash: float) -> SizedOrder | None:
        raise NotImplementedError
