from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx

from app.database import Order


class BinancePayError(RuntimeError):
    pass


@dataclass(frozen=True)
class BinancePaymentMatch:
    transaction_id: str
    amount: Decimal
    currency: str
    transaction_time_ms: int


class BinancePayHistoryClient:
    def __init__(self, *, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self._time_offset_ms = 0
        self._time_offset_checked_at: float | None = None
        self._time_offset_ttl_seconds = 300

    async def get_pay_transactions(
        self, *, start_time_ms: int, end_time_ms: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30) as client:
            timestamp_ms = await self._signed_timestamp_ms(client)
            adjusted_start_time_ms = self.to_binance_time_ms(start_time_ms)
            adjusted_end_time_ms = min(self.to_binance_time_ms(end_time_ms), timestamp_ms)
            if adjusted_start_time_ms > adjusted_end_time_ms:
                adjusted_start_time_ms = adjusted_end_time_ms
            params: dict[str, Any] = {
                "startTime": adjusted_start_time_ms,
                "endTime": adjusted_end_time_ms,
                "limit": limit,
                "timestamp": timestamp_ms,
                "recvWindow": 60000,
            }
            query = urlencode(params)
            signature = hmac.new(
                self.api_secret, query.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            url = f"{self.base_url}/sapi/v1/pay/transactions?{query}&signature={signature}"
            response = await client.get(url, headers={"X-MBX-APIKEY": self.api_key})
        try:
            payload = response.json()
        except ValueError as exc:
            raise BinancePayError(response.text) from exc
        if response.status_code >= 400:
            raise BinancePayError(str(payload.get("msg") or payload.get("message") or payload))
        if not payload.get("success", False):
            raise BinancePayError(str(payload.get("message") or payload))
        return list(payload.get("data") or [])

    def to_binance_time_ms(self, local_time_ms: int) -> int:
        return local_time_ms + self._time_offset_ms

    async def _signed_timestamp_ms(self, client: httpx.AsyncClient) -> int:
        now = time.monotonic()
        if (
            self._time_offset_checked_at is None
            or now - self._time_offset_checked_at >= self._time_offset_ttl_seconds
        ):
            await self._sync_time_offset(client)
        return self.to_binance_time_ms(int(time.time() * 1000))

    async def _sync_time_offset(self, client: httpx.AsyncClient) -> None:
        request_started_ms = int(time.time() * 1000)
        response = await client.get(f"{self.base_url}/api/v3/time")
        request_finished_ms = int(time.time() * 1000)
        try:
            payload = response.json()
            server_time_ms = int(payload["serverTime"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BinancePayError(f"Could not read Binance server time: {response.text}") from exc
        if response.status_code >= 400:
            if isinstance(payload, dict):
                message = payload.get("msg") or payload.get("message") or payload
            else:
                message = payload
            raise BinancePayError(str(message))
        local_midpoint_ms = (request_started_ms + request_finished_ms) // 2
        self._time_offset_ms = server_time_ms - local_midpoint_ms
        self._time_offset_checked_at = time.monotonic()

    async def verify_reference(
        self,
        *,
        order: Order,
        reference: str,
        tolerance: Decimal,
        receiver_binance_id: str | None = None,
    ) -> BinancePaymentMatch | None:
        created_ms = int(order.created_at.timestamp() * 1000) - 60_000
        now_ms = int(time.time() * 1000)
        transactions = await self.get_pay_transactions(
            start_time_ms=created_ms,
            end_time_ms=now_ms,
            limit=100,
        )
        normalized_reference = reference.strip()
        if not normalized_reference:
            return None
        return self.match_transactions(
            order=order,
            transactions=transactions,
            tolerance=tolerance,
            receiver_binance_id=receiver_binance_id,
            transaction_id=normalized_reference,
        )

    async def find_payment(
        self,
        *,
        order: Order,
        tolerance: Decimal,
        receiver_binance_id: str | None = None,
    ) -> BinancePaymentMatch | None:
        created_ms = int(order.created_at.timestamp() * 1000) - 60_000
        now_ms = int(time.time() * 1000)
        transactions = await self.get_pay_transactions(
            start_time_ms=created_ms,
            end_time_ms=now_ms,
            limit=100,
        )
        return self.match_transactions(
            order=order,
            transactions=transactions,
            tolerance=tolerance,
            receiver_binance_id=receiver_binance_id,
        )

    def match_transactions(
        self,
        *,
        order: Order,
        transactions: list[dict[str, Any]],
        tolerance: Decimal,
        receiver_binance_id: str | None = None,
        transaction_id: str | None = None,
    ) -> BinancePaymentMatch | None:
        created_ms = self.to_binance_time_ms(int(order.created_at.timestamp() * 1000)) - 60_000
        expected = order.amount_usdt
        normalized_transaction_id = (transaction_id or "").strip()
        normalized_receiver_id = (receiver_binance_id or "").strip()
        for item in transactions:
            item_transaction_id = str(item.get("transactionId") or "").strip()
            if not item_transaction_id:
                continue
            if normalized_transaction_id and item_transaction_id != normalized_transaction_id:
                continue
            currency = str(item.get("currency") or "").upper()
            try:
                amount = Decimal(str(item.get("amount") or "0"))
                transaction_time = int(item.get("transactionTime") or 0)
            except (InvalidOperation, TypeError, ValueError):
                continue
            if currency != "USDT":
                continue
            if amount <= 0:
                continue
            receiver_info = item.get("receiverInfo") or {}
            returned_receiver_ids = {
                str(receiver_info.get("binanceId") or "").strip(),
                str(receiver_info.get("accountId") or "").strip(),
            }
            returned_receiver_ids.discard("")
            if (
                normalized_receiver_id
                and returned_receiver_ids
                and normalized_receiver_id not in returned_receiver_ids
            ):
                continue
            if transaction_time < created_ms:
                continue
            if abs(amount - expected) > tolerance:
                continue
            return BinancePaymentMatch(
                transaction_id=item_transaction_id,
                amount=amount,
                currency=currency,
                transaction_time_ms=transaction_time,
            )
        return None
