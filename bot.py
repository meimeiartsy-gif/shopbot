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
    set_setting, get_setting, tx_run
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

def main_menu(uid: int) -> ReplyKeyboardMarkup:
    if is_admin(uid):
        return ReplyKeyboardMarkup(
            [
                ["🏠 Home", "🛍 Shop"],
                ["👤 My Account", "📩 Reseller Register"],
                ["💬 Chat Admin", "🆘 Help"],
                ["🔐 Admin"],
            ],
            resize_keyboard=True
        )
    # customer menu (NO ADMIN)
    return ReplyKeyboardMarkup(
        [
            ["🏠 Home", "🛍 Shop"],
            ["👤 My Account", "📩 Reseller Register"],
            ["💬 Chat Admin", "🆘 Help"],
        ],
        resize_keyboard=True
    )

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
    thumb = get_setting("START_THUMB_FILE_ID")  # optional file_id

    msg = update.message if update.message else update.callback_query.message

    if thumb:
        await msg.reply_photo(
            photo=thumb,
            caption=welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(user.id)
        )
    else:
        await msg.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu(user.id))

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)

async def home_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)

# -------------------- ACCOUNT --------------------
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    u = fetch_one("""
        SELECT user_id, username, joined_at, points, is_reseller, balance
        FROM users WHERE user_id=%s
    """, (user.id,))
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
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu(user.id))

# -------------------- HELP / CHAT ADMIN --------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse\n"
        "• Choose package → quantity → confirm\n"
        "• Delivery is automatic after confirm\n",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(update.effective_user.id)
    )

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_user = get_setting("ADMIN_USERNAME") or "@lovebylunaa"
    await update.message.reply_text(
        f"💬 Chat admin here:\n{admin_user}",
        reply_markup=main_menu(update.effective_user.id)
    )

# -------------------- SHOP UI --------------------
async def shop_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    await update.message.reply_text(
        "🛍 **Shop Categories**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows)
    )

def shop_categories_markup():
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    return InlineKeyboardMarkup([[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats])

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
            await q.edit_message_text("No products yet in this category.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅ Back", callback_data="shop:back:cats")]
            ]))
            return

        rows = [[InlineKeyboardButton(p["name"], callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:back:cats")])
        await q.edit_message_text("Select a product:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if parts[1] == "prod":
        pid = int(parts[2])
        prod = fetch_one("SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not prod:
            await q.edit_message_text("Product not found.", reply_markup=shop_categories_markup())
            return

        vars_ = fetch_all("""
            SELECT id,name,price FROM variants
            WHERE product_id=%s AND is_active=TRUE
            ORDER BY id
        """, (pid,))
        if not vars_:
            await q.edit_message_text("No packages yet.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅ Back", callback_data="shop:back:cats")]
            ]))
            return

        rows = []
        for v in vars_:
            stock = fetch_one("""
                SELECT COUNT(*) AS c
                FROM file_stocks
                WHERE variant_id=%s AND is_sold=FALSE
            """, (v["id"],))
            s = int(stock["c"]) if stock else 0
            rows.append([InlineKeyboardButton(
                f"{v['name']} — {money(int(v['price']))} (Stock: {s})",
                callback_data=f"cart:pick:{v['id']}"
            )])

        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:back:cats")])

        text = f"🧾 **{prod['name']}**\n\n{prod['description']}"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

    if parts[1] == "back":
        # back to categories
        await q.edit_message_text("🛍 **Shop Categories**", parse_mode=ParseMode.MARKDOWN, reply_markup=shop_categories_markup())
        return

# -------------------- CART FLOW (QTY -> CONFIRM -> CANCEL) --------------------
async def cb_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    parts = q.data.split(":")
    action = parts[1]

    if action == "pick":
        variant_id = int(parts[2])
        v = fetch_one("""
            SELECT v.id, v.name, v.price, p.name AS product_name
            FROM variants v
            JOIN products p ON p.id=v.product_id
            WHERE v.id=%s AND v.is_active=TRUE
        """, (variant_id,))
        if not v:
            await q.edit_message_text("Package not found.")
            return

        stock = fetch_one("SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE", (variant_id,))
        s = int(stock["c"]) if stock else 0

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Qty 1", callback_data=f"cart:qty:{variant_id}:1"),
             InlineKeyboardButton("Qty 2", callback_data=f"cart:qty:{variant_id}:2")],
            [InlineKeyboardButton("Qty 5", callback_data=f"cart:qty:{variant_id}:5"),
             InlineKeyboardButton("Qty 10", callback_data=f"cart:qty:{variant_id}:10")],
            [InlineKeyboardButton("⬅ Back", callback_data="shop:back:cats")]
        ])

        await q.edit_message_text(
            f"🛒 **{v['product_name']}**\n"
            f"Package: **{v['name']}**\n"
            f"Price each: **{money(int(v['price']))}**\n"
            f"Available stock: **{s}**\n\n"
            f"Choose quantity:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return

    if action == "qty":
        variant_id = int(parts[2])
        qty = int(parts[3])

        v = fetch_one("""
            SELECT v.id, v.name, v.price, p.name AS product_name
            FROM variants v
            JOIN products p ON p.id=v.product_id
            WHERE v.id=%s AND v.is_active=TRUE
        """, (variant_id,))
        if not v:
            await q.edit_message_text("Package not found.")
            return

        stock = fetch_one("SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE", (variant_id,))
        s = int(stock["c"]) if stock else 0

        total = int(v["price"]) * qty
        u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user.id,))
        bal = int(u["balance"]) if u else 0

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data=f"cart:confirm:{variant_id}:{qty}"),
             InlineKeyboardButton("❌ Cancel", callback_data="cart:cancel")],
            [InlineKeyboardButton("⬅ Back", callback_data=f"cart:pick:{variant_id}")]
        ])

        await q.edit_message_text(
            f"✅ **Confirm Order**\n\n"
            f"Product: **{v['product_name']}**\n"
            f"Package: **{v['name']}**\n"
            f"Qty: **{qty}**\n"
            f"Total: **{money(total)}**\n"
            f"Your balance: **{money(bal)}**\n"
            f"Stock available: **{s}**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return

    if action == "cancel":
        await q.edit_message_text("❌ Order cancelled.", reply_markup=shop_categories_markup())
        return

    if action == "confirm":
        variant_id = int(parts[2])
        qty = int(parts[3])

        # transactional confirm: check balance + lock stock rows, then mark sold, deduct, deliver
        def do_confirm(conn):
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # load variant
                cur.execute("""
                    SELECT v.id, v.name, v.price, p.name AS product_name
                    FROM variants v
                    JOIN products p ON p.id=v.product_id
                    WHERE v.id=%s AND v.is_active=TRUE
                """, (variant_id,))
                v = cur.fetchone()
                if not v:
                    return ("ERR", "Package not found.", None)

                total = int(v["price"]) * qty

                # balance lock
                cur.execute("SELECT balance FROM users WHERE user_id=%s FOR UPDATE", (user.id,))
                row = cur.fetchone()
                bal = int(row["balance"]) if row else 0
                if bal < total:
                    return ("ERR", f"❌ Not enough balance.\nNeed {money(total)}, you have {money(bal)}.", None)

                # lock stock rows
                cur.execute("""
                    SELECT id, file_id, delivery_text
                    FROM file_stocks
                    WHERE variant_id=%s AND is_sold=FALSE
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                """, (variant_id, qty))
                stocks = cur.fetchall()
                if len(stocks) < qty:
                    return ("ERR", "❌ Not enough stock. Please reduce quantity.", None)

                # deduct balance
                cur.execute("UPDATE users SET balance = balance - %s, points = points + %s WHERE user_id=%s",
                            (total, qty, user.id))

                # mark sold
                ids = [s["id"] for s in stocks]
                cur.execute("""
                    UPDATE file_stocks
                    SET is_sold=TRUE, sold_to=%s, sold_at=NOW()
                    WHERE id = ANY(%s)
                """, (user.id, ids))

                # purchase record
                cur.execute("""
                    INSERT INTO purchases(user_id, variant_id, qty, total_price)
                    VALUES(%s,%s,%s,%s)
                """, (user.id, variant_id, qty, total))

                return ("OK", f"✅ Paid {money(total)} — delivering now...", {"variant": v, "stocks": stocks, "total": total})

        status, msg, payload = tx_run(do_confirm)
        if status != "OK":
            await q.message.reply_text(msg, reply_markup=main_menu(user.id))
            # keep screen
            return

        # show paid message (edit the confirm screen)
        await q.edit_message_text(msg)

        v = payload["variant"]
        stocks = payload["stocks"]

        # deliver each stock row
        delivered = 0
        for s in stocks:
            if s.get("delivery_text"):
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"✅ **Delivery**: {v['product_name']} — {v['name']}\n\n`{s['delivery_text']}`",
                    parse_mode=ParseMode.MARKDOWN
                )
                delivered += 1
            elif s.get("file_id"):
                await context.bot.send_document(
                    chat_id=user.id,
                    document=s["file_id"],
                    caption=f"✅ Delivery: {v['product_name']} — {v['name']}"
                )
                delivered += 1

        await q.message.reply_text(
            f"✅ Done! Delivered **{delivered}** item(s).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(user.id)
        )
        return

# -------------------- RESELLER REGISTRATION FORM --------------------
async def reseller_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reseller_step"] = 1
    await update.message.reply_text(
        "📩 **Reseller Registration**\n\nSend your **Full Name**:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(update.effective_user.id)
    )

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

        await update.message.reply_text("✅ Submitted! Waiting for admin approval 💗", reply_markup=main_menu(user.id))

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

# -------------------- ADMIN (approve/reject reseller) --------------------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.", reply_markup=main_menu(update.effective_user.id))
        return
    await update.message.reply_text(
        "🔐 Admin ready ✅\n\nAdmin commands:\n"
        "• /setwelcome <text>\n"
        "• /setthumb (then send photo)\n"
        "• /fileid (then send file/photo)\n\n"
        "SQL only adding products/stocks is OK ✅",
        reply_markup=main_menu(update.effective_user.id)
    )

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.", reply_markup=main_menu(q.from_user.id))
        return

    data = q.data.split(":")
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

# -------------------- ADMIN: welcome + start thumb --------------------
async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    txt = update.message.text.replace("/setwelcome", "", 1).strip()
    if not txt:
        await update.message.reply_text("Usage: /setwelcome <text>", reply_markup=main_menu(update.effective_user.id))
        return
    set_setting("WELCOME_TEXT", txt)
    await update.message.reply_text("✅ Welcome text updated!", reply_markup=main_menu(update.effective_user.id))

async def setthumb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_thumb"] = True
    await update.message.reply_text("Send the thumbnail photo now (as PHOTO).", reply_markup=main_menu(update.effective_user.id))

async def capture_thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("await_thumb"):
        return
    if not update.message.photo:
        await update.message.reply_text("Send as PHOTO please.", reply_markup=main_menu(update.effective_user.id))
        return
    file_id = update.message.photo[-1].file_id
    set_setting("START_THUMB_FILE_ID", file_id)
    context.user_data["await_thumb"] = False
    await update.message.reply_text("✅ Start thumbnail saved!", reply_markup=main_menu(update.effective_user.id))

# -------------------- /fileid helper --------------------
async def fileid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_fileid"] = True
    await update.message.reply_text("Send the FILE (document) or PHOTO now. I will reply with file_id.", reply_markup=main_menu(update.effective_user.id))

async def capture_fileid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("await_fileid"):
        return

    fid = None
    if update.message.document:
        fid = update.message.document.file_id
    elif update.message.photo:
        fid = update.message.photo[-1].file_id

    if not fid:
        await update.message.reply_text("Send a document or photo.", reply_markup=main_menu(update.effective_user.id))
        return

    context.user_data["await_fileid"] = False
    await update.message.reply_text(f"✅ file_id:\n`{fid}`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu(update.effective_user.id))

# -------------------- BOOT --------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))

    # Admin
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("setthumb", setthumb_cmd))
    app.add_handler(CommandHandler("fileid", fileid_cmd))

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
    app.add_handler(CallbackQueryHandler(cb_cart, pattern=r"^cart:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))

    # Admin captures
    app.add_handler(MessageHandler(filters.PHOTO, capture_thumb))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, capture_fileid))

    # Reseller form steps
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reseller_form_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
