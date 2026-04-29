from __future__ import annotations

from typing import Any

from app.canboso_client import CanbosoError, Product
from app.local_products import LocalProductsClient


class HybridProductsClient:
    """Canboso-compatible facade that combines API products and local products."""

    def __init__(self, api_client: Any, local_client: LocalProductsClient):
        self.api_client = api_client
        self.local_client = local_client

    async def list_products(self) -> list[Product]:
        api_products = await self.api_client.list_products()
        local_products = await self.local_client.list_products()
        duplicate_ids = {
            product.product_id for product in api_products
        } & {product.product_id for product in local_products}
        if duplicate_ids:
            names = ", ".join(sorted(duplicate_ids))
            raise CanbosoError(f"Duplicate product id between API and local products: {names}")
        return [*api_products, *local_products]

    async def get_product(self, product_id: str) -> Product:
        if self.local_client.has_product(product_id):
            return await self.local_client.get_product(product_id)
        return await self.api_client.get_product(product_id)

    async def get_balance(self) -> dict[str, Any]:
        return await self.api_client.get_balance()

    async def purchase(
        self,
        *,
        product_id: str,
        quantity: int = 1,
        customer_email: str | None = None,
        slot_months: int | None = None,
    ) -> dict[str, Any]:
        if self.local_client.has_product(product_id):
            return await self.local_client.purchase(
                product_id=product_id,
                quantity=quantity,
                customer_email=customer_email,
                slot_months=slot_months,
            )
        return await self.api_client.purchase(
            product_id=product_id,
            quantity=quantity,
            customer_email=customer_email,
            slot_months=slot_months,
        )
