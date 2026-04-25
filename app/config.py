from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _get_decimal(name: str, default: str) -> Decimal:
    value = os.getenv(name)
    if not value:
        return Decimal(default)
    return Decimal(value)


def _get_csv(name: str, default: str = "") -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    canboso_api_key: str
    canboso_base_url: str
    database_path: Path
    admin_usernames: tuple[str, ...]
    admin_user_ids: tuple[int, ...]
    binance_pay_id: str
    binance_pay_history_api_key: str
    binance_pay_history_api_secret: str
    binance_pay_history_base_url: str
    usdt_bep20_receiver_address: str
    usdt_trc20_receiver_address: str
    bsc_rpc_url: str
    bsc_rpc_fallback_urls: tuple[str, ...]
    bsc_usdt_contract: str
    bsc_confirmations: int
    bsc_log_chunk_size: int
    tron_grid_base_url: str
    tron_grid_api_key: str
    tron_usdt_contract: str
    tron_page_limit: int
    tron_scan_safety_window_seconds: int
    selling_markup_percent: Decimal
    payment_expiry_minutes: int
    payment_poll_interval_seconds: int
    payment_amount_tolerance_usdt: Decimal
    payment_unique_fraction_max_usdt: Decimal

    @property
    def binance_id_enabled(self) -> bool:
        return bool(self.binance_pay_id)

    @property
    def binance_history_enabled(self) -> bool:
        return bool(
            self.binance_pay_history_api_key and self.binance_pay_history_api_secret
        )

    @property
    def usdt_bep20_enabled(self) -> bool:
        return bool(self.usdt_bep20_receiver_address)

    @property
    def usdt_trc20_enabled(self) -> bool:
        return bool(self.usdt_trc20_receiver_address)


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    canboso_key = os.getenv("CANBOSO_API_KEY", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")
    if not canboso_key:
        raise RuntimeError("CANBOSO_API_KEY is required.")

    return Settings(
        telegram_bot_token=token,
        canboso_api_key=canboso_key,
        canboso_base_url=os.getenv("CANBOSO_BASE_URL", "https://canboso.com").rstrip("/"),
        database_path=Path(os.getenv("DATABASE_PATH", "bot.sqlite3")),
        admin_usernames=tuple(
            username.lower().lstrip("@")
            for username in _get_csv("ADMIN_USERNAMES", "shinbutchj")
        ),
        admin_user_ids=tuple(
            int(user_id) for user_id in _get_csv("ADMIN_USER_IDS", "") if user_id.isdigit()
        ),
        binance_pay_id=os.getenv("BINANCE_PAY_ID", "").strip(),
        binance_pay_history_api_key=os.getenv("BINANCE_PAY_HISTORY_API_KEY", "").strip(),
        binance_pay_history_api_secret=os.getenv(
            "BINANCE_PAY_HISTORY_API_SECRET", ""
        ).strip(),
        binance_pay_history_base_url=os.getenv(
            "BINANCE_PAY_HISTORY_BASE_URL", "https://api.binance.com"
        ).rstrip("/"),
        usdt_bep20_receiver_address=os.getenv("USDT_BEP20_RECEIVER_ADDRESS", "").strip(),
        usdt_trc20_receiver_address=os.getenv("USDT_TRC20_RECEIVER_ADDRESS", "").strip(),
        bsc_rpc_url=os.getenv("BSC_RPC_URL", "https://bsc-rpc.publicnode.com").strip(),
        bsc_rpc_fallback_urls=_get_csv(
            "BSC_RPC_FALLBACK_URLS",
            "https://bsc.drpc.org,https://1rpc.io/bnb,https://bsc-rpc.publicnode.com",
        ),
        bsc_usdt_contract=os.getenv(
            "BSC_USDT_CONTRACT", "0x55d398326f99059fF775485246999027B3197955"
        ).strip(),
        bsc_confirmations=_get_int("BSC_CONFIRMATIONS", 8),
        bsc_log_chunk_size=_get_int("BSC_LOG_CHUNK_SIZE", 200),
        tron_grid_base_url=os.getenv("TRON_GRID_BASE_URL", "https://api.trongrid.io").strip(),
        tron_grid_api_key=os.getenv("TRON_GRID_API_KEY", "").strip(),
        tron_usdt_contract=os.getenv(
            "TRON_USDT_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        ).strip(),
        tron_page_limit=_get_int("TRON_PAGE_LIMIT", 200),
        tron_scan_safety_window_seconds=_get_int("TRON_SCAN_SAFETY_WINDOW_SECONDS", 180),
        selling_markup_percent=_get_decimal("SELLING_MARKUP_PERCENT", "25"),
        payment_expiry_minutes=_get_int("PAYMENT_EXPIRY_MINUTES", 45),
        payment_poll_interval_seconds=_get_int("PAYMENT_POLL_INTERVAL_SECONDS", 45),
        payment_amount_tolerance_usdt=_get_decimal(
            "PAYMENT_AMOUNT_TOLERANCE_USDT", "0.000001"
        ),
        payment_unique_fraction_max_usdt=_get_decimal(
            "PAYMENT_UNIQUE_FRACTION_MAX_USDT", "0.009999"
        ),
    )
