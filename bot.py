import os
import logging
from datetime import datetime

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

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🏠 Home", "🛍 Shop"],
        ["👤 My Account", "📩 Reseller Register"],
        ["💬 Chat Admin", "🆘 Help"],
        ["🔐 Admin"],
    ],
    resize_keyboard=True
)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{n:,}"

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

    if update.message:
        msg = update.message
    else:
        msg = update.callback_query.message

    if thumb:
        await msg.reply_photo(
            photo=thumb,
            caption=welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=MAIN_MENU
        )
    else:
        await msg.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)

async def home_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)

# -------------------- ACCOUNT / POINTS --------------------
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    u = fetch_one("SELECT user_id, username, joined_at, points, is_reseller FROM users WHERE user_id=%s", (user.id,))
    purchases = fetch_one("SELECT COUNT(*) AS c FROM purchases WHERE user_id=%s", (user.id,))
    count = int(purchases["c"]) if purchases else 0

    username = f"@{u['username']}" if u and u.get("username") else "(no username)"
    joined = u["joined_at"].strftime("%Y-%m-%d %H:%M") if u else "—"
    points = int(u["points"]) if u else 0
    reseller = "✅ YES" if u and u["is_reseller"] else "❌ NO"

    text = (
        "👤 **My Account**\n\n"
        f"Username: **{username}**\n"
        f"User ID: `{user.id}`\n"
        f"Joined: **{joined}**\n"
        f"Purchases: **{count}**\n"
        f"Points: **{points}**\n"
        f"Reseller: **{reseller}**"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# -------------------- HELP / CHAT ADMIN --------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse\n"
        "• Buy = auto delivery\n"
        "• **Reseller Register** = apply for reseller\n",
        parse_mode=ParseMode.MARKDOWN
    )

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_user = get_setting("ADMIN_USERNAME") or "@lovebylunaa"
    await update.message.reply_text(
        f"💬 Chat admin here:\n{admin_user}"
    )

# -------------------- SHOP UI --------------------
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
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:back")])
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
            stock = fetch_one("SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE", (v["id"],))
            s = int(stock["c"]) if stock else 0
            rows.append([InlineKeyboardButton(
                f"{v['name']} — {money(int(v['price']))} (Stock: {s})",
                callback_data=f"buy:{v['id']}"
            )])

        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:back")])

        text = f"🧾 **{prod['name']}**\n\n{prod['description']}"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

    if parts[1] == "back":
        # back to categories
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        await q.edit_message_text("🛍 **Shop Categories**", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

# -------------------- BUY + AUTO DELIVERY (NO DOUBLE) --------------------
async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    variant_id = int(q.data.split(":")[1])
    v = fetch_one("""
        SELECT v.id, v.name, v.price, v.delivery_type, v.delivery_file_id, v.delivery_text,
               p.name AS product_name
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.message.reply_text("Package not found.")
        return

    # Take 1 file stock row and lock it (safe)
    with_row = fetch_one("""
        SELECT id, file_id FROM file_stocks
        WHERE variant_id=%s AND is_sold=FALSE
        ORDER BY id
        LIMIT 1
    """, (variant_id,))
    if not with_row:
        await q.message.reply_text("❌ Out of stock.")
        return

    price = int(v["price"])

    # For now: no balance system in this snippet (you can add it back later)
    # Auto deliver:
    stock_id = int(with_row["id"])
    file_id = with_row["file_id"]

    # Mark sold first (prevents double delivery)
    exec_sql("""
        UPDATE file_stocks SET is_sold=TRUE, sold_to=%s, sold_at=NOW()
        WHERE id=%s AND is_sold=FALSE
    """, (user.id, stock_id))

    # Save purchase + points
    exec_sql("INSERT INTO purchases(user_id, variant_id, price) VALUES(%s,%s,%s)", (user.id, variant_id, price))
    exec_sql("UPDATE users SET points=points+1 WHERE user_id=%s", (user.id,))

    await context.bot.send_document(
        chat_id=user.id,
        document=file_id,
        caption=f"✅ Delivery: {v['product_name']} — {v['name']}\nPrice: {money(price)}"
    )
    await q.message.reply_text("✅ Purchase delivered!")

# -------------------- RESELLER REGISTRATION FORM --------------------
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

        # notify admin
        for admin_id in ADMIN_IDS:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"admin:res:approve:{user.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin:res:reject:{user.id}")
            ]])
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📩 New Reseller Application\n\n"
                    f"User: @{user.username} (`{user.id}`)\n"
                    f"Name: {full_name}\n"
                    f"Contact: {contact}\n"
                    f"Shop: {shop_link}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )

# -------------------- ADMIN (basic approve/reject reseller) --------------------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return
    await update.message.reply_text("🔐 Admin ready ✅\n\n(Next: product add/edit/delete panel)")

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    data = q.data.split(":")
    # admin:res:approve:<user_id>
    if data[1] == "res":
        action = data[2]
        uid = int(data[3])

        if action == "approve":
            exec_sql("UPDATE users SET is_reseller=TRUE WHERE user_id=%s", (uid,))
            exec_sql("""
                UPDATE reseller_applications
                SET status='APPROVED', decided_at=NOW(), admin_id=%s
                WHERE user_id=%s AND status='PENDING'
            """, (q.from_user.id, uid))
            await context.bot.send_message(uid, "✅ Your reseller application is APPROVED! 💗")
            await q.message.reply_text("✅ Approved.")
            return

        if action == "reject":
            exec_sql("""
                UPDATE reseller_applications
                SET status='REJECTED', decided_at=NOW(), admin_id=%s
                WHERE user_id=%s AND status='PENDING'
            """, (q.from_user.id, uid))
            await context.bot.send_message(uid, "❌ Your reseller application was rejected.")
            await q.message.reply_text("❌ Rejected.")
            return

# -------------------- ADMIN: editable welcome + start thumb --------------------
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
    await update.message.reply_text("Send the thumbnail photo now (as PHOTO).")
    context.user_data["await_thumb"] = True

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
    await update.message.reply_text("✅ Start thumbnail saved!")

# -------------------- BOOT --------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))

    # Admin setup
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("setthumb", setthumb_cmd))

    # Menu buttons
    app.add_handler(MessageHandler(filters.Regex(r"^🏠 Home$"), home_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 My Account$"), my_account))
    app.add_handler(MessageHandler(filters.Regex(r"^📩 Reseller Register$"), reseller_register))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Chat Admin$"), chat_admin))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))

    # Admin photo capture for thumb
    app.add_handler(MessageHandler(filters.PHOTO, capture_thumb))

    # Reseller form steps
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reseller_form_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
