from __future__ import annotations

from decimal import Decimal, ROUND_UP

from app.canboso_client import Product


USDT_QUANT = Decimal("0.000001")


def apply_markup(amount: Decimal, markup_percent: Decimal) -> Decimal:
    multiplier = Decimal("1") + (markup_percent / Decimal("100"))
    return (amount * multiplier).quantize(USDT_QUANT, rounding=ROUND_UP)


def product_unit_usdt(product: Product, *, markup_percent: Decimal) -> Decimal:
    if not product.apply_markup:
        return product.estimated_unit_usdt().quantize(USDT_QUANT, rounding=ROUND_UP)
    return apply_markup(product.estimated_unit_usdt(), markup_percent)


def base_usdt_amount(
    product: Product,
    *,
    quantity: int,
    slot_months: int | None,
    markup_percent: Decimal = Decimal("0"),
) -> Decimal:
    unit = product_unit_usdt(product, markup_percent=markup_percent)
    multiplier = Decimal(slot_months or quantity or 1)
    return (unit * multiplier).quantize(USDT_QUANT, rounding=ROUND_UP)


def unique_fraction(order_seed: int, max_fraction: Decimal = Decimal("0.009999")) -> Decimal:
    max_units = int((max_fraction / USDT_QUANT).to_integral_value(rounding=ROUND_UP))
    if max_units <= 0:
        return Decimal("0")
    units = (order_seed % max_units) + 1
    return (Decimal(units) * USDT_QUANT).quantize(USDT_QUANT)


def amount_with_unique_fraction(
    base_amount: Decimal,
    *,
    order_seed: int,
    max_fraction: Decimal = Decimal("0.009999"),
) -> Decimal:
    return (base_amount + unique_fraction(order_seed, max_fraction)).quantize(
        USDT_QUANT, rounding=ROUND_UP
    )
