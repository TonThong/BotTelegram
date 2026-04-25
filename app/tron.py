from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.database import Order


USDT_DECIMALS = 6


class TronError(RuntimeError):
    pass


@dataclass(frozen=True)
class TronPayment:
    tx_hash: str
    amount: Decimal
    block_timestamp: int


def _is_tron_address(address: str) -> bool:
    value = address.strip()
    return (value.startswith("T") and len(value) == 34) or (
        value.lower().startswith("41") and len(value) == 42
    )


def _raw_token_amount(value: Any, decimals: int) -> Decimal:
    return Decimal(str(value)) / (Decimal(10) ** decimals)


class TronUsdtScanner:
    def __init__(
        self,
        *,
        api_base_url: str,
        usdt_contract: str,
        receiver_address: str,
        api_key: str = "",
        page_limit: int = 200,
        scan_safety_window_seconds: int = 180,
    ):
        if not _is_tron_address(receiver_address):
            raise TronError("Invalid TRC20 receiver address.")
        if not _is_tron_address(usdt_contract):
            raise TronError("Invalid TRC20 USDT contract address.")
        self.api_base_url = api_base_url.rstrip("/")
        self.usdt_contract = usdt_contract.strip()
        self.receiver_address = receiver_address.strip()
        self.api_key = api_key.strip()
        self.page_limit = max(1, min(page_limit, 200))
        self.scan_safety_window_ms = max(0, scan_safety_window_seconds) * 1000

    async def current_timestamp_ms(self) -> int:
        return int(time.time() * 1000)

    async def find_payment(
        self,
        *,
        order: Order,
        tolerance: Decimal,
        max_pages: int = 20,
    ) -> tuple[TronPayment | None, int]:
        current_marker = await self.current_timestamp_ms()
        fallback_start = max(0, current_marker - self.scan_safety_window_ms)
        min_timestamp = order.last_checked_block or order.start_block or fallback_start
        min_timestamp = max(0, min_timestamp)
        expected = order.amount_usdt
        next_timestamp = min_timestamp
        saw_transfer = False
        fingerprint: str | None = None

        for _ in range(max(1, max_pages)):
            transfers, fingerprint = await self._get_trc20_transfers(
                min_timestamp=min_timestamp,
                fingerprint=fingerprint,
            )
            for transfer in transfers:
                payment = self._payment_from_transfer(transfer)
                if payment is None:
                    continue
                saw_transfer = True
                next_timestamp = max(next_timestamp, payment.block_timestamp + 1)
                if abs(payment.amount - expected) <= tolerance:
                    return payment, next_timestamp
            if not fingerprint:
                break
        else:
            return None, min_timestamp

        return None, self._safe_next_timestamp(
            min_timestamp=min_timestamp,
            next_timestamp=next_timestamp,
            saw_transfer=saw_transfer,
        )

    async def _get_trc20_transfers(
        self, *, min_timestamp: int, fingerprint: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "only_confirmed": "true",
            "only_to": "true",
            "limit": self.page_limit,
            "order_by": "block_timestamp,asc",
            "min_timestamp": min_timestamp,
            "contract_address": self.usdt_contract,
        }
        if fingerprint:
            params["fingerprint"] = fingerprint

        headers = {"accept": "application/json"}
        if self.api_key:
            headers["TRON-PRO-API-KEY"] = self.api_key

        url = f"{self.api_base_url}/v1/accounts/{self.receiver_address}/transactions/trc20"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TronError(f"TRON Grid request failed: {exc}") from exc

        if payload.get("success") is False:
            raise TronError(str(payload))
        meta = payload.get("meta") or {}
        return list(payload.get("data") or []), meta.get("fingerprint")

    def _payment_from_transfer(self, transfer: dict[str, Any]) -> TronPayment | None:
        if transfer.get("type") and transfer.get("type") != "Transfer":
            return None
        if str(transfer.get("to") or "") != self.receiver_address:
            return None

        token_info = transfer.get("token_info") or {}
        contract_address = str(token_info.get("address") or transfer.get("contract_address") or "")
        if contract_address and contract_address != self.usdt_contract:
            return None

        try:
            decimals = int(token_info.get("decimals") or USDT_DECIMALS)
            amount = _raw_token_amount(transfer["value"], decimals)
            block_timestamp = int(transfer["block_timestamp"])
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise TronError(f"Invalid TRC20 transfer payload: {exc}") from exc

        tx_hash = str(transfer.get("transaction_id") or transfer.get("txID") or "")
        if not tx_hash:
            raise TronError("Invalid TRC20 transfer payload: missing transaction hash.")
        return TronPayment(tx_hash=tx_hash, amount=amount, block_timestamp=block_timestamp)

    def _safe_next_timestamp(
        self, *, min_timestamp: int, next_timestamp: int, saw_transfer: bool
    ) -> int:
        safe_upper = max(min_timestamp, int(time.time() * 1000) - self.scan_safety_window_ms)
        if not saw_transfer:
            return safe_upper
        return min(max(min_timestamp, next_timestamp), safe_upper)
