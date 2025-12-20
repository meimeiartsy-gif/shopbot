import os
import random
import string
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db import (
    connect,
    init_db,
    ensure_user,
    get_balance,
    add_balance,
    set_setting,
    get_setting,
)

# ================== ENV ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa").strip()

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

SHOP_NAME = "🌙 Luna’s Prem Shop"

# ================== HELPERS ==================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

def main_menu_kb() -> ReplyKeyboardMarkup:
    # ReplyKeyboard buttons send TEXT, so our text router must handle these.
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📦 List Produk"), KeyboardButton("💰 Balance")],
            [KeyboardButton("➕ Add Balance"), KeyboardButton("💬 Chat Admin")],
            [KeyboardButton("❌ Cancel")],
        ],
        resize_keyboard=True
    )

def random_code(n=6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

def make_topup_code(topup_id: int) -> str:
    # Format requested: shopnluna: topup_id then numbers/letters
    return f"SHOPNLUNA-{topup_id}-{random_code(6)}"

def parse_product(name: str):
    # supports "Category | Product"
    if "|" in name:
        a, b = name.split("|", 1)
        return a.strip(), b.strip()
    return "All", name.strip()

# ================== DB HELPERS (PRODUCTS) ==================
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

# ================== DB HELPERS (TOPUPS) ==================
def create_topup(user_id: int, amount: int, method: str) -> tuple[int, str]:
    """
    Create topup row and return (topup_id, topup_code)
    status: PENDING
    """
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO topups(user_id,status,proof_file_id) VALUES(%s,'PENDING','') RETURNING id",
            (user_id,)
        )
        topup_id = int(cur.fetchone()[0])
        topup_code = make_topup_code(topup_id)

        # store details in settings-like pattern? Better store on topups table.
        # We'll store as a small payload string inside proof_file_id initially for metadata.
        # But to keep db.py unchanged, we use settings table for mapping is not good.
        # We'll create a new settings key per topup id (simple & works):
        set_setting(f"TOPUP:{topup_id}:CODE", topup_code)
        set_setting(f"TOPUP:{topup_id}:AMOUNT", str(int(amount)))
        set_setting(f"TOPUP:{topup_id}:METHOD", method)

        db.commit()
        return topup_id, topup_code

def set_topup_proof(topup_id: int, proof_file_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET proof_file_id=%s WHERE id=%s", (proof_file_id, topup_id))
        db.commit()

def get_topup_row(topup_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT id, user_id, status, proof_file_id FROM topups WHERE id=%s", (topup_id,))
        return cur.fetchone()

def approve_topup_db(topup_id: int) -> int | None:
    row = get_topup_row(topup_id)
    if not row:
        return None
    _id, user_id, status, _proof = row
    if status != "PENDING":
        return None

    amount = int(get_setting(f"TOPUP:{topup_id}:AMOUNT") or "0")
    if amount <= 0:
        return None

    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET status='APPROVED' WHERE id=%s", (topup_id,))
        cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
        db.commit()
    return int(user_id)

# ================== STARTUP ==================
async def on_startup(app: Application):
    print("Initializing DB...")
    init_db()
    print("DB ready.")

# ================== UI / MENUS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    text = (
        f"{SHOP_NAME}\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 Balance: {money(bal)}\n\n"
        "Choose an option:"
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb())

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    # Add quick buttons
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Balance", callback_data="add_balance")],
        [InlineKeyboardButton("📦 List Produk", callback_data="list_products")],
    ])
    await update.message.reply_text(f"💳 Your balance: {money(bal)}", reply_markup=kb)

# ================== PRODUCT LIST FLOW ==================
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = list_categories()
    if not cats:
        await update.message.reply_text("No products yet.")
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

    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home")])

    await update.message.reply_text(
        "🛍 Choose a category:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id

    if data == "home":
        await q.message.reply_text("Type /start to return home.", reply_markup=main_menu_kb())
        return

    if data == "list_products":
        await list_products(update, context)
        return

    if data == "add_balance":
        await add_balance_menu(update, context)
        return

    # category -> products
    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        products = list_products_in_category(cat)
        if not products:
            await q.message.reply_text("No products in this category.")
            return

        buttons = [[InlineKeyboardButton(f"• {title}", callback_data=f"prod:{pid}")]
                   for pid, title in products]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="list_products")])

        await q.message.reply_text(
            f"✨ {cat}\n━━━━━━━━━━━━━━\nSelect product:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # product -> variants
    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        variants = list_variants_for_product(pid)
        if not variants:
            await q.message.reply_text("No variants yet.")
            return

        buttons = []
        for vid, vname, price, file_id in variants:
            stock_label = "∞" if file_id else str(count_stock(vid))
            buttons.append([InlineKeyboardButton(
                f"{vname} — {money(price)} (Stock: {stock_label})",
                callback_data=f"buy:{vid}"
            )])

        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="list_products")])
        await q.message.reply_text("Choose package:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # buy -> ask qty
    if data.startswith("buy:"):
        context.user_data["variant"] = int(data.split(":")[1])
        await q.message.reply_text("Enter quantity (1–50):")
        return

    # TOPUP FLOW
    if data.startswith("amt:"):
        amount = int(data.split(":")[1])
        context.user_data["topup_amount"] = amount
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟦 GCash", callback_data="paymethod:GCASH")],
            [InlineKeyboardButton("🟩 GoTyme", callback_data="paymethod:GOTYME")],
            [InlineKeyboardButton("⬅ Back", callback_data="add_balance")]
        ])
        await q.message.reply_text(
            f"Choose payment method for {money(amount)}:",
            reply_markup=kb
        )
        return

    if data.startswith("paymethod:"):
        method = data.split(":", 1)[1]
        amount = int(context.user_data.get("topup_amount") or 0)
        if amount <= 0:
            await q.message.reply_text("Please choose amount again.")
            await add_balance_menu(update, context)
            return

        # Create topup now (so we have topup id/code before user sends proof)
        topup_id, topup_code = create_topup(user_id, amount, method)
        context.user_data["awaiting_proof"] = topup_id

        # Get QR file id and caption from settings
        if method == "GCASH":
            qr_file_id = get_setting("PAYMENT_QR_GCASH_FILE_ID")
            caption = get_setting("PAYMENT_QR_GCASH_CAPTION") or "Scan to pay via GCash."
        else:
            qr_file_id = get_setting("PAYMENT_QR_GOTYME_FILE_ID")
            caption = get_setting("PAYMENT_QR_GOTYME_CAPTION") or "Scan to pay via GoTyme."

        guide = (
            f"🧾 TOP UP REQUEST\n"
            "━━━━━━━━━━━━━━\n"
            f"Amount: {money(amount)}\n"
            f"Method: {method}\n"
            f"Topup ID: {topup_code}\n\n"
            "✅ After payment, just send your screenshot here.\n"
            "⏳ Waiting for admin approval.\n"
            f"Admin: {ADMIN_USERNAME}"
        )

        if qr_file_id:
            # send QR photo with caption + guide
            await q.message.reply_photo(photo=qr_file_id, caption=f"{caption}\n\n{guide}")
        else:
            await q.message.reply_text(
                f"⚠️ QR for {method} is not set yet.\nAdmin must set it first.\n\n{guide}"
            )
        return

    # ADMIN APPROVE BUTTONS
    if data.startswith("approve:"):
        if not is_admin(user_id):
            await q.message.reply_text("❌ Admin only.")
            return
        topup_id = int(data.split(":")[1])
        row = get_topup_row(topup_id)
        if not row:
            await q.message.reply_text("Topup not found.")
            return
        _id, topup_user_id, status, _proof = row
        if status != "PENDING":
            await q.message.reply_text(f"Topup already {status}.")
            return

        credited_user_id = approve_topup_db(topup_id)
        if not credited_user_id:
            await q.message.reply_text("❌ Could not approve (missing amount or already processed).")
            return

        amount = int(get_setting(f"TOPUP:{topup_id}:AMOUNT") or "0")
        topup_code = get_setting(f"TOPUP:{topup_id}:CODE") or f"TOPUP-{topup_id}"

        await q.message.reply_text(f"✅ Approved {topup_code} (+{money(amount)})")
        await context.bot.send_message(
            chat_id=credited_user_id,
            text=f"✅ Top up approved!\nTopup ID: {topup_code}\nAdded: {money(amount)}"
        )
        return

    if data.startswith("reject:"):
        if not is_admin(user_id):
            await q.message.reply_text("❌ Admin only.")
            return
        topup_id = int(data.split(":")[1])
        row = get_topup_row(topup_id)
        if not row:
            await q.message.reply_text("Topup not found.")
            return
        _id, topup_user_id, status, _proof = row
        if status != "PENDING":
            await q.message.reply_text(f"Topup already {status}.")
            return

        # Mark rejected
        with connect() as db:
            cur = db.cursor()
            cur.execute("UPDATE topups SET status='REJECTED' WHERE id=%s", (topup_id,))
            db.commit()

        topup_code = get_setting(f"TOPUP:{topup_id}:CODE") or f"TOPUP-{topup_id}"
        await q.message.reply_text(f"❌ Rejected {topup_code}")
        await context.bot.send_message(chat_id=topup_user_id, text=f"❌ Top up rejected.\see admin: {ADMIN_USERNAME}")
        return

# ================== ADD BALANCE MENU ==================
async def add_balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("₱50", callback_data="amt:50"),
         InlineKeyboardButton("₱100", callback_data="amt:100")],
        [InlineKeyboardButton("₱300", callback_data="amt:300"),
         InlineKeyboardButton("₱500", callback_data="amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="amt:1000")],
        [InlineKeyboardButton("⬅ Back", callback_data="home")]
    ])
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text("Choose top up amount:", reply_markup=kb)

# ================== BUY FLOW (qty) ==================
async def buy_qty_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # only runs if "variant" exists, routed by on_text_router
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
            SELECT v.price, p.name, v.name, v.telegram_file_id
            FROM variants v JOIN products p ON p.id=v.product_id
            WHERE v.id=%s
        """, (variant_id,))
        row = cur.fetchone()

    if not row:
        await update.message.reply_text("Package not found.")
        return

    price, _product_full, vname, file_id = row
    total = int(price) * qty
    bal = get_balance(user_id)

    # FILE PRODUCT
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

# ================== ADMIN: SET QR (GCASH/GOTYME) ==================
async def setqrgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["awaiting_qr"] = "GCASH"
    await update.message.reply_text("✅ Send your GCash QR as PHOTO now (no file).")

async def setqrgoytme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["awaiting_qr"] = "GOTYME"
    await update.message.reply_text("✅ Send your GoTyme QR as PHOTO now (no file).")

async def setqrcaption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setqrcaption gcash <text...>
    /setqrcaption gotyme <text...>
    """
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setqrcaption gcash|gotyme <caption text>")
        return
    which = context.args[0].lower()
    text = " ".join(context.args[1:]).strip()
    if which == "gcash":
        set_setting("PAYMENT_QR_GCASH_CAPTION", text)
        await update.message.reply_text("✅ GCash caption saved.")
    elif which == "gotyme":
        set_setting("PAYMENT_QR_GOTYME_CAPTION", text)
        await update.message.reply_text("✅ GoTyme caption saved.")
    else:
        await update.message.reply_text("First arg must be gcash or gotyme.")

async def capture_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    which = context.user_data.get("awaiting_qr")
    if not which:
        return

    if not update.message.photo:
        await update.message.reply_text("❌ Please send as PHOTO (not document/file).")
        return

    file_id = update.message.photo[-1].file_id

    if which == "GCASH":
        set_setting("PAYMENT_QR_GCASH_FILE_ID", file_id)
        await update.message.reply_text("✅ GCash QR saved!")
    else:
        set_setting("PAYMENT_QR_GOTYME_FILE_ID", file_id)
        await update.message.reply_text("✅ GoTyme QR saved!")

    context.user_data["awaiting_qr"] = None

# ================== PAYMENT PROOF (CUSTOMER SENDS SCREENSHOT) ==================
async def capture_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Customer sends photo/document after creating a topup request.
    We check context.user_data["awaiting_proof"] for this user.
    """
    user_id = update.effective_user.id
    topup_id = context.user_data.get("awaiting_proof")

    if not topup_id:
        # ignore random photos
        return

    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    if not proof_file_id:
        await update.message.reply_text("❌ Please send a screenshot as photo.")
        return

    row = get_topup_row(int(topup_id))
    if not row:
        await update.message.reply_text("Topup not found. Please create a new top up.")
        context.user_data["awaiting_proof"] = None
        return

    _id, owner_id, status, _old_proof = row
    if int(owner_id) != int(user_id):
        await update.message.reply_text("That topup is not yours.")
        return
    if status != "PENDING":
        await update.message.reply_text(f"Topup already {status}.")
        context.user_data["awaiting_proof"] = None
        return

    set_topup_proof(int(topup_id), proof_file_id)

    topup_code = get_setting(f"TOPUP:{topup_id}:CODE") or f"TOPUP-{topup_id}"
    amount = int(get_setting(f"TOPUP:{topup_id}:AMOUNT") or "0")
    method = get_setting(f"TOPUP:{topup_id}:METHOD") or "UNKNOWN"

    await update.message.reply_text(
        f"✅ Proof received!\nTopup ID: {topup_code}\nAmount: {money(amount)}\nMethod: {method}\n\n⏳ Waiting for admin approval."
    )

    # send to admins with approve/reject inline buttons
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{topup_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{topup_id}"),
        ]
    ])

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "🧾 Topup proof submitted\n"
                    f"Topup ID: {topup_code}\n"
                    f"User: {user_id}\n"
                    f"Amount: {money(amount)}\n"
                    f"Method: {method}\n"
                ),
                reply_markup=kb
            )
            await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
        except Exception as e:
            print("Failed to notify admin:", e)

    # clear awaiting proof for user
    context.user_data["awaiting_proof"] = None

# ================== TEXT BUTTON ROUTING ==================
async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "📦 List Produk":
        await list_products(update, context)
        return

    if text == "💰 Balance":
        await show_balance(update, context)
        return

    if text == "➕ Add Balance":
        await add_balance_menu(update, context)
        return

    if text == "💬 Chat Admin":
        await update.message.reply_text(f"Message admin: {ADMIN_USERNAME}")
        return

    if text == "❌ Cancel":
        # cancel current flows
        context.user_data.pop("variant", None)
        context.user_data.pop("topup_amount", None)
        context.user_data.pop("awaiting_proof", None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=main_menu_kb())
        return

    # fallback
    await update.message.reply_text("Please choose a button, or type /start.", reply_markup=main_menu_kb())

async def on_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If user is in buy quantity mode
    if "variant" in context.user_data:
        await buy_qty_message(update, context)
        return

    # otherwise menu
    await on_text_menu(update, context)

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # Commands
    app.add_handler(CommandHandler("start", start))

    # Admin QR commands
    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgoytme", setqrgoytme_cmd))
    app.add_handler(CommandHandler("setqrgotyme", setqrgoytme_cmd))  # alias
    app.add_handler(CommandHandler("setqrcaption", setqrcaption_cmd))

    # Callbacks (inline buttons)
    app.add_handler(CallbackQueryHandler(on_callback))

    # Photos/Documents:
    # 1) admin QR upload
    # 2) customer payment proof
    app.add_handler(MessageHandler(filters.PHOTO, capture_qr_photo))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, capture_payment_proof))

    # ONE text router for reply keyboard + qty input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_router))

    print("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
