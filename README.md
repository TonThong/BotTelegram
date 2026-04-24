# Telegram Product Bot

This project builds an English-language Telegram shop bot that:

- lists products from the Canboso buyer API;
- accepts Binance ID and USDT BEP20 payment flows;
- automatically checks USDT BEP20 transfers on BNB Smart Chain;
- verifies Binance ID references through Binance Pay history when API credentials are configured;
- purchases from Canboso after payment is confirmed;
- stores orders in SQLite to prevent duplicate fulfillment.

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
- `BINANCE_PAY_HISTORY_API_KEY` and `BINANCE_PAY_HISTORY_API_SECRET`: required for Binance ID reference checks.
- `USDT_BEP20_RECEIVER_ADDRESS`: the BEP20 wallet address buyers should send USDT to.
- `BSC_RPC_URL`: use a reliable RPC provider in production. The Binance dataseed RPC often rejects `eth_getLogs`, so the default uses PublicNode with fallback URLs.
- `SELLING_MARKUP_PERCENT`: markup added to the Canboso source USD price before showing USDT prices and creating payment amounts. Default is `25`.
- `ADMIN_USERNAMES`: comma-separated Telegram usernames allowed to broadcast. Default is `shinbutchj`.
- `ADMIN_USER_IDS`: optional comma-separated Telegram numeric user IDs for stricter admin access.

## Payment Flow

For every order, the bot calculates a USDT amount from the Canboso source price plus the configured markup, then adds a tiny unique fraction. This makes automatic matching safer, especially for on-chain USDT transfers where there is no memo field.

Binance ID:

1. The bot shows the Binance Pay ID and exact amount.
2. The buyer sends USDT and pastes the transaction reference.
3. The bot queries `/sapi/v1/pay/transactions`.
4. If transaction ID, currency, amount, and time match, the order is fulfilled.

USDT BEP20:

1. The bot shows the BEP20 receiving address and exact amount.
2. A background job scans USDT `Transfer` logs to the receiving address.
3. After the configured number of confirmations, the bot fulfills the order.

## Important Notes

- Canboso purchases are wallet purchases. Your Canboso buyer key must have enough wallet balance for orders after customer payment is confirmed.
- Binance ID verification needs Binance API credentials with access to Pay history. A Binance ID alone is not enough for automatic verification.
- Keep `.env` private. It is ignored by git.
- If a bot token was shared in chat or screenshots, rotate it in BotFather before production.

## Commands

- `/start`: open the main menu
- `/products`: browse products
- `/orders`: view recent orders
- `/check`: check pending USDT BEP20 orders immediately
- `/help`: show payment help
- `/broadcast message`: admin only, send a plain-text message to all users who have interacted with the bot
