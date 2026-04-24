from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from app.database import Order


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USDT_DECIMALS = Decimal("1000000000000000000")


class BscError(RuntimeError):
    pass


@dataclass(frozen=True)
class OnChainPayment:
    tx_hash: str
    amount: Decimal
    block_number: int


def _hex_quantity(value: int) -> str:
    return hex(value)


def _topic_address(address: str) -> str:
    normalized = address.lower().replace("0x", "")
    if len(normalized) != 40:
        raise BscError("Invalid BEP20 receiver address.")
    return "0x" + normalized.rjust(64, "0")


class BscUsdtScanner:
    def __init__(
        self,
        *,
        rpc_url: str,
        usdt_contract: str,
        receiver_address: str,
        confirmations: int,
        rpc_fallback_urls: Sequence[str] = (),
        log_chunk_size: int = 200,
    ):
        rpc_urls = [rpc_url, *rpc_fallback_urls]
        self.rpc_urls = tuple(dict.fromkeys(url for url in rpc_urls if url))
        self.usdt_contract = usdt_contract
        self.receiver_address = receiver_address
        self.confirmations = confirmations
        self.log_chunk_size = max(1, log_chunk_size)
        self._request_id = 0

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=30) as client:
            for rpc_url in self.rpc_urls:
                try:
                    response = await client.post(rpc_url, json=body)
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("error"):
                        raise BscError(str(payload["error"]))
                    return payload.get("result")
                except (httpx.HTTPError, ValueError, BscError) as exc:
                    last_error = exc
                    continue
        raise BscError(f"All BSC RPC URLs failed: {last_error}")

    async def current_block(self) -> int:
        return int(await self._rpc("eth_blockNumber", []), 16)

    async def find_payment(
        self,
        *,
        order: Order,
        tolerance: Decimal,
        max_block_span: int = 4_000,
    ) -> tuple[OnChainPayment | None, int]:
        latest = await self.current_block()
        confirmed_latest = latest - self.confirmations
        if confirmed_latest <= 0:
            return None, latest

        from_block = order.last_checked_block or order.start_block or max(0, confirmed_latest - 20_000)
        from_block = max(0, from_block)
        to_block = min(confirmed_latest, from_block + max_block_span)
        expected = order.amount_usdt

        cursor = from_block
        while cursor <= to_block:
            chunk_end = min(to_block, cursor + self.log_chunk_size - 1)
            logs = await self._get_transfer_logs(from_block=cursor, to_block=chunk_end)
            for log in logs:
                amount = Decimal(int(log["data"], 16)) / USDT_DECIMALS
                if abs(amount - expected) <= tolerance:
                    return (
                        OnChainPayment(
                            tx_hash=str(log["transactionHash"]),
                            amount=amount,
                            block_number=int(log["blockNumber"], 16),
                        ),
                        chunk_end + 1,
                    )
            cursor = chunk_end + 1
        return None, to_block + 1

    async def _get_transfer_logs(self, *, from_block: int, to_block: int) -> list[dict[str, Any]]:
        if to_block < from_block:
            return []
        params = [
            {
                "fromBlock": _hex_quantity(from_block),
                "toBlock": _hex_quantity(to_block),
                "address": self.usdt_contract,
                "topics": [
                    TRANSFER_TOPIC,
                    None,
                    _topic_address(self.receiver_address),
                ],
            }
        ]
        result = await self._rpc("eth_getLogs", params)
        return list(result or [])
