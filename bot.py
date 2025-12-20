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
    init_db, ensure_user, get_balance, add_balance,
    set_setting, get_setting,
    create_topup, get_topup, attach_topup_proof, approve_topup,
    connect
)

# ================== ENV ==================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa")

# IMPORTANT: put numeric telegram id(s) here in Railway Variables.
# Example: ADMIN_IDS = "123456789,987654321"
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

# ================== HELPERS ==================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

def parse_product(full_name: str):
    # "Category | Product Name"
    if "|" in full_name:
        a, b = full_name.split("|", 1)
        return a.strip(), b.strip()
    return "All", full_name.strip()

def main_menu_keyboard():
    # Reply Keyboard (shows at bottom like your screenshot)
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🛍 List Products"), KeyboardButton("➕ Add Balance")],
            [KeyboardButton("💰 Balance"), KeyboardButton("💬 Chat Admin")]
        ],
        resize_keyboard=True
    )

# ================== DB QUERIES FOR SHOP ==================
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
            out.append((int(pid), title))
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

# ================== COMMANDS / MENU ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    bal = get_balance(user_id)
    text = (
        "🌙 Luna’s Prem Shop\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 Balance: {money(bal)}\n\n"
        "Choose an option below 👇"
    )

    await update.message.reply_text(text, reply_markup=main_menu_keyboard())

    # also show categories directly after start
    await show_categories(update, context)

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)
    await update.message.reply_text(
        f"💳 Your balance: {money(bal)}\nChoose an option 👇",
        reply_markup=main_menu_keyboard()
    )

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Balance", callback_data="addbal")],
        [InlineKeyboardButton("🛍 List Products", callback_data="list_products")],
    ])

    await update.message.reply_text(
        f"💳 Your balance: {money(bal)}",
        reply_markup=main_menu_keyboard()
    )
    await update.message.reply_text("Quick actions:", reply_markup=buttons)

async def chat_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💬 Chat admin here: {ADMIN_USERNAME}",
        reply_markup=main_menu_keyboard()
    )

# ================== SHOP UI ==================
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = list_categories()
    if not cats:
        await update.message.reply_text("No products yet. Admin must add products.", reply_markup=main_menu_keyboard())
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

    buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="home_menu")])
    await update.message.reply_text(
        "🛍 Categories:\nChoose a category:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ================== ADD BALANCE FLOW ==================
AMOUNTS = [50, 100, 300, 500, 1000]

async def add_balance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Amount picker
    buttons = []
    row = []
    for a in AMOUNTS:
        row.append(InlineKeyboardButton(money(a), callback_data=f"topup_amt:{a}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home_menu")])

    await update.message.reply_text(
        "➕ Add Balance\nChoose amount:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def pick_payment_method(chat, amount: int):
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📲 GCash", callback_data=f"paymethod:gcash:{amount}"),
            InlineKeyboardButton("🏦 GoTyme", callback_data=f"paymethod:gotyme:{amount}")
        ],
        [InlineKeyboardButton("⬅ Back", callback_data="addbal")]
    ])
    await chat.reply_text(
        f"Amount: {money(amount)}\nChoose payment method:",
        reply_markup=kb
    )

def payment_instruction_text(method: str) -> str:
    # editable via settings if you want
    # keys: PAY_TEXT_GCASH, PAY_TEXT_GOTYME
    if method == "gcash":
        return get_setting("PAY_TEXT_GCASH") or (
            "✅ Payment Rules:\n"
            "• No proof = no balance added\n"
            "• No refund once payment sent\n"
            "• Fake screenshot = banned\n\n"
            "How to send proof:\n"
            "Send screenshot with caption:\n"
            "/paid <TOPUP_ID>\n\n"
            f"Message admin for faster: {ADMIN_USERNAME}"
        )
    return get_setting("PAY_TEXT_GOTYME") or (
        "✅ Payment Rules:\n"
        "• No proof = no balance added\n"
        "• No refund once payment sent\n"
        "• Fake screenshot = banned\n\n"
        "How to send proof:\n"
        "Send screenshot with caption:\n"
        "/paid <TOPUP_ID>\n\n"
        f"Message admin for faster: {ADMIN_USERNAME}"
    )

async def show_qr_and_instructions(chat, method: str, amount: int, topup_id: int):
    method_key = "QR_GCASH_FILE_ID" if method == "gcash" else "QR_GOTYME_FILE_ID"
    qr_file_id = get_setting(method_key)

    text = (
        f"🧾 TOPUP REQUEST #{topup_id}\n"
        "━━━━━━━━━━━━━━\n"
        f"Method: {method.upper()}\n"
        f"Amount: {money(amount)}\n\n"
        f"{payment_instruction_text(method)}\n\n"
        f"✅ Send screenshot with caption:\n/paid {topup_id}"
    )

    if qr_file_id:
        await chat.reply_photo(photo=qr_file_id, caption=text)
    else:
        await chat.reply_text(
            text + f"\n\n⚠️ Admin has not set {method.upper()} QR yet.\nAdmin: use /setqr{method} (then send QR photo)."
        )

# ================== /paid PROOF FLOW ==================
async def paid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    if not context.args:
        await update.message.reply_text("Usage: /paid <topup_id> (attach screenshot/photo)")
        return

    try:
        topup_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid topup id.")
        return

    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    if not proof_file_id:
        # user sent /paid first without image → wait for next photo
        context.user_data["awaiting_proof_topup_id"] = topup_id
        await update.message.reply_text("✅ Now send the screenshot as PHOTO (or file).")
        return

    row = get_topup(topup_id)
    if not row:
        await update.message.reply_text("Topup not found.")
        return

    _id, owner_id, amount, method, status, _proof = row
    if int(owner_id) != int(user_id):
        await update.message.reply_text("That topup ID is not yours.")
        return
    if status != "PENDING":
        await update.message.reply_text(f"Topup already {status}.")
        return

    attach_topup_proof(topup_id, proof_file_id)
    await update.message.reply_text("✅ Proof received. Waiting for admin approval.")

    # notify admins
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🧾 Topup proof submitted\n"
                f"Topup #{topup_id}\n"
                f"User: {user_id}\n"
                f"Amount: {money(amount)}\n"
                f"Method: {method.upper()}\n\n"
                f"Approve with:\n/approve {topup_id}"
            )
        )
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
        except:
            pass

async def capture_proof_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    If user typed /paid <id> first, we store awaiting_proof_topup_id then capture next photo/document.
    """
    topup_id = context.user_data.get("awaiting_proof_topup_id")
    if not topup_id:
        return

    user_id = update.effective_user.id

    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    if not proof_file_id:
        return

    row = get_topup(int(topup_id))
    if not row:
        await update.message.reply_text("Topup not found. Try again.")
        context.user_data["awaiting_proof_topup_id"] = None
        return

    _id, owner_id, amount, method, status, _proof = row
    if int(owner_id) != int(user_id):
        await update.message.reply_text("That topup ID is not yours.")
        context.user_data["awaiting_proof_topup_id"] = None
        return
    if status != "PENDING":
        await update.message.reply_text(f"Topup already {status}.")
        context.user_data["awaiting_proof_topup_id"] = None
        return

    attach_topup_proof(int(topup_id), proof_file_id)
    context.user_data["awaiting_proof_topup_id"] = None

    await update.message.reply_text("✅ Proof received. Waiting for admin approval.")

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🧾 Topup proof submitted\n"
                f"Topup #{topup_id}\n"
                f"User: {user_id}\n"
                f"Amount: {money(amount)}\n"
                f"Method: {method.upper()}\n\n"
                f"Approve with:\n/approve {topup_id}"
            )
        )
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
        except:
            pass

# ================== ADMIN: APPROVE TOPUP ==================
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /approve <topup_id>")
        return

    try:
        topup_id = int(context.args[0])
    except:
        await update.message.reply_text("Invalid topup id.")
        return

    result = approve_topup(topup_id)
    if not result:
        await update.message.reply_text("❌ Topup not found or not pending.")
        return

    user_id, amount = result
    await update.message.reply_text(f"✅ Approved topup #{topup_id}. Added {money(amount)}.")

    try:
        await context.bot.send_message(chat_id=user_id, text=f"✅ Topup approved! +{money(amount)}")
    except:
        pass

# ================== ADMIN: SET QR (GCASH/GOTYME) ==================
async def setqrgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    # caption method: photo + /setqrgcash together
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        set_setting("QR_GCASH_FILE_ID", file_id)
        await update.message.reply_text("✅ GCash QR saved!")
        return

    context.user_data["awaiting_qr"] = "gcash"
    await update.message.reply_text("✅ Now send the GCash QR as PHOTO.")

async def setqrgotyme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        set_setting("QR_GOTYME_FILE_ID", file_id)
        await update.message.reply_text("✅ GoTyme QR saved!")
        return

    context.user_data["awaiting_qr"] = "gotyme"
    await update.message.reply_text("✅ Now send the GoTyme QR as PHOTO.")

async def capture_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    method = context.user_data.get("awaiting_qr")
    if method not in ("gcash", "gotyme"):
        return

    if not update.message.photo:
        await update.message.reply_text("❌ Please send as PHOTO.")
        return

    file_id = update.message.photo[-1].file_id

    if method == "gcash":
        set_setting("QR_GCASH_FILE_ID", file_id)
        await update.message.reply_text("✅ GCash QR saved!")
    else:
        set_setting("QR_GOTYME_FILE_ID", file_id)
        await update.message.reply_text("✅ GoTyme QR saved!")

    context.user_data["awaiting_qr"] = None

# ================== CALLBACKS ==================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # menu back
    if data == "home_menu":
        await q.message.reply_text("Back to menu 👇", reply_markup=main_menu_keyboard())
        return

    if data == "list_products":
        await show_categories_from_callback(q, context)
        return

    if data == "addbal":
        await q.message.reply_text("➕ Add Balance:", reply_markup=main_menu_keyboard())
        await add_balance_start_from_callback(q, context)
        return

    if data.startswith("topup_amt:"):
        amt = int(data.split(":")[1])
        await pick_payment_method(q.message, amt)
        return

    if data.startswith("paymethod:"):
        _, method, amt_str = data.split(":")
        amount = int(amt_str)

        user_id = update.effective_user.id
        ensure_user(user_id)

        topup_id = create_topup(user_id, amount, method)
        await show_qr_and_instructions(q.message, method, amount, topup_id)
        return

    # product browse
    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        products = list_products_in_category(cat)

        buttons = [[InlineKeyboardButton(f"• {title}", callback_data=f"prod:{pid}")]
                   for pid, title in products]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="list_products")])

        await q.edit_message_text(
            f"✨ {cat}\nSelect product:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        variants = list_variants_for_product(pid)

        buttons = []
        for vid, vname, price, file_id in variants:
            stock_label = "∞" if file_id else str(count_stock(int(vid)))
            buttons.append([InlineKeyboardButton(
                f"{vname} — {money(price)} (Stock: {stock_label})",
                callback_data=f"buy:{int(vid)}"
            )])

        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="list_products")])
        await q.edit_message_text("Choose package:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("buy:"):
        variant_id = int(data.split(":")[1])
        context.user_data["variant"] = variant_id
        await q.message.reply_text("Enter quantity (1–50):")
        return

async def show_categories_from_callback(q, context):
    cats = list_categories()
    if not cats:
        await q.message.reply_text("No products yet.", reply_markup=main_menu_keyboard())
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
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home_menu")])

    await q.message.reply_text("🛍 Categories:", reply_markup=InlineKeyboardMarkup(buttons))

async def add_balance_start_from_callback(q, context):
    buttons = []
    row = []
    for a in AMOUNTS:
        row.append(InlineKeyboardButton(money(a), callback_data=f"topup_amt:{a}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home_menu")])

    await q.message.reply_text("Choose amount:", reply_markup=InlineKeyboardMarkup(buttons))

# ================== BUY FLOW (simple) ==================
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

    # file products: qty must be 1
    if file_id:
        if qty != 1:
            await update.message.reply_text("❌ File product qty must be 1.")
            return
        if bal < total:
            await update.message.reply_text(f"❌ Need {money(total)}. You have {money(bal)}.")
            return

        # deduct
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

    # stock products
    if bal < total:
        await update.message.reply_text(f"❌ Need {money(total)}. You have {money(bal)}.")
        return

    if count_stock(variant_id) < qty:
        await update.message.reply_text("❌ Not enough stock.")
        return

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

# ================== TEXT BUTTONS (Reply Keyboard) ==================
async def on_text_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🛍 List Products":
        await show_categories(update, context)
        return

    if text == "➕ Add Balance":
        await add_balance_start(update, context)
        return

    if text == "💰 Balance":
        await balance_cmd(update, context)
        return

    if text == "💬 Chat Admin":
        await chat_admin_cmd(update, context)
        return

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("paid", paid_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))

    # admin set qr
    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgotyme", setqrgotyme_cmd))

    # callbacks + media capture
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, capture_qr_photo))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, capture_proof_media))

    # reply keyboard buttons + qty input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty_message))

    print("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
