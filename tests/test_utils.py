from decimal import Decimal

from app.canboso_client import Product
from app.payment_amounts import (
    amount_with_unique_fraction,
    apply_markup,
    product_unit_usdt,
    unique_fraction,
)
from app.text_utils import clean_repeated_lines, format_usdt, format_usdt_price


def test_clean_repeated_lines_removes_duplicate_text() -> None:
    text = "Fast delivery\nFast delivery\n  Fresh account  \n"
    assert clean_repeated_lines(text) == "Fast delivery\nFresh account"


def test_unique_fraction_is_small_and_stable() -> None:
    value = unique_fraction(123)
    assert value == Decimal("0.000124")
    assert unique_fraction(123) == value


def test_amount_with_unique_fraction() -> None:
    assert amount_with_unique_fraction(Decimal("10"), order_seed=123) == Decimal("10.000124")


def test_apply_markup_adds_percent() -> None:
    assert apply_markup(Decimal("3.5"), Decimal("25")) == Decimal("4.375000")


def test_vnd_price_converts_to_usdt_before_markup() -> None:
    product = Product(
        product_id="slot_chatgpt_business",
        name="ChatGPT Business",
        raw_name="ChatGPT Business",
        description="",
        wallet_currency="VND",
        wallet_pricing=Decimal("340000"),
        wallet_pricing_text="340000 VND",
        usd_pricing=None,
        available=None,
        is_slot_product=True,
        slot_durations=[1],
        requires_customer_email=True,
        requires_slot_months=True,
        quantity_fixed=None,
    )

    assert product_unit_usdt(product, markup_percent=Decimal("25")) == Decimal("12.500000")


def test_format_usdt_uses_six_decimals() -> None:
    assert format_usdt(Decimal("1.2")) == "1.200000 USDT"


def test_format_usdt_price_uses_two_decimals() -> None:
    assert format_usdt_price(Decimal("1.2")) == "1.20 USDT"
    assert format_usdt_price(Decimal("1.234")) == "1.24 USDT"
