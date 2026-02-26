from __future__ import annotations

import json
from typing import Any

import httpx

from .types import MarketSnapshot

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
UA = {"User-Agent": "polytrader/0.1", "Accept": "application/json"}


def _parse_list_field(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            return []
    return []


def fetch_markets(query: str, limit: int) -> list[dict[str, Any]]:
    q = query.strip().lower()
    # Gamma search behavior can vary by deployment; fetch active list and filter client-side.
    params = {"active": "true", "closed": "false", "limit": str(max(limit * 4, limit))}
    with httpx.Client(timeout=20.0, headers=UA) as client:
        resp = client.get(f"{GAMMA_BASE}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        return []
    if not q:
        return data[:limit]
    filtered = []
    for m in data:
        blob = " ".join(
            [
                str(m.get("question", "")),
                str(m.get("slug", "")),
                str(m.get("category", "")),
                str(m.get("subcategory", "")),
            ]
        ).lower()
        if q in blob:
            filtered.append(m)
    return filtered[:limit]


def fetch_midpoint(token_id: str) -> float | None:
    with httpx.Client(timeout=20.0, headers=UA) as client:
        resp = client.get(f"{CLOB_BASE}/midpoint", params={"token_id": token_id})
        if resp.status_code != 200:
            return None
        obj = resp.json()
    try:
        return float(obj.get("mid"))
    except Exception:
        return None


def fetch_book(token_id: str) -> dict[str, Any] | None:
    with httpx.Client(timeout=20.0, headers=UA) as client:
        resp = client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        if resp.status_code != 200:
            return None
        data = resp.json()
    return data if isinstance(data, dict) else None


def build_market_snapshot(market: dict[str, Any]) -> MarketSnapshot | None:
    token_ids = _parse_list_field(market.get("clobTokenIds"))
    if len(token_ids) < 2:
        return None
    yes_token = token_ids[0]
    no_token = token_ids[1]
    yes_mid = fetch_midpoint(yes_token)
    no_mid = fetch_midpoint(no_token)
    if yes_mid is None or no_mid is None:
        return None
    return MarketSnapshot(
        market_id=str(market.get("id", "")),
        slug=str(market.get("slug", "")),
        question=str(market.get("question", "")),
        end_date=str(market.get("endDate", "")),
        yes_token_id=yes_token,
        no_token_id=no_token,
        yes_mid=yes_mid,
        no_mid=no_mid,
        liquidity=float(market.get("liquidityNum") or 0.0),
        volume=float(market.get("volumeNum") or 0.0),
    )
