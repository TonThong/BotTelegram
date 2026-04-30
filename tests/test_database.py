from __future__ import annotations

from decimal import Decimal

from app.database import Database


def create_test_order(db: Database, *, expiry_minutes: int = 45):
    return db.create_order(
        telegram_user_id=123,
        telegram_chat_id=456,
        username="buyer",
        product_id="product",
        product_name="Product",
        quantity=1,
        slot_months=None,
        customer_email=None,
        amount_usdt=Decimal("10"),
        base_amount_usdt=Decimal("10"),
        payment_method="binance_id",
        expiry_minutes=expiry_minutes,
    )


def test_database_startup_keeps_terminal_orders_for_history(tmp_path) -> None:
    path = tmp_path / "bot.sqlite3"
    db = Database(path)
    order = create_test_order(db)
    db.mark_expired(order.id)

    reopened = Database(path)

    assert reopened.get_order(order.id).status == "expired"
    assert reopened.pending_orders() == []


def test_database_startup_marks_expired_pending_orders_for_history(tmp_path) -> None:
    path = tmp_path / "bot.sqlite3"
    db = Database(path)
    order = create_test_order(db, expiry_minutes=-1)

    reopened = Database(path)

    assert reopened.get_order(order.id).status == "expired"
    assert reopened.pending_orders() == []


def test_delete_order_removes_active_request(tmp_path) -> None:
    db = Database(tmp_path / "bot.sqlite3")
    order = create_test_order(db)

    db.delete_order(order.id)

    assert db.pending_orders() == []
