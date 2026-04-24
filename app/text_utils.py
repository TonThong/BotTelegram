from __future__ import annotations

from decimal import Decimal, ROUND_UP


def clean_repeated_lines(text: str | None) -> str:
    if not text:
        return ""
    seen: set[str] = set()
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def format_usdt(amount: Decimal) -> str:
    normalized = amount.quantize(Decimal("0.000001"), rounding=ROUND_UP)
    return f"{normalized:f} USDT"


def normalize_decimal(value: Decimal | int | float | str | None, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))
