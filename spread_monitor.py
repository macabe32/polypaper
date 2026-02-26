#!/usr/bin/env python3
"""
Spread monitor: Spot/Perp vs Polymarket order book.

Design goals:
- Use Rust Polymarket CLI as transport (`polymarket -o json ...`)
- Default to paper mode (no real orders)
- Execute only when expected net edge > threshold after costs
- Keep resource usage light for laptops
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_polymarket(args: list[str]) -> Any:
    cmd = ["polymarket", "-o", "json", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout)


def write_log(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=True) + "\n")


RUNTIME_CONFIG_KEYS = {
    "query",
    "limit",
    "crypto_terms",
    "min_liquidity_usd",
    "min_volume_usd",
    "require_accepting_orders",
    "require_orderbook",
    "bankroll_usd",
    "kelly_fraction",
    "max_paper_order_usd",
    "live_order_usd",
    "threshold",
    "fee_bps",
    "slippage_bps",
    "gas_usd",
    "paper_only",
    "execute_live",
    "confirm_live",
    "min_persist_runs",
    "signal_cooldown_runs",
    "min_improvement_bps",
}


def _coerce_like(value: Any, sample: Any) -> Any:
    if isinstance(sample, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(sample, int) and not isinstance(sample, bool):
        return int(value)
    if isinstance(sample, float):
        return float(value)
    if isinstance(sample, Path):
        return Path(value)
    return value


def apply_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    path: Path | None = getattr(args, "config_file", None)
    if not path:
        return {}
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
    except Exception:
        return {}

    applied: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in RUNTIME_CONFIG_KEYS:
            continue
        if not hasattr(args, k):
            continue
        current = getattr(args, k)
        try:
            coerced = _coerce_like(v, current)
        except Exception:
            continue
        setattr(args, k, coerced)
        applied[k] = coerced
    return applied


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            out = json.loads(value)
            return out if isinstance(out, list) else []
        except json.JSONDecodeError:
            return []
    return []


def to_decimal_token_id(hex_token: str) -> str:
    return str(int(hex_token, 16))


def chunked(seq: list[str], size: int) -> list[list[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "spread-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class RefMarket:
    symbol: str
    spot: float
    perp: float
    basis_annual: float
    sigma_annual: float
    spot_source: str
    perp_source: str
    vol_source: str


def get_kraken_spot(symbol: str = "BTCUSD") -> float:
    obj = fetch_json("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")
    pair = next(iter(obj["result"]))
    return float(obj["result"][pair]["c"][0])


def get_bybit_perp(symbol: str = "BTCUSDT") -> float:
    obj = fetch_json(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")
    return float(obj["result"]["list"][0]["lastPrice"])


def get_kraken_sigma_annual() -> float:
    obj = fetch_json("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60")
    pair = [k for k in obj["result"] if k != "last"][0]
    rows = obj["result"][pair]
    closes = [float(r[4]) for r in rows[-240:]]
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    sigma_hour = pstdev(rets) if rets else 0.0
    return max(sigma_hour * math.sqrt(24.0 * 365.0), 0.05)


def get_reference_market() -> RefMarket:
    spot_px = get_kraken_spot("BTCUSD")
    spot_source = "kraken_spot"

    try:
        perp_px = get_bybit_perp("BTCUSDT")
        perp_source = "bybit_linear"
    except Exception:
        # Fallback keeps monitor alive in paper mode when perp source is blocked.
        perp_px = spot_px
        perp_source = "fallback_equals_spot"

    sigma_annual = get_kraken_sigma_annual()
    vol_source = "kraken_ohlc_1h"
    basis_annual = ((perp_px / spot_px) - 1.0) * 365.0

    return RefMarket(
        symbol="BTCUSD/BTCUSDT_PERP",
        spot=spot_px,
        perp=perp_px,
        basis_annual=basis_annual,
        sigma_annual=sigma_annual,
        spot_source=spot_source,
        perp_source=perp_source,
        vol_source=vol_source,
    )


def parse_binary_price_target(question: str) -> tuple[float, str] | None:
    # Example support:
    # "Will Bitcoin be above $120,000 on Dec 31, 2026?"
    # "Will BTC hit $1m before GTA VI?" -> this is not a fixed-date strike, skip.
    if "before" in question.lower():
        return None
    q = question.lower()
    m = re.search(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)([mk]?)", q)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    suffix = m.group(2)
    if suffix == "m":
        num *= 1_000_000.0
    elif suffix == "k":
        num *= 1_000.0
    below_markers = ("below", "under", "dip", "drop", "fall", "at or below", "to or below")
    if any(marker in q for marker in below_markers):
        return num, "below_or_hit"
    return num, "above_or_hit"


def time_to_expiry_years(end_date_iso: str) -> float:
    try:
        dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        secs = max((dt - now).total_seconds(), 60.0)
        return secs / (365.0 * 24.0 * 3600.0)
    except Exception:
        return 0.05


def probability_price_above(ref: RefMarket, strike: float, t_years: float) -> float:
    s = max(ref.spot, 1e-9)
    sigma = max(ref.sigma_annual, 1e-6)
    mu = ref.basis_annual
    z = (math.log(s / strike) + (mu - 0.5 * sigma * sigma) * t_years) / (sigma * math.sqrt(t_years))
    return min(max(norm_cdf(z), 0.0), 1.0)


def build_midpoint_map(markets: list[dict[str, Any]]) -> dict[str, float]:
    all_tokens: list[str] = []
    for m in markets:
        tokens = parse_json_list(m.get("clobTokenIds"))
        if len(tokens) >= 2:
            all_tokens.extend(tokens[:2])

    out: dict[str, float] = {}
    for batch in chunked(all_tokens, 100):
        if not batch:
            continue
        mids = run_polymarket(["clob", "midpoints", ",".join(batch)])
        if not isinstance(mids, dict):
            continue
        for k, v in mids.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def evaluate_market(
    m: dict[str, Any],
    ref: RefMarket,
    midpoint_map: dict[str, float],
    fee_frac: float,
    bankroll_usd: float,
    kelly_fraction: float,
    max_paper_order_usd: float,
    live_order_usd: float,
    execute_live: bool,
    slippage_bps: float,
    gas_usd: float,
) -> dict[str, Any] | None:
    parsed = parse_binary_price_target(str(m.get("question", "")))
    if not parsed:
        return None
    strike, _kind = parsed
    tokens = parse_json_list(m.get("clobTokenIds"))
    if len(tokens) < 2:
        return None
    yes_token, no_token = tokens[0], tokens[1]
    yes_mid = midpoint_map.get(to_decimal_token_id(yes_token))
    no_mid = midpoint_map.get(to_decimal_token_id(no_token))
    if yes_mid is None or no_mid is None:
        return None

    t_years = time_to_expiry_years(str(m.get("endDate") or ""))
    prob_above = probability_price_above(ref, strike, t_years)
    if _kind == "below_or_hit":
        model_yes = 1.0 - prob_above
    else:
        model_yes = prob_above
    model_no = 1.0 - model_yes

    # Pick side with positive model edge.
    if model_yes > yes_mid:
        side = "buy_yes"
        token = yes_token
        market_px = yes_mid
        model_px = model_yes
        gross_edge = model_yes - yes_mid
    else:
        side = "buy_no"
        token = no_token
        market_px = no_mid
        model_px = model_no
        gross_edge = model_no - no_mid

    # Kelly sizing for binary market priced in [0,1].
    # f* for buying a YES-like claim at price q with model probability p:
    # f* = (p - q) / (1 - q), clamped to [0,1].
    q = min(max(market_px, 1e-6), 0.999999)
    p = min(max(model_px, 0.0), 1.0)
    kelly_full = max(0.0, min(1.0, (p - q) / max(1.0 - q, 1e-6)))
    kelly_scaled = max(0.0, min(1.0, kelly_fraction * kelly_full))
    suggested_order_usd = bankroll_usd * kelly_scaled
    if execute_live:
        order_usd = min(live_order_usd, suggested_order_usd)
    else:
        order_usd = min(max_paper_order_usd, suggested_order_usd)

    gas_frac = max(gas_usd / max(order_usd, 0.01), 0.0)
    slip = slippage_bps / 10000.0
    total_cost = fee_frac + gas_frac + slip
    net_edge = gross_edge - total_cost

    return {
        "ts": now_iso(),
        "question": m.get("question"),
        "slug": m.get("slug"),
        "market_id": m.get("id"),
        "strike": strike,
        "t_years": t_years,
        "yes_mid": yes_mid,
        "no_mid": no_mid,
        "sum_mid": yes_mid + no_mid,
        "model_yes": model_yes,
        "model_no": model_no,
        "choice": side,
        "token": token,
        "gross_edge": gross_edge,
        "fee_frac": fee_frac,
        "slippage_frac": slip,
        "gas_frac": gas_frac,
        "total_cost_frac": total_cost,
        "net_edge": net_edge,
        "order_usd": order_usd,
        "kelly_full_fraction": kelly_full,
        "kelly_scaled_fraction": kelly_scaled,
        "suggested_order_usd": suggested_order_usd,
        "reference": {
            "symbol": ref.symbol,
            "spot": ref.spot,
            "perp": ref.perp,
            "basis_annual": ref.basis_annual,
            "sigma_annual": ref.sigma_annual,
        },
    }


def maybe_execute(
    opp: dict[str, Any],
    threshold: float,
    execute_live: bool,
    confirm_live: bool,
    max_live_order_usd: float,
    run_id: str,
    run_seq: int,
    gate_state: dict[str, dict[str, float]],
    min_persist_runs: int,
    signal_cooldown_runs: int,
    min_improvement_bps: float,
    log_path: Path,
) -> None:
    slug = str(opp.get("slug") or "")
    net_edge = float(opp.get("net_edge", 0.0) or 0.0)
    persist_runs = int(opp.get("persist_runs", 0) or 0)
    decision = {
        "ts": now_iso(),
        "action": "decision",
        "run_id": run_id,
        "run_seq": run_seq,
        "slug": slug,
        "choice": opp["choice"],
        "net_edge": net_edge,
        "threshold": threshold,
        "persist_runs": persist_runs,
        "execute_live": execute_live,
    }
    write_log(log_path, decision)

    if net_edge < threshold:
        return

    if persist_runs < min_persist_runs:
        write_log(
            log_path,
            {
                "ts": now_iso(),
                "action": "signal_blocked_persistence",
                "run_id": run_id,
                "run_seq": run_seq,
                "slug": slug,
                "persist_runs": persist_runs,
                "required_runs": min_persist_runs,
                "net_edge": net_edge,
            },
        )
        return

    last_run = int(gate_state["last_signal_run"].get(slug, -10**9))
    last_edge = float(gate_state["last_signal_edge"].get(slug, -1e9))
    runs_since = run_seq - last_run
    improvement_bps = (net_edge - last_edge) * 10000.0 if last_edge > -1e8 else 10**9
    if runs_since < signal_cooldown_runs and improvement_bps < min_improvement_bps:
        write_log(
            log_path,
            {
                "ts": now_iso(),
                "action": "signal_blocked_cooldown",
                "run_id": run_id,
                "run_seq": run_seq,
                "slug": slug,
                "runs_since_last_signal": runs_since,
                "cooldown_runs": signal_cooldown_runs,
                "improvement_bps": improvement_bps,
                "required_improvement_bps": min_improvement_bps,
                "net_edge": net_edge,
                "last_signal_net_edge": last_edge,
            },
        )
        return

    if not execute_live:
        write_log(log_path, {"ts": now_iso(), "action": "paper_trade_signal", "run_id": run_id, "run_seq": run_seq, "payload": opp})
        gate_state["last_signal_run"][slug] = float(run_seq)
        gate_state["last_signal_edge"][slug] = net_edge
        return

    if not confirm_live:
        write_log(log_path, {"ts": now_iso(), "action": "blocked_missing_confirm_live", "run_id": run_id, "run_seq": run_seq, "slug": slug})
        return

    # Keep strict cap for live execution only.
    if opp["order_usd"] > max_live_order_usd:
        write_log(
            log_path,
            {"ts": now_iso(), "action": "blocked_live_over_cap", "run_id": run_id, "run_seq": run_seq, "slug": slug, "order_usd": opp["order_usd"]},
        )
        return

    side = "buy"
    token = opp["token"]
    amount = str(round(float(opp["order_usd"]), 2))
    out = run_polymarket(["clob", "market-order", "--token", token, "--side", side, "--amount", amount])
    write_log(log_path, {"ts": now_iso(), "action": "live_order_submitted", "run_id": run_id, "run_seq": run_seq, "slug": slug, "response": out})
    gate_state["last_signal_run"][slug] = float(run_seq)
    gate_state["last_signal_edge"][slug] = net_edge


def run_scan(
    args: argparse.Namespace,
    runtime_overrides: dict[str, Any] | None = None,
    gate_state: dict[str, dict[str, float]] | None = None,
    run_seq: int = 0,
) -> dict[str, Any]:
    run_id = uuid4().hex[:12]
    if gate_state is None:
        gate_state = {"streak_by_slug": {}, "last_signal_run": {}, "last_signal_edge": {}}
    markets = run_polymarket(["markets", "search", args.query, "--limit", str(args.limit)])
    if not isinstance(markets, list):
        raise SystemExit("Unexpected markets response")
    crypto_terms = tuple(t.strip().lower() for t in args.crypto_terms.split(",") if t.strip())
    filtered: list[dict[str, Any]] = []
    for m in markets:
        if not m.get("active") or m.get("closed"):
            continue
        if args.require_accepting_orders and not m.get("acceptingOrders", False):
            continue
        if args.require_orderbook and not m.get("enableOrderBook", False):
            continue
        liq = float(m.get("liquidityNum") or 0.0)
        vol = float(m.get("volumeNum") or 0.0)
        if liq < args.min_liquidity_usd or vol < args.min_volume_usd:
            continue
        text_blob = " ".join(
            [
                str(m.get("question", "")),
                str(m.get("slug", "")),
                str(m.get("category", "")),
                str(m.get("subcategory", "")),
            ]
        ).lower()
        if crypto_terms and not any(term in text_blob for term in crypto_terms):
            continue
        filtered.append(m)
    markets = filtered
    midpoint_map = build_midpoint_map(markets)
    fee_frac = max(args.fee_bps, 0.0) / 10000.0

    ref = get_reference_market()
    write_log(
        args.log_file,
        {
            "ts": now_iso(),
            "action": "run_start",
            "run_id": run_id,
            "query": args.query,
            "active_markets": len(markets),
            "threshold": args.threshold,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "gas_usd": args.gas_usd,
            "bankroll_usd": args.bankroll_usd,
            "kelly_fraction": args.kelly_fraction,
            "max_paper_order_usd": args.max_paper_order_usd,
            "live_order_usd": args.live_order_usd,
            "min_persist_runs": args.min_persist_runs,
            "signal_cooldown_runs": args.signal_cooldown_runs,
            "min_improvement_bps": args.min_improvement_bps,
            "execute_live": args.execute_live,
            "config_file": str(args.config_file),
            "runtime_overrides": runtime_overrides or {},
            "reference": ref.__dict__,
        },
    )

    opportunities: list[dict[str, Any]] = []
    for m in markets:
        try:
            ev = evaluate_market(
                m,
                ref,
                midpoint_map,
                fee_frac,
                args.bankroll_usd,
                args.kelly_fraction,
                args.max_paper_order_usd,
                args.live_order_usd,
                args.execute_live,
                args.slippage_bps,
                args.gas_usd,
            )
            if ev:
                opportunities.append(ev)
                write_log(
                    args.log_file,
                    {"ts": now_iso(), "action": "evaluation", "run_id": run_id, "payload": ev},
                )
        except Exception as exc:  # noqa: BLE001
            write_log(
                args.log_file,
                {
                    "ts": now_iso(),
                    "action": "market_error",
                    "run_id": run_id,
                    "slug": m.get("slug"),
                    "error": str(exc),
                },
            )

    opportunities.sort(key=lambda x: float(x["net_edge"]), reverse=True)
    pass_slugs = {str(o.get("slug") or "") for o in opportunities if float(o.get("net_edge", 0.0) or 0.0) >= args.threshold}
    streak = gate_state["streak_by_slug"]
    for slug in list(streak.keys()):
        if slug not in pass_slugs:
            streak[slug] = 0.0
    for o in opportunities:
        slug = str(o.get("slug") or "")
        if not slug:
            o["persist_runs"] = 0
            continue
        if slug in pass_slugs:
            streak[slug] = float(streak.get(slug, 0.0) + 1.0)
        else:
            streak[slug] = 0.0
        o["persist_runs"] = int(streak[slug])

    top = opportunities[:5]
    for opp in top:
        maybe_execute(
            opp=opp,
            threshold=args.threshold,
            execute_live=args.execute_live,
            confirm_live=args.confirm_live,
            max_live_order_usd=args.live_order_usd,
            run_id=run_id,
            run_seq=run_seq,
            gate_state=gate_state,
            min_persist_runs=args.min_persist_runs,
            signal_cooldown_runs=args.signal_cooldown_runs,
            min_improvement_bps=args.min_improvement_bps,
            log_path=args.log_file,
        )
    summary = {
        "timestamp": now_iso(),
        "run_id": run_id,
        "active_markets_scanned": len(markets),
        "price_target_markets_evaluated": len(opportunities),
        "candidates_over_threshold": sum(1 for o in opportunities if o["net_edge"] >= args.threshold),
        "top_candidates": [
            {
                "slug": o["slug"],
                "choice": o["choice"],
                "net_edge_pct": round(o["net_edge"] * 100.0, 3),
                "gross_edge_pct": round(o["gross_edge"] * 100.0, 3),
                "cost_pct": round(o["total_cost_frac"] * 100.0, 3),
                "order_usd": round(o["order_usd"], 2),
                "kelly_scaled_pct_bankroll": round(o["kelly_scaled_fraction"] * 100.0, 3),
                "persist_runs": int(o.get("persist_runs", 0)),
            }
            for o in top
        ],
        "log_file": str(args.log_file.resolve()),
        "mode": "live" if args.execute_live else "paper",
    }
    write_log(args.log_file, {"ts": now_iso(), "action": "run_summary", "run_id": run_id, "payload": summary})
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Spot/perp vs Polymarket spread monitor")
    p.add_argument("--query", default="bitcoin", help="Topic query for markets search")
    p.add_argument("--limit", type=int, default=50, help="How many markets to scan")
    p.add_argument(
        "--crypto-terms",
        default="bitcoin,btc,ethereum,eth,solana,sol,crypto,binance,coinbase",
        help="Comma-separated terms used to keep scan crypto-focused",
    )
    p.add_argument("--min-liquidity-usd", type=float, default=50000.0, help="Minimum market liquidity to scan")
    p.add_argument("--min-volume-usd", type=float, default=100000.0, help="Minimum market volume to scan")
    p.add_argument(
        "--require-accepting-orders",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Only include markets currently accepting orders",
    )
    p.add_argument(
        "--require-orderbook",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Only include markets with CLOB orderbook enabled",
    )
    p.add_argument("--bankroll-usd", type=float, default=1000.0, help="Paper bankroll for Kelly sizing")
    p.add_argument("--kelly-fraction", type=float, default=0.25, help="Fractional Kelly multiplier (0..1)")
    p.add_argument("--max-paper-order-usd", type=float, default=100.0, help="Paper-mode cap per order")
    p.add_argument("--live-order-usd", type=float, default=5.0, help="Live mode cap and max submitted amount")
    p.add_argument("--threshold", type=float, default=0.012, help="Minimum net edge to act, e.g. 0.012 = 1.2%")
    p.add_argument("--fee-bps", type=float, default=0.0, help="Assumed CLOB fee in bps for net-edge calc")
    p.add_argument("--slippage-bps", type=float, default=20.0, help="Estimated slippage in basis points")
    p.add_argument("--gas-usd", type=float, default=0.02, help="Estimated gas/network cost in USD per order")
    p.add_argument("--log-file", type=Path, default=Path("spread_monitor.log.jsonl"), help="JSONL log path")
    p.add_argument(
        "--config-file",
        type=Path,
        default=Path("strategy.runtime.json"),
        help="Optional runtime JSON config (reloaded every loop)",
    )
    p.add_argument("--execute-live", action="store_true", help="Submit live orders when threshold passes")
    p.add_argument("--confirm-live", action="store_true", help="Second safety switch required with --execute-live")
    p.add_argument("--loop-secs", type=int, default=0, help="If >0, run continuously every N seconds")
    p.add_argument("--max-runs", type=int, default=0, help="Optional stop after N runs when looping")
    p.add_argument("--paper-only", action="store_true", help="Force paper mode even if live flags are set")
    p.add_argument("--min-persist-runs", type=int, default=2, help="Require N consecutive runs above threshold")
    p.add_argument("--signal-cooldown-runs", type=int, default=5, help="Cooldown runs between same-slug signals")
    p.add_argument(
        "--min-improvement-bps",
        type=float,
        default=25.0,
        help="Within cooldown, require this net-edge improvement in bps to re-signal",
    )
    return p


def validate_args(args: argparse.Namespace) -> None:
    if args.live_order_usd > 5.0:
        raise SystemExit("Safety guard: --live-order-usd cannot exceed 5.0.")
    if not (0.0 <= args.kelly_fraction <= 1.0):
        raise SystemExit("Expected --kelly-fraction between 0 and 1.")
    if args.paper_only and args.execute_live:
        raise SystemExit("paper-only mode blocks --execute-live.")
    if args.min_persist_runs < 1:
        raise SystemExit("min-persist-runs must be >= 1.")
    if args.signal_cooldown_runs < 0:
        raise SystemExit("signal-cooldown-runs must be >= 0.")
    if args.min_improvement_bps < 0:
        raise SystemExit("min-improvement-bps must be >= 0.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(args)

    runs = 0
    gate_state: dict[str, dict[str, float]] = {
        "streak_by_slug": {},
        "last_signal_run": {},
        "last_signal_edge": {},
    }
    while True:
        try:
            runtime_overrides = apply_runtime_config(args)
            if args.paper_only:
                args.execute_live = False
                args.confirm_live = False
            validate_args(args)
            summary = run_scan(args, runtime_overrides=runtime_overrides, gate_state=gate_state, run_seq=runs + 1)
            print(json.dumps(summary, ensure_ascii=True))
        except Exception as exc:  # noqa: BLE001
            err = {"ts": now_iso(), "action": "run_error", "error": str(exc)}
            write_log(args.log_file, err)
            print(json.dumps(err, ensure_ascii=True))

        runs += 1
        if args.loop_secs <= 0:
            break
        if args.max_runs > 0 and runs >= args.max_runs:
            break
        time.sleep(args.loop_secs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
