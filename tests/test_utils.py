from decimal import Decimal

from app.payment_amounts import amount_with_unique_fraction, apply_markup, unique_fraction
from app.text_utils import clean_repeated_lines, format_usdt


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


def test_format_usdt_uses_six_decimals() -> None:
    assert format_usdt(Decimal("1.2")) == "1.200000 USDT"
