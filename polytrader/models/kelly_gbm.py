from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import httpx

from ..types import MarketSnapshot, Signal
from .base import BaseModel


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _parse_target(question: str) -> tuple[float, str] | None:
    q = question.lower()
    if " before " in q:
        return None
    m = re.search(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)([mk]?)", q)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    suf = m.group(2)
    if suf == "m":
        num *= 1_000_000
    elif suf == "k":
        num *= 1_000
    below_markers = ("below", "under", "dip", "drop", "fall", "at or below", "to or below")
    kind = "below_or_hit" if any(k in q for k in below_markers) else "above_or_hit"
    return num, kind


def _time_to_expiry_years(end_date: str) -> float:
    try:
        dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        secs = max((dt - datetime.now(timezone.utc)).total_seconds(), 60.0)
        return secs / (365.0 * 24.0 * 3600.0)
    except Exception:
        return 0.02


def _kraken_spot() -> float:
    with httpx.Client(timeout=10.0, headers={"User-Agent": "polytrader/0.1"}) as c:
        res = c.get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")
        res.raise_for_status()
        obj = res.json()
    pair = next(iter(obj["result"]))
    return float(obj["result"][pair]["c"][0])


def _kraken_sigma_annual() -> float:
    with httpx.Client(timeout=10.0, headers={"User-Agent": "polytrader/0.1"}) as c:
        res = c.get("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60")
        res.raise_for_status()
        obj = res.json()
    pair = [k for k in obj["result"] if k != "last"][0]
    rows = obj["result"][pair][-240:]
    closes = [float(r[4]) for r in rows]
    if len(closes) < 3:
        return 0.5
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if not rets:
        return 0.5
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    sigma_hour = math.sqrt(max(var, 1e-12))
    return max(sigma_hour * math.sqrt(24.0 * 365.0), 0.05)


class KellyGBMModel(BaseModel):
    name = "kelly_gbm"

    def __init__(self, min_edge: float = 0.002):
        self.min_edge = min_edge

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        parsed = _parse_target(market.question)
        if parsed is None:
            return None
        strike, kind = parsed
        spot = _kraken_spot()
        sigma = _kraken_sigma_annual()
        t = _time_to_expiry_years(market.end_date)
        if t <= 0:
            return None
        z = (math.log(max(spot, 1e-9) / max(strike, 1e-9)) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
        p_above = min(max(_norm_cdf(z), 0.0), 1.0)
        p_yes = 1.0 - p_above if kind == "below_or_hit" else p_above
        p_no = 1.0 - p_yes
        if p_yes >= market.yes_mid:
            side = "buy_yes"
            token = market.yes_token_id
            market_px = market.yes_mid
            model_px = p_yes
        else:
            side = "buy_no"
            token = market.no_token_id
            market_px = market.no_mid
            model_px = p_no
        edge = model_px - market_px
        if edge < self.min_edge:
            return None
        confidence = min(max(edge / 0.1, 0.0), 1.0)
        return Signal(
            model=self.name,
            side=side,
            token_id=token,
            market_price=market_px,
            model_price=model_px,
            edge=edge,
            confidence=confidence,
            metadata={"spot": spot, "sigma_annual": sigma, "strike": strike, "kind": kind, "t_years": t},
        )
