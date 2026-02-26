from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MarketSnapshot:
    market_id: str
    slug: str
    question: str
    end_date: str
    yes_token_id: str
    no_token_id: str
    yes_mid: float
    no_mid: float
    liquidity: float
    volume: float


@dataclass
class Signal:
    model: str
    side: str  # buy_yes | buy_no
    token_id: str
    market_price: float
    model_price: float
    edge: float
    confidence: float
    metadata: dict


@dataclass
class SizedOrder:
    sizer: str
    side: str
    token_id: str
    order_usd: float
    metadata: dict


@dataclass
class FillResult:
    avg_price: float
    shares: float
    spent_usd: float
    levels_used: int
    slippage_bps: float
