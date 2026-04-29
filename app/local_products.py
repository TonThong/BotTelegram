from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.canboso_client import CanbosoError, Product
from app.text_utils import clean_repeated_lines


_MULTILINE_FIELDS = {"description", "data", "sold"}
_KNOWN_FIELDS = {
    "id",
    "product_id",
    "name",
    "product_name",
    "price",
    "pricing",
    "usd_price",
    "usd_pricing",
    "currency",
    "wallet_currency",
    "available",
    "stock",
    "is_slot_product",
    "slot_durations",
    "requires_customer_email",
    "requires_slot_months",
    "quantity_fixed",
}
_FIELD_ALIASES = {
    "product": "name",
    "product_name": "name",
    "product_id": "id",
    "gia": "price",
    "mo_ta": "description",
    "du_lieu": "data",
    "key": "data",
    "keys": "data",
    "stock": "available",
}


@dataclass(frozen=True)
class LocalProductRecord:
    path: Path
    fields: dict[str, str]
    description: str
    data: tuple[str, ...]
    sold: tuple[str, ...]
    product: Product


class LocalProductsClient:
    """A small Canboso-compatible product source backed by products/*.txt."""

    def __init__(self, products_dir: Path | str):
        self.products_dir = Path(products_dir)
        self._lock = asyncio.Lock()

    async def list_products(self) -> list[Product]:
        return [record.product for record in self._load_records()]

    async def get_product(self, product_id: str) -> Product:
        return self._find_record(product_id).product

    def has_product(self, product_id: str) -> bool:
        return any(record.product.product_id == product_id for record in self._load_records())

    async def get_balance(self) -> dict[str, object]:
        return {"success": True, "source": "local", "balance": 0}

    async def purchase(
        self,
        *,
        product_id: str,
        quantity: int = 1,
        customer_email: str | None = None,
        slot_months: int | None = None,
    ) -> dict[str, object]:
        async with self._lock:
            record = self._find_record(product_id)
            purchase_quantity = 1 if slot_months else quantity
            if purchase_quantity <= 0:
                raise CanbosoError("Quantity must be positive.")

            delivered_items: list[str] = []
            if purchase_quantity > len(record.data):
                raise CanbosoError("Not enough local stock is available.")
            delivered_items = list(record.data[:purchase_quantity])
            remaining_data = list(record.data[purchase_quantity:])
            sold = [*record.sold, *delivered_items]
            _write_record(record, remaining_data=remaining_data, sold=sold)

            payload: dict[str, object] = {
                "success": True,
                "source": "local",
                "productId": record.product.product_id,
                "productType": record.product.name,
                "quantity": purchase_quantity,
            }
            if customer_email:
                payload["customerEmail"] = customer_email
            if slot_months:
                payload["slotMonths"] = slot_months
            if delivered_items:
                payload["deliveredItems"] = delivered_items
            return payload

    def _load_records(self) -> list[LocalProductRecord]:
        if not self.products_dir.exists():
            return []
        records = [
            _parse_product_file(path)
            for path in sorted(self.products_dir.glob("*.txt"), key=lambda item: item.name.casefold())
        ]
        return [record for record in records if record.product.name]

    def _find_record(self, product_id: str) -> LocalProductRecord:
        for record in self._load_records():
            if record.product.product_id == product_id:
                return record
        raise CanbosoError("Product not found.")


def _parse_product_file(path: Path) -> LocalProductRecord:
    text = path.read_text(encoding="utf-8-sig")
    fields: dict[str, str] = {}
    sections: dict[str, list[str]] = {"description": [], "data": [], "sold": []}
    section: str | None = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped and section != "description":
            continue
        if stripped.startswith("#") and section is None:
            continue

        key_value = _split_key_value(raw_line)
        if key_value is not None:
            key, value = key_value
            normalized_key = _normalize_field_name(key)
            if normalized_key in _MULTILINE_FIELDS:
                section = normalized_key
                if value:
                    sections[section].append(value)
                continue
            if section is None or normalized_key in _KNOWN_FIELDS:
                fields[normalized_key] = value
                section = None
                continue

        if section is not None:
            if section == "description":
                sections[section].append(raw_line.rstrip())
            elif stripped and not stripped.startswith("#"):
                sections[section].append(stripped)
        elif stripped:
            sections["description"].append(raw_line.rstrip())

    product_id = fields.get("id") or _slugify(path.stem)
    name = fields.get("name") or path.stem.strip()
    currency = (fields.get("currency") or fields.get("wallet_currency") or "USDT").upper()
    price = _parse_decimal(
        fields.get("usd_price")
        or fields.get("usd_pricing")
        or fields.get("price")
        or fields.get("pricing")
        or "0"
    )
    data = tuple(item for item in sections["data"] if item.strip())
    available = len(data)

    slot_durations = _parse_int_list(fields.get("slot_durations"))
    description = clean_repeated_lines("\n".join(sections["description"]))
    product = Product(
        product_id=product_id,
        name=name,
        raw_name=name,
        description=description,
        wallet_currency=currency,
        wallet_pricing=price,
        wallet_pricing_text=f"{price:f} {currency}",
        usd_pricing=price if currency in {"USD", "USDT"} else None,
        available=available,
        is_slot_product=_parse_bool(fields.get("is_slot_product")),
        slot_durations=slot_durations,
        requires_customer_email=_parse_bool(fields.get("requires_customer_email")),
        requires_slot_months=_parse_bool(fields.get("requires_slot_months")),
        quantity_fixed=_parse_optional_int(fields.get("quantity_fixed")),
        apply_markup=False,
    )
    return LocalProductRecord(
        path=path,
        fields=fields,
        description=description,
        data=data,
        sold=tuple(item for item in sections["sold"] if item.strip()),
        product=product,
    )


def _write_record(
    record: LocalProductRecord,
    *,
    remaining_data: list[str],
    sold: list[str],
) -> None:
    product = record.product
    lines = [
        f"id: {product.product_id}",
        f"name: {product.name}",
        f"price: {product.wallet_pricing:f}",
        f"currency: {product.wallet_currency or 'USDT'}"
    ]
    if product.is_slot_product:
        lines.append("is_slot_product: true")
    if product.slot_durations:
        lines.append(f"slot_durations: {', '.join(str(item) for item in product.slot_durations)}")
    if product.requires_customer_email:
        lines.append("requires_customer_email: true")
    if product.requires_slot_months:
        lines.append("requires_slot_months: true")
    if product.quantity_fixed is not None:
        lines.append(f"quantity_fixed: {product.quantity_fixed}")

    lines.append("")
    lines.append("description:")
    lines.extend(product.description.splitlines())
    lines.append("")
    lines.append("data:")
    lines.extend(remaining_data)
    if sold:
        lines.append("")
        lines.append("sold:")
        lines.extend(sold)
    record.path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _split_key_value(line: str) -> tuple[str, str] | None:
    match = re.match(r"^\s*([^:]+)\s*:\s*(.*)\s*$", line)
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _normalize_field_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    key = "_".join(ascii_name.strip().lower().split())
    return _FIELD_ALIASES.get(key, key)


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_value).strip("_").lower()
    return slug or "product"


def _parse_decimal(value: str) -> Decimal:
    normalized = value.strip().replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return Decimal("0")
    return Decimal(match.group(0))


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"-?\d+", value)
    if not match:
        return None
    return int(match.group(0))


def _parse_int_list(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item) for item in re.findall(r"\d+", value)]
