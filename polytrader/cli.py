from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import typer

from . import SCHEMA_VERSION
from . import db as db_mod
from .engine import mark_open_positions, scan_once
from .market_data import fetch_markets
from .registry import BUILTIN_MODELS, BUILTIN_SIZERS, make_model, make_sizer
from .specs import TournamentSpec
from .types import utc_now_iso

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Agent-first Polymarket paper trading framework.")

MODEL_TEMPLATE = """from __future__ import annotations

from polytrader.models.base import BaseModel
from polytrader.types import MarketSnapshot, Signal


class {class_name}(BaseModel):
    name = "{model_name}"

    def __init__(self, edge_threshold: float = 0.01):
        self.edge_threshold = edge_threshold

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        # Example placeholder logic:
        # Trade the cheaper side when YES+NO midpoint is below 1.0 by threshold.
        total = market.yes_mid + market.no_mid
        if (1.0 - total) < self.edge_threshold:
            return None
        if market.yes_mid <= market.no_mid:
            side = "buy_yes"
            token_id = market.yes_token_id
            market_px = market.yes_mid
            model_px = min(max(market.yes_mid + (1.0 - total), 0.0), 1.0)
        else:
            side = "buy_no"
            token_id = market.no_token_id
            market_px = market.no_mid
            model_px = min(max(market.no_mid + (1.0 - total), 0.0), 1.0)
        edge = model_px - market_px
        if edge <= 0:
            return None
        return Signal(
            model=self.name,
            side=side,
            token_id=token_id,
            market_price=market_px,
            model_price=model_px,
            edge=edge,
            confidence=min(max(edge / 0.1, 0.0), 1.0),
            metadata={{"total_mid": total}},
        )
"""

SIZER_TEMPLATE = """from __future__ import annotations

from polytrader.sizers.base import BaseSizer
from polytrader.types import Signal, SizedOrder


class {class_name}(BaseSizer):
    name = "{sizer_name}"

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
            metadata={{"risk_fraction": self.risk_fraction}},
        )
"""

VARIABLE_CATALOG: dict[str, Any] = {
    "scan": {
        "model": {"type": "str", "default": "kelly_gbm"},
        "sizer": {"type": "str", "default": "kelly"},
        "query": {"type": "str", "default": "bitcoin"},
        "limit": {"type": "int", "default": 50},
        "min_liquidity": {"type": "float", "default": 50000.0},
        "min_volume": {"type": "float", "default": 100000.0},
        "experiment_tag": {"type": "str", "default": ""},
    },
    "model_config": {
        "kelly_gbm": {"min_edge": {"type": "float", "default": 0.002}},
        "always_pass": {},
    },
    "sizer_config": {
        "kelly": {"fraction": {"type": "float", "default": 0.25}, "max_usd": {"type": "float", "default": 250.0}},
        "fixed": {"usd": {"type": "float", "default": 25.0}},
        "equal_weight": {"slots": {"type": "int", "default": 10}},
    },
    "plugin_format": {"model": "module.path:ClassName", "sizer": "module.path:ClassName"},
}


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=True))
    else:
        typer.echo(json.dumps(payload, ensure_ascii=True, indent=2))


def fail(message: str, as_json: bool) -> None:
    emit({"schema_version": SCHEMA_VERSION, "ok": False, "error": message}, as_json=as_json)
    raise typer.Exit(code=1)


def _set_nested(root: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    node = root
    for p in parts[:-1]:
        if p not in node or not isinstance(node[p], dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value


def _get_nested(root: dict[str, Any], dotted_path: str) -> Any:
    node: Any = root
    for p in dotted_path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node


def _experiment_metrics(dbf: Path) -> dict[str, Any]:
    conn = db_mod.connect(dbf)
    acct = db_mod.get_account(conn)
    closed = db_mod.list_closed_trades(conn)
    open_pos = mark_open_positions(conn)
    runs = db_mod.list_runs(conn, limit=500)
    realized = sum(float(r.get("realized_pnl") or 0.0) for r in closed)
    unrealized = sum(float(p.get("unrealized_pnl") or 0.0) for p in open_pos)
    wins = sum(1 for r in closed if float(r.get("realized_pnl") or 0.0) > 0)
    win_rate = (wins / len(closed)) if closed else 0.0
    signal_count = int(sum(int(r.get("signals") or 0) for r in runs))
    latest_tag = runs[0].get("experiment_tag") if runs else None
    score = realized + (0.5 * unrealized) + (100.0 * win_rate) + (0.1 * signal_count)
    return {
        "db": str(dbf.resolve()),
        "latest_experiment_tag": latest_tag,
        "cash": float(acct["cash"]),
        "starting_bankroll": float(acct["starting_bankroll"]),
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "closed_trades": len(closed),
        "open_positions": len(open_pos),
        "win_rate": win_rate,
        "signal_count": signal_count,
        "equity": float(acct["cash"]) + unrealized,
        "score": score,
    }


def _pareto_front(items: list[dict[str, Any]], metrics: list[str]) -> list[dict[str, Any]]:
    if not items:
        return []
    keep = []
    for i, a in enumerate(items):
        dominated = False
        for j, b in enumerate(items):
            if i == j:
                continue
            ge_all = all(float(b.get(m, 0.0)) >= float(a.get(m, 0.0)) for m in metrics)
            gt_any = any(float(b.get(m, 0.0)) > float(a.get(m, 0.0)) for m in metrics)
            if ge_all and gt_any:
                dominated = True
                break
        if not dominated:
            keep.append(a)
    return keep


@app.command()
def init(
    bankroll: float = typer.Option(10000.0, help="Starting paper bankroll"),
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        conn = db_mod.connect(db)
        db_mod.init_db(conn, bankroll=bankroll)
        acct = db_mod.get_account(conn)
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "timestamp": utc_now_iso(),
                "command": "init",
                "db": str(db.resolve()),
                "account": acct,
            },
            as_json=json_output,
        )
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def models(json_output: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    emit(
        {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "models": sorted(BUILTIN_MODELS.keys()),
            "sizers": sorted(BUILTIN_SIZERS.keys()),
            "plugin_format": "module.path:ClassName",
        },
        as_json=json_output,
    )


@app.command("vars")
def vars_cmd(json_output: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    emit({"schema_version": SCHEMA_VERSION, "ok": True, "variables": VARIABLE_CATALOG}, as_json=json_output)


@app.command()
def markets(
    query: str = typer.Option("", help="Search query"),
    limit: int = typer.Option(20, help="Max markets"),
    min_liquidity: float = typer.Option(0.0, help="Min liquidity"),
    min_volume: float = typer.Option(0.0, help="Min volume"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        rows = fetch_markets(query=query, limit=limit)
        out = []
        for m in rows:
            liq = float(m.get("liquidityNum") or 0.0)
            vol = float(m.get("volumeNum") or 0.0)
            if liq < min_liquidity or vol < min_volume:
                continue
            out.append(
                {
                    "id": m.get("id"),
                    "slug": m.get("slug"),
                    "question": m.get("question"),
                    "endDate": m.get("endDate"),
                    "liquidity": liq,
                    "volume": vol,
                    "acceptingOrders": m.get("acceptingOrders"),
                    "enableOrderBook": m.get("enableOrderBook"),
                    "clobTokenIds": m.get("clobTokenIds"),
                }
            )
        emit({"schema_version": SCHEMA_VERSION, "ok": True, "count": len(out), "markets": out}, as_json=json_output)
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def scan(
    model: str = typer.Option("kelly_gbm", help="Model name or plugin path"),
    sizer: str = typer.Option("kelly", help="Sizer name or plugin path"),
    query: str = typer.Option("bitcoin", help="Gamma query"),
    limit: int = typer.Option(50, help="Fetch limit"),
    min_liquidity: float = typer.Option(50000.0, help="Min liquidity"),
    min_volume: float = typer.Option(100000.0, help="Min volume"),
    experiment_tag: str = typer.Option("", help="Optional experiment label for run comparisons"),
    model_config: str = typer.Option("{}", help="JSON dict for model kwargs"),
    sizer_config: str = typer.Option("{}", help="JSON dict for sizer kwargs"),
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        conn = db_mod.connect(db)
        model_kwargs = json.loads(model_config)
        sizer_kwargs = json.loads(sizer_config)
        model_obj = make_model(model, kwargs=model_kwargs)
        sizer_obj = make_sizer(sizer, kwargs=sizer_kwargs)
        result = scan_once(
            conn=conn,
            model=model_obj,
            sizer=sizer_obj,
            experiment_tag=experiment_tag.strip() or None,
            query=query,
            limit=limit,
            min_liquidity=min_liquidity,
            min_volume=min_volume,
        )
        result["ok"] = True
        emit(result, as_json=json_output)
        if len(result.get("orders_placed", [])) == 0:
            raise typer.Exit(code=2)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def tournament(
    spec_file: Path = typer.Option(Path("tournament.spec.json"), help="Path to tournament JSON spec"),
    base_dir: Path = typer.Option(Path("experiments"), help="Default directory for per-experiment db files"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        raw = json.loads(spec_file.read_text(encoding="utf-8"))
        spec = TournamentSpec.model_validate(raw)
        if not spec.experiments:
            fail("No experiments provided in spec file.", json_output)
            return
        results: list[dict[str, Any]] = []
        for exp in spec.experiments:
            db_path = Path(exp.db) if exp.db else (base_dir / f"{exp.tag}.sqlite3")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = db_mod.connect(db_path)
            db_mod.init_db(conn, bankroll=exp.init_bankroll)
            model_obj = make_model(exp.scan.model, kwargs=exp.scan.model_kwargs)
            sizer_obj = make_sizer(exp.scan.sizer, kwargs=exp.scan.sizer_kwargs)
            run = scan_once(
                conn=conn,
                model=model_obj,
                sizer=sizer_obj,
                experiment_tag=exp.tag,
                query=exp.scan.query,
                limit=exp.scan.limit,
                min_liquidity=exp.scan.min_liquidity,
                min_volume=exp.scan.min_volume,
            )
            acct = db_mod.get_account(conn)
            positions = mark_open_positions(conn)
            equity = float(acct["cash"]) + sum(float(p["mark_value"]) for p in positions)
            score = float(len(run.get("orders_placed", [])))
            results.append(
                {
                    "tag": exp.tag,
                    "db": str(db_path.resolve()),
                    "run_id": run["run_id"],
                    "markets_scanned": run["markets_scanned"],
                    "opportunities_count": len(run.get("opportunities", [])),
                    "orders_count": len(run.get("orders_placed", [])),
                    "cash_after": float(acct["cash"]),
                    "equity": equity,
                    "score": score,
                }
            )
        leaderboard = sorted(results, key=lambda x: x["score"], reverse=True)
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "spec_file": str(spec_file.resolve()),
                "experiments_run": len(results),
                "leaderboard": leaderboard,
            },
            as_json=json_output,
        )
        if not any(r["orders_count"] > 0 for r in results):
            raise typer.Exit(code=2)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(str(exc), json_output)


@app.command("mutate-spec")
def mutate_spec(
    base_spec_file: Path = typer.Option(Path("tournament.spec.example.json"), help="Base tournament spec file"),
    search_space_file: Path = typer.Option(Path("search_space.json"), help="JSON map: dotted_path -> candidate list"),
    output_file: Path = typer.Option(Path("tournament.mutated.json"), help="Where to write generated spec"),
    max_variants: int = typer.Option(200, help="Maximum generated experiments"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        base_raw = json.loads(base_spec_file.read_text(encoding="utf-8"))
        search_raw = json.loads(search_space_file.read_text(encoding="utf-8"))
        spec = TournamentSpec.model_validate(base_raw)
        if not isinstance(search_raw, dict) or not search_raw:
            fail("search_space_file must be a JSON object with non-empty candidate lists.", json_output)
            return
        keys = []
        values = []
        for k, v in search_raw.items():
            if not isinstance(k, str) or not isinstance(v, list) or len(v) == 0:
                fail("Invalid search space shape. Expected: {\"scan.model_config.min_edge\": [0.001, 0.002]}", json_output)
                return
            keys.append(k)
            values.append(v)

        generated: list[dict[str, Any]] = []
        variant_idx = 0
        for base_exp in spec.experiments:
            base_obj = base_exp.model_dump(by_alias=True)
            for combo in itertools.product(*values):
                variant_idx += 1
                if len(generated) >= max_variants:
                    break
                obj = json.loads(json.dumps(base_obj))
                for i, val in enumerate(combo):
                    _set_nested(obj, keys[i], val)
                obj["tag"] = f"{base_exp.tag}__v{variant_idx:04d}"
                generated.append(obj)
            if len(generated) >= max_variants:
                break

        out_obj = {"experiments": generated}
        output_file.write_text(json.dumps(out_obj, ensure_ascii=True, indent=2), encoding="utf-8")
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "base_spec_file": str(base_spec_file.resolve()),
                "search_space_file": str(search_space_file.resolve()),
                "output_file": str(output_file.resolve()),
                "generated_experiments": len(generated),
                "paths_mutated": keys,
            },
            as_json=json_output,
        )
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def rank(
    experiments_dir: Path = typer.Option(Path("experiments"), help="Directory containing experiment SQLite files"),
    by: str = typer.Option(
        "score",
        help="Ranking metric: score|realized_pnl|unrealized_pnl|equity|win_rate|signal_count|closed_trades|open_positions",
    ),
    pareto: bool = typer.Option(False, help="Return Pareto-optimal rows only"),
    pareto_metrics: str = typer.Option(
        "realized_pnl,unrealized_pnl,win_rate,signal_count",
        help="Comma-separated metrics for Pareto frontier",
    ),
    top_n: int = typer.Option(20, help="Top N rows to return"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        db_files = sorted(experiments_dir.glob("*.sqlite3"))
        if not db_files:
            emit(
                {
                    "schema_version": SCHEMA_VERSION,
                    "ok": True,
                    "experiments_dir": str(experiments_dir.resolve()),
                    "count": 0,
                    "ranked": [],
                },
                as_json=json_output,
            )
            raise typer.Exit(code=2)
        ranked = [_experiment_metrics(dbf) for dbf in db_files]
        valid_metrics = {
            "score",
            "realized_pnl",
            "unrealized_pnl",
            "equity",
            "win_rate",
            "signal_count",
            "closed_trades",
            "open_positions",
        }
        if by not in valid_metrics:
            fail(f"Invalid --by metric '{by}'", json_output)
            return
        if pareto:
            metrics = [m.strip() for m in pareto_metrics.split(",") if m.strip()]
            bad = [m for m in metrics if m not in valid_metrics]
            if bad:
                fail(f"Invalid pareto metric(s): {', '.join(bad)}", json_output)
                return
            ranked = _pareto_front(ranked, metrics=metrics)
        ranked.sort(key=lambda x: float(x.get(by, 0.0)), reverse=True)
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "experiments_dir": str(experiments_dir.resolve()),
                "count": len(ranked),
                "ranked": ranked[:top_n],
                "rank_by": by,
                "pareto": pareto,
                "pareto_metrics": [m.strip() for m in pareto_metrics.split(",") if m.strip()] if pareto else [],
                "scoring_formula": "score = realized_pnl + 0.5*unrealized_pnl + 100*win_rate + 0.1*signal_count",
            },
            as_json=json_output,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def replay(
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    start_ts: str = typer.Option("", help="Filter start timestamp (ISO8601)"),
    end_ts: str = typer.Option("", help="Filter end timestamp (ISO8601)"),
    include_open_marks: bool = typer.Option(True, help="Mark open trades to current midpoint"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        conn = db_mod.connect(db)
        trades = db_mod.list_trades_window(conn, start_ts=start_ts or None, end_ts=end_ts or None)
        marks_by_token = {}
        if include_open_marks:
            for p in mark_open_positions(conn):
                marks_by_token[str(p.get("token_id"))] = float(p.get("mark_price") or 0.0)

        timeline: list[dict[str, Any]] = []
        realized_total = 0.0
        unrealized_total = 0.0
        for t in trades:
            status = str(t.get("status") or "")
            row = {
                "opened_at": t.get("opened_at"),
                "market_slug": t.get("market_slug"),
                "side": t.get("side"),
                "status": status,
                "entry_price": float(t.get("entry_price") or 0.0),
                "shares": float(t.get("shares") or 0.0),
                "notional_usd": float(t.get("notional_usd") or 0.0),
                "realized_pnl": float(t.get("realized_pnl") or 0.0) if status == "closed" else None,
                "unrealized_pnl": None,
            }
            if status == "closed":
                realized_total += float(t.get("realized_pnl") or 0.0)
            elif include_open_marks:
                token = str(t.get("token_id") or "")
                mark = marks_by_token.get(token)
                if mark is not None and mark > 0:
                    shares = float(t.get("shares") or 0.0)
                    notional = float(t.get("notional_usd") or 0.0)
                    unrl = shares * mark - notional
                    row["mark_price"] = mark
                    row["unrealized_pnl"] = unrl
                    unrealized_total += unrl
            timeline.append(row)

        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "db": str(db.resolve()),
                "start_ts": start_ts or None,
                "end_ts": end_ts or None,
                "trades_count": len(trades),
                "realized_pnl_total": realized_total,
                "unrealized_pnl_total": unrealized_total,
                "timeline": timeline,
                "note": "Replay uses recorded paper trades; open-trade marks are current midpoint marks.",
            },
            as_json=json_output,
        )
        if len(trades) == 0:
            raise typer.Exit(code=2)
    except typer.Exit:
        raise
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def runs(
    limit: int = typer.Option(30, help="Max run records"),
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        conn = db_mod.connect(db)
        rows = db_mod.list_runs(conn, limit=limit)
        emit({"schema_version": SCHEMA_VERSION, "ok": True, "count": len(rows), "runs": rows}, as_json=json_output)
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def account(
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        conn = db_mod.connect(db)
        acct = db_mod.get_account(conn)
        positions = mark_open_positions(conn)
        equity = float(acct["cash"]) + sum(float(p["mark_value"]) for p in positions)
        out = {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "cash": float(acct["cash"]),
            "starting_bankroll": float(acct["starting_bankroll"]),
            "open_positions": len(positions),
            "positions_mark_value": sum(float(p["mark_value"]) for p in positions),
            "equity": equity,
            "pnl": equity - float(acct["starting_bankroll"]),
        }
        emit(out, as_json=json_output)
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def positions(
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        conn = db_mod.connect(db)
        rows = mark_open_positions(conn)
        emit({"schema_version": SCHEMA_VERSION, "ok": True, "count": len(rows), "positions": rows}, as_json=json_output)
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def resolve(
    slug: str = typer.Option(..., help="Market slug to settle"),
    outcome: str = typer.Option(..., help="yes | no | 1 | 0"),
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        out = outcome.strip().lower()
        if out in {"yes", "1", "true"}:
            outcome_yes = True
        elif out in {"no", "0", "false"}:
            outcome_yes = False
        else:
            fail("Outcome must be yes/no/1/0", json_output)
            return
        conn = db_mod.connect(db)
        closed = db_mod.close_market_positions(conn, slug=slug, outcome_yes=outcome_yes)
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "slug": slug,
                "outcome_yes": outcome_yes,
                "closed_count": len(closed),
                "closed": closed,
            },
            as_json=json_output,
        )
    except Exception as exc:
        fail(str(exc), json_output)


@app.command()
def history(
    db: Path = typer.Option(Path("polytrader.sqlite3"), "--db", help="SQLite path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        conn = db_mod.connect(db)
        rows = db_mod.list_closed_trades(conn)
        realized = sum(float(r.get("realized_pnl") or 0.0) for r in rows)
        win_count = sum(1 for r in rows if float(r.get("realized_pnl") or 0.0) > 0)
        out = {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "count": len(rows),
            "realized_pnl": realized,
            "win_rate": (win_count / len(rows)) if rows else 0.0,
            "trades": rows,
        }
        emit(out, as_json=json_output)
    except Exception as exc:
        fail(str(exc), json_output)


@app.command("scaffold-model")
def scaffold_model(
    name: str = typer.Option(..., help="Model symbolic name"),
    class_name: str = typer.Option("CustomModel", help="Python class name"),
    output: Path = typer.Option(Path("plugins/custom_model.py"), help="Output path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        init_file = output.parent / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")
        output.write_text(
            MODEL_TEMPLATE.format(class_name=class_name, model_name=name),
            encoding="utf-8",
        )
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "command": "scaffold-model",
                "path": str(output.resolve()),
                "use_with": f"{output.with_suffix('').as_posix().replace('/', '.')}" + f":{class_name}",
            },
            as_json=json_output,
        )
    except Exception as exc:
        fail(str(exc), json_output)


@app.command("scaffold-sizer")
def scaffold_sizer(
    name: str = typer.Option(..., help="Sizer symbolic name"),
    class_name: str = typer.Option("CustomSizer", help="Python class name"),
    output: Path = typer.Option(Path("plugins/custom_sizer.py"), help="Output path"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        init_file = output.parent / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")
        output.write_text(
            SIZER_TEMPLATE.format(class_name=class_name, sizer_name=name),
            encoding="utf-8",
        )
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "command": "scaffold-sizer",
                "path": str(output.resolve()),
                "use_with": f"{output.with_suffix('').as_posix().replace('/', '.')}" + f":{class_name}",
            },
            as_json=json_output,
        )
    except Exception as exc:
        fail(str(exc), json_output)
