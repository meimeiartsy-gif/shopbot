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
    set_setting, get_setting
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{n:,}"

def build_main_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        ["🏠 Home", "🛍 Shop"],
        ["👤 My Account", "📩 Reseller Register"],
        ["💬 Chat Admin", "🆘 Help"],
    ]
    if is_admin(uid):
        rows.append(["🔐 Admin"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

# -------------------- HOME / START --------------------
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    welcome = get_setting("WELCOME_TEXT") or "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:"
    thumb = get_setting("START_THUMB_FILE_ID")  # optional

    menu = build_main_menu(user.id)

    msg = update.message if update.message else update.callback_query.message

    if thumb:
        await msg.reply_photo(
            photo=thumb,
            caption=welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=menu
        )
    else:
        await msg.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=menu)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)

# -------------------- ACCOUNT --------------------
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    u = fetch_one("SELECT user_id, username, joined_at, points, is_reseller, balance FROM users WHERE user_id=%s", (user.id,))
    purchases = fetch_one("SELECT COUNT(*) AS c FROM purchases WHERE user_id=%s", (user.id,))
    count = int(purchases["c"]) if purchases else 0

    username = f"@{u['username']}" if u and u.get("username") else "(no username)"
    joined = u["joined_at"].strftime("%Y-%m-%d %H:%M") if u else "—"
    points = int(u["points"]) if u else 0
    balance = int(u["balance"]) if u else 0
    reseller = "✅ YES" if u and u["is_reseller"] else "❌ NO"

    text = (
        "👤 **My Account**\n\n"
        f"Username: **{username}**\n"
        f"User ID: `{user.id}`\n"
        f"Joined: **{joined}**\n"
        f"Purchases: **{count}**\n"
        f"Points: **{points}**\n"
        f"Balance: **{money(balance)}**\n"
        f"Reseller: **{reseller}**"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# -------------------- HELP / CHAT ADMIN --------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse\n"
        "• Tap a package to buy\n",
        parse_mode=ParseMode.MARKDOWN
    )

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_user = get_setting("ADMIN_USERNAME") or "@lovebylunaa"
    await update.message.reply_text(f"💬 Chat admin here:\n{admin_user}")

# -------------------- SHOP UI --------------------
def stock_count(variant_id: int) -> int:
    r = fetch_one("""
        SELECT COUNT(*) AS c
        FROM file_stocks
        WHERE variant_id=%s AND is_sold=FALSE
          AND (file_id IS NOT NULL OR delivery_text IS NOT NULL)
    """, (variant_id,))
    return int(r["c"]) if r else 0

async def shop_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    await update.message.reply_text(
        "🛍 **Shop Categories**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    if parts[1] == "cat":
        cat_id = int(parts[2])
        prods = fetch_all("""
            SELECT id,name,description FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id
        """, (cat_id,))
        if not prods:
            await q.edit_message_text("No products yet in this category.")
            return

        rows = [[InlineKeyboardButton(p["name"], callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")])
        await q.edit_message_text("Select a product:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if parts[1] == "prod":
        pid = int(parts[2])
        prod = fetch_one("SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not prod:
            await q.edit_message_text("Product not found.")
            return

        vars_ = fetch_all("""
            SELECT id,name,price FROM variants
            WHERE product_id=%s AND is_active=TRUE
            ORDER BY id
        """, (pid,))
        if not vars_:
            await q.edit_message_text("No packages yet.")
            return

        rows = []
        for v in vars_:
            s = stock_count(int(v["id"]))
            rows.append([InlineKeyboardButton(
                f"{v['name']} — {money(int(v['price']))} (Stock: {s})",
                callback_data=f"buy:preview:{v['id']}"
            )])

        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")])

        text = f"🧾 **{prod['name']}**\n\n{prod['description']}"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

    if parts[1] == "backcats":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        await q.edit_message_text("🛍 **Shop Categories**", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

# -------------------- BUY FLOW (preview -> confirm) --------------------
async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    parts = q.data.split(":")  # buy:preview:<id> OR buy:confirm:<id> OR buy:cancel
    action = parts[1]

    if action == "cancel":
        await q.edit_message_text("❌ Cancelled.")
        return

    variant_id = int(parts[2])

    v = fetch_one("""
        SELECT v.id, v.name, v.price, v.delivery_type,
               p.name AS product_name
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.message.reply_text("Package not found.")
        return

    price = int(v["price"])
    s = stock_count(variant_id)

    u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user.id,))
    bal = int(u["balance"]) if u else 0

    if action == "preview":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data=f"buy:confirm:{variant_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="buy:cancel:0")]
        ])
        text = (
            "🧾 **Checkout**\n\n"
            f"Product: **{v['product_name']}**\n"
            f"Package: **{v['name']}**\n"
            f"Price: **{money(price)}**\n"
            f"Stock: **{s}**\n"
            f"Your balance: **{money(bal)}**\n\n"
            "Tap Confirm to purchase."
        )
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    # action == confirm
    if s <= 0:
        await q.edit_message_text("❌ Out of stock.")
        return
    if bal < price:
        await q.edit_message_text(f"❌ Not enough balance.\nNeed {money(price)}, you have {money(bal)}.")
        return

    # Take 1 stock row (file or text)
    stock_row = fetch_one("""
        SELECT id, file_id, delivery_text
        FROM file_stocks
        WHERE variant_id=%s AND is_sold=FALSE
          AND (file_id IS NOT NULL OR delivery_text IS NOT NULL)
        ORDER BY id
        LIMIT 1
    """, (variant_id,))
    if not stock_row:
        await q.edit_message_text("❌ Out of stock.")
        return

    stock_id = int(stock_row["id"])

    # IMPORTANT: only mark sold AFTER we deduct balance successfully.
    # Mark sold atomically (prevents double sell)
    exec_sql("""
        UPDATE file_stocks
        SET is_sold=TRUE, sold_to=%s, sold_at=NOW()
        WHERE id=%s AND is_sold=FALSE
    """, (user.id, stock_id))

    # Deduct balance + save purchase + points
    exec_sql("UPDATE users SET balance=balance-%s, points=points+1 WHERE user_id=%s", (price, user.id))
    exec_sql(
        "INSERT INTO purchases(user_id, variant_id, qty, price_each, total_price) VALUES(%s,%s,1,%s,%s)",
        (user.id, variant_id, price, price)
    )

    # Deliver
    if stock_row.get("file_id"):
        await context.bot.send_document(
            chat_id=user.id,
            document=stock_row["file_id"],
            caption=f"✅ Delivery: {v['product_name']} — {v['name']}\nPrice: {money(price)}"
        )
    else:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                f"✅ **Delivery**\n\n"
                f"Product: **{v['product_name']}**\n"
                f"Package: **{v['name']}**\n"
                f"Price: **{money(price)}**\n\n"
                f"📌 Stock:\n`{stock_row['delivery_text']}`"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

    await q.edit_message_text("✅ Purchased and delivered!")

# -------------------- RESELLER REGISTRATION --------------------
async def reseller_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reseller_step"] = 1
    await update.message.reply_text("📩 **Reseller Registration**\n\nSend your **Full Name**:", parse_mode=ParseMode.MARKDOWN)

async def reseller_form_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "reseller_step" not in context.user_data:
        return

    step = context.user_data["reseller_step"]
    text = update.message.text.strip()

    if step == 1:
        context.user_data["res_full_name"] = text
        context.user_data["reseller_step"] = 2
        await update.message.reply_text("Send your **Contact** (Telegram/FB/etc):", parse_mode=ParseMode.MARKDOWN)
        return

    if step == 2:
        context.user_data["res_contact"] = text
        context.user_data["reseller_step"] = 3
        await update.message.reply_text("Send your **Shop Link** (FB page / Telegram channel / etc):", parse_mode=ParseMode.MARKDOWN)
        return

    if step == 3:
        user = update.effective_user
        full_name = context.user_data.pop("res_full_name")
        contact = context.user_data.pop("res_contact")
        shop_link = text
        context.user_data.pop("reseller_step", None)

        exec_sql("""
            INSERT INTO reseller_applications(user_id, username, full_name, contact, shop_link)
            VALUES(%s,%s,%s,%s,%s)
        """, (user.id, user.username, full_name, contact, shop_link))

        await update.message.reply_text("✅ Submitted! Waiting for admin approval 💗")

# -------------------- ADMIN + SETTINGS + FILEID --------------------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🔐 **Admin ready ✅**\n\n"
        "Commands:\n"
        "• /setwelcome <text>\n"
        "• /setthumb (then send photo)\n"
        "• /fileid (then send file/photo)\n"
        "• /addbal <user_id> <amount>\n",
        parse_mode=ParseMode.MARKDOWN
    )

async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    txt = update.message.text.replace("/setwelcome", "", 1).strip()
    if not txt:
        await update.message.reply_text("Usage: /setwelcome <text>")
        return
    set_setting("WELCOME_TEXT", txt)
    await update.message.reply_text("✅ Welcome text updated!")

async def setthumb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_thumb"] = True
    await update.message.reply_text("Send the thumbnail photo now (as PHOTO).")

async def capture_thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("await_thumb"):
        return
    if not update.message.photo:
        await update.message.reply_text("Send as PHOTO please.")
        return
    file_id = update.message.photo[-1].file_id
    set_setting("START_THUMB_FILE_ID", file_id)
    context.user_data["await_thumb"] = False
    await update.message.reply_text(f"✅ Start thumbnail saved!\nfile_id:\n`{file_id}`", parse_mode=ParseMode.MARKDOWN)

async def fileid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send the FILE (document) or PHOTO now. I will reply with file_id.")
    context.user_data["await_any_fileid"] = True

async def capture_any_fileid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_any_fileid"):
        return

    file_id = None
    if update.message.document:
        file_id = update.message.document.file_id
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id

    if not file_id:
        await update.message.reply_text("Send a document or photo.")
        return

    context.user_data["await_any_fileid"] = False
    await update.message.reply_text(f"✅ file_id:\n`{file_id}`", parse_mode=ParseMode.MARKDOWN)

async def addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    parts = update.message.text.strip().split()
    if len(parts) != 3:
        await update.message.reply_text("Usage: /addbal <user_id> <amount>")
        return
    uid = int(parts[1])
    amt = int(parts[2])
    exec_sql("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amt, uid))
    await update.message.reply_text(f"✅ Added {money(amt)} to `{uid}`", parse_mode=ParseMode.MARKDOWN)

# -------------------- TEXT ROUTER (fixes “only shop works”) --------------------
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    t = update.message.text.strip().lower()

    # normalize (so emoji differences won’t break buttons)
    if "home" in t:
        await show_home(update, context)
        return
    if "shop" in t:
        await shop_btn(update, context)
        return
    if "my account" in t:
        await my_account(update, context)
        return
    if "reseller" in t:
        await reseller_register(update, context)
        return
    if "chat admin" in t:
        await chat_admin(update, context)
        return
    if "help" in t:
        await help_cmd(update, context)
        return
    if "admin" in t and is_admin(update.effective_user.id):
        await admin_cmd(update, context)
        return

    # reseller form fallback
    await reseller_form_text(update, context)

# -------------------- BOOT --------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))

    # admin commands
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("setthumb", setthumb_cmd))
    app.add_handler(CommandHandler("fileid", fileid_cmd))
    app.add_handler(CommandHandler("addbal", addbal_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))

    # captures
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, capture_thumb))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, capture_any_fileid))

    # main router (fixes button mismatch)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
