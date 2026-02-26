from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScanSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    model: str = "kelly_gbm"
    sizer: str = "kelly"
    query: str = "bitcoin"
    limit: int = 50
    min_liquidity: float = 50000.0
    min_volume: float = 100000.0
    model_kwargs: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    sizer_kwargs: dict[str, Any] = Field(default_factory=dict, alias="sizer_config")


class ExperimentSpec(BaseModel):
    tag: str
    db: str | None = None
    init_bankroll: float = 10000.0
    scan: ScanSpec = Field(default_factory=ScanSpec)


class TournamentSpec(BaseModel):
    experiments: list[ExperimentSpec] = Field(default_factory=list)
