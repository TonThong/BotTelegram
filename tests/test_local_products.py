from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.canboso_client import Product
from app.database import Order
from app.local_products import LocalProductsClient
from app.messages import delivery_message
from app.payment_amounts import product_unit_usdt
from app.product_sources import HybridProductsClient


class FakeApiClient:
    def __init__(self) -> None:
        self.api_product = Product(
            product_id="api_product",
            name="API Product",
            raw_name="API Product",
            description="From API",
            wallet_currency="USDT",
            wallet_pricing=Decimal("10"),
            wallet_pricing_text="10 USDT",
            usd_pricing=Decimal("10"),
            available=3,
            is_slot_product=False,
            slot_durations=[],
            requires_customer_email=False,
            requires_slot_months=False,
            quantity_fixed=None,
        )
        self.purchased: list[tuple[str, int]] = []

    async def list_products(self) -> list[Product]:
        return [self.api_product]

    async def get_product(self, product_id: str) -> Product:
        return self.api_product

    async def get_balance(self) -> dict[str, object]:
        return {"success": True}

    async def purchase(
        self,
        *,
        product_id: str,
        quantity: int = 1,
        customer_email: str | None = None,
        slot_months: int | None = None,
    ) -> dict[str, object]:
        self.purchased.append((product_id, quantity))
        return {"success": True, "productType": self.api_product.name}


def test_local_products_load_txt_file_and_purchase_key(tmp_path) -> None:
    product_file = tmp_path / "CDK GPT PLUS 1M (No War).txt"
    product_file.write_text(
        "\n".join(
            [
                "name: CDK GPT PLUS 1M (No War)",
                "price: 20.00 USDT",
                "available: 99",
                "",
                "description:",
                "ChatGPT Plus CDK valid for 1 month.",
                "",
                "data:",
                "AI-C6EER-WV9YN-QBPQ2",
                "AI-SECOND-LOCAL-KEY",
            ]
        ),
        encoding="utf-8",
    )
    client = LocalProductsClient(tmp_path)

    products = asyncio.run(client.list_products())

    assert len(products) == 1
    assert products[0].product_id == "cdk_gpt_plus_1m_no_war"
    assert products[0].name == "CDK GPT PLUS 1M (No War)"
    assert products[0].usd_pricing == Decimal("20.00")
    assert products[0].available == 2
    assert product_unit_usdt(products[0], markup_percent=Decimal("25")) == Decimal("20.000000")

    payload = asyncio.run(client.purchase(product_id=products[0].product_id, quantity=2))

    assert payload["deliveredItems"] == ["AI-C6EER-WV9YN-QBPQ2", "AI-SECOND-LOCAL-KEY"]
    refreshed = asyncio.run(client.get_product(products[0].product_id))
    assert refreshed.available == 0
    assert "sold:\nAI-C6EER-WV9YN-QBPQ2" in product_file.read_text(encoding="utf-8")
    assert "AI-SECOND-LOCAL-KEY" in product_file.read_text(encoding="utf-8")


def test_hybrid_products_lists_and_routes_api_and_local_products(tmp_path) -> None:
    product_file = tmp_path / "Local CDK.txt"
    product_file.write_text(
        "\n".join(
            [
                "id: local_cdk",
                "name: Local CDK",
                "price: 5",
                "",
                "data:",
                "LOCAL-KEY-1",
            ]
        ),
        encoding="utf-8",
    )
    api_client = FakeApiClient()
    client = HybridProductsClient(api_client, LocalProductsClient(tmp_path))

    products = asyncio.run(client.list_products())

    assert [product.product_id for product in products] == ["api_product", "local_cdk"]

    local_payload = asyncio.run(client.purchase(product_id="local_cdk", quantity=1))
    api_payload = asyncio.run(client.purchase(product_id="api_product", quantity=2))

    assert local_payload["deliveredItems"] == ["LOCAL-KEY-1"]
    assert api_payload["productType"] == "API Product"
    assert api_client.purchased == [("api_product", 2)]


def test_delivery_message_shows_delivered_items() -> None:
    now = datetime.now(UTC)
    order = Order(
        id=1,
        telegram_user_id=123,
        telegram_chat_id=456,
        username="buyer",
        status="fulfilled",
        product_id="cdk",
        product_name="CDK",
        quantity=1,
        slot_months=None,
        customer_email=None,
        amount_usdt=Decimal("20"),
        base_amount_usdt=Decimal("20"),
        payment_method="binance_id",
        payment_reference=None,
        tx_hash=None,
        created_at=now,
        paid_at=now,
        fulfilled_at=now,
        expires_at=now + timedelta(minutes=45),
        start_block=None,
        last_checked_block=None,
        canboso_order_code=None,
        canboso_response=None,
    )

    message = delivery_message(
        order,
        {
            "productType": "CDK GPT PLUS 1M (No War)",
            "deliveredItems": ["AI-C6EER-WV9YN-QBPQ2"],
        },
    )

    assert "<b>Delivered items</b>" in message
    assert "<code>AI-C6EER-WV9YN-QBPQ2</code>" in message
