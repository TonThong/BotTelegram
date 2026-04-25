from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.database import Order
from app.tron import TronUsdtScanner


RECEIVER = "TJmmqjb1DK9TTZbQXzRQ2AuA94z4gKAPFh"
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


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
        payment_method="usdt_trc20",
        payment_reference=None,
        tx_hash=None,
        created_at=now,
        paid_at=None,
        fulfilled_at=None,
        expires_at=now + timedelta(minutes=45),
        start_block=1_000,
        last_checked_block=1_000,
        canboso_order_code=None,
        canboso_response=None,
    )


class FakeTronScanner(TronUsdtScanner):
    def __init__(self, transfers: list[dict[str, Any]]):
        super().__init__(
            api_base_url="https://api.trongrid.io",
            usdt_contract=USDT_CONTRACT,
            receiver_address=RECEIVER,
        )
        self.transfers = transfers

    async def current_timestamp_ms(self) -> int:
        return 200_000

    async def _get_trc20_transfers(
        self, *, min_timestamp: int, fingerprint: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        return self.transfers, None


def test_tron_scanner_matches_usdt_trc20_transfer() -> None:
    scanner = FakeTronScanner(
        [
            {
                "type": "Transfer",
                "to": RECEIVER,
                "transaction_id": "abc123",
                "value": "10000124",
                "block_timestamp": 1_234,
                "token_info": {"address": USDT_CONTRACT, "decimals": 6},
            }
        ]
    )

    payment, next_marker = asyncio.run(
        scanner.find_payment(
            order=make_order(amount=Decimal("10.000124")),
            tolerance=Decimal("0.000001"),
        )
    )

    assert payment is not None
    assert payment.tx_hash == "abc123"
    assert payment.amount == Decimal("10.000124")
    assert next_marker == 1_235


def test_tron_scanner_ignores_different_amount() -> None:
    scanner = FakeTronScanner(
        [
            {
                "type": "Transfer",
                "to": RECEIVER,
                "transaction_id": "abc123",
                "value": "9000000",
                "block_timestamp": 1_234,
                "token_info": {"address": USDT_CONTRACT, "decimals": 6},
            }
        ]
    )

    payment, _ = asyncio.run(
        scanner.find_payment(
            order=make_order(amount=Decimal("10.000124")),
            tolerance=Decimal("0.000001"),
        )
    )

    assert payment is None
