import os
from datetime import datetime
import threading

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

from db import (
    connect,
    init_db,
    ensure_user,
    get_balance,
    add_balance,
    set_setting,
    get_setting
)


print("BOT.PY STARTED")

    return "OK", 200

def run_health_server():
    health_app.run(host="0.0.0.0", port=PORT)

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

from db import connect, init_db, ensure_user, get_balance, add_balance, set_setting, get_setting

print("BOT.PY STARTED")

# ================== ENV ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ================== HELPERS ==================
def parse_product(name: str):
    if "|" in name:
        a, b = name.split("|", 1)
        return a.strip(), b.strip()
    return "All", name.strip()

def money(n: int) -> str:
    return f"₱{n:,}"

# ================== POSTGRES HELPERS ==================
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

def take_stock_items(variant_id: int, qty: int):
    """
    Atomically take qty available items and mark sold.
    Returns list of payload strings.
    """
    with connect() as db:
        cur = db.cursor()

        # lock rows (safe)
        cur.execute("""
            SELECT id, payload
            FROM inventory_items
            WHERE variant_id=%s AND status='available'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        """, (variant_id, qty))
        rows = cur.fetchall()

        if len(rows) != qty:
            db.rollback()
            return None

        now = datetime.utcnow()
        for iid, _payload in rows:
            cur.execute("""
                UPDATE inventory_items
                SET status='sold', sold_to_user=%s, sold_at=%s
                WHERE id=%s
            """, (None, now, iid))

        db.commit()
        return [p for (_id, p) in rows]

# ================== TOPUP HELPERS ==================
def create_topup(user_id: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute("INSERT INTO topups(user_id,status) VALUES(%s,'PENDING') RETURNING id", (user_id,))
        topup_id = cur.fetchone()[0]
        db.commit()
        return int(topup_id)

def get_topup(topup_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT id, user_id, status, proof_file_id FROM topups WHERE id=%s", (topup_id,))
        return cur.fetchone()

def attach_topup_proof(topup_id: int, proof_file_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE topups SET proof_file_id=%s WHERE id=%s",
            (proof_file_id, topup_id)
        )
        db.commit()

def approve_topup(topup_id: int, amount: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT user_id, status FROM topups WHERE id=%s", (topup_id,))
        row = cur.fetchone()
        if not row:
            return None
        user_id, status = row
        if status != "PENDING":
            return None

        # mark approved
        cur.execute("UPDATE topups SET status='APPROVED' WHERE id=%s", (topup_id,))
        # credit balance
        cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
        db.commit()
        return int(user_id)

# ================== ADMIN PRODUCT HELPERS ==================
def add_product(name: str) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute("INSERT INTO products(name) VALUES(%s) RETURNING id", (name,))
        pid = cur.fetchone()[0]
        db.commit()
        return int(pid)

def add_variant(product_id: int, price: int, name: str) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO variants(product_id,name,price) VALUES(%s,%s,%s) RETURNING id",
            (product_id, name, price)
        )
        vid = cur.fetchone()[0]
        db.commit()
        return int(vid)

def add_stock_items(variant_id: int, items: list[str]) -> int:
    with connect() as db:
        cur = db.cursor()
        for it in items:
            cur.execute(
                "INSERT INTO inventory_items(variant_id,payload,status) VALUES(%s,%s,'available')",
                (variant_id, it)
            )
        db.commit()
    return len(items)

def set_variant_file(variant_id: int, file_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE variants SET telegram_file_id=%s WHERE id=%s", (file_id, variant_id))
        db.commit()

# ================== STARTUP ==================
async def on_startup(app: Application):
    print("Initializing DB...")
    init_db()
    print("DB ready.")

# ================== UI ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)
    cats = list_categories()

    text = (
        "🌙 Luna’s Prem Shop\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 Balance: {money(bal)}\n"
        "🛍 Choose a category:"
    )

    buttons = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(f"✨ {c}", callback_data=f"cat:{c}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton("➕ Top Up", callback_data="topup"),
        InlineKeyboardButton("💰 Balance", callback_data="balance")
    ])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# ================== CALLBACKS ==================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id

    if data == "balance":
        bal = get_balance(user_id)
        await q.message.reply_text(f"💳 Balance: {money(bal)}")
        return

    if data == "topup":
        await topup_cmd(update, context)
        return

    if data == "home":
        # easiest: tell user to /start again
        await q.message.reply_text("Type /start to return home.")
        return

    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        products = list_products_in_category(cat)

        buttons = [[InlineKeyboardButton(f"• {title}", callback_data=f"prod:{pid}")]
                   for pid, title in products]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home")])

        await q.edit_message_text(
            f"✨ {cat}\n━━━━━━━━━━━━━━\nSelect product:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
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

        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home")])
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
            SELECT v.price, p.name, v.name, v.telegram_file_id
            FROM variants v JOIN products p ON p.id=v.product_id
            WHERE v.id=%s
        """, (variant_id,))
        row = cur.fetchone()

    if not row:
        await update.message.reply_text("Package not found.")
        return

    price, product_full, vname, file_id = row
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

        # deduct balance
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

# ================== TOPUP ==================
async def topup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    topup_id = create_topup(user_id)
    qr_file_id = get_setting("PAYMENT_QR_FILE_ID")
    pay_text = get_setting("PAYMENT_TEXT") or (
        "📌 Payment Instructions:\n"
        "1) Pay using the QR (GCash/GoTyme)\n"
        "2) After paying, message admin and send proof\n"
        "3) Then send the screenshot here with:\n"
    )

    text = (
        "🧾 TOP UP (Manual)\n"
        "━━━━━━━━━━━━━━\n"
        f"{pay_text}\n\n"
        f"✅ Send your screenshot with caption:\n/paid {topup_id}\n\n"
        "⏳ Waiting for admin confirmation."
    )

    chat = update.callback_query.message if update.callback_query else update.message
    if qr_file_id:
        await chat.reply_photo(photo=qr_file_id, caption=text)
    else:
        await chat.reply_text(text + "\n\n⚠️ Admin has not set QR yet. Admin use /setqr first.")

async def paid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /paid <topup_id> (attach screenshot)")
        return
    topup_id = int(context.args[0])

    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    row = get_topup(topup_id)
    if not row:
        await update.message.reply_text("Topup not found.")
        return

    _id, owner_id, status, _proof = row
    if int(owner_id) != int(user_id):
        await update.message.reply_text("That topup ID is not yours.")
        return
    if status != "PENDING":
        await update.message.reply_text(f"Topup already {status}.")
        return

    attach_topup_proof(topup_id, proof_file_id or "")
    await update.message.reply_text("✅ Proof received. Waiting for admin approval.")

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"🧾 Topup proof submitted\nTopup #{topup_id}\nUser: {user_id}\nApprove: /approve {topup_id} <amount>"
        )
        if proof_file_id:
            try:
                await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
            except:
                pass

async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /approve <topup_id> <amount>")
        return

    topup_id = int(context.args[0])
    amount = int(context.args[1])

    credited_user_id = approve_topup(topup_id, amount)
    if not credited_user_id:
        await update.message.reply_text("❌ Topup not found or not pending.")
        return

    await update.message.reply_text(f"✅ Approved topup #{topup_id}, credited {money(amount)}.")
    await context.bot.send_message(chat_id=credited_user_id, text=f"✅ Topup approved. +{money(amount)}")

# ================== ADMIN: QR SETUP ==================
async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["awaiting_qr"] = True
    await update.message.reply_text("✅ Send your QR image now (as PHOTO).")

async def capture_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("awaiting_qr"):
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("❌ Please send the QR as a PHOTO (or document).")
        return

    set_setting("PAYMENT_QR_FILE_ID", file_id)
    context.user_data["awaiting_qr"] = False
    await update.message.reply_text("✅ QR saved! Now /topup will show it.")

async def setpaytext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    text = update.message.text.replace("/setpaytext", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage:\n/setpaytext <your payment instructions text>")
        return

    set_setting("PAYMENT_TEXT", text)
    await update.message.reply_text("✅ Payment instructions saved!")

# ================== ADMIN: PRODUCTS ==================
async def addproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Usage: /addproduct <Category | Product Name>")
        return
    pid = add_product(name)
    await update.message.reply_text(f"✅ Product added. ID: {pid}")

async def addvariant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /addvariant <product_id> <price> <variant name...>")
        return
    product_id = int(context.args[0])
    price = int(context.args[1])
    name = " ".join(context.args[2:]).strip()
    vid = add_variant(product_id, price, name)
    await update.message.reply_text(f"✅ Variant added. ID: {vid}")

async def addstock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage:\n/addstock <variant_id>\n<one item per line>")
        return
    variant_id = int(context.args[0])
    lines = update.message.text.splitlines()
    if len(lines) < 2:
        await update.message.reply_text("Put items on the next lines (one per line).")
        return
    items = [ln.strip() for ln in lines[1:] if ln.strip()]
    n = add_stock_items(variant_id, items)
    await update.message.reply_text(f"✅ Added {n} stock items.")

async def setfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /setfile <variant_id> (attach file as Document)")
        return
    if not update.message.document:
        await update.message.reply_text("Attach the file as Document with caption: /setfile <variant_id>")
        return
    variant_id = int(context.args[0])
    file_id = update.message.document.file_id
    set_variant_file(variant_id, file_id)
    await update.message.reply_text("✅ File auto-delivery set.")

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
threading.Thread(target=run_health_server, daemon=True).start()

    # handlers...
    app.run_polling()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("topup", topup_cmd))
    app.add_handler(CommandHandler("paid", paid_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))

    app.add_handler(CommandHandler("setqr", setqr_cmd))
    app.add_handler(CommandHandler("setpaytext", setpaytext_cmd))

    app.add_handler(CommandHandler("addproduct", addproduct_cmd))
    app.add_handler(CommandHandler("addvariant", addvariant_cmd))
    app.add_handler(CommandHandler("addstock", addstock_cmd))
    app.add_handler(CommandHandler("setfile", setfile_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, capture_qr_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty_message))

    print("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
