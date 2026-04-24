from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class Order:
    id: int
    telegram_user_id: int
    telegram_chat_id: int
    username: str
    status: str
    product_id: str
    product_name: str
    quantity: int
    slot_months: int | None
    customer_email: str | None
    amount_usdt: Decimal
    base_amount_usdt: Decimal
    payment_method: str
    payment_reference: str | None
    tx_hash: str | None
    created_at: datetime
    paid_at: datetime | None
    fulfilled_at: datetime | None
    expires_at: datetime
    start_block: int | None
    last_checked_block: int | None
    canboso_order_code: str | None
    canboso_response: dict[str, Any] | None

    @property
    def is_expired(self) -> bool:
        return utc_now() > self.expires_at


@dataclass(frozen=True)
class BotUser:
    telegram_user_id: int
    telegram_chat_id: int
    username: str
    full_name: str
    first_seen_at: datetime
    last_seen_at: datetime
    is_blocked: bool


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True) if self.path.parent != Path(".") else None
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys = ON")
            yield con
            con.commit()
        finally:
            con.close()

    def init(self) -> None:
        with self.connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    telegram_chat_id INTEGER NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    slot_months INTEGER,
                    customer_email TEXT,
                    amount_usdt TEXT NOT NULL,
                    base_amount_usdt TEXT NOT NULL,
                    payment_method TEXT NOT NULL,
                    payment_reference TEXT,
                    tx_hash TEXT,
                    created_at TEXT NOT NULL,
                    paid_at TEXT,
                    fulfilled_at TEXT,
                    expires_at TEXT NOT NULL,
                    start_block INTEGER,
                    last_checked_block INTEGER,
                    canboso_order_code TEXT,
                    canboso_response TEXT
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_tx_hash
                ON orders(tx_hash)
                WHERE tx_hash IS NOT NULL
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_payment_reference
                ON orders(payment_method, payment_reference)
                WHERE payment_reference IS NOT NULL
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    telegram_chat_id INTEGER NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    full_name TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    is_blocked INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            now = to_iso(utc_now())
            con.execute(
                """
                INSERT OR IGNORE INTO bot_users (
                    telegram_user_id, telegram_chat_id, username, full_name,
                    first_seen_at, last_seen_at, is_blocked
                )
                SELECT
                    telegram_user_id,
                    telegram_chat_id,
                    username,
                    username,
                    ?,
                    ?,
                    0
                FROM orders
                GROUP BY telegram_user_id
                """,
                (now, now),
            )

    def upsert_user(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        username: str,
        full_name: str,
    ) -> None:
        now = to_iso(utc_now())
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO bot_users (
                    telegram_user_id, telegram_chat_id, username, full_name,
                    first_seen_at, last_seen_at, is_blocked
                )
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    telegram_chat_id = excluded.telegram_chat_id,
                    username = excluded.username,
                    full_name = excluded.full_name,
                    last_seen_at = excluded.last_seen_at,
                    is_blocked = 0
                """,
                (telegram_user_id, telegram_chat_id, username, full_name, now, now),
            )

    def list_broadcast_users(self) -> list[BotUser]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT * FROM bot_users
                WHERE is_blocked = 0
                ORDER BY last_seen_at DESC
                """
            ).fetchall()
        return [self._row_to_bot_user(row) for row in rows]

    def mark_user_blocked(self, telegram_user_id: int) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE bot_users SET is_blocked = 1 WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )

    def create_order(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        username: str,
        product_id: str,
        product_name: str,
        quantity: int,
        slot_months: int | None,
        customer_email: str | None,
        amount_usdt: Decimal,
        base_amount_usdt: Decimal,
        payment_method: str,
        expiry_minutes: int,
        start_block: int | None = None,
    ) -> Order:
        now = utc_now()
        expires_at = now + timedelta(minutes=expiry_minutes)
        with self.connect() as con:
            cursor = con.execute(
                """
                INSERT INTO orders (
                    telegram_user_id, telegram_chat_id, username, status,
                    product_id, product_name, quantity, slot_months, customer_email,
                    amount_usdt, base_amount_usdt, payment_method,
                    created_at, expires_at, start_block, last_checked_block
                )
                VALUES (?, ?, ?, 'awaiting_payment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    telegram_chat_id,
                    username,
                    product_id,
                    product_name,
                    quantity,
                    slot_months,
                    customer_email,
                    str(amount_usdt),
                    str(base_amount_usdt),
                    payment_method,
                    to_iso(now),
                    to_iso(expires_at),
                    start_block,
                    start_block,
                ),
            )
            order_id = int(cursor.lastrowid)
        return self.get_order(order_id)

    def get_order(self, order_id: int) -> Order:
        with self.connect() as con:
            row = con.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(f"Order {order_id} not found.")
        return self._row_to_order(row)

    def list_recent_orders(self, telegram_user_id: int, limit: int = 10) -> list[Order]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT * FROM orders
                WHERE telegram_user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()
        return [self._row_to_order(row) for row in rows]

    def pending_orders(self, payment_method: str | None = None) -> list[Order]:
        sql = "SELECT * FROM orders WHERE status = 'awaiting_payment'"
        params: tuple[Any, ...] = ()
        if payment_method:
            sql += " AND payment_method = ?"
            params = (payment_method,)
        sql += " ORDER BY id ASC"
        with self.connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_order(row) for row in rows]

    def set_last_checked_block(self, order_id: int, block_number: int) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE orders SET last_checked_block = ? WHERE id = ?",
                (block_number, order_id),
            )

    def mark_expired(self, order_id: int) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE orders SET status = 'expired' WHERE id = ? AND status = 'awaiting_payment'",
                (order_id,),
            )

    def mark_paid(
        self,
        order_id: int,
        *,
        payment_reference: str | None = None,
        tx_hash: str | None = None,
    ) -> Order:
        now = utc_now()
        with self.connect() as con:
            con.execute(
                """
                UPDATE orders
                SET status = 'payment_received',
                    paid_at = ?,
                    payment_reference = COALESCE(?, payment_reference),
                    tx_hash = COALESCE(?, tx_hash)
                WHERE id = ? AND status = 'awaiting_payment'
                """,
                (to_iso(now), payment_reference, tx_hash, order_id),
            )
        return self.get_order(order_id)

    def mark_fulfilled(self, order_id: int, payload: dict[str, Any]) -> Order:
        now = utc_now()
        with self.connect() as con:
            con.execute(
                """
                UPDATE orders
                SET status = 'fulfilled',
                    fulfilled_at = ?,
                    canboso_order_code = ?,
                    canboso_response = ?
                WHERE id = ? AND status IN ('payment_received', 'awaiting_payment')
                """,
                (
                    to_iso(now),
                    payload.get("orderCode"),
                    json.dumps(payload, ensure_ascii=False),
                    order_id,
                ),
            )
        return self.get_order(order_id)

    def mark_failed(self, order_id: int, payload: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """
                UPDATE orders
                SET status = 'failed',
                    canboso_response = ?
                WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), order_id),
            )

    def _row_to_order(self, row: sqlite3.Row) -> Order:
        response = row["canboso_response"]
        return Order(
            id=int(row["id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            telegram_chat_id=int(row["telegram_chat_id"]),
            username=str(row["username"] or ""),
            status=str(row["status"]),
            product_id=str(row["product_id"]),
            product_name=str(row["product_name"]),
            quantity=int(row["quantity"]),
            slot_months=int(row["slot_months"]) if row["slot_months"] is not None else None,
            customer_email=row["customer_email"],
            amount_usdt=Decimal(str(row["amount_usdt"])),
            base_amount_usdt=Decimal(str(row["base_amount_usdt"])),
            payment_method=str(row["payment_method"]),
            payment_reference=row["payment_reference"],
            tx_hash=row["tx_hash"],
            created_at=from_iso(row["created_at"]),
            paid_at=from_iso(row["paid_at"]) if row["paid_at"] else None,
            fulfilled_at=from_iso(row["fulfilled_at"]) if row["fulfilled_at"] else None,
            expires_at=from_iso(row["expires_at"]),
            start_block=int(row["start_block"]) if row["start_block"] is not None else None,
            last_checked_block=(
                int(row["last_checked_block"]) if row["last_checked_block"] is not None else None
            ),
            canboso_order_code=row["canboso_order_code"],
            canboso_response=json.loads(response) if response else None,
        )

    def _row_to_bot_user(self, row: sqlite3.Row) -> BotUser:
        return BotUser(
            telegram_user_id=int(row["telegram_user_id"]),
            telegram_chat_id=int(row["telegram_chat_id"]),
            username=str(row["username"] or ""),
            full_name=str(row["full_name"] or ""),
            first_seen_at=from_iso(row["first_seen_at"]),
            last_seen_at=from_iso(row["last_seen_at"]),
            is_blocked=bool(row["is_blocked"]),
        )
