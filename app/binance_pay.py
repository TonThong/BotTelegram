from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import Decimal
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

    async def get_pay_transactions(
        self, *, start_time_ms: int, end_time_ms: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": limit,
            "timestamp": int(time.time() * 1000),
            "recvWindow": 60000,
        }
        query = urlencode(params)
        signature = hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{self.base_url}/sapi/v1/pay/transactions?{query}&signature={signature}"
        async with httpx.AsyncClient(timeout=30) as client:
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
        expected = order.amount_usdt
        normalized_reference = reference.strip()
        for item in transactions:
            transaction_id = str(item.get("transactionId") or "").strip()
            if transaction_id != normalized_reference:
                continue
            currency = str(item.get("currency") or "").upper()
            amount = Decimal(str(item.get("amount") or "0"))
            transaction_time = int(item.get("transactionTime") or 0)
            if currency != "USDT":
                continue
            if amount <= 0:
                continue
            receiver_info = item.get("receiverInfo") or {}
            returned_receiver_id = str(receiver_info.get("binanceId") or "").strip()
            if receiver_binance_id and returned_receiver_id and returned_receiver_id != receiver_binance_id:
                continue
            if transaction_time < created_ms:
                continue
            if abs(amount - expected) > tolerance:
                continue
            return BinancePaymentMatch(
                transaction_id=transaction_id,
                amount=amount,
                currency=currency,
                transaction_time_ms=transaction_time,
            )
        return None
