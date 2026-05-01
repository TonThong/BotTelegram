# Telegram Product Bot

This project builds an English-language Telegram shop bot that:

- lists products from the Canboso buyer API or local `products/*.txt` files;
- accepts Binance ID, USDT BEP20, and USDT TRC20 payment flows;
- automatically checks Binance ID payments through Binance Pay history;
- automatically checks USDT BEP20 transfers on BNB Smart Chain and USDT TRC20 transfers on TRON;
- purchases from Canboso after payment is confirmed;
- stores active payment requests and retained order history in SQLite.

## Product Sources

If `PRODUCT_SOURCE=auto`, the bot uses both Canboso API products and local
`products/*.txt` files when both are configured. Set `PRODUCT_SOURCE=api` to force
the live API, `PRODUCT_SOURCE=local` to force local files, or `PRODUCT_SOURCE=hybrid`
to always load both.

Each local `.txt` file is one product:

```text
id: cdk_gpt_plus_1m_no_war
name: CDK GPT PLUS 1M (No War)
price: 20.00
currency: USDT

description:
ChatGPT Plus CDK valid for 1 month.

data:
```

After payment is confirmed, local fulfillment delivers the first item from `data:`
and moves it to `sold:` so it is not sold again. Local availability is counted from
the number of non-empty lines under `data:`; one line equals one sellable unit.
Local `price:` is the selling price shown to buyers; `SELLING_MARKUP_PERCENT` only
applies to live API products.

## API Sources

Canboso Swagger exposes:

- `GET /api/telegram-buyer/products?key=...`
- `GET /api/telegram-buyer/balance?key=...`
- `POST /api/telegram-buyer/purchase`

The bot uses those endpoints only through `app/canboso_client.py`.

References:

- Canboso Swagger: https://canboso.com/api/swagger/
- Binance Pay history: https://developers.binance.com/docs/pay/rest-api/Get-Pay-Trade-History
- Binance Pay request signing: https://developers.binance.com/docs/binance-pay/api-common

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

The local `.env` file has already been created. Fill these before accepting real payments:

- `BINANCE_PAY_ID`: the Binance ID buyers should send to.
- `BINANCE_PAY_HISTORY_API_KEY` and `BINANCE_PAY_HISTORY_API_SECRET`: required for automatic Binance ID checks.
- `USDT_BEP20_RECEIVER_ADDRESS`: the BEP20 wallet address buyers should send USDT to.
- `BSC_RPC_URL`: use a reliable RPC provider in production. The Binance dataseed RPC often rejects `eth_getLogs`, so the default uses PublicNode with fallback URLs.
- `USDT_TRC20_RECEIVER_ADDRESS`: the TRC20 wallet address buyers should send USDT to.
- `TRON_GRID_API_KEY`: optional TronGrid API key. The bot can use the public API, but a key is recommended for production rate limits.
- `SELLING_MARKUP_PERCENT`: markup added to the Canboso source USD price before showing USDT prices and creating payment amounts. Default is `25`.
- `ADMIN_USERNAMES`: comma-separated Telegram usernames allowed to broadcast. Default is `shinbutchj`.
- `ADMIN_USER_IDS`: optional comma-separated Telegram numeric user IDs for stricter admin access.

## Payment Flow

For every order, the bot calculates a USDT amount from the Canboso source price plus the configured markup, then adds a tiny unique fraction. This makes automatic matching safer, especially for on-chain USDT transfers where there is no memo field.

Binance ID:

1. The bot shows the Binance Pay ID and exact amount.
2. The buyer sends the exact USDT amount.
3. A background job queries `/sapi/v1/pay/transactions`.
4. If currency, amount, receiver, and time match, the order is fulfilled automatically. The buyer does not need to paste a transaction ID.

USDT BEP20:

1. The bot shows the BEP20 receiving address and exact amount.
2. A background job scans USDT `Transfer` logs to the receiving address.
3. After the configured number of confirmations, the bot fulfills the order.

USDT TRC20:

1. The bot shows the TRC20 receiving address and exact amount.
2. A background job queries TronGrid for confirmed USDT TRC20 transfers to the receiving address.
3. When the exact unique amount is found, the bot fulfills the order.

## Important Notes

- Canboso purchases are wallet purchases. Your Canboso buyer key must have enough wallet balance for orders after customer payment is confirmed.
- Binance ID verification needs Binance API credentials with access to Pay history. A Binance ID alone is not enough for automatic verification.
- Finished, expired, and failed orders stay in the `orders` table for audit/history, but buyer-facing commands only check active pending requests.
- Keep `.env` private. It is ignored by git.
- If a bot token was shared in chat or screenshots, rotate it in BotFather before production.

## Commands

- `/start`: open the main menu
- `/products`: browse products
- `/check`: check pending Binance ID and USDT BEP20/TRC20 orders immediately
- `/help`: show payment help
- `/broadcast message`: admin only, start a background plain-text broadcast to all users who have interacted with the bot
