import os
import random
import string
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db import connect, init_db, ensure_user, get_balance, set_setting, get_setting

# ================== ENV ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa")

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{n:,}"

def gen_public_topup_id() -> str:
    token = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"shopnluna:topup_{token}"

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📦 List Produk"), KeyboardButton("💰 Balance")],
            [KeyboardButton("➕ Add Balance"), KeyboardButton("💬 Chat Admin")],
            [KeyboardButton("❌ Cancel")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

# ================== Products helpers ==================
def parse_product(name: str):
    if "|" in name:
        a, b = name.split("|", 1)
        return a.strip(), b.strip()
    return "All", name.strip()

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

# ================== TOPUP helpers ==================
def create_topup(user_id: int, amount: int) -> tuple[int, str]:
    public_id = gen_public_topup_id()
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO topups(user_id, amount, status, public_id) VALUES(%s,%s,'PENDING',%s) RETURNING id",
            (user_id, amount, public_id)
        )
        tid = int(cur.fetchone()[0])
        db.commit()
    return tid, public_id

def set_topup_method(topup_id: int, method: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET method=%s WHERE id=%s", (method, topup_id))
        db.commit()

def attach_topup_proof(topup_id: int, proof_file_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET proof_file_id=%s WHERE id=%s", (proof_file_id, topup_id))
        db.commit()

def get_latest_pending_topup(user_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT id, public_id, amount, method, status, proof_file_id
            FROM topups
            WHERE user_id=%s AND status='PENDING'
            ORDER BY created_at DESC
            LIMIT 1
        """, (user_id,))
        return cur.fetchone()

def approve_topup(public_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT id, user_id, status, amount
            FROM topups
            WHERE public_id=%s
        """, (public_id,))
        row = cur.fetchone()
        if not row:
            return None
        tid, user_id, status, amount = row
        if status != "PENDING":
            return None

        cur.execute("UPDATE topups SET status='APPROVED' WHERE id=%s", (tid,))
        cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (int(amount), int(user_id)))
        db.commit()
        return int(user_id), int(amount)

def reject_topup(public_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT id, status FROM topups WHERE public_id=%s
        """, (public_id,))
        row = cur.fetchone()
        if not row:
            return False
        tid, status = row
        if status != "PENDING":
            return False
        cur.execute("UPDATE topups SET status='REJECTED' WHERE id=%s", (tid,))
        db.commit()
        return True

# ================== STARTUP ==================
async def on_startup(app: Application):
    init_db()

# ================== UI ==================
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    cats = list_categories()
    if not cats:
        msg = "No products yet."
        if update.message:
            await update.message.reply_text(msg, reply_markup=main_keyboard())
        else:
            await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=main_keyboard())
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
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close")])

    text = "🛍 Choose a category:"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))

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
    await update.message.reply_text(text, reply_markup=main_keyboard())
    await show_categories(update, context)

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)
    await update.message.reply_text(f"💳 Balance: {money(bal)}", reply_markup=main_keyboard())
    await show_categories(update, context)

# ================== CALLBACKS ==================
async def show_add_balance_amounts(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("₱50", callback_data="topup_amt:50"),
         InlineKeyboardButton("₱100", callback_data="topup_amt:100")],
        [InlineKeyboardButton("₱300", callback_data="topup_amt:300"),
         InlineKeyboardButton("₱500", callback_data="topup_amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="topup_amt:1000")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pay_cancel")]
    ]
    await context.bot.send_message(
        chat_id=chat_id,
        text="➕ Add Balance\n━━━━━━━━━━━━━━\nChoose amount:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id

    if data == "close":
        try:
            await q.message.delete()
        except:
            pass
        return

    # Products
    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        products = list_products_in_category(cat)
        buttons = [[InlineKeyboardButton(f"• {title}", callback_data=f"prod:{pid}")]
                   for pid, title in products]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="cats_refresh")])
        await q.edit_message_text(
            f"✨ {cat}\n━━━━━━━━━━━━━━\nSelect product:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data == "cats_refresh":
        await show_categories(update, context, chat_id=q.message.chat_id)
        return

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        variants = list_variants_for_product(pid)
        buttons = []
        for vid, vname, price, file_id in variants:
            stock_label = "∞" if file_id else str(count_stock(vid))
            buttons.append([InlineKeyboardButton(
                f"{vname} — {money(price)} (Stock: {stock_label})",
                callback_data=f"buy:{vid}"
            )])
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="cats_refresh")])
        await q.edit_message_text("Choose package:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("buy:"):
        context.user_data["variant"] = int(data.split(":")[1])
        await q.message.reply_text("Enter quantity (1–50):", reply_markup=main_keyboard())
        return

    # Add balance flow
    if data.startswith("topup_amt:"):
        amt = int(data.split(":")[1])
        tid, public_id = create_topup(user_id, amt)
        context.user_data["awaiting_topup_id"] = tid
        await q.message.reply_text(
            f"🧾 Top Up Created\n"
            f"━━━━━━━━━━━━━━\n"
            f"TopUp ID: {public_id}\n"
            f"Amount: {money(amt)}\n\n"
            "Choose payment method:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟦 GCash", callback_data="paymethod:gcash")],
                [InlineKeyboardButton("🟩 GoTyme", callback_data="paymethod:gotyme")],
                [InlineKeyboardButton("❌ Cancel", callback_data="pay_cancel")],
            ])
        )
        return

    if data.startswith("paymethod:"):
        method = data.split(":", 1)[1]
        tid = context.user_data.get("awaiting_topup_id")
        if not tid:
            await q.message.reply_text("❌ No active top up. Tap Add Balance again.", reply_markup=main_keyboard())
            return

        set_topup_method(tid, method)
        row = get_latest_pending_topup(user_id)
        public_id = row[1]
        amount = row[2]

        if method == "gcash":
            qr_file_id = get_setting("QR_GCASH_FILE_ID")
            title = "GCash"
        else:
            qr_file_id = get_setting("QR_GOTYME_FILE_ID")
            title = "GoTyme"

        pay_text = get_setting("PAYMENT_TEXT") or (
            "📌 Payment Instructions:\n"
            "1) Scan the QR\n"
            "2) Pay exact amount\n"
            "3) Upload screenshot proof here\n"
        )

        msg = (
            f"✅ {title} Payment\n"
            f"━━━━━━━━━━━━━━\n"
            f"TopUp ID: {public_id}\n"
            f"Amount: {money(int(amount))}\n\n"
            f"{pay_text}\n"
            f"Admin: {ADMIN_USERNAME}\n\n"
            "📩 Now upload your proof screenshot here.\n"
            "⏳ Waiting for proof..."
        )

        context.user_data["awaiting_proof"] = True

        if qr_file_id:
            await q.message.reply_photo(photo=qr_file_id, caption=msg, reply_markup=main_keyboard())
        else:
            await q.message.reply_text(
                msg + "\n\n⚠️ Admin has not set QR yet. Admin use /setqrgcash or /setqrgoytme.",
                reply_markup=main_keyboard()
            )
        return

    if data == "pay_cancel":
        context.user_data["awaiting_topup_id"] = None
        context.user_data["awaiting_proof"] = False
        await q.message.reply_text("❌ Cancelled.", reply_markup=main_keyboard())
        return

    # ✅ Admin inline approve/reject
    if data.startswith("admin_approve:"):
        if not is_admin(user_id):
            await q.message.reply_text("❌ Admin only.")
            return
        public_id = data.split(":", 1)[1]
        res = approve_topup(public_id)
        if not res:
            await q.edit_message_caption(caption="❌ Not found or already handled.")
            return
        target_user_id, amt = res
        await q.edit_message_caption(caption=f"✅ APPROVED\nTopUp ID: {public_id}\nAdded: {money(amt)}")
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"✅ Top up approved!\nTopUp ID: {public_id}\nAdded: {money(amt)}"
            )
        except:
            pass
        return

    if data.startswith("admin_reject:"):
        if not is_admin(user_id):
            await q.message.reply_text("❌ Admin only.")
            return
        public_id = data.split(":", 1)[1]
        ok = reject_topup(public_id)
        if not ok:
            await q.edit_message_caption(caption="❌ Not found or already handled.")
            return
        await q.edit_message_caption(caption=f"❌ REJECTED\nTopUp ID: {public_id}")
        return

# ================== BUY quantity text ==================
async def buy_qty_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "variant" not in context.user_data:
        return

    user_id = update.effective_user.id
    ensure_user(user_id)

    try:
        qty = int(update.message.text.strip())
        if qty < 1 or qty > 50:
            await update.message.reply_text("Qty must be 1–50.", reply_markup=main_keyboard())
            return
    except:
        await update.message.reply_text("Please enter a number.", reply_markup=main_keyboard())
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
        await update.message.reply_text("Package not found.", reply_markup=main_keyboard())
        return

    price, _product_full, vname, file_id = row
    total = int(price) * qty
    bal = get_balance(user_id)

    if file_id:
        if qty != 1:
            await update.message.reply_text("❌ File products quantity must be 1.", reply_markup=main_keyboard())
            return
        if bal < total:
            await update.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.", reply_markup=main_keyboard())
            return

        with connect() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE users SET balance=balance-%s WHERE user_id=%s AND balance>=%s",
                (total, user_id, total)
            )
            if cur.rowcount != 1:
                db.rollback()
                await update.message.reply_text("❌ Insufficient balance.", reply_markup=main_keyboard())
                return
            db.commit()

        await update.message.reply_document(
            document=file_id,
            caption=f"✅ Purchase Successful\n📦 {vname}\nTotal: {money(total)}",
        )
        return

    if bal < total:
        await update.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.", reply_markup=main_keyboard())
        return

    if count_stock(variant_id) < qty:
        await update.message.reply_text("❌ Not enough stock.", reply_markup=main_keyboard())
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
            await update.message.reply_text("❌ Insufficient balance.", reply_markup=main_keyboard())
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
            await update.message.reply_text("❌ Stock changed. Try again.", reply_markup=main_keyboard())
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
    await update.message.reply_text(f"✅ Purchase Successful\n📦 {vname}\n\n{payloads}", reply_markup=main_keyboard())

# ================== Reply buttons ==================
async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    user_id = update.effective_user.id
    ensure_user(user_id)

    if txt == "📦 List Produk":
        await show_categories(update, context)
        return

    if txt == "💰 Balance":
        bal = get_balance(user_id)
        await update.message.reply_text(
            f"💳 Your balance: {money(bal)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Balance", callback_data="open_add_balance")]
            ])
        )
        return

    if txt == "➕ Add Balance":
        await show_add_balance_amounts(update.message.chat_id, context)
        return

    if txt == "💬 Chat Admin":
        await update.message.reply_text(f"Message admin: {ADMIN_USERNAME}", reply_markup=main_keyboard())
        return

    if txt == "❌ Cancel":
        context.user_data.clear()
        await update.message.reply_text("✅ Cancelled.", reply_markup=main_keyboard())
        return

# handle inline from Balance
async def open_add_balance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_add_balance_amounts(q.message.chat_id, context)

# ================== Customer proof auto ==================
async def capture_any_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        return

    row = get_latest_pending_topup(user_id)
    if not row:
        await update.message.reply_text("❌ No pending top up found. Tap Add Balance first.", reply_markup=main_keyboard())
        return

    topup_id, public_id, amount, method, status, proof = row

    if proof and proof.strip():
        await update.message.reply_text(
            f"✅ Proof already sent for:\nTopUp ID: {public_id}\n⏳ Waiting for admin confirmation.",
            reply_markup=main_keyboard()
        )
        return

    attach_topup_proof(int(topup_id), file_id)

    await update.message.reply_text(
        f"✅ Proof received!\n"
        f"━━━━━━━━━━━━━━\n"
        f"TopUp ID: {public_id}\n"
        f"Amount: {money(int(amount))}\n"
        f"Method: {method or 'not set'}\n\n"
        "⏳ Waiting for admin to confirm your payment.",
        reply_markup=main_keyboard()
    )

    # Send admin notification with inline buttons
    admin_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve:{public_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject:{public_id}")
        ]
    ])

    caption = (
        f"🧾 Topup proof submitted\n"
        f"━━━━━━━━━━━━━━\n"
        f"TopUp ID: {public_id}\n"
        f"User ID: {user_id}\n"
        f"Amount: {money(int(amount))}\n"
        f"Method: {method}\n\n"
        "Tap Approve/Reject:"
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=caption,
                reply_markup=admin_buttons
            )
        except:
            pass

# ================== ADMIN: set QR ==================
async def setqrgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    context.user_data["awaiting_qr"] = "gcash"
    await update.message.reply_text("✅ Now send your GCash QR as PHOTO (no caption needed).")

async def setqrgoytme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    context.user_data["awaiting_qr"] = "gotyme"
    await update.message.reply_text("✅ Now send your GoTyme QR as PHOTO (no caption needed).")

async def capture_payment_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    mode = context.user_data.get("awaiting_qr")
    if mode not in ("gcash", "gotyme"):
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please send as PHOTO (not document).")
        return

    file_id = update.message.photo[-1].file_id
    if mode == "gcash":
        set_setting("QR_GCASH_FILE_ID", file_id)
        await update.message.reply_text("✅ GCash QR saved!")
    else:
        set_setting("QR_GOTYME_FILE_ID", file_id)
        await update.message.reply_text("✅ GoTyme QR saved!")

    context.user_data["awaiting_qr"] = None

async def setpaytext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.replace("/setpaytext", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage:\n/setpaytext <instructions text>")
        return
    set_setting("PAYMENT_TEXT", text)
    await update.message.reply_text("✅ Payment instructions saved!")

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))

    # admin commands
    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgoytme", setqrgoytme_cmd))
    app.add_handler(CommandHandler("setpaytext", setpaytext_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(open_add_balance_cb, pattern=r"^open_add_balance$"))
    app.add_handler(CallbackQueryHandler(on_callback))

    # IMPORTANT ORDER:
    # 1) Admin QR photos first
    app.add_handler(MessageHandler(filters.PHOTO, capture_payment_qr))

    # 2) Customer proofs (photo or docs)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, capture_any_proof))

    # buy qty
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty_message))

    # reply keyboard menu
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_menu))

    print("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
