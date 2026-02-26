
#!/usr/bin/env python3
"""
Simple agentic research loop for Polymarket CLI.

Features:
- Pull active markets from `polymarket -o json markets list --limit N`
- Fetch CLOB midpoints for YES/NO tokens
- Detect opportunities where YES + NO midpoint diverges from 1.0
- Log opportunities and actions with timestamps
- Optional tiny trading path (disabled by default), hard-capped to <= $5 per market
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_polymarket(args: list[str]) -> Any:
    cmd = ["polymarket", "-o", "json", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return json.loads(proc.stdout)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_jsonish_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def chunked(seq: list[str], size: int) -> list[list[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def write_log(log_path: Path, payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def collect_markets(limit: int) -> list[dict[str, Any]]:
    data = run_polymarket(["markets", "list", "--active", "true", "--closed", "false", "--limit", str(limit)])
    if not isinstance(data, list):
        raise RuntimeError("Unexpected markets response: expected list")
    return data


def collect_midpoints(token_ids: list[str], batch_size: int = 100) -> dict[str, float]:
    result: dict[str, float] = {}
    for batch in chunked(token_ids, batch_size):
        if not batch:
            continue
        data = run_polymarket(["clob", "midpoints", ",".join(batch)])
        if not isinstance(data, dict):
            continue
        for k, v in data.items():
            try:
                # CLI returns decimal token id keys in this endpoint.
                result[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return result


def to_decimal_token_id(hex_token: str) -> str:
    return str(int(hex_token, 16))


def extract_opportunities(
    markets: list[dict[str, Any]],
    midpoint_map: dict[str, float],
    divergence_threshold: float,
) -> list[dict[str, Any]]:
    opportunities: list[dict[str, Any]] = []
    for m in markets:
        tokens = parse_jsonish_list(m.get("clobTokenIds"))
        outcomes = parse_jsonish_list(m.get("outcomes"))
        if len(tokens) < 2:
            continue

        yes_token_hex = tokens[0]
        no_token_hex = tokens[1]
        yes_mid = midpoint_map.get(to_decimal_token_id(yes_token_hex))
        no_mid = midpoint_map.get(to_decimal_token_id(no_token_hex))
        if yes_mid is None or no_mid is None:
            continue

        midpoint_sum = yes_mid + no_mid
        divergence = midpoint_sum - 1.0
        if abs(divergence) < divergence_threshold:
            continue

        opportunities.append(
            {
                "ts": now_iso(),
                "market_id": m.get("id"),
                "slug": m.get("slug"),
                "question": m.get("question"),
                "outcomes": outcomes if outcomes else ["Yes", "No"],
                "yes_token": yes_token_hex,
                "no_token": no_token_hex,
                "yes_mid": yes_mid,
                "no_mid": no_mid,
                "midpoint_sum": midpoint_sum,
                "divergence": divergence,
                "volume_num": float(m.get("volumeNum") or 0.0),
                "liquidity_num": float(m.get("liquidityNum") or 0.0),
            }
        )
    opportunities.sort(key=lambda x: abs(float(x["divergence"])), reverse=True)
    return opportunities


def maybe_trade(
    opportunities: list[dict[str, Any]],
    enable_trading: bool,
    execute: bool,
    max_total_order_usd: float,
    min_confidence_divergence: float,
    log_path: Path,
) -> None:
    if not enable_trading:
        return

    if max_total_order_usd > 5.0:
        raise ValueError("Safety guard: max_total_order_usd cannot exceed 5.0 without explicit human confirmation.")

    for opp in opportunities:
        divergence = float(opp["divergence"])
        if abs(divergence) < min_confidence_divergence:
            continue

        # Basic, conservative logic:
        # If YES+NO < 1, buying both sides may capture under-round opportunities.
        # If YES+NO > 1, skip (would overpay at current midpoint view).
        if divergence >= 0:
            write_log(
                log_path,
                {
                    "ts": now_iso(),
                    "action": "skip_trade_overround",
                    "reason": "midpoint_sum>=1",
                    "slug": opp["slug"],
                    "midpoint_sum": opp["midpoint_sum"],
                    "divergence": divergence,
                },
            )
            continue

        each_side_amount = round(max_total_order_usd / 2.0, 2)
        plan = {
            "ts": now_iso(),
            "action": "trade_plan_buy_both_sides",
            "slug": opp["slug"],
            "question": opp["question"],
            "yes_token": opp["yes_token"],
            "no_token": opp["no_token"],
            "amount_each_side_usd": each_side_amount,
            "total_market_usd": round(each_side_amount * 2, 2),
            "divergence": divergence,
            "execute": execute,
        }
        write_log(log_path, plan)

        if not execute:
            continue

        for side_name, token in (("yes", opp["yes_token"]), ("no", opp["no_token"])):
            cmd = ["clob", "market-order", "--token", token, "--side", "buy", "--amount", str(each_side_amount)]
            try:
                resp = run_polymarket(cmd)
                write_log(
                    log_path,
                    {
                        "ts": now_iso(),
                        "action": "trade_executed",
                        "side": side_name,
                        "slug": opp["slug"],
                        "token": token,
                        "amount": each_side_amount,
                        "response": resp,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                write_log(
                    log_path,
                    {
                        "ts": now_iso(),
                        "action": "trade_error",
                        "side": side_name,
                        "slug": opp["slug"],
                        "token": token,
                        "amount": each_side_amount,
                        "error": str(exc),
                    },
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Agentic Polymarket research loop")
    parser.add_argument("--limit", type=int, default=50, help="How many active markets to scan")
    parser.add_argument(
        "--divergence-threshold",
        type=float,
        default=0.02,
        help="Flag opportunities where abs(YES+NO-1.0) exceeds this threshold",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.04,
        help="Minimum divergence to consider trading logic",
    )
    parser.add_argument("--enable-trading", action="store_true", help="Enable trading logic (still safe by default)")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually place orders. Without this flag, trade actions are logged only.",
    )
    parser.add_argument(
        "--max-total-order-usd",
        type=float,
        default=5.0,
        help="Total USD per market across all orders (hard-capped at 5.0 by safety rule)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("opportunities.log.jsonl"),
        help="Path to JSONL log file",
    )
    args = parser.parse_args()

    if args.execute and not args.enable_trading:
        print("Refusing to execute trades because --enable-trading is not set.", file=sys.stderr)
        return 2

    try:
        markets = collect_markets(args.limit)
        token_ids: list[str] = []
        for m in markets:
            token_ids.extend(parse_jsonish_list(m.get("clobTokenIds")))
        midpoint_map = collect_midpoints(token_ids)
        opportunities = extract_opportunities(markets, midpoint_map, args.divergence_threshold)
    except Exception as exc:  # noqa: BLE001
        write_log(args.log_file, {"ts": now_iso(), "action": "fatal_error", "error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    for opp in opportunities:
        write_log(args.log_file, {"ts": now_iso(), "action": "opportunity", "payload": opp})

    maybe_trade(
        opportunities=opportunities,
        enable_trading=args.enable_trading,
        execute=args.execute,
        max_total_order_usd=args.max_total_order_usd,
        min_confidence_divergence=args.confidence_threshold,
        log_path=args.log_file,
    )

    print(
        json.dumps(
            {
                "timestamp": now_iso(),
                "markets_scanned": len(markets),
                "opportunities_found": len(opportunities),
                "log_file": str(args.log_file.resolve()),
                "divergence_threshold": args.divergence_threshold,
                "trading_enabled": args.enable_trading,
                "execute": args.execute,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
