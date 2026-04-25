from __future__ import annotations

import html
from decimal import Decimal
from typing import Any

from app.canboso_client import Product
from app.database import Order
from app.text_utils import format_usdt


def h(value: Any) -> str:
    return html.escape(str(value), quote=False)


def product_summary(product: Product, *, unit_usdt: Decimal | None = None) -> str:
    availability = "Available now" if product.available is None else f"{product.available} available"
    price = format_usdt(unit_usdt) if unit_usdt is not None else (
        product.wallet_pricing_text or f"{product.wallet_pricing} {product.wallet_currency}"
    )
    parts = [
        f"<b>{h(product.name)}</b>",
        f"Price: {h(price)}",
        f"Stock: {h(availability)}",
    ]
    if product.is_slot_product and product.slot_durations:
        durations = ", ".join(str(item) for item in product.slot_durations)
        parts.append(f"Durations: {h(durations)} month(s)")
    if product.description:
        parts.append("")
        parts.append(h(product.description))
    return "\n".join(parts)


def payment_amount_line(amount: Decimal) -> str:
    return f"<b>Exact amount:</b> <code>{h(format_usdt(amount))}</code>"


def delivery_message(order: Order, payload: dict[str, Any]) -> str:
    lines = [
        "Payment confirmed. Your order is ready.",
        "",
        f"Product: {h(payload.get('productType') or order.product_name)}",
    ]

    accounts = payload.get("deliveredAccounts") or []
    if accounts:
        lines.append("")
        lines.append("<b>Delivered accounts</b>")
        for index, account in enumerate(accounts, start=1):
            lines.append(f"{index}. User: <code>{h(account.get('user', ''))}</code>")
            if account.get("password"):
                lines.append(f"   Password: <code>{h(account['password'])}</code>")
            if account.get("verifyEmail"):
                lines.append(f"   Recovery email: <code>{h(account['verifyEmail'])}</code>")

    if payload.get("workspaceInviteStatus"):
        lines.append("")
        lines.append(f"Workspace invite: {h(payload['workspaceInviteStatus'])}")
        if payload.get("workspaceOwnerEmail"):
            lines.append(f"Owner: <code>{h(payload['workspaceOwnerEmail'])}</code>")
        if payload.get("inviteError"):
            lines.append(f"Invite note: {h(payload['inviteError'])}")

    return "\n".join(lines)
