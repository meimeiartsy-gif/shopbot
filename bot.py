import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from db import (
    init_db, ensure_user, get_balance,
    set_setting, get_setting,
    create_topup, attach_topup_proof, get_topup,
    approve_topup, reject_topup, list_pending_topups,
    connect
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

ADMIN_USERNAME = "@lovebylunaa"   # your username
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

print("BOT.PY STARTED")


# ================== UI HELPERS ==================
def money(n: int) -> str:
    return f"₱{n:,}"


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def main_menu_keyboard():
    # Reply keyboard (shows under chat input)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🛍 List Products"), KeyboardButton("➕ Add Balance")],
            [KeyboardButton("💰 Balance"), KeyboardButton("💬 Chat Admin")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def parse_product(name: str):
    # "Category | Product"
    if "|" in name:
        a, b = name.split("|", 1)
        return a.strip(), b.strip()
    return "All", name.strip()


# ================== DB product helpers ==================
def list_categories():
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT name FROM products ORDER BY id ASC")
        rows = cur.fetchall()
    cats = {parse_product(r[0])[0] for r in rows}
    return sorted(cats)


def list_products_in_category(category: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT id, name FROM products ORDER BY id ASC")
        rows = cur.fetchall()
    out = []
    for pid, pname in rows:
        cat, title = parse_product(pname)
        if cat == category:
            out.append((pid, title))
    return out


def list_variants_for_product(product_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, name, price, telegram_file_id FROM variants WHERE product_id=%s ORDER BY id ASC",
            (product_id,)
        )
        return cur.fetchall()


def count_stock(variant_id: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM inventory_items WHERE variant_id=%s AND status='available'",
            (variant_id,)
        )
        return int(cur.fetchone()[0])


# ================== STARTUP ==================
async def on_startup(app: Application):
    print("Initializing DB...")
    init_db()
    print("DB ready.")


# ================== MENU / START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    text = (
        "🌙 Luna’s Prem Shop\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 Balance: {money(bal)}\n\n"
        "Choose an option:"
    )

    await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    # Also show products immediately
    await show_categories(update, context)


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = list_categories()

    if not cats:
        await update.message.reply_text("No products yet. Admin must add products first.")
        return

    buttons = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(f"✨ {c}", callback_data=f"cat:{c}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    await update.message.reply_text(
        "🛍 **Product Categories:**",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )


# ================== TEXT BUTTONS ==================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()

    if t == "🛍 List Products":
        await show_categories(update, context)
        return

    if t == "💰 Balance":
        user_id = update.effective_user.id
        ensure_user(user_id)
        bal = get_balance(user_id)

        buttons = [[InlineKeyboardButton("➕ Add Balance", callback_data="addbal")]]
        await update.message.reply_text(
            f"💳 Your balance: {money(bal)}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if t == "➕ Add Balance":
        await add_balance_flow(update, context)
        return

    if t == "💬 Chat Admin":
        await update.message.reply_text(f"Message admin here: https://t.me/{ADMIN_USERNAME.lstrip('@')}")
        return


# ================== ADD BALANCE FLOW (AUTO) ==================
AMOUNTS = [50, 100, 300, 500, 1000]

async def add_balance_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 1: choose amount
    btns = []
    row = []
    for a in AMOUNTS:
        row.append(InlineKeyboardButton(f"{money(a)}", callback_data=f"amt:{a}"))
        if len(row) == 3:
            btns.append(row)
            row = []
    if row:
        btns.append(row)

    btns.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_topup")])

    await update.message.reply_text(
        "➕ Add Balance\n━━━━━━━━━━━━━━\nChoose amount:",
        reply_markup=InlineKeyboardMarkup(btns)
    )


async def send_method_choice(q):
    btns = [
        [InlineKeyboardButton("GCash", callback_data="method:gcash")],
        [InlineKeyboardButton("GoTyme", callback_data="method:gotyme")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_topup")]
    ]
    await q.edit_message_text(
        f"Choose payment method for {money(q._bot_data['chosen_amount'])}:",
        reply_markup=InlineKeyboardMarkup(btns)
    )


def get_method_settings(method: str):
    if method == "gcash":
        qr_key = "QR_GCASH_FILE_ID"
        text_key = "PAYTEXT_GCASH"
        title = "GCash"
    else:
        qr_key = "QR_GOTYME_FILE_ID"
        text_key = "PAYTEXT_GOTYME"
        title = "GoTyme"
    return qr_key, text_key, title


async def show_qr_and_wait_proof(update_or_q_msg, context: ContextTypes.DEFAULT_TYPE, method: str, amount: int, topup_id: int):
    qr_key, text_key, title = get_method_settings(method)
    qr_file_id = get_setting(qr_key)
    guide_text = get_setting(text_key) or (
        "📌 Instructions:\n"
        "1) Scan the QR\n"
        "2) Pay exact amount. Strictly No Refund\n"
        "3) Upload screenshot proof here\n"
        f"Admin: {ADMIN_USERNAME}\n"
        "\n⚠️ No proof = No balance\n⚠️ Fake proof = banned"
    )

    caption = (
        f"✅ {title} Payment\n"
        "━━━━━━━━━━━━━━\n"
        f"Amount: {money(amount)}\n"
        f"Topup ID: {topup_id}\n\n"
        f"{guide_text}\n\n"
        "📤 Now upload your payment screenshot here (NO caption needed)."
    )

    if qr_file_id:
        await update_or_q_msg.reply_photo(photo=qr_file_id, caption=caption)
    else:
        await update_or_q_msg.reply_text(
            caption + f"\n\n⚠️ Admin has not set {title} QR yet."
        )


# ================== CALLBACKS ==================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id

    if data == "addbal":
        # from Balance message
        await add_balance_flow(update, context)
        return

    if data == "cancel_topup":
        context.user_data.pop("awaiting_proof", None)
        context.user_data.pop("pending_topup_id", None)
        context.user_data.pop("pending_amount", None)
        context.user_data.pop("pending_method", None)
        await q.message.reply_text("❌ Cancelled.")
        return

    if data.startswith("amt:"):
        amount = int(data.split(":")[1])
        context.user_data["pending_amount"] = amount

        # method buttons
        btns = [
            [InlineKeyboardButton("GCash", callback_data="method:gcash")],
            [InlineKeyboardButton("GoTyme", callback_data="method:gotyme")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_topup")]
        ]
        await q.edit_message_text(
            f"Amount selected: {money(amount)}\nChoose payment method:",
            reply_markup=InlineKeyboardMarkup(btns)
        )
        return

    if data.startswith("method:"):
        method = data.split(":")[1]
        amount = context.user_data.get("pending_amount")
        if not amount:
            await q.message.reply_text("Please choose amount again: tap ➕ Add Balance.")
            return

        # Create topup NOW (so it has an ID)
        topup_id = create_topup(user_id, amount, method)

        context.user_data["pending_topup_id"] = topup_id
        context.user_data["pending_method"] = method
        context.user_data["awaiting_proof"] = True

        await q.edit_message_text(
            f"✅ Created Topup\nTopup ID: {topup_id}\nAmount: {money(amount)}\nMethod: {method.upper()}\n\nSending QR…"
        )

        await show_qr_and_wait_proof(q.message, context, method, amount, topup_id)
        return

    # ----- Products browsing -----
    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        products = list_products_in_category(cat)

        if not products:
            await q.message.reply_text("No products in this category yet.")
            return

        buttons = [[InlineKeyboardButton(f"• {title}", callback_data=f"prod:{pid}")]
                   for pid, title in products]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="back_cats")])

        await q.edit_message_text(
            f"✨ {cat}\n━━━━━━━━━━━━━━\nSelect product:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data == "back_cats":
        # re-show categories
        cats = list_categories()
        buttons = []
        row = []
        for c in cats:
            row.append(InlineKeyboardButton(f"✨ {c}", callback_data=f"cat:{c}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await q.edit_message_text(
            "🛍 Product Categories:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        variants = list_variants_for_product(pid)

        if not variants:
            await q.message.reply_text("No variants yet for this product.")
            return

        buttons = []
        for vid, vname, price, file_id in variants:
            stock_label = "∞" if file_id else str(count_stock(vid))
            buttons.append([InlineKeyboardButton(
                f"{vname} — {money(int(price))} (Stock: {stock_label})",
                callback_data=f"buy:{vid}"
            )])

        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="back_cats")])
        await q.edit_message_text("Choose package:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("buy:"):
        context.user_data["variant"] = int(data.split(":")[1])
        await q.message.reply_text("Enter quantity (1–50):")
        return


# ================== BUY ==================
async def buy_qty_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "variant" not in context.user_data:
        return

    user_id = update.effective_user.id
    ensure_user(user_id)

    try:
        qty = int(update.message.text.strip())
        if qty < 1 or qty > 50:
            await update.message.reply_text("Qty must be 1–50.")
            return
    except:
        await update.message.reply_text("Please enter a number.")
        return

    variant_id = int(context.user_data.pop("variant"))

    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT v.price, v.name, v.telegram_file_id
            FROM variants v
            WHERE v.id=%s
        """, (variant_id,))
        row = cur.fetchone()

    if not row:
        await update.message.reply_text("Package not found.")
        return

    price, vname, file_id = row
    total = int(price) * qty
    bal = get_balance(user_id)

    # FILE PRODUCT (only 1 qty)
    if file_id:
        if qty != 1:
            await update.message.reply_text("❌ File products quantity must be 1.")
            return
        if bal < total:
            await update.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.")
            return

        with connect() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE users SET balance=balance-%s WHERE user_id=%s AND balance>=%s",
                (total, user_id, total)
            )
            if cur.rowcount != 1:
                db.rollback()
                await update.message.reply_text("❌ Insufficient balance.")
                return
            db.commit()

        await update.message.reply_document(
            document=file_id,
            caption=f"✅ Purchase Successful\n📦 {vname}\nTotal: {money(total)}"
        )
        return

    # STOCK PRODUCT
    if bal < total:
        await update.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.")
        return

    if count_stock(variant_id) < qty:
        await update.message.reply_text("❌ Not enough stock.")
        return

    # Deduct + deliver stock
    with connect() as db:
        cur = db.cursor()
        cur.execute("BEGIN;")

        cur.execute(
            "UPDATE users SET balance=balance-%s WHERE user_id=%s AND balance>=%s",
            (total, user_id, total)
        )
        if cur.rowcount != 1:
            db.rollback()
            await update.message.reply_text("❌ Insufficient balance.")
            return

        cur.execute("""
            SELECT id, payload
            FROM inventory_items
            WHERE variant_id=%s AND status='available'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        """, (variant_id, qty))
        items = cur.fetchall()
        if len(items) != qty:
            db.rollback()
            await update.message.reply_text("❌ Stock changed. Try again.")
            return

        now = datetime.utcnow()
        for iid, _payload in items:
            cur.execute("""
                UPDATE inventory_items
                SET status='sold', sold_to_user=%s, sold_at=%s
                WHERE id=%s
            """, (user_id, now, iid))

        db.commit()

    payloads = "\n".join(p for (_id, p) in items)
    await update.message.reply_text(f"✅ Purchase Successful\n📦 {vname}\n\n{payloads}")


# ================== AUTO PROOF CAPTURE (NO /paid) ==================
async def proof_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get("awaiting_proof"):
        return

    topup_id = context.user_data.get("pending_topup_id")
    amount = context.user_data.get("pending_amount")
    method = context.user_data.get("pending_method")

    if not topup_id:
        await update.message.reply_text("❌ No active topup. Tap ➕ Add Balance again.")
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("❌ Please upload the screenshot as a photo or document.")
        return

    ok = attach_topup_proof(topup_id, file_id)
    if not ok:
        await update.message.reply_text("❌ That topup is not pending anymore. Create a new one.")
        context.user_data["awaiting_proof"] = False
        return

    context.user_data["awaiting_proof"] = False

    await update.message.reply_text(
        "✅ Proof received!\n"
        f"Topup ID: {topup_id}\n"
        f"Amount: {money(amount)}\n"
        "⏳ Waiting for admin approval."
    )

    # Notify admins
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "🧾 NEW TOPUP PROOF\n"
                f"Topup ID: {topup_id}\n"
                f"User ID: {user_id}\n"
                f"Amount: {money(amount)}\n"
                f"Method: {str(method).upper()}\n\n"
                f"Approve: /approve {topup_id}\n"
                f"Reject: /reject {topup_id}"
            )
        )
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=file_id)
        except:
            try:
                await context.bot.send_document(chat_id=admin_id, document=file_id)
            except:
                pass


# ================== ADMIN COMMANDS ==================
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve <topup_id>")
        return

    topup_id = int(context.args[0])
    res = approve_topup(topup_id)
    if not res:
        await update.message.reply_text("❌ Topup not found or not pending.")
        return

    credited_user_id, amount = res
    await update.message.reply_text(f"✅ Approved topup #{topup_id} (+{money(amount)}).")
    await context.bot.send_message(
        chat_id=credited_user_id,
        text=f"✅ Topup approved!\nTopup ID: {topup_id}\nAdded: {money(amount)}"
    )


async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /reject <topup_id>")
        return

    topup_id = int(context.args[0])
    ok = reject_topup(topup_id)
    if not ok:
        await update.message.reply_text("❌ Topup not found or not pending.")
        return
    await update.message.reply_text(f"✅ Rejected topup #{topup_id}.")


async def topups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = list_pending_topups(10)
    if not rows:
        await update.message.reply_text("No pending topups.")
        return

    lines = ["🧾 Pending Topups (latest 10):\n"]
    for r in rows:
        lines.append(f"#{r['id']} | user {r['user_id']} | {money(int(r['amount']))} | {r['method'].upper()}")

    await update.message.reply_text("\n".join(lines))


# --- QR SETUP ---
async def setqrgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Send your GCash QR as PHOTO with caption: /setqrgcash")
        return

    set_setting("QR_GCASH_FILE_ID", file_id)
    await update.message.reply_text("✅ GCash QR saved! (Customers can now click GCash.)")


async def setqrgoytme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Send your GoTyme QR as PHOTO with caption: /setqrgoytme")
        return

    set_setting("QR_GOTYME_FILE_ID", file_id)
    await update.message.reply_text("✅ GoTyme QR saved! (Customers can now click GoTyme.)")


async def setpaytextgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.replace("/setpaytextgcash", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage:\n/setpaytextgcash <your gcash instructions text>")
        return
    set_setting("PAYTEXT_GCASH", text)
    await update.message.reply_text("✅ GCash instructions saved!")


async def setpaytextgotyme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.replace("/setpaytextgotyme", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage:\n/setpaytextgotyme <your gotyme instructions text>")
        return
    set_setting("PAYTEXT_GOTYME", text)
    await update.message.reply_text("✅ GoTyme instructions saved!")


# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # user
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))

    # admin
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))
    app.add_handler(CommandHandler("topups", topups_cmd))

    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgoytme", setqrgoytme_cmd))
    app.add_handler(CommandHandler("setpaytextgcash", setpaytextgcash_cmd))
    app.add_handler(CommandHandler("setpaytextgotyme", setpaytextgotyme_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # capture screenshots (auto proof)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, proof_capture))

    # buy qty / menu buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty_message))

    print("Starting bot...")
    app.run_polling()


if __name__ == "__main__":
    main()

