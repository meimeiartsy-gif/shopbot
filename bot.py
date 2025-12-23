import os
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from db import (
    ensure_schema, ensure_user, fetch_one, fetch_all, exec_sql,
    exec_sql_returning, set_setting, get_setting
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def money(n: int) -> str:
    return f"₱{n:,}"


async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass


# -------------------- MAIN MENU (CUSTOMERS HIDE ADMIN) --------------------
def build_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        ["🏠 Home", "🛍 Shop"],
        ["👤 My Account", "📩 Reseller Register"],
        ["💬 Chat Admin", "🆘 Help"],
    ]
    if is_admin(uid):
        rows.append(["🔐 Admin"])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


# ============================================================
# SETTINGS (TEXT)
# ============================================================
DEFAULT_TEXT = {
    "WELCOME_TEXT": "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
    "SHOP_CATS_TEXT": "🛍 **Shop Categories**",
    "SHOP_PRODS_TEXT": "📦 **Select a product:**",
    "SHOP_VARIANTS_TEXT": "🧾 **Choose a package:**",
    "NO_PRODUCTS_TEXT": "❌ No products yet in this category.",
    "NO_VARIANTS_TEXT": "❌ No packages yet.",
    "HELP_TEXT": (
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse\n"
        "• Buy = auto delivery\n"
        "• **Reseller Register** = apply for reseller\n"
    ),
    "CHAT_ADMIN_TEXT": "💬 Chat admin here:\n@lovebylunaa",
    "CHECKOUT_TEXT": "🧾 **Checkout**\nAdjust quantity then confirm:",
}


# ============================================================
# HOME / START
# ============================================================
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    welcome = get_setting("WELCOME_TEXT") or DEFAULT_TEXT["WELCOME_TEXT"]
    thumb = get_setting("START_THUMB_FILE_ID")

    msg = update.message if update.message else update.callback_query.message
    menu = build_menu(user.id)

    if thumb:
        await msg.reply_photo(photo=thumb, caption=welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=menu)
    else:
        await msg.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=menu)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)


async def home_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)


# ============================================================
# ACCOUNT / POINTS / BALANCE
# ============================================================
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    u = fetch_one(
        "SELECT user_id, username, joined_at, points, is_reseller, balance FROM users WHERE user_id=%s",
        (user.id,)
    )
    purchases = fetch_one("SELECT COALESCE(SUM(qty),0) AS c FROM purchases WHERE user_id=%s", (user.id,))
    count = int(purchases["c"]) if purchases else 0

    username = f"@{u['username']}" if u and u.get("username") else "(no username)"
    joined = u["joined_at"].strftime("%Y-%m-%d %H:%M") if u else "—"
    points = int(u["points"]) if u else 0
    reseller = "✅ YES" if u and u["is_reseller"] else "❌ NO"
    bal = int(u["balance"]) if u else 0

    text = (
        "👤 **My Account**\n\n"
        f"Username: **{username}**\n"
        f"User ID: `{user.id}`\n"
        f"Joined: **{joined}**\n"
        f"Purchases: **{count}**\n"
        f"Points: **{points}**\n"
        f"Balance: **{money(bal)}**\n"
        f"Reseller: **{reseller}**"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ============================================================
# HELP / CHAT ADMIN
# ============================================================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_setting("HELP_TEXT") or DEFAULT_TEXT["HELP_TEXT"]
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_setting("CHAT_ADMIN_TEXT") or DEFAULT_TEXT["CHAT_ADMIN_TEXT"]
    await update.message.reply_text(text)


# ============================================================
# SHOP UI (EDIT SAME MESSAGE; BACK DOESN'T SPAM CHAT)
# ============================================================
async def shop_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    header = get_setting("SHOP_CATS_TEXT") or DEFAULT_TEXT["SHOP_CATS_TEXT"]
    await update.message.reply_text(header, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))


async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    if action == "cat":
        cat_id = int(parts[2])
        context.user_data["shop_cat_id"] = cat_id

        prods = fetch_all("""
            SELECT id,name,description FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id
        """, (cat_id,))

        if not prods:
            await q.message.edit_text(
                get_setting("NO_PRODUCTS_TEXT") or DEFAULT_TEXT["NO_PRODUCTS_TEXT"],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="shop:back")]])
            )
            return

        header = get_setting("SHOP_PRODS_TEXT") or DEFAULT_TEXT["SHOP_PRODS_TEXT"]
        rows = [[InlineKeyboardButton(p["name"], callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:back")])

        await q.message.edit_text(header, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "prod":
        pid = int(parts[2])
        prod = fetch_one("SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not prod:
            await q.message.edit_text("Product not found.")
            return

        vars_ = fetch_all("""
            SELECT id,name,price,delivery_type FROM variants
            WHERE product_id=%s AND is_active=TRUE
            ORDER BY id
        """, (pid,))

        if not vars_:
            cat_id = context.user_data.get("shop_cat_id")
            back_cb = f"shop:cat:{cat_id}" if cat_id else "shop:back"
            await q.message.edit_text(
                get_setting("NO_VARIANTS_TEXT") or DEFAULT_TEXT["NO_VARIANTS_TEXT"],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data=back_cb)]])
            )
            return

        header = get_setting("SHOP_VARIANTS_TEXT") or DEFAULT_TEXT["SHOP_VARIANTS_TEXT"]

        rows = []
        for v in vars_:
            stock = fetch_one(
                "SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE",
                (v["id"],)
            )
            s = int(stock["c"]) if stock else 0
            rows.append([InlineKeyboardButton(
                f"{v['name']} — {money(int(v['price']))} (Stock: {s})",
                callback_data=f"cart:start:{v['id']}"
            )])

        cat_id = context.user_data.get("shop_cat_id")
        back_cb = f"shop:cat:{cat_id}" if cat_id else "shop:back"
        rows.append([InlineKeyboardButton("⬅ Back", callback_data=back_cb)])

        text = f"🧾 **{prod['name']}**\n\n{prod['description']}\n\n{header}"
        await q.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "back":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        header = get_setting("SHOP_CATS_TEXT") or DEFAULT_TEXT["SHOP_CATS_TEXT"]
        await q.message.edit_text(header, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return


# ============================================================
# CART / CHECKOUT
# ============================================================
def cart_kb(variant_id: int, qty: int, back_cb: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"cart:dec:{variant_id}"),
            InlineKeyboardButton(f"Qty: {qty}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"cart:inc:{variant_id}"),
        ],
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"cart:confirm:{variant_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cart:cancel:{variant_id}"),
        ],
        [InlineKeyboardButton("⬅ Back", callback_data=back_cb)],
    ])


async def cb_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    if action == "start":
        variant_id = int(parts[2])
        context.user_data["cart_qty"] = 1
        context.user_data["cart_variant_id"] = variant_id

    variant_id = int(parts[2])
    qty = int(context.user_data.get("cart_qty", 1))

    if action == "inc":
        qty += 1
    elif action == "dec":
        qty = max(1, qty - 1)
    elif action == "cancel":
        context.user_data.pop("cart_qty", None)
        context.user_data.pop("cart_variant_id", None)
        await q.message.edit_text("❌ Cancelled.")
        return

    context.user_data["cart_qty"] = qty

    v = fetch_one("""
        SELECT v.id, v.name, v.price, v.delivery_type,
               p.name AS product_name
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.message.edit_text("Package not found.")
        return

    stock = fetch_one("SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE", (variant_id,))
    s = int(stock["c"]) if stock else 0

    user_id = q.from_user.id
    u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user_id,))
    bal = int(u["balance"]) if u else 0

    total = int(v["price"]) * qty
    checkout_text = get_setting("CHECKOUT_TEXT") or DEFAULT_TEXT["CHECKOUT_TEXT"]

    cat_id = context.user_data.get("shop_cat_id")
    back_cb = f"shop:cat:{cat_id}" if cat_id else "shop:back"

    text = (
        f"{checkout_text}\n\n"
        f"**{v['product_name']} — {v['name']}**\n"
        f"Price each: **{money(int(v['price']))}**\n"
        f"Stock available: **{s}**\n"
        f"Your balance: **{money(bal)}**\n\n"
        f"Total: **{money(total)}**"
    )

    await q.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=cart_kb(variant_id, qty, back_cb))


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)


# ============================================================
# CONFIRM PURCHASE (ROLLBACK SAFE)
# ============================================================
async def cb_cart_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    variant_id = int(q.data.split(":")[2])
    qty = int(context.user_data.get("cart_qty", 1))

    v = fetch_one("""
        SELECT v.id, v.name, v.price, v.delivery_type,
               p.name AS product_name
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.message.edit_text("Package not found.")
        return

    price_each = int(v["price"])
    total = price_each * qty

    u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user.id,))
    bal = int(u["balance"]) if u else 0
    if bal < total:
        await q.message.edit_text(f"❌ Not enough balance.\nNeed {money(total)}, you have {money(bal)}.")
        return

    # Pick stocks and mark sold
    picked = exec_sql_returning("""
        WITH picked AS (
            SELECT id, file_id, delivery_text
            FROM file_stocks
            WHERE variant_id=%s AND is_sold=FALSE
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        UPDATE file_stocks s
        SET is_sold=TRUE, sold_to=%s, sold_at=NOW()
        FROM picked
        WHERE s.id = picked.id
        RETURNING picked.id, picked.file_id, picked.delivery_text;
    """, (variant_id, qty, user.id))

    if len(picked) < qty:
        await q.message.edit_text("❌ Not enough stock. Please reduce quantity.")
        return

    purchase_id = None
    stock_ids = [int(r["id"]) for r in picked]

    try:
        # Deduct balance + record purchase
        exec_sql("UPDATE users SET balance = balance - %s, points = points + %s WHERE user_id=%s", (total, qty, user.id))

        row = exec_sql_returning(
            "INSERT INTO purchases(user_id, variant_id, qty, price_each, total_price) VALUES(%s,%s,%s,%s,%s) RETURNING id",
            (user.id, variant_id, qty, price_each, total)
        )
        purchase_id = int(row[0]["id"])

        # Deliver
        if (v["delivery_type"] or "").lower() == "file":
            for r in picked:
                if not r["file_id"]:
                    raise RuntimeError("Missing file_id in stock row")
                await context.bot.send_document(
                    chat_id=user.id,
                    document=r["file_id"],
                    caption=f"✅ Delivery: {v['product_name']} — {v['name']}\nPrice: {money(price_each)}"
                )
        else:
            for r in picked:
                payload = (r["delivery_text"] or "").strip()
                if not payload:
                    raise RuntimeError("Missing delivery_text in stock row")
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"✅ Delivery: {v['product_name']} — {v['name']}\nPrice: {money(price_each)}\n\n`{payload}`",
                    parse_mode=ParseMode.MARKDOWN
                )

        # success
        context.user_data.pop("cart_qty", None)
        context.user_data.pop("cart_variant_id", None)
        await q.message.edit_text(f"✅ Purchased **{qty}** and delivered!\nTotal: {money(total)}",
                                  parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        log.exception("Delivery failed, rolling back: %s", e)

        # ROLLBACK: restore stocks
        exec_sql(
            "UPDATE file_stocks SET is_sold=FALSE, sold_to=NULL, sold_at=NULL WHERE id = ANY(%s)",
            (stock_ids,)
        )

        # refund balance/points
        exec_sql("UPDATE users SET balance = balance + %s, points = GREATEST(points - %s, 0) WHERE user_id=%s",
                 (total, qty, user.id))

        # delete purchase
        if purchase_id:
            exec_sql("DELETE FROM purchases WHERE id=%s", (purchase_id,))

        await q.message.edit_text("❌ Delivery failed. Your balance & stock were restored.\nTry again.")


# ============================================================
# ADMIN COMMANDS
# ============================================================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return
    await update.message.reply_text("🔐 Admin ready ✅")


async def addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    parts = update.message.text.strip().split()
    if len(parts) != 3:
        await update.message.reply_text("Usage: /addbal <user_id> <amount>")
        return
    uid = int(parts[1])
    amt = int(parts[2])
    exec_sql("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amt, uid))
    await update.message.reply_text(f"✅ Added {money(amt)} to `{uid}`", parse_mode=ParseMode.MARKDOWN)


# ============================================================
# BOOT
# ============================================================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("addbal", addbal_cmd))

    app.add_handler(MessageHandler(filters.Regex(r"^🏠 Home$"), home_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 My Account$"), my_account))
    app.add_handler(MessageHandler(filters.Regex(r"^📩 Reseller Register$"), lambda u, c: None))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Chat Admin$"), chat_admin))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))

    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_cart, pattern=r"^cart:(start|inc|dec|cancel):"))
    app.add_handler(CallbackQueryHandler(cb_cart_confirm, pattern=r"^cart:confirm:"))
    app.add_handler(CallbackQueryHandler(cb_noop, pattern=r"^noop$"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
