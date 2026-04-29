from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.text_utils import clean_repeated_lines, normalize_decimal


class CanbosoError(RuntimeError):
    pass


VND_PER_USD = Decimal("34000")
VND_CURRENCIES = {"VND", "VNĐ", "DONG", "Đ", "₫"}


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    raw_name: str
    description: str
    wallet_currency: str
    wallet_pricing: Decimal
    wallet_pricing_text: str
    usd_pricing: Decimal | None
    available: int | None
    is_slot_product: bool
    slot_durations: list[int]
    requires_customer_email: bool
    requires_slot_months: bool
    quantity_fixed: int | None
    apply_markup: bool = True

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Product":
        stats = payload.get("stats") or {}
        raw_available = stats.get("available")
        available = int(raw_available) if raw_available is not None else None
        usd_pricing = payload.get("usdPricing")
        product_id = payload.get("_id") or payload.get("product_id") or ""
        name = payload.get("product_name") or payload.get("product_name_raw") or product_id
        raw_name = payload.get("product_name_raw") or name
        description = clean_repeated_lines(
            payload.get("description") or payload.get("description_raw") or ""
        )
        return cls(
            product_id=str(product_id),
            name=str(name),
            raw_name=str(raw_name),
            description=description,
            wallet_currency=str(payload.get("walletCurrency") or ""),
            wallet_pricing=normalize_decimal(payload.get("walletPricing") or payload.get("pricing")),
            wallet_pricing_text=str(payload.get("walletPricingText") or ""),
            usd_pricing=normalize_decimal(usd_pricing) if usd_pricing is not None else None,
            available=available,
            is_slot_product=bool(payload.get("isSlotProduct")),
            slot_durations=[int(item) for item in payload.get("slotDurations") or []],
            requires_customer_email=bool(payload.get("requiresCustomerEmail")),
            requires_slot_months=bool(payload.get("requiresSlotMonths")),
            quantity_fixed=(
                int(payload["quantityFixed"]) if payload.get("quantityFixed") is not None else None
            ),
        )

    def estimated_unit_usdt(self) -> Decimal:
        if self.usd_pricing is not None:
            return self.usd_pricing
        currency = self.wallet_currency.strip().upper()
        if currency in {"USD", "USDT"}:
            return self.wallet_pricing
        if currency in VND_CURRENCIES:
            return self.wallet_pricing / VND_PER_USD
        raise CanbosoError(
            f"Product {self.product_id} has no USD price. Configure a USD buyer key or product pricing."
        )


class CanbosoClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success", False):
                raise CanbosoError(str(payload.get("message") or "Canboso request failed."))
            return payload
        except httpx.HTTPStatusError as exc:
            message = exc.response.text
            try:
                message = exc.response.json().get("message", message)
            except ValueError:
                pass
            raise CanbosoError(message) from exc
        except httpx.HTTPError as exc:
            raise CanbosoError(str(exc)) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def list_products(self) -> list[Product]:
        payload = await self._request(
            "GET", "/api/telegram-buyer/products", params={"key": self.api_key}
        )
        return [Product.from_api(item) for item in payload.get("products") or []]

    async def get_product(self, product_id: str) -> Product:
        products = await self.list_products()
        for product in products:
            if product.product_id == product_id:
                return product
        raise CanbosoError("Product not found.")

    async def get_balance(self) -> dict[str, Any]:
        return await self._request(
            "GET", "/api/telegram-buyer/balance", params={"key": self.api_key}
        )

    async def purchase(
        self,
        *,
        product_id: str,
        quantity: int = 1,
        customer_email: str | None = None,
        slot_months: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "key": self.api_key,
            "product_id": product_id,
        }
        if product_id == "slot_chatgpt_business" or slot_months:
            body["quantity"] = 1
        else:
            body["quantity"] = quantity
        if customer_email:
            body["customer_email"] = customer_email
        if slot_months:
            body["slot_months"] = slot_months
        return await self._request("POST", "/api/telegram-buyer/purchase", json=body)
