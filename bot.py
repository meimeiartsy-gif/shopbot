import os
import logging

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from db import (
    ensure_schema, ensure_user, fetch_one, fetch_all, exec_sql,
    set_setting, get_setting, purchase_variant
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

# -------------------- HELPERS --------------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{n:,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

def build_main_menu(uid: int):
    rows = [
        ["🏠 Home", "🛍 Shop"],
        ["👤 My Account", "💰 Add Balance"],
        ["📩 Reseller Register", "💬 Chat Admin"],
        ["🆘 Help"],
    ]
    if is_admin(uid):
        rows.append(["🔐 Admin"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

PANEL_TEXT_KEYS = {
    "home": "TEXT_HOME",
    "shop": "TEXT_SHOP",
    "help": "TEXT_HELP",
    "account": "TEXT_ACCOUNT",
}

PANEL_THUMB_KEYS = {
    "home": "THUMB_HOME",
    "shop": "THUMB_SHOP",
    "help": "THUMB_HELP",
    "account": "THUMB_ACCOUNT",
}

# -------------------- PANEL SEND (TEXT OR THUMB) --------------------
async def send_panel(msg, panel: str, default_text: str, menu=None):
    text_key = PANEL_TEXT_KEYS.get(panel, "")
    thumb_key = PANEL_THUMB_KEYS.get(panel, "")

    text = get_setting(text_key) or default_text
    thumb = get_setting(thumb_key)

    if thumb:
        await msg.reply_photo(
            photo=thumb,
            caption=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=menu
        )
    else:
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=menu)

# -------------------- START / HOME --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    await send_panel(
        update.message,
        "home",
        "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
        menu=build_main_menu(user.id)
    )

async def home_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)
    await send_panel(
        update.message,
        "home",
        "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
        menu=build_main_menu(user.id)
    )

# -------------------- HELP / CHAT ADMIN --------------------
async def help_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_panel(
        update.message,
        "help",
        "🆘 **Help**\n\n• Tap **Shop** to browse\n• Buy = auto delivery\n• Add Balance = admin approval\n",
        menu=None
    )

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_user = get_setting("ADMIN_USERNAME") or "@lovebylunaa"
    await update.message.reply_text(f"💬 Chat admin here:\n{admin_user}")

# -------------------- MY ACCOUNT --------------------
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    u = fetch_one("SELECT * FROM users WHERE user_id=%s", (user.id,))
    purchases = fetch_one("SELECT COUNT(*) AS c FROM purchases WHERE user_id=%s", (user.id,))
    count = int(purchases["c"]) if purchases else 0

    username = f"@{u['username']}" if u and u.get("username") else "(no username)"
    joined = u["joined_at"].strftime("%Y-%m-%d %H:%M") if u else "—"
    points = int(u["points"]) if u else 0
    reseller = "✅ YES" if u and u["is_reseller"] else "❌ NO"
    balance = int(u["balance"]) if u else 0

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
    await send_panel(update.message, "account", text, menu=None)

# -------------------- SHOP UI --------------------
async def shop_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    if not cats:
        await update.message.reply_text("No categories yet.")
        return

    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    await send_panel(
        update.message,
        "shop",
        "🛍 **Shop Categories**",
        menu=InlineKeyboardMarkup(rows)
    )

async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    # back to categories
    if action == "backcats":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        await q.edit_message_text("🛍 **Shop Categories**", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=InlineKeyboardMarkup(rows))
        return

    # products in category
    if action == "cat":
        cat_id = int(parts[2])
        prods = fetch_all("""
            SELECT id,name FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id
        """, (cat_id,))

        if not prods:
            await q.edit_message_text("No products yet in this category.",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")]
                                      ]))
            return

        rows = [[InlineKeyboardButton(p["name"], callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")])
        await q.edit_message_text("Select a product:", reply_markup=InlineKeyboardMarkup(rows))
        return

    # variants in product
    if action == "prod":
        pid = int(parts[2])
        prod = fetch_one("SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not prod:
            await q.edit_message_text("Product not found.")
            return

        vars_ = fetch_all("""
            SELECT id,name,price,bundle_qty FROM variants
            WHERE product_id=%s AND is_active=TRUE
            ORDER BY id
        """, (pid,))

        if not vars_:
            await q.edit_message_text("No packages yet.",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")]
                                      ]))
            return

        rows = []
        for v in vars_:
            available_items = fetch_one(
                "SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE",
                (v["id"],)
            )
            stock_items = int(available_items["c"]) if available_items else 0
            bundle_qty = int(v["bundle_qty"])
            stock_units = stock_items // bundle_qty if bundle_qty > 0 else 0

            rows.append([InlineKeyboardButton(
                f"{v['name']} — {money(int(v['price']))} (Stock: {stock_units})",
                callback_data=f"buy:open:{v['id']}"
            )])

        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")])

        text = f"🧾 **{prod['name']}**\n\n{prod.get('description') or ''}\n\nChoose a package below:"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

# -------------------- CHECKOUT UI --------------------
def checkout_keyboard(qty: int, variant_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"buy:qty:{variant_id}:-1"),
            InlineKeyboardButton(f"Qty: {qty}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"buy:qty:{variant_id}:+1"),
        ],
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"buy:confirm:{variant_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"buy:cancel:{variant_id}"),
        ],
        [InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")]
    ])

async def show_checkout(q, user_id: int, variant_id: int, qty: int):
    v = fetch_one("""
        SELECT v.id, v.name, v.price, v.bundle_qty, v.delivery_type,
               p.name AS product_name
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.edit_message_text("Package not found.")
        return

    u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user_id,))
    balance = int(u["balance"]) if u else 0

    available_items = fetch_one(
        "SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE",
        (variant_id,)
    )
    stock_items = int(available_items["c"]) if available_items else 0
    bundle_qty = int(v["bundle_qty"])
    stock_units = stock_items // bundle_qty if bundle_qty > 0 else 0

    total = int(v["price"]) * qty

    text = (
        "🧾 **Checkout**\n\n"
        f"Product: **{v['product_name']}**\n"
        f"Package: **{v['name']}**\n"
        f"Bundle qty: **{bundle_qty}**\n"
        f"Price each: **{money(int(v['price']))}**\n"
        f"Stock available: **{stock_units}**\n"
        f"Your balance: **{money(balance)}**\n\n"
        f"Total: **{money(total)}**\n\n"
        "Adjust quantity then confirm:"
    )

    await q.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=checkout_keyboard(qty, variant_id)
    )

async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    parts = q.data.split(":")
    action = parts[1]

    if action == "open":
        variant_id = int(parts[2])
        context.user_data["checkout_variant"] = variant_id
        context.user_data["checkout_qty"] = 1
        await show_checkout(q, user.id, variant_id, 1)
        return

    if action == "qty":
        variant_id = int(parts[2])
        delta = int(parts[3].replace("+", ""))

        if context.user_data.get("checkout_variant") != variant_id:
            context.user_data["checkout_variant"] = variant_id
            context.user_data["checkout_qty"] = 1

        qty = int(context.user_data.get("checkout_qty", 1))
        qty = max(1, qty + delta)
        context.user_data["checkout_qty"] = qty

        await show_checkout(q, user.id, variant_id, qty)
        return

    if action == "cancel":
        await q.edit_message_text("❌ Cancelled. Tap **Shop** to browse again.", parse_mode=ParseMode.MARKDOWN)
        return

    if action == "confirm":
        variant_id = int(parts[2])
        qty = int(context.user_data.get("checkout_qty", 1))

        result = purchase_variant(user.id, variant_id, qty)

        if not result["ok"]:
            if result["error"] == "NOT_ENOUGH_BALANCE":
                await q.edit_message_text(
                    f"❌ Not enough balance.\nNeed {money(result['need'])}, you have {money(result['have'])}.",
                    reply_markup=checkout_keyboard(qty, variant_id)
                )
                return
            if result["error"] == "NOT_ENOUGH_STOCK":
                await q.edit_message_text(
                    f"❌ Not enough stock.\nNeed {result['need']} items, only {result['have']} available.",
                    reply_markup=checkout_keyboard(qty, variant_id)
                )
                return

            await q.edit_message_text("❌ Purchase failed.")
            return

        v = result["variant"]
        stocks = result["stocks"]
        total = result["total"]

        # deliver
        if v["delivery_type"] == "file":
            sent = 0
            for s in stocks:
                if s.get("file_id"):
                    await context.bot.send_document(
                        chat_id=user.id,
                        document=s["file_id"],
                        caption=f"✅ Delivery: {v['product_name']} — {v['name']}"
                    )
                    sent += 1
            await context.bot.send_message(user.id, f"✅ Delivered {sent} file(s). Total paid: {money(total)}")
        else:
            lines = []
            for s in stocks:
                if s.get("delivery_text"):
                    lines.append(s["delivery_text"])
            if not lines:
                lines = ["(No delivery text found in stock rows)"]
            chunk = "\n".join(lines)
            await context.bot.send_message(
                user.id,
                f"✅ **Delivery**: {v['product_name']} — {v['name']}\n"
                f"Total paid: **{money(total)}**\n\n"
                f"{chunk}",
                parse_mode=ParseMode.MARKDOWN
            )

        await q.edit_message_text("✅ Purchase completed & delivered! Tap **Shop** to buy again.", parse_mode=ParseMode.MARKDOWN)
        return

# -------------------- ADD BALANCE --------------------
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    gcash_qr = get_setting("PAY_GCASH_QR")
    gotyme_qr = get_setting("PAY_GOTYME_QR")

    text = (
        "💰 **Add Balance**\n\n"
        "1) Pay via GCash / GoTyme\n"
        "2) Then send:\n"
        "• **Amount** (type number)\n"
        "• **Proof screenshot** (send as PHOTO)\n\n"
        "Choose where to pay:"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 GCash", callback_data="topup:pay:gcash"),
            InlineKeyboardButton("🏦 GoTyme", callback_data="topup:pay:gotyme"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="topup:cancel")]
    ])

    context.user_data["topup_step"] = "choose"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def cb_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    if action == "cancel":
        context.user_data.pop("topup_step", None)
        context.user_data.pop("topup_method", None)
        context.user_data.pop("topup_amount", None)
        await q.edit_message_text("❌ Cancelled.")
        return

    if action == "pay":
        method = parts[2]
        context.user_data["topup_method"] = method
        context.user_data["topup_step"] = "amount"

        qr_key = "PAY_GCASH_QR" if method == "gcash" else "PAY_GOTYME_QR"
        qr = get_setting(qr_key)

        if qr:
            await q.message.reply_photo(
                photo=qr,
                caption="Send the **amount** now (number only).",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await q.edit_message_text("Send the **amount** now (number only).", parse_mode=ParseMode.MARKDOWN)

        return

    if action in ("ok", "no"):
        # admin approve/reject
        if not is_admin(q.from_user.id):
            await q.message.reply_text("❌ Access denied.")
            return

        req_id = int(parts[2])
        req = fetch_one("SELECT * FROM topup_requests WHERE id=%s", (req_id,))
        if not req or req["status"] != "PENDING":
            await q.edit_message_caption("Already decided.")
            return

        if action == "ok":
            exec_sql("UPDATE users SET balance=balance+%s WHERE user_id=%s", (int(req["amount"]), int(req["user_id"])))
            exec_sql("UPDATE topup_requests SET status='APPROVED', decided_at=NOW(), admin_id=%s WHERE id=%s",
                     (q.from_user.id, req_id))
            await context.bot.send_message(int(req["user_id"]), f"✅ Topup approved! Added {money(int(req['amount']))}")
            await q.edit_message_caption("✅ Approved")
        else:
            exec_sql("UPDATE topup_requests SET status='REJECTED', decided_at=NOW(), admin_id=%s WHERE id=%s",
                     (q.from_user.id, req_id))
            await context.bot.send_message(int(req["user_id"]), "❌ Topup rejected.")
            await q.edit_message_caption("❌ Rejected")
        return

async def topup_amount_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("topup_step") != "amount":
        return

    try:
        amt = int(update.message.text.strip())
        if amt <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Send amount as number only (example: 100).")
        return

    context.user_data["topup_amount"] = amt
    context.user_data["topup_step"] = "proof"
    await update.message.reply_text("✅ Amount saved. Now send **proof screenshot** as PHOTO.", parse_mode=ParseMode.MARKDOWN)

async def topup_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("topup_step") != "proof":
        return

    user = update.effective_user
    ensure_user(user.id, user.username)

    proof_id = update.message.photo[-1].file_id
    amt = int(context.user_data.get("topup_amount", 0))

    # save request
    exec_sql(
        "INSERT INTO topup_requests(user_id, amount, proof_file_id) VALUES(%s,%s,%s)",
        (user.id, amt, proof_id)
    )
    req = fetch_one("SELECT id FROM topup_requests WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user.id,))
    req_id = int(req["id"])

    # notify admin
    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"topup:ok:{req_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"topup:no:{req_id}")
            ]
        ])
        await context.bot.send_photo(
            chat_id=admin_id,
            photo=proof_id,
            caption=(
                "💰 **Topup Request**\n\n"
                f"User: @{user.username}\n"
                f"ID: `{user.id}`\n"
                f"Amount: **{money(amt)}**\n\n"
                "Approve?"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )

    context.user_data.pop("topup_step", None)
    context.user_data.pop("topup_method", None)
    context.user_data.pop("topup_amount", None)

    await update.message.reply_text("✅ Submitted! Waiting for admin approval.")

# -------------------- RESELLER REGISTER (keep your old flow) --------------------
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
        await update.message.reply_text("Send your **Shop Link**:", parse_mode=ParseMode.MARKDOWN)
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

# -------------------- ADMIN PANEL --------------------
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return

    text = (
        "🔐 **Admin Panel** ✅\n\n"
        "Commands:\n"
        "• `/settext <home|shop|help|account> <text>`\n"
        "• `/setthumb <home|shop|help|account>` then send PHOTO\n"
        "• `/setpay <gcash|gotyme>` then send PHOTO (QR)\n"
        "• `/addbal <user_id> <amount>`\n"
        "• `/fileid` (reply to a photo/doc to get file_id)\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def settext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: /settext <home|shop|help|account> <text>")
        return

    panel = parts[1].strip().lower()
    text = parts[2].strip()

    if panel not in PANEL_TEXT_KEYS:
        await update.message.reply_text("Panel must be: home/shop/help/account")
        return

    set_setting(PANEL_TEXT_KEYS[panel], text)
    await update.message.reply_text(f"✅ Text updated for {panel}.")

async def setthumb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /setthumb <home|shop|help|account>")
        return

    panel = parts[1].strip().lower()
    if panel not in PANEL_THUMB_KEYS:
        await update.message.reply_text("Panel must be: home/shop/help/account")
        return

    context.user_data["await_thumb_panel"] = panel
    await update.message.reply_text("Send the PHOTO now.")

async def setpay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /setpay <gcash|gotyme>")
        return

    method = parts[1].strip().lower()
    if method not in ("gcash", "gotyme"):
        await update.message.reply_text("Method must be gcash or gotyme")
        return

    context.user_data["await_pay_qr"] = method
    await update.message.reply_text("Send the QR as PHOTO now.")

async def admin_photo_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # admin setthumb
    if is_admin(update.effective_user.id) and context.user_data.get("await_thumb_panel"):
        panel = context.user_data.pop("await_thumb_panel")
        file_id = update.message.photo[-1].file_id
        set_setting(PANEL_THUMB_KEYS[panel], file_id)
        await update.message.reply_text(f"✅ Thumbnail saved for {panel}.")
        return

    # admin setpay
    if is_admin(update.effective_user.id) and context.user_data.get("await_pay_qr"):
        method = context.user_data.pop("await_pay_qr")
        file_id = update.message.photo[-1].file_id
        key = "PAY_GCASH_QR" if method == "gcash" else "PAY_GOTYME_QR"
        set_setting(key, file_id)
        await update.message.reply_text(f"✅ {method.upper()} QR saved!")
        return

async def addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    parts = update.message.text.split()
    if len(parts) != 3:
        await update.message.reply_text("Usage: /addbal <user_id> <amount>")
        return
    uid = int(parts[1])
    amt = int(parts[2])
    exec_sql("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amt, uid))
    await update.message.reply_text(f"✅ Added {money(amt)} to {uid}")

async def fileid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Reply to a PHOTO/DOCUMENT message then type /fileid to get file_id.
    """
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a photo/document message then send /fileid")
        return

    r = msg.reply_to_message
    if r.photo:
        await msg.reply_text(f"PHOTO file_id:\n`{r.photo[-1].file_id}`", parse_mode=ParseMode.MARKDOWN)
        return
    if r.document:
        await msg.reply_text(f"DOCUMENT file_id:\n`{r.document.file_id}`", parse_mode=ParseMode.MARKDOWN)
        return

    await msg.reply_text("Replied message has no photo/document.")

# -------------------- BOOT --------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("settext", settext_cmd))
    app.add_handler(CommandHandler("setthumb", setthumb_cmd))
    app.add_handler(CommandHandler("setpay", setpay_cmd))
    app.add_handler(CommandHandler("addbal", addbal_cmd))
    app.add_handler(CommandHandler("fileid", fileid_cmd))

    # Menu buttons
    app.add_handler(MessageHandler(filters.Regex(r"^🏠 Home$"), home_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 My Account$"), my_account))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 Add Balance$"), add_balance))
    app.add_handler(MessageHandler(filters.Regex(r"^📩 Reseller Register$"), reseller_register))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Chat Admin$"), chat_admin))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_topup, pattern=r"^topup:"))

    # Topup flow text + photos
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, topup_amount_text))
    app.add_handler(MessageHandler(filters.PHOTO, topup_proof_photo))

    # Admin photo router for thumbs/pay QR
    app.add_handler(MessageHandler(filters.PHOTO, admin_photo_router))

    # Reseller form steps
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reseller_form_text))

    # IMPORTANT:
    # If you see "terminated by other getUpdates request",
    # you have TWO bot instances running. Stop the other one.
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
