from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .types import utc_now_iso


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    cols = _table_columns(conn, table)
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def init_db(conn: sqlite3.Connection, bankroll: float) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            starting_bankroll REAL NOT NULL,
            cash REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            model TEXT NOT NULL,
            sizer TEXT NOT NULL,
            experiment_tag TEXT,
            query TEXT,
            markets_scanned INTEGER NOT NULL,
            opportunities INTEGER NOT NULL,
            signals INTEGER NOT NULL,
            params_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            opened_at TEXT NOT NULL,
            market_id TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            question TEXT NOT NULL,
            side TEXT NOT NULL,
            token_id TEXT NOT NULL,
            entry_price REAL NOT NULL,
            shares REAL NOT NULL,
            notional_usd REAL NOT NULL,
            model_price REAL NOT NULL,
            edge REAL NOT NULL,
            confidence REAL NOT NULL,
            status TEXT NOT NULL,
            closed_at TEXT,
            exit_price REAL,
            realized_pnl REAL,
            notes_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    _ensure_column(conn, "runs", "experiment_tag", "TEXT")
    now = utc_now_iso()
    cur = conn.execute("SELECT id FROM accounts WHERE id = 1").fetchone()
    if cur is None:
        conn.execute(
            "INSERT INTO accounts(id, starting_bankroll, cash, created_at, updated_at) VALUES(1, ?, ?, ?, ?)",
            (bankroll, bankroll, now, now),
        )
    conn.commit()


def get_account(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM accounts WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("Account not initialized. Run `polytrader init`.")
    return dict(row)


def update_cash(conn: sqlite3.Connection, cash: float) -> None:
    conn.execute("UPDATE accounts SET cash = ?, updated_at = ? WHERE id = 1", (cash, utc_now_iso()))
    conn.commit()


def insert_run(
    conn: sqlite3.Connection,
    model: str,
    sizer: str,
    experiment_tag: str | None,
    query: str,
    markets_scanned: int,
    opportunities: int,
    signals: int,
    params: dict[str, Any],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs(ts, model, sizer, experiment_tag, query, markets_scanned, opportunities, signals, params_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_iso(),
            model,
            sizer,
            experiment_tag,
            query,
            markets_scanned,
            opportunities,
            signals,
            json.dumps(params, ensure_ascii=True),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_trade(
    conn: sqlite3.Connection,
    run_id: int,
    market_id: str,
    market_slug: str,
    question: str,
    side: str,
    token_id: str,
    entry_price: float,
    shares: float,
    notional_usd: float,
    model_price: float,
    edge: float,
    confidence: float,
    notes: dict[str, Any],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO trades(
            run_id, opened_at, market_id, market_slug, question, side, token_id, entry_price,
            shares, notional_usd, model_price, edge, confidence, status, notes_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            run_id,
            utc_now_iso(),
            market_id,
            market_slug,
            question,
            side,
            token_id,
            entry_price,
            shares,
            notional_usd,
            model_price,
            edge,
            confidence,
            json.dumps(notes, ensure_ascii=True),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_open_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY opened_at DESC").fetchall()
    return [dict(r) for r in rows]


def list_closed_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM trades WHERE status = 'closed' ORDER BY closed_at DESC").fetchall()
    return [dict(r) for r in rows]


def list_trades_window(conn: sqlite3.Connection, start_ts: str | None, end_ts: str | None) -> list[dict[str, Any]]:
    query = "SELECT * FROM trades"
    params: list[Any] = []
    clauses: list[str] = []
    if start_ts:
        clauses.append("opened_at >= ?")
        params.append(start_ts)
    if end_ts:
        clauses.append("opened_at <= ?")
        params.append(end_ts)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY opened_at ASC"
    rows = conn.execute(query, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    _ensure_column(conn, "runs", "experiment_tag", "TEXT")
    rows = conn.execute(
        """
        SELECT id, ts, model, sizer, experiment_tag, query, markets_scanned, opportunities, signals, params_json
        FROM runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["params_json"] = json.loads(d.get("params_json") or "{}")
        except Exception:
            pass
        out.append(d)
    return out


def close_market_positions(conn: sqlite3.Connection, slug: str, outcome_yes: bool) -> list[dict[str, Any]]:
    open_rows = conn.execute(
        "SELECT * FROM trades WHERE status = 'open' AND market_slug = ? ORDER BY opened_at ASC", (slug,)
    ).fetchall()
    closed: list[dict[str, Any]] = []
    account = get_account(conn)
    cash = float(account["cash"])
    for row in open_rows:
        trade = dict(row)
        shares = float(trade["shares"])
        notional = float(trade["notional_usd"])
        side = str(trade["side"])
        if side == "buy_yes":
            payout_per_share = 1.0 if outcome_yes else 0.0
        elif side == "buy_no":
            payout_per_share = 0.0 if outcome_yes else 1.0
        else:
            payout_per_share = 0.0
        payout = shares * payout_per_share
        pnl = payout - notional
        cash += payout
        conn.execute(
            """
            UPDATE trades
            SET status='closed', closed_at=?, exit_price=?, realized_pnl=?
            WHERE id=?
            """,
            (utc_now_iso(), payout_per_share, pnl, int(trade["id"])),
        )
        trade["exit_price"] = payout_per_share
        trade["realized_pnl"] = pnl
        closed.append(trade)
    update_cash(conn, cash)
    conn.commit()
    return closed
