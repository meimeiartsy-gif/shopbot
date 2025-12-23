import os
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from db import (
    ensure_user,
    fetch_one,
    fetch_all,
    exec_sql,
    set_setting,
    get_setting,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

def menu_for(uid: int) -> ReplyKeyboardMarkup:
    # Hide Admin button for customers
    if is_admin(uid):
        rows = [
            ["🏠 Home", "🛍 Shop"],
            ["➕ Add Balance", "👤 My Account"],
            ["📩 Reseller Register", "🆘 Help"],
            ["💬 Chat Admin", "🔐 Admin"],
        ]
    else:
        rows = [
            ["🏠 Home", "🛍 Shop"],
            ["➕ Add Balance", "👤 My Account"],
            ["📩 Reseller Register", "🆘 Help"],
            ["💬 Chat Admin"],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# -------------------- HOME / START --------------------
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    welcome = get_setting("WELCOME_TEXT") or "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:"
    thumb = get_setting("START_THUMB_FILE_ID")  # optional photo file_id

    msg = update.message if update.message else update.callback_query.message

    if thumb:
        await msg.reply_photo(
            photo=thumb,
            caption=welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=menu_for(user.id),
        )
    else:
        await msg.reply_text(
            welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=menu_for(user.id),
        )

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)

# -------------------- MY ACCOUNT --------------------
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    u = fetch_one(
        "SELECT user_id, username, joined_at, points, balance, is_reseller FROM users WHERE user_id=%s",
        (user.id,),
    )
    purchases = fetch_one("SELECT COUNT(*) AS c FROM purchases WHERE user_id=%s", (user.id,))
    count = int(purchases["c"]) if purchases else 0

    username = f"@{u['username']}" if u and u.get("username") else "(no username)"
    joined = u["joined_at"].strftime("%Y-%m-%d %H:%M") if u else "—"
    points = int(u["points"]) if u else 0
    bal = int(u["balance"]) if u else 0
    reseller = "✅ YES" if u and u["is_reseller"] else "❌ NO"

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
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=menu_for(user.id))

# -------------------- HELP / CHAT ADMIN --------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse\n"
        "• Tap **Add Balance** to top up\n"
        "• Purchases are auto-delivered after confirm\n",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=menu_for(update.effective_user.id),
    )

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_user = get_setting("ADMIN_USERNAME") or "@lovebylunaa"
    await update.message.reply_text(
        f"💬 Chat admin here:\n{admin_user}",
        reply_markup=menu_for(update.effective_user.id),
    )

# -------------------- SHOP UI (inline, edits messages) --------------------
async def shop_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    if not cats:
        await update.message.reply_text("No categories yet.")
        return
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    await update.message.reply_text(
        "🛍 **Shop Categories**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows),
    )

async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    if action == "cat":
        cat_id = int(parts[2])
        prods = fetch_all(
            """
            SELECT id,name,description FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id
            """,
            (cat_id,),
        )
        if not prods:
            await q.edit_message_text("No products yet in this category.")
            return

        rows = [[InlineKeyboardButton(p["name"], callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:cats")])
        await q.edit_message_text("Select a product:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "prod":
        pid = int(parts[2])
        prod = fetch_one(
            "SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE",
            (pid,),
        )
        if not prod:
            await q.edit_message_text("Product not found.")
            return

        vars_ = fetch_all(
            """
            SELECT id,name,price FROM variants
            WHERE product_id=%s AND is_active=TRUE
            ORDER BY id
            """,
            (pid,),
        )
        if not vars_:
            await q.edit_message_text("No packages yet.")
            return

        rows = []
        for v in vars_:
            stock = fetch_one(
                "SELECT COUNT(*) AS c FROM stocks WHERE variant_id=%s AND is_sold=FALSE",
                (v["id"],),
            )
            s = int(stock["c"]) if stock else 0
            rows.append([InlineKeyboardButton(
                f"{v['name']} — {money(v['price'])} (Stock: {s})",
                callback_data=f"buy:open:{v['id']}"
            )])

        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:cats")])

        text = f"🧾 **{prod['name']}**\n\n{prod['description'] or ''}"
        await q.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if action == "cats":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        await q.edit_message_text(
            "🛍 **Shop Categories**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

# -------------------- BUY FLOW (confirm only deducts on confirm) --------------------
async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    data = q.data.split(":")  # buy:open:<variant_id> OR buy:confirm:<variant_id> OR buy:cancel
    action = data[1]

    if action == "open":
        variant_id = int(data[2])

        v = fetch_one(
            """
            SELECT v.id, v.name, v.price, p.name AS product_name, p.id AS product_id
            FROM variants v
            JOIN products p ON p.id=v.product_id
            WHERE v.id=%s AND v.is_active=TRUE
            """,
            (variant_id,),
        )
        if not v:
            await q.edit_message_text("Package not found.")
            return

        stock = fetch_one(
            "SELECT COUNT(*) AS c FROM stocks WHERE variant_id=%s AND is_sold=FALSE",
            (variant_id,),
        )
        s = int(stock["c"]) if stock else 0

        u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user.id,))
        bal = int(u["balance"]) if u else 0

        text = (
            f"🧾 **Checkout**\n\n"
            f"Product: **{v['product_name']}**\n"
            f"Package: **{v['name']}**\n"
            f"Price: **{money(v['price'])}**\n"
            f"Stock: **{s}**\n"
            f"Your balance: **{money(bal)}**\n\n"
            f"Confirm to receive delivery."
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data=f"buy:confirm:{variant_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="buy:cancel")],
        ])
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if action == "cancel":
        await q.edit_message_text("❌ Cancelled.")
        return

    if action == "confirm":
        variant_id = int(data[2])

        # Re-check everything at confirm time (so no stock is reduced before confirm)
        v = fetch_one(
            """
            SELECT v.id, v.name, v.price, p.name AS product_name
            FROM variants v
            JOIN products p ON p.id=v.product_id
            WHERE v.id=%s AND v.is_active=TRUE
            """,
            (variant_id,),
        )
        if not v:
            await q.edit_message_text("Package not found.")
            return

        # balance check
        u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user.id,))
        bal = int(u["balance"]) if u else 0
        price = int(v["price"])

        if bal < price:
            await q.edit_message_text(f"❌ Not enough balance.\nNeed {money(price)}, you have {money(bal)}.")
            return

        # Take 1 stock row safely (lock-like behavior via single UPDATE after selecting oldest available)
        # Note: we do it in 2 steps to keep your db.py simple. This still avoids “deduct before confirm”.
        stock_row = fetch_one(
            """
            SELECT id, stock_type, file_id, delivery_text
            FROM stocks
            WHERE variant_id=%s AND is_sold=FALSE
            ORDER BY id
            LIMIT 1
            """,
            (variant_id,),
        )
        if not stock_row:
            await q.edit_message_text("❌ Out of stock.")
            return

        stock_id = int(stock_row["id"])

        # Mark sold FIRST (prevents double delivery)
        exec_sql(
            """
            UPDATE stocks
            SET is_sold=TRUE, sold_to=%s, sold_at=NOW()
            WHERE id=%s AND is_sold=FALSE
            """,
            (user.id, stock_id),
        )

        # Deduct balance + save purchase + points
        exec_sql("UPDATE users SET balance=balance-%s, points=points+1 WHERE user_id=%s", (price, user.id))
        exec_sql("INSERT INTO purchases(user_id, variant_id, price) VALUES(%s,%s,%s)", (user.id, variant_id, price))

        # Deliver
        if stock_row["stock_type"] == "file":
            await context.bot.send_document(
                chat_id=user.id,
                document=stock_row["file_id"],
                caption=f"✅ Delivery: {v['product_name']} — {v['name']}\nPrice: {money(price)}",
            )
        else:
            await context.bot.send_message(
                chat_id=user.id,
                text=f"✅ Delivery: {v['product_name']} — {v['name']}\nPrice: {money(price)}\n\n{stock_row['delivery_text']}",
            )

        await q.edit_message_text("✅ Order confirmed and delivered!")
        return

# -------------------- ADD BALANCE (Customer -> Admin approve) --------------------
async def add_balance_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    gcash_qr = get_setting("GCASH_QR_FILE_ID")
    gotyme_qr = get_setting("GOTYME_QR_FILE_ID")

    text = (
        "➕ **Add Balance**\n\n"
        "1) Scan QR (GCash / GoTyme)\n"
        "2) Send payment\n"
        "3) Upload screenshot proof here\n\n"
        "After you upload proof, I will ask your amount."
    )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=menu_for(user.id))

    if gcash_qr:
        await update.message.reply_photo(gcash_qr, caption="GCash QR")
    if gotyme_qr:
        await update.message.reply_photo(gotyme_qr, caption="GoTyme QR")

    context.user_data["await_topup_proof"] = True

async def topup_capture_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_topup_proof"):
        return

    user = update.effective_user
    ensure_user(user.id, user.username)

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Please send a screenshot as PHOTO or DOCUMENT.")
        return

    context.user_data["topup_proof_file_id"] = file_id
    context.user_data["await_topup_proof"] = False
    context.user_data["await_topup_amount"] = True
    await update.message.reply_text("✅ Proof received. Now send the **amount** (numbers only).", parse_mode=ParseMode.MARKDOWN)

async def topup_capture_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_topup_amount"):
        return

    user = update.effective_user
    txt = (update.message.text or "").strip()

    if not txt.isdigit():
        await update.message.reply_text("Send amount as numbers only (example: 100).")
        return

    amount = int(txt)
    proof_file_id = context.user_data.get("topup_proof_file_id")
    if not proof_file_id:
        await update.message.reply_text("Proof missing. Tap Add Balance again.")
        context.user_data["await_topup_amount"] = False
        return

    # save topup request
    row = fetch_one(
        "INSERT INTO topups(user_id, amount, proof_file_id) VALUES(%s,%s,%s) RETURNING id",
        (user.id, amount, proof_file_id),
    )
    topup_id = int(row["id"])

    context.user_data["await_topup_amount"] = False
    context.user_data.pop("topup_proof_file_id", None)

    await update.message.reply_text("✅ Submitted! Waiting for admin approval 💗")

    # notify admins
    uname = f"@{user.username}" if user.username else "(no username)"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"topup:approve:{topup_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"topup:reject:{topup_id}")
    ]])

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"➕ New Topup Request\n\nUser: {uname} (`{user.id}`)\nAmount: {money(amount)}\nTopup ID: {topup_id}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )
        await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id, caption="Proof")

async def cb_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ Admin only.")
        return

    _, action, topup_id_s = q.data.split(":")
    topup_id = int(topup_id_s)

    t = fetch_one("SELECT id, user_id, amount, status FROM topups WHERE id=%s", (topup_id,))
    if not t:
        await q.edit_message_text("Topup not found.")
        return
    if t["status"] != "PENDING":
        await q.edit_message_text(f"Already {t['status']}.")
        return

    if action == "approve":
        exec_sql(
            "UPDATE topups SET status='APPROVED', admin_id=%s, decided_at=NOW() WHERE id=%s",
            (q.from_user.id, topup_id),
        )
        exec_sql("UPDATE users SET balance=balance+%s WHERE user_id=%s", (int(t["amount"]), int(t["user_id"])))
        await context.bot.send_message(int(t["user_id"]), f"✅ Topup approved! Added {money(t['amount'])} to your balance.")
        await q.edit_message_text("✅ Approved.")
        return

    if action == "reject":
        exec_sql(
            "UPDATE topups SET status='REJECTED', admin_id=%s, decided_at=NOW() WHERE id=%s",
            (q.from_user.id, topup_id),
        )
        await context.bot.send_message(int(t["user_id"]), "❌ Topup rejected.")
        await q.edit_message_text("❌ Rejected.")
        return

# -------------------- ADMIN COMMANDS --------------------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return

    await update.message.reply_text(
        "🔐 Admin ready ✅\n\n"
        "Commands:\n"
        "• /setwelcome <text>\n"
        "• /setthumb (send photo)\n"
        "• /setgcashqr (send photo)\n"
        "• /setgotymeqr (send photo)\n"
        "• /fileid (then send photo/doc)\n",
        reply_markup=menu_for(update.effective_user.id),
    )

# Store “what admin is uploading now”
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
    context.user_data["await_admin_upload_key"] = "START_THUMB_FILE_ID"
    await update.message.reply_text("Send the START thumbnail as PHOTO now.")

async def setgcashqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_admin_upload_key"] = "GCASH_QR_FILE_ID"
    await update.message.reply_text("Send the GCash QR as PHOTO now.")

async def setgotymeqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_admin_upload_key"] = "GOTYME_QR_FILE_ID"
    await update.message.reply_text("Send the GoTyme QR as PHOTO now.")

async def fileid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_fileid"] = True
    await update.message.reply_text("Send the FILE (document) or PHOTO now. I will reply with file_id.")

async def admin_upload_catcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    # /fileid flow
    if context.user_data.get("await_fileid"):
        file_id = None
        if update.message.document:
            file_id = update.message.document.file_id
        elif update.message.photo:
            file_id = update.message.photo[-1].file_id

        if not file_id:
            await update.message.reply_text("Send as PHOTO or DOCUMENT.")
            return

        context.user_data["await_fileid"] = False
        await update.message.reply_text(f"✅ file_id:\n`{file_id}`", parse_mode=ParseMode.MARKDOWN)
        return

    # Admin setting upload flow
    key = context.user_data.get("await_admin_upload_key")
    if key:
        if not update.message.photo:
            await update.message.reply_text("Send as PHOTO please.")
            return
        file_id = update.message.photo[-1].file_id
        set_setting(key, file_id)
        context.user_data["await_admin_upload_key"] = None
        await update.message.reply_text(f"✅ Saved {key}!")
        return

# -------------------- BOOT --------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Start/Home
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^🏠 Home$"), show_home))

    # Main menu
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 My Account$"), my_account))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Chat Admin$"), chat_admin))
    app.add_handler(MessageHandler(filters.Regex(r"^➕ Add Balance$"), add_balance_btn))

    # Admin
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("setthumb", setthumb_cmd))
    app.add_handler(CommandHandler("setgcashqr", setgcashqr_cmd))
    app.add_handler(CommandHandler("setgotymeqr", setgotymeqr_cmd))
    app.add_handler(CommandHandler("fileid", fileid_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_topup, pattern=r"^topup:"))

    # Topup capture: proof then amount
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, topup_capture_proof))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, topup_capture_amount))

    # Admin uploads catcher (thumb/qr + fileid)
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, admin_upload_catcher))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
