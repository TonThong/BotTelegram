from __future__ import annotations

import logging
from decimal import Decimal
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.binance_pay import BinancePayError, BinancePayHistoryClient
from app.bsc import BscError, BscUsdtScanner
from app.canboso_client import CanbosoClient, CanbosoError, Product
from app.config import Settings, load_settings
from app.database import Database, Order
from app.messages import delivery_message, h, order_brief, payment_amount_line, product_summary
from app.payment_amounts import amount_with_unique_fraction, base_usdt_amount, product_unit_usdt
from app.text_utils import format_usdt


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

STATE_QUANTITY = "quantity"
STATE_SLOT_EMAIL = "slot_email"
STATE_BINANCE_REFERENCE = "binance_reference"


def product_is_available(product: Product) -> bool:
    return product.available is None or product.available > 0


def product_has_quantity(product: Product, quantity: int) -> bool:
    return product.available is None or quantity <= product.available


def product_label(product: Product, settings: Settings) -> str:
    price = format_usdt(product_unit_usdt(product, markup_percent=settings.selling_markup_percent))
    suffix = f" - {price}"
    name_limit = max(8, 64 - len(suffix))
    name = product.name
    if len(name) > name_limit:
        name = f"{name[: name_limit - 3]}..."
    return f"{name}{suffix}"


def admin_username(settings: Settings) -> str:
    return settings.admin_usernames[0] if settings.admin_usernames else "shinbutchj"


def is_admin(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    if user is None:
        return False
    if user.id in settings.admin_user_ids:
        return True
    username = (user.username or "").lower()
    return username in settings.admin_usernames


def track_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return
    db: Database = context.application.bot_data["db"]
    db.upsert_user(
        telegram_user_id=user.id,
        telegram_chat_id=chat.id,
        username=user.username or "",
        full_name=user.full_name or "",
    )


def main_menu(settings: Settings | None = None) -> InlineKeyboardMarkup:
    contact_username = admin_username(settings) if settings else "shinbutchj"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Browse products", callback_data="products:0")],
            [InlineKeyboardButton("My orders", callback_data="orders")],
            [InlineKeyboardButton("Help", callback_data="help")],
            [InlineKeyboardButton("Contact admin", url=f"https://t.me/{contact_username}")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    settings: Settings = context.application.bot_data["settings"]
    contact_username = admin_username(settings)
    text = (
        "Welcome. Choose a product, select a payment method, and the bot will "
        "deliver your order after payment is confirmed.\n\n"
        f"Need help? Contact admin: @{contact_username}."
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu(settings))
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu(settings))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    await send_help(update, context)


async def send_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Payment help\n\n"
        "Binance ID: send the exact USDT amount, then paste the transaction reference here.\n\n"
        "USDT BEP20: send the exact amount to the shown BEP20 address. The bot checks the blockchain automatically.\n\n"
        "Use the exact amount shown for your order. It includes a small unique fraction for matching."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Back to menu", callback_data="menu")]])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard)


async def products_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    await show_products(update, context, page=0)


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    await show_orders(update, context)


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, *, page: int) -> None:
    canboso: CanbosoClient = context.application.bot_data["canboso"]
    settings: Settings = context.application.bot_data["settings"]
    try:
        products = [
            product for product in await canboso.list_products() if product_is_available(product)
        ]
    except CanbosoError as exc:
        await reply_or_edit(update, f"Could not load products: {h(exc)}", parse_mode=ParseMode.HTML)
        return

    if not products:
        await reply_or_edit(update, "No products are available right now.", reply_markup=main_menu())
        return

    page_size = 6
    page_count = max(1, (len(products) + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))
    chunk = products[page * page_size : (page + 1) * page_size]
    rows = [
        [
            InlineKeyboardButton(
                product_label(product, settings),
                callback_data=f"product:{product.product_id}",
            )
        ]
        for product in chunk
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("Previous", callback_data=f"products:{page - 1}"))
    if page < page_count - 1:
        nav.append(InlineKeyboardButton("Next", callback_data=f"products:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("Back to menu", callback_data="menu")])
    await reply_or_edit(
        update,
        f"Products ({page + 1}/{page_count})",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str) -> None:
    canboso: CanbosoClient = context.application.bot_data["canboso"]
    settings: Settings = context.application.bot_data["settings"]
    try:
        product = await canboso.get_product(product_id)
    except CanbosoError as exc:
        await reply_or_edit(update, f"Could not load product: {h(exc)}", parse_mode=ParseMode.HTML)
        return
    context.user_data["product"] = product
    rows = []
    if not product_is_available(product):
        rows.append([InlineKeyboardButton("Back to products", callback_data="products:0")])
        await reply_or_edit(
            update,
            f"{product_summary(product, unit_usdt=product_unit_usdt(product, markup_percent=settings.selling_markup_percent))}\n\nThis product is currently out of stock.",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
        )
        return
    if product.is_slot_product:
        for duration in product.slot_durations or [1]:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{duration} month(s)",
                        callback_data=f"slot_months:{duration}",
                    )
                ]
            )
    else:
        quick_buttons = [
            InlineKeyboardButton(f"Buy {qty}", callback_data=f"qty:{qty}")
            for qty in (1, 2, 5)
            if product_has_quantity(product, qty)
        ]
        if quick_buttons:
            rows.append(quick_buttons)
        if product.available is None or product.available > 1:
            rows.append([InlineKeyboardButton("Enter quantity", callback_data="qty:custom")])
    rows.append([InlineKeyboardButton("Back to products", callback_data="products:0")])
    await reply_or_edit(
        update,
        product_summary(
            product,
            unit_usdt=product_unit_usdt(product, markup_percent=settings.selling_markup_percent),
        ),
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def ask_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    context.user_data["flow_state"] = STATE_QUANTITY
    await update.callback_query.edit_message_text("Enter the quantity you want to buy.")


async def receive_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        quantity = int((update.message.text or "").strip())
    except ValueError:
        await update.message.reply_text("Please enter a whole number.")
        return
    if quantity <= 0 or quantity > 100:
        await update.message.reply_text("Quantity must be between 1 and 100.")
        return
    product: Product | None = context.user_data.get("product")
    if product and not product_has_quantity(product, quantity):
        await update.message.reply_text(f"Only {product.available} item(s) are available.")
        return
    context.user_data.pop("flow_state", None)
    await choose_payment(update, context, quantity=quantity, slot_months=None, customer_email=None)


async def select_slot_months(update: Update, context: ContextTypes.DEFAULT_TYPE, months: int) -> None:
    product: Product | None = context.user_data.get("product")
    if product is None:
        await update.callback_query.answer("Please select a product again.", show_alert=True)
        return
    context.user_data["slot_months"] = months
    context.user_data["flow_state"] = STATE_SLOT_EMAIL
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Enter the customer email for the workspace invite.")


async def receive_slot_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    email = (update.message.text or "").strip()
    if "@" not in email or "." not in email:
        await update.message.reply_text("Please enter a valid email address.")
        return
    context.user_data.pop("flow_state", None)
    await choose_payment(
        update,
        context,
        quantity=1,
        slot_months=int(context.user_data["slot_months"]),
        customer_email=email,
    )


async def choose_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    quantity: int,
    slot_months: int | None,
    customer_email: str | None,
) -> None:
    product: Product | None = context.user_data.get("product")
    if product is None:
        await update.effective_message.reply_text("Please select a product again.")
        return
    if not product_has_quantity(product, quantity):
        await update.effective_message.reply_text("This quantity is no longer available.")
        return

    try:
        settings: Settings = context.application.bot_data["settings"]
        base_amount = base_usdt_amount(
            product,
            quantity=quantity,
            slot_months=slot_months,
            markup_percent=settings.selling_markup_percent,
        )
    except CanbosoError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    context.user_data["draft_order"] = {
        "product_id": product.product_id,
        "product_name": product.name,
        "quantity": quantity,
        "slot_months": slot_months,
        "customer_email": customer_email,
        "base_amount_usdt": str(base_amount),
    }

    settings: Settings = context.application.bot_data["settings"]
    rows: list[list[InlineKeyboardButton]] = []
    if settings.binance_id_enabled:
        rows.append([InlineKeyboardButton("Pay with Binance ID", callback_data="pay:binance_id")])
    if settings.usdt_bep20_enabled:
        rows.append([InlineKeyboardButton("Pay with USDT BEP20", callback_data="pay:usdt_bep20")])
    rows.append([InlineKeyboardButton("Back to products", callback_data="products:0")])

    if len(rows) == 1:
        await update.effective_message.reply_text(
            "No payment method is configured yet. Please contact support."
        )
        return

    label = "1 unit" if quantity == 1 else f"{quantity} units"
    if slot_months:
        label = f"{slot_months} month(s)"
    await update.effective_message.reply_text(
        f"Selected: {product.name}\nQuantity: {label}\nPrice: {format_usdt(base_amount)}\n\nChoose a payment method.",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def create_payment_order(
    update: Update, context: ContextTypes.DEFAULT_TYPE, payment_method: str
) -> None:
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get("draft_order")
    if not draft:
        await query.edit_message_text("Your selection expired. Please choose a product again.")
        context.user_data.pop("flow_state", None)
        return

    settings: Settings = context.application.bot_data["settings"]
    db: Database = context.application.bot_data["db"]
    canboso: CanbosoClient = context.application.bot_data["canboso"]
    bsc_scanner: BscUsdtScanner | None = context.application.bot_data.get("bsc_scanner")
    try:
        current_product = await canboso.get_product(draft["product_id"])
    except CanbosoError as exc:
        await query.edit_message_text(f"Could not verify stock: {h(exc)}", parse_mode=ParseMode.HTML)
        return
    if not product_has_quantity(current_product, int(draft["quantity"])):
        await query.edit_message_text(
            "This product is out of stock now. Please choose another product.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back to products", callback_data="products:0")]]
            ),
        )
        return
    try:
        refreshed_base_amount = base_usdt_amount(
            current_product,
            quantity=int(draft["quantity"]),
            slot_months=draft["slot_months"],
            markup_percent=settings.selling_markup_percent,
        )
    except CanbosoError as exc:
        await query.edit_message_text(str(exc))
        return
    draft["product_name"] = current_product.name
    draft["base_amount_usdt"] = str(refreshed_base_amount)
    user = update.effective_user
    start_block = None
    if payment_method == "usdt_bep20" and bsc_scanner is not None:
        try:
            start_block = max(0, await bsc_scanner.current_block() - settings.bsc_confirmations)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch current BSC block: %s", exc)

    seed_order = db.create_order(
        telegram_user_id=user.id,
        telegram_chat_id=update.effective_chat.id,
        username=user.username or user.full_name or "",
        product_id=draft["product_id"],
        product_name=draft["product_name"],
        quantity=int(draft["quantity"]),
        slot_months=draft["slot_months"],
        customer_email=draft["customer_email"],
        amount_usdt=Decimal(draft["base_amount_usdt"]),
        base_amount_usdt=Decimal(draft["base_amount_usdt"]),
        payment_method=payment_method,
        expiry_minutes=settings.payment_expiry_minutes,
        start_block=start_block,
    )
    amount = amount_with_unique_fraction(
        seed_order.base_amount_usdt,
        order_seed=seed_order.id,
        max_fraction=settings.payment_unique_fraction_max_usdt,
    )
    update_order_amount(db, seed_order.id, amount)
    order = db.get_order(seed_order.id)

    if payment_method == "binance_id":
        if not settings.binance_history_enabled:
            extra = (
                "\n\nAutomatic reference checking needs Binance Pay history API credentials. "
                "This order was created, but verification is not active yet."
            )
        else:
            extra = "\n\nAfter sending, paste the transaction reference in this chat."
        await query.edit_message_text(
            "\n".join(
                [
                    f"<b>Order #{order.id}</b>",
                    payment_amount_line(order.amount_usdt),
                    f"<b>Binance ID:</b> <code>{h(settings.binance_pay_id)}</code>",
                    f"Expires: {h(order.expires_at.strftime('%Y-%m-%d %H:%M UTC'))}",
                    extra,
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
        context.user_data["awaiting_binance_order_id"] = order.id
        context.user_data["flow_state"] = STATE_BINANCE_REFERENCE
        return

    await query.edit_message_text(
        "\n".join(
            [
                f"<b>Order #{order.id}</b>",
                payment_amount_line(order.amount_usdt),
                f"<b>Network:</b> BNB Smart Chain (BEP20)",
                f"<b>Address:</b> <code>{h(settings.usdt_bep20_receiver_address)}</code>",
                f"Expires: {h(order.expires_at.strftime('%Y-%m-%d %H:%M UTC'))}",
                "",
                "The bot will confirm this automatically after enough block confirmations.",
            ]
        ),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Check payment", callback_data=f"check_usdt:{order.id}")]]
        ),
        parse_mode=ParseMode.HTML,
    )
    context.user_data.pop("flow_state", None)


def update_order_amount(db: Database, order_id: int, amount: Decimal) -> None:
    with db.connect() as con:
        con.execute("UPDATE orders SET amount_usdt = ? WHERE id = ?", (str(amount), order_id))


async def receive_binance_reference(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    db: Database = context.application.bot_data["db"]
    reference = (update.message.text or "").strip()
    order_id = context.user_data.get("awaiting_binance_order_id")
    if not order_id:
        await update.message.reply_text("No Binance payment is waiting for a reference.")
        context.user_data.pop("flow_state", None)
        return
    order = db.get_order(int(order_id))
    if order.is_expired:
        db.mark_expired(order.id)
        await update.message.reply_text("This order expired. Please create a new order.")
        context.user_data.pop("flow_state", None)
        return
    if not settings.binance_history_enabled:
        await update.message.reply_text(
            "Reference checking is not configured yet. Please contact support."
        )
        return

    verifier: BinancePayHistoryClient = context.application.bot_data["binance_pay"]
    await update.message.reply_text("Checking the payment reference...")
    try:
        match = await verifier.verify_reference(
            order=order,
            reference=reference,
            tolerance=settings.payment_amount_tolerance_usdt,
            receiver_binance_id=settings.binance_pay_id,
        )
    except BinancePayError as exc:
        logger.warning("Binance verification failed: %s", exc)
        await update.message.reply_text("Could not verify this reference right now. Try again shortly.")
        return

    if match is None:
        await update.message.reply_text(
            "Payment was not found. Check the reference and exact amount, then send it again."
        )
        return

    paid_order = db.mark_paid(order.id, payment_reference=match.transaction_id)
    await fulfill_and_notify(context, paid_order)
    context.user_data.pop("awaiting_binance_order_id", None)
    context.user_data.pop("flow_state", None)


async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    orders = db.list_recent_orders(update.effective_user.id)
    if not orders:
        await reply_or_edit(update, "You have no orders yet.", reply_markup=main_menu())
        return
    text = "\n\n".join(order_brief(order) for order in orders)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Back to menu", callback_data="menu")]])
    await reply_or_edit(update, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def fulfill_and_notify(context: ContextTypes.DEFAULT_TYPE, order: Order) -> None:
    canboso: CanbosoClient = context.application.bot_data["canboso"]
    db: Database = context.application.bot_data["db"]
    try:
        payload = await canboso.purchase(
            product_id=order.product_id,
            quantity=order.quantity,
            customer_email=order.customer_email,
            slot_months=order.slot_months,
        )
    except CanbosoError as exc:
        db.mark_failed(order.id, {"success": False, "message": str(exc)})
        await context.bot.send_message(
            chat_id=order.telegram_chat_id,
            text=(
                f"Payment confirmed for order #{order.id}, but supplier fulfillment failed. "
                "Support has been notified."
            ),
        )
        logger.exception("Canboso fulfillment failed for order %s: %s", order.id, exc)
        return

    fulfilled = db.mark_fulfilled(order.id, payload)
    await context.bot.send_message(
        chat_id=order.telegram_chat_id,
        text=delivery_message(fulfilled, payload),
        parse_mode=ParseMode.HTML,
    )


async def check_usdt_order(context: ContextTypes.DEFAULT_TYPE, order: Order) -> bool:
    settings: Settings = context.application.bot_data["settings"]
    scanner: BscUsdtScanner | None = context.application.bot_data.get("bsc_scanner")
    if scanner is None:
        return False
    db: Database = context.application.bot_data["db"]
    payment, next_block = await scanner.find_payment(
        order=order,
        tolerance=settings.payment_amount_tolerance_usdt,
    )
    db.set_last_checked_block(order.id, next_block)
    if payment is None:
        return False
    paid_order = db.mark_paid(order.id, tx_hash=payment.tx_hash)
    await fulfill_and_notify(context, paid_order)
    return True


async def poll_usdt_payments(context: ContextTypes.DEFAULT_TYPE) -> None:
    scanner: BscUsdtScanner | None = context.application.bot_data.get("bsc_scanner")
    if scanner is None:
        return
    db: Database = context.application.bot_data["db"]
    for order in db.pending_orders("usdt_bep20"):
        try:
            found = await check_usdt_order(context, order)
        except (BscError, Exception) as exc:  # noqa: BLE001
            logger.warning("USDT scan failed for order %s: %s", order.id, exc)
            continue
        if found:
            continue
        if order.is_expired:
            db.mark_expired(order.id)
            await context.bot.send_message(
                chat_id=order.telegram_chat_id,
                text=f"Order #{order.id} expired before payment was confirmed.",
            )


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    db: Database = context.application.bot_data["db"]
    pending = [
        order
        for order in db.pending_orders("usdt_bep20")
        if order.telegram_user_id == update.effective_user.id
    ]
    if not pending:
        await update.message.reply_text("No pending USDT BEP20 orders were found.")
        return

    confirmed = 0
    for order in pending:
        try:
            if await check_usdt_order(context, order):
                confirmed += 1
        except (BscError, Exception) as exc:  # noqa: BLE001
            logger.warning("Manual USDT scan failed for order %s: %s", order.id, exc)
            await update.message.reply_text("Payment checking is temporarily unavailable.")
            return

    if confirmed == 0:
        await update.message.reply_text(
            "No confirmed payment was found yet. If you just paid, wait for confirmations and try again."
        )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    settings: Settings = context.application.bot_data["settings"]
    if not is_admin(update, settings):
        await update.message.reply_text("This command is only available to the admin.")
        return

    text = (update.message.text or "").partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("Usage: /broadcast your message")
        return

    db: Database = context.application.bot_data["db"]
    users = db.list_broadcast_users()
    sent = 0
    blocked = 0
    failed = 0
    for user in users:
        try:
            await context.bot.send_message(chat_id=user.telegram_chat_id, text=text)
            sent += 1
        except Forbidden:
            db.mark_user_blocked(user.telegram_user_id)
            blocked += 1
        except TelegramError as exc:
            logger.warning("Broadcast failed for user %s: %s", user.telegram_user_id, exc)
            failed += 1

    await update.message.reply_text(
        f"Broadcast finished.\nSent: {sent}\nBlocked: {blocked}\nFailed: {failed}"
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    query = update.callback_query
    data = query.data or ""
    if data == "menu":
        context.user_data.pop("flow_state", None)
        await query.answer()
        await start(update, context)
        return
    if data == "help":
        context.user_data.pop("flow_state", None)
        await query.answer()
        await send_help(update, context)
        return
    if data == "orders":
        context.user_data.pop("flow_state", None)
        await query.answer()
        await show_orders(update, context)
        return
    if data.startswith("products:"):
        context.user_data.pop("flow_state", None)
        await query.answer()
        await show_products(update, context, page=int(data.split(":", 1)[1]))
        return
    if data.startswith("product:"):
        context.user_data.pop("flow_state", None)
        await query.answer()
        await show_product(update, context, data.split(":", 1)[1])
        return
    if data == "qty:custom":
        await ask_quantity(update, context)
        return
    if data.startswith("qty:"):
        await query.answer()
        context.user_data.pop("flow_state", None)
        await choose_payment(
            update,
            context,
            quantity=int(data.split(":", 1)[1]),
            slot_months=None,
            customer_email=None,
        )
        return
    if data.startswith("slot_months:"):
        await select_slot_months(update, context, int(data.split(":", 1)[1]))
        return
    if data == "pay:binance_id":
        await create_payment_order(update, context, "binance_id")
        return
    if data == "pay:usdt_bep20":
        await create_payment_order(update, context, "usdt_bep20")
        return
    if data.startswith("check_usdt:"):
        await query.answer()
        db: Database = context.application.bot_data["db"]
        order = db.get_order(int(data.split(":", 1)[1]))
        if order.telegram_user_id != update.effective_user.id:
            await query.answer("This order does not belong to you.", show_alert=True)
            return
        if order.status != "awaiting_payment":
            await query.edit_message_text(order_brief(order), parse_mode=ParseMode.HTML)
            return
        try:
            found = await check_usdt_order(context, order)
        except (BscError, Exception) as exc:  # noqa: BLE001
            logger.warning("Manual USDT scan failed for order %s: %s", order.id, exc)
            await query.answer("Payment checking is temporarily unavailable.", show_alert=True)
            return
        if not found:
            await query.answer("No confirmed payment found yet.", show_alert=True)
        return
    await query.answer()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update, context)
    state = context.user_data.get("flow_state")
    if state == STATE_QUANTITY:
        await receive_quantity(update, context)
        return
    if state == STATE_SLOT_EMAIL:
        await receive_slot_email(update, context)
        return
    if state == STATE_BINANCE_REFERENCE:
        await receive_binance_reference(update, context)
        return
    await update.message.reply_text("Use the menu or /products to start an order.")


async def reply_or_edit(
    update: Update,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


def build_application(settings: Settings) -> Application:
    db = Database(settings.database_path)
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["db"] = db
    app.bot_data["canboso"] = CanbosoClient(settings.canboso_base_url, settings.canboso_api_key)
    if settings.binance_history_enabled:
        app.bot_data["binance_pay"] = BinancePayHistoryClient(
            base_url=settings.binance_pay_history_base_url,
            api_key=settings.binance_pay_history_api_key,
            api_secret=settings.binance_pay_history_api_secret,
        )
    if settings.usdt_bep20_enabled:
        app.bot_data["bsc_scanner"] = BscUsdtScanner(
            rpc_url=settings.bsc_rpc_url,
            rpc_fallback_urls=settings.bsc_rpc_fallback_urls,
            usdt_contract=settings.bsc_usdt_contract,
            receiver_address=settings.usdt_bep20_receiver_address,
            confirmations=settings.bsc_confirmations,
            log_chunk_size=settings.bsc_log_chunk_size,
        )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("products", products_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if settings.usdt_bep20_enabled:
        app.job_queue.run_repeating(
            poll_usdt_payments,
            interval=settings.payment_poll_interval_seconds,
            first=10,
            name="poll_usdt_payments",
        )
    return app


def run() -> None:
    settings = load_settings()
    application = build_application(settings)
    logger.info("Bot is starting.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run()
