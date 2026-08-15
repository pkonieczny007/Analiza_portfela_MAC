"""Baza czesci EVM — OSOBNY plik SQLite, ten sam schemat co X1.

Dlaczego osobny plik, a nie kolumna `network` we wspolnych tabelach:
`matching.py` przyjmuje polaczenie jako pierwszy argument i nigdzie nie
filtruje po sieci. Druga baza o identycznym schemacie daje wiec CALY silnik
par (auto_match, manual_match, move_match, tx_view, group_stats) za darmo,
bez jednej linijki zmiany w kodzie X1 — a kolumna `network` wymagalaby
przerobienia kazdego zapytania po stronie X1 i groziloby, ze zakladka Pary
na X1 pokaze transakcje z Base.

Cena: portfele i ustawienia sa per baza (tabela `meta` tez), co akurat jest
zaleta — DRY-RUN, poslizg i filtr dat zakladki ETH nie mieszaja sie z X1.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import db as dbm
import evm_config as cfg

DB_PATH = cfg.BASE_DIR / "portfel_eth.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init() -> None:
    """Zaklada schemat (ten sam SQL co X1) i migracje specyficzne dla EVM.

    Roznica wobec `db.init()`: nie zasiewamy portfela z `config.WALLET`, bo to
    adres solanowy. Portfele EVM dodaje uzytkownik albo dopisuje je sync
    z adresow kluczy w `wallet_evm/`.
    """
    with connect() as con:
        con.executescript(dbm.SCHEMA)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(tx)")]
        if "wallet_id" not in cols:
            con.execute("ALTER TABLE tx ADD COLUMN wallet_id INTEGER REFERENCES wallet(id)")
        # tx.signature trzyma hash transakcji EVM; dla logow Swap doklejamy
        # indeks logu (0x..-3), bo jedna transakcja moze zawierac kilka swapow
        con.commit()


def ensure_wallet(con: sqlite3.Connection, address: str, name: str = "",
                  grp: str = "") -> int:
    """Zwraca id portfela EVM, zakladajac wiersz przy pierwszym uzyciu.

    Adresy EVM porownujemy po malych literach — ten sam portfel bywa zapisany
    w checksum-case i bez, a `address` ma UNIQUE, wiec inaczej zrobilyby sie
    duplikaty tego samego konta.
    """
    addr = address.lower()
    row = con.execute("SELECT id FROM wallet WHERE lower(address)=?", (addr,)).fetchone()
    if row:
        return row["id"]
    top = con.execute("SELECT COALESCE(MAX(sort),0)+1 s FROM wallet").fetchone()["s"]
    cur = con.execute(
        "INSERT INTO wallet(address, name, grp, sort) VALUES (?,?,?,?)",
        (address, name or address[:8], grp, top))
    return cur.lastrowid


# pomocniki `meta` i `insert_tx` z db.py dzialaja na dowolnym polaczeniu,
# wiec re-eksportujemy je, zeby moduly EVM nie importowaly dwoch baz naraz
meta_get = dbm.meta_get
meta_set = dbm.meta_set
insert_tx = dbm.insert_tx
add_match = dbm.add_match
selected_wallet_ids = dbm.selected_wallet_ids
