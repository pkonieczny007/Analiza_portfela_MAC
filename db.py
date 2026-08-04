"""SQLite (stdlib sqlite3) — schemat i pomocniki."""

from __future__ import annotations

import sqlite3
import time

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tx (
    id INTEGER PRIMARY KEY,
    signature TEXT UNIQUE,
    block_time INTEGER NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
    token TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    quote_amount REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'chain',
    group_id INTEGER REFERENCES grp(id) ON DELETE SET NULL,
    note TEXT,
    hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS match (
    id INTEGER PRIMARY KEY,
    buy_id INTEGER NOT NULL REFERENCES tx(id) ON DELETE CASCADE,
    sell_id INTEGER NOT NULL REFERENCES tx(id) ON DELETE CASCADE,
    qty REAL NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS grp (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS wallet (
    id INTEGER PRIMARY KEY,
    address TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS multi_order (
    id INTEGER PRIMARY KEY,
    created_at INTEGER NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy','sell')),
    token TEXT NOT NULL,
    key_file TEXT NOT NULL,
    total_amount REAL NOT NULL,
    amount_unit TEXT NOT NULL,
    num_slices INTEGER NOT NULL,
    price_min REAL,
    price_max REAL,
    trigger_mode TEXT NOT NULL DEFAULT 'time_price',
    window_start INTEGER NOT NULL,
    window_end INTEGER NOT NULL,
    slippage_bps INTEGER NOT NULL DEFAULT 300,
    dry_run INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'running',
    note TEXT,
    hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS multi_slice (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES multi_order(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    amount REAL NOT NULL,
    scheduled_at INTEGER NOT NULL,
    price_offset_pct REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    executed_price REAL,
    tx_signature TEXT,
    filled_at INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_slice_order ON multi_slice(order_id);
CREATE INDEX IF NOT EXISTS idx_tx_token_time ON tx(token, block_time);
CREATE INDEX IF NOT EXISTS idx_match_buy ON match(buy_id);
CREATE INDEX IF NOT EXISTS idx_match_sell ON match(sell_id);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        # migracja: kolumna tx.wallet_id + portfel domyslny z config
        cols = [r["name"] for r in con.execute("PRAGMA table_info(tx)")]
        if "wallet_id" not in cols:
            con.execute("ALTER TABLE tx ADD COLUMN wallet_id INTEGER REFERENCES wallet(id)")
        if not con.execute("SELECT 1 FROM wallet LIMIT 1").fetchone():
            con.execute(
                "INSERT INTO wallet(address, name, grp, sort) VALUES (?,?,?,0)",
                (config.WALLET, "MAC", ""),
            )
        default_id = con.execute("SELECT id FROM wallet ORDER BY sort, id").fetchone()["id"]
        con.execute("UPDATE tx SET wallet_id=? WHERE wallet_id IS NULL", (default_id,))
        con.commit()


def selected_wallet_ids(con: sqlite3.Connection) -> list[int]:
    return [r["id"] for r in con.execute("SELECT id FROM wallet WHERE selected=1")]


def meta_get(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def insert_tx(con: sqlite3.Connection, *, signature: str | None, block_time: int,
              side: str, token: str, qty: float, price: float,
              quote_amount: float, source: str = "chain", note: str | None = None,
              wallet_id: int | None = None) -> int | None:
    """Zwraca id nowego wiersza albo None, gdy sygnatura juz istnieje."""
    if signature:
        dup = con.execute("SELECT id FROM tx WHERE signature=?", (signature,)).fetchone()
        if dup:
            return None
    cur = con.execute(
        "INSERT INTO tx(signature, block_time, side, token, qty, price, quote_amount, "
        "source, note, wallet_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (signature, block_time, side, token, qty, price, quote_amount, source, note, wallet_id),
    )
    return cur.lastrowid


def add_match(con: sqlite3.Connection, buy_id: int, sell_id: int, qty: float) -> int:
    cur = con.execute(
        "INSERT INTO match(buy_id, sell_id, qty, created_at) VALUES (?,?,?,?)",
        (buy_id, sell_id, qty, int(time.time())),
    )
    return cur.lastrowid
