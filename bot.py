import os
from datetime import datetime
import aiosqlite
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

from db import (
    DB_PATH, init_db, ensure_user, get_balance,
    create_topup, attach_topup_proof, get_topup, approve_topup,
    add_product, add_variant, add_stock_items, count_stock
    set_setting, get_setting

)

print("BOT.PY STARTED")

# ================== HELPERS ==================
def parse_product(name: str):
    if "|" in name:
        a, b = name.split("|", 1)
        return a.strip(), b.strip()
    return "All", name.strip()

def money(n: int) -> str:
    return f"₱{n:,}"

async def db_conn():
    return aiosqlite.connect(DB_PATH)

# ================== ENV ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in .env")

# ================== DB HELPERS ==================
async def set_variant_file(db, variant_id: int, file_id: str):
    await db.execute(
        "UPDATE variants SET telegram_file_id=? WHERE id=?",
        (file_id, variant_id)
    )

async def get_variant_file(db, variant_id: int):
    cur = await db.execute(
        "SELECT telegram_file_id FROM variants WHERE id=?",
        (variant_id,)
    )
    row = await cur.fetchone()
    return row[0] if row else None

async def list_categories(db):
    cur = await db.execute("SELECT name FROM products")
    rows = await cur.fetchall()
    return sorted({parse_product(n)[0] for (n,) in rows})

async def list_products_in_category(db, category):
    cur = await db.execute("SELECT id, name FROM products")
    rows = await cur.fetchall()
    return [(pid, parse_product(n)[1]) for pid, n in rows if parse_product(n)[0] == category]

async def list_variants(db, product_id):
    cur = await db.execute("SELECT id, name, price FROM variants WHERE product_id=?", (product_id,))
    return await cur.fetchall()

# ================== STARTUP ==================
async def on_startup(app: Application):
    print("Initializing DB...")
    await init_db()
    print("DB ready.")

# ================== UI ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with await db_conn() as db:
        await ensure_user(db, user_id)
        bal = await get_balance(db, user_id)
        cats = await list_categories(db)
        await db.commit()

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
        async with await db_conn() as db:
            bal = await get_balance(db, user_id)
        await q.message.reply_text(f"💳 Balance: {money(bal)}")
        return

    if data == "topup":
        await q.message.reply_text("Type /topup to create a top-up request.")
        return

    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        async with await db_conn() as db:
            products = await list_products_in_category(db, cat)

        buttons = [[InlineKeyboardButton(t, callback_data=f"prod:{pid}")]
                   for pid, t in products]
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home")])

        await q.edit_message_text(
            f"✨ {cat}\n━━━━━━━━━━━━━━\nSelect product:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data == "home":
        await start(update, context)
        return

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        async with await db_conn() as db:
            variants = await list_variants(db, pid)

        buttons = []
        for vid, vname, price in variants:
            async with await db_conn() as db2:
                stock = await count_stock(db2, vid)
            buttons.append([InlineKeyboardButton(
                f"{vname} — {money(price)} (Stock: {stock})",
                callback_data=f"buy:{vid}"
            )])

        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="home")])
        await q.edit_message_text("Choose package:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("buy:"):
        context.user_data["variant"] = int(data.split(":")[1])
        await q.message.reply_text("Enter quantity:")
        return

# ================== BUY ==================
async def buy_qty_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "variant" not in context.user_data:
        return

    user_id = update.effective_user.id
    qty = int(update.message.text.strip())
    variant_id = context.user_data.pop("variant")

    async with await db_conn() as db:
        await ensure_user(db, user_id)

        cur = await db.execute("""
            SELECT v.price, p.name, v.name
            FROM variants v JOIN products p ON p.id=v.product_id
            WHERE v.id=?
        """, (variant_id,))
        row = await cur.fetchone()
        price, pname, vname = row
        total = price * qty
        bal = await get_balance(db, user_id)

        file_id = await get_variant_file(db, variant_id)

        # ===== FILE DELIVERY =====
        if file_id:
            if qty != 1:
                await update.message.reply_text("File products quantity = 1 only.")
                return
            if bal < total:
                await update.message.reply_text("Not enough balance.")
                return

            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "UPDATE users SET balance=balance-? WHERE user_id=?",
                (total, user_id)
            )
            await db.commit()

            await update.message.reply_document(
                document=file_id,
                caption=f"✅ Purchase Successful\n📦 {vname}\nTotal: {money(total)}"
            )
            return

        # ===== ACCOUNT / CODE DELIVERY =====
        stock = await count_stock(db, variant_id)
        if stock < qty:
            await update.message.reply_text("Not enough stock.")
            return
        if bal < total:
            await update.message.reply_text("Not enough balance.")
            return

        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "UPDATE users SET balance=balance-? WHERE user_id=?",
            (total, user_id)
        )

        cur = await db.execute("""
            SELECT id, payload FROM inventory_items
            WHERE variant_id=? AND status='available'
            LIMIT ?
        """, (variant_id, qty))
        items = await cur.fetchall()

        for iid, _ in items:
            await db.execute(
                "UPDATE inventory_items SET status='sold', sold_to_user=?, sold_at=? WHERE id=?",
                (user_id, datetime.utcnow().isoformat(), iid)
            )

        await db.commit()

    payloads = "\n".join(p for _, p in items)
    await update.message.reply_text(
        f"✅ Purchase Successful\n📦 {vname}\n\n{payloads}"
    )

# ================== ADMIN ==================
async def setfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    variant_id = int(context.args[0])
    file_id = update.message.document.file_id

    async with await db_conn() as db:
        await set_variant_file(db, variant_id, file_id)
        await db.commit()

    await update.message.reply_text("✅ File auto-delivery set.")

# ================== MAIN ==================
# ---------- TOPUP ----------

async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # admin only
    if update.effective_user.id not in ADMIN_IDS:
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Attach your QR image (photo) with caption: /setqr")
        return

    async with await db_conn() as db:
        await set_setting(db, "PAYMENT_QR_FILE_ID", file_id)
        await db.commit()

    await update.message.reply_text("✅ QR saved! /topup will now show your QR.")

async def setpaytext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # admin only
    if update.effective_user.id not in ADMIN_IDS:
        return

    text = update.message.text.replace("/setpaytext", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage:\n/setpaytext <your payment instructions text>")
        return

    async with await db_conn() as db:
        await set_setting(db, "PAYMENT_TEXT", text)
        await db.commit()

    await update.message.reply_text("✅ Payment instructions saved! /topup will show it.")

async def topup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with await db_conn() as db:
        await ensure_user(db, user_id)
        topup_id = await create_topup(db, user_id)

        qr_file_id = await get_setting(db, "PAYMENT_QR_FILE_ID")
        pay_text = await get_setting(db, "PAYMENT_TEXT")
        await db.commit()

    if not pay_text:
        pay_text = (
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

    # If button press: update.callback_query exists
    if update.callback_query:
        chat = update.callback_query.message
    else:
        chat = update.message

    if qr_file_id:
        await chat.reply_photo(photo=qr_file_id, caption=text)
    else:
        await chat.reply_text(text + "\n\n⚠️ Admin has not set QR yet. Use /setqr first.")



# ---------- ADMIN PRODUCTS ----------
async def addproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    name = " ".join(context.args)
    if not name:
        await update.message.reply_text("Usage: /addproduct <Category | Product Name>")
        return

    async with await db_conn() as db:
        pid = await add_product(db, name)
        await db.commit()

    await update.message.reply_text(f"✅ Product added. ID: {pid}")


async def addvariant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    if len(context.args) < 3:
        await update.message.reply_text("Usage: /addvariant <product_id> <price> <variant name>")
        return

    product_id = int(context.args[0])
    price = int(context.args[1])
    name = " ".join(context.args[2:])

    async with await db_conn() as db:
        vid = await add_variant(db, product_id, name, price)
        await db.commit()

    await update.message.reply_text(f"✅ Variant added. ID: {vid}")


async def addstock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text("Usage:\n/addstock <variant_id>\n<one item per line>")
        return

    variant_id = int(context.args[0])
    lines = update.message.text.splitlines()[1:]
    items = [x.strip() for x in lines if x.strip()]

    async with await db_conn() as db:
        count = await add_stock_items(db, variant_id, items)
        await db.commit()

    await update.message.reply_text(f"✅ Added {count} stock items.")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("setqr", setqr_cmd))
    app.add_handler(CommandHandler("setpaytext", setpaytext_cmd))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("topup", topup_cmd))
    app.add_handler(CommandHandler("paid", paid_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("addproduct", addproduct_cmd))
    app.add_handler(CommandHandler("addvariant", addvariant_cmd))
    app.add_handler(CommandHandler("addstock", addstock_cmd))
    app.add_handler(CommandHandler("setfile", setfile_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty_message))

    print("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
