from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import app.binance_pay as binance_pay_module
from app.binance_pay import BinancePayHistoryClient
from app.database import Order


def make_order(*, amount: Decimal) -> Order:
    now = datetime.now(UTC)
    return Order(
        id=1,
        telegram_user_id=123,
        telegram_chat_id=456,
        username="buyer",
        status="awaiting_payment",
        product_id="product",
        product_name="Product",
        quantity=1,
        slot_months=None,
        customer_email=None,
        amount_usdt=amount,
        base_amount_usdt=amount,
        payment_method="binance_id",
        payment_reference=None,
        tx_hash=None,
        created_at=now,
        paid_at=None,
        fulfilled_at=None,
        expires_at=now + timedelta(minutes=45),
        start_block=None,
        last_checked_block=None,
        canboso_order_code=None,
        canboso_response=None,
    )


def test_binance_pay_history_matches_without_transaction_id() -> None:
    client = BinancePayHistoryClient(base_url="https://api.binance.com", api_key="", api_secret="")
    order = make_order(amount=Decimal("10.000124"))
    transaction_time = int(order.created_at.timestamp() * 1000) + 1_000

    match = client.match_transactions(
        order=order,
        transactions=[
            {
                "transactionId": "M_P_123",
                "transactionTime": transaction_time,
                "amount": "10.000124",
                "currency": "USDT",
                "receiverInfo": {"accountId": "pay-id-123"},
            }
        ],
        tolerance=Decimal("0.000001"),
        receiver_binance_id="pay-id-123",
    )

    assert match is not None
    assert match.transaction_id == "M_P_123"
    assert match.amount == Decimal("10.000124")


def test_binance_pay_history_ignores_different_amount() -> None:
    client = BinancePayHistoryClient(base_url="https://api.binance.com", api_key="", api_secret="")
    order = make_order(amount=Decimal("10.000124"))
    transaction_time = int(order.created_at.timestamp() * 1000) + 1_000

    match = client.match_transactions(
        order=order,
        transactions=[
            {
                "transactionId": "M_P_123",
                "transactionTime": transaction_time,
                "amount": "9.000000",
                "currency": "USDT",
                "receiverInfo": {"accountId": "pay-id-123"},
            }
        ],
        tolerance=Decimal("0.000001"),
        receiver_binance_id="pay-id-123",
    )

    assert match is None


class FakeTimeResponse:
    status_code = 200
    text = '{"serverTime":1001000}'

    def json(self):
        return {"serverTime": 1_001_000}


class FakeTimeClient:
    def __init__(self):
        self.urls: list[str] = []

    async def get(self, url: str):
        self.urls.append(url)
        return FakeTimeResponse()


def test_signed_timestamp_uses_binance_server_time(monkeypatch) -> None:
    client = BinancePayHistoryClient(base_url="https://api.binance.com", api_key="", api_secret="")
    fake_client = FakeTimeClient()
    times = iter([1000.0, 1000.1, 1000.2])
    monkeypatch.setattr(binance_pay_module.time, "time", lambda: next(times))
    monkeypatch.setattr(binance_pay_module.time, "monotonic", lambda: 1000.0)

    timestamp_ms = asyncio.run(client._signed_timestamp_ms(fake_client))

    assert fake_client.urls == ["https://api.binance.com/api/v3/time"]
    assert client.to_binance_time_ms(1_000_000) == 1_000_950
    assert timestamp_ms == 1_001_150
