# bot.py
# Python Telegram Bot v21.6
# Requirements:
#   python-telegram-bot==21.6
#   python-dotenv==1.0.1
#   psycopg2-binary==2.9.9
#
# ENV needed:
#   BOT_TOKEN=...
#   DATABASE_URL=postgresql://...
#   ADMIN_IDS=12345,67890        (comma-separated Telegram user IDs)
#   GCASH_QR_FILE_ID=...         (Telegram file_id for your GCash QR image)
#   GOTYME_QR_FILE_ID=...        (Telegram file_id for your GoTyme QR image)

import os
import uuid
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple, Any

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

ADMIN_IDS = set()
for x in (os.getenv("ADMIN_IDS", "") or "").split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.add(int(x))

GCASH_QR_FILE_ID = (os.getenv("GCASH_QR_FILE_ID") or "").strip()
GOTYME_QR_FILE_ID = (os.getenv("GOTYME_QR_FILE_ID") or "").strip()

AMOUNTS = [50, 100, 300, 500, 1000]
PAYMENT_METHODS = ["GCASH", "GOTYME"]

# ----------------------------
# Database helpers
# ----------------------------

def db_conn():
    # Railway provides DATABASE_URL
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def db_exec(sql: str, params: Tuple[Any, ...] = ()):
    conn = db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    finally:
        conn.close()

def db_fetchall(sql: str, params: Tuple[Any, ...] = ()) -> List[tuple]:
    conn = db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    finally:
        conn.close()

def db_fetchone(sql: str, params: Tuple[Any, ...] = ()) -> Optional[tuple]:
    rows = db_fetchall(sql, params)
    return rows[0] if rows else None

async def adb_exec(sql: str, params: Tuple[Any, ...] = ()):
    return await asyncio.to_thread(db_exec, sql, params)

async def adb_fetchall(sql: str, params: Tuple[Any, ...] = ()) -> List[tuple]:
    return await asyncio.to_thread(db_fetchall, sql, params)

async def adb_fetchone(sql: str, params: Tuple[Any, ...] = ()) -> Optional[tuple]:
    return await asyncio.to_thread(db_fetchone, sql, params)

async def ensure_schema():
    # users/products/topups/purchases assumed exist, but we create if missing.
    # Also create deliveries/settings to support dedup + editable text.
    await adb_exec("""
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    await adb_exec("""
    CREATE TABLE IF NOT EXISTS products(
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        price INTEGER NOT NULL DEFAULT 0,
        file_id TEXT,
        photo_file_id TEXT,
        category TEXT DEFAULT 'General',
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    await adb_exec("""
    CREATE TABLE IF NOT EXISTS purchases(
        id SERIAL PRIMARY KEY,
        transaction_id TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL,
        product_id INTEGER NOT NULL,
        price INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    await adb_exec("""
    CREATE TABLE IF NOT EXISTS topups(
        id SERIAL PRIMARY KEY,
        topup_id TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL,
        amount INTEGER NOT NULL,
        method TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        proof_file_id TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        decided_at TIMESTAMP,
        admin_id BIGINT
    );
    """)

    # DEDUP DELIVERY: if purchase_id already exists here, we will never deliver again.
    await adb_exec("""
    CREATE TABLE IF NOT EXISTS deliveries(
        purchase_id BIGINT PRIMARY KEY,
        delivered_at TIMESTAMP DEFAULT NOW()
    );
    """)

    await adb_exec("""
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    # Seed default texts if missing
    await adb_exec("""
    INSERT INTO settings(key,value) VALUES
      ('welcome', 'Welcome to Luna''s Prem Shop ✨\\n\\nUse the buttons below to shop, add balance, or view history.'),
      ('help', 'Need help? Contact admin using *Chat Admin* button.'),
      ('instr_GCASH', 'GCash payment instructions (you can edit this later).'),
      ('instr_GOTYME', 'GoTyme payment instructions (you can edit this later).')
    ON CONFLICT (key) DO NOTHING;
    """)

# ----------------------------
# UI helpers
# ----------------------------

def main_menu_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("🛍️ Shop"), KeyboardButton("➕ Add Balance")],
        [KeyboardButton("💰 Balance"), KeyboardButton("🧾 History")],
        [KeyboardButton("💬 Chat Admin"), KeyboardButton("❓ Help")],
    ]
    if is_admin:
        rows.append([KeyboardButton("🛠️ Admin")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def money(n: int) -> str:
    return f"₱{n:,}"

async def get_setting(key: str) -> str:
    row = await adb_fetchone("SELECT value FROM settings WHERE key=%s", (key,))
    return row[0] if row else ""

async def upsert_user(user) -> None:
    await adb_exec("""
    INSERT INTO users(user_id, username, first_name)
    VALUES (%s,%s,%s)
    ON CONFLICT (user_id) DO UPDATE SET
      username = EXCLUDED.username,
      first_name = EXCLUDED.first_name
    """, (user.id, user.username or "", user.first_name or ""))

async def get_balance(user_id: int) -> int:
    row = await adb_fetchone("SELECT balance FROM users WHERE user_id=%s", (user_id,))
    return int(row[0]) if row else 0

# ----------------------------
# Routing state (simple)
# ----------------------------

@dataclass
class PendingTopup:
    method: Optional[str] = None
    amount: Optional[int] = None

# context.user_data keys used:
#   pending_topup: PendingTopup
#   waiting_proof: bool
#   admin_mode: str
#   admin_edit_key: str

# ----------------------------
# Handlers
# ----------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user)

    is_admin = user.id in ADMIN_IDS
    welcome = await get_setting("welcome")
    await update.message.reply_text(
        welcome,
        reply_markup=main_menu_kb(is_admin),
        parse_mode=ParseMode.MARKDOWN
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await get_setting("help")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = await get_balance(user_id)
    await update.message.reply_text(f"💰 Your balance: *{money(bal)}*", parse_mode=ParseMode.MARKDOWN)

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    topups = await adb_fetchall("""
      SELECT topup_id, amount, method, status, created_at
      FROM topups WHERE user_id=%s
      ORDER BY created_at DESC
      LIMIT 10
    """, (user_id,))
    purchases = await adb_fetchall("""
      SELECT p.transaction_id, pr.name, p.price, p.created_at
      FROM purchases p JOIN products pr ON pr.id=p.product_id
      WHERE p.user_id=%s
      ORDER BY p.created_at DESC
      LIMIT 10
    """, (user_id,))

    msg = ["🧾 *History*"]
    msg.append("\n*Top ups (last 10)*")
    if not topups:
        msg.append("_No top ups yet._")
    else:
        for tid, amt, m, st, created in topups:
            msg.append(f"• `{tid}` — {money(int(amt))} — {m} — *{st}*")

    msg.append("\n*Purchases (last 10)*")
    if not purchases:
        msg.append("_No purchases yet._")
    else:
        for tx, name, price, created in purchases:
            msg.append(f"• `{tx}` — {name} — {money(int(price))}")

    await update.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN)

# ----------------------------
# Add Balance flow
# ----------------------------

async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pending_topup"] = PendingTopup()
    context.user_data["waiting_proof"] = False

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💙 GCash", callback_data="pay_method:GCASH"),
            InlineKeyboardButton("🟦 GoTyme", callback_data="pay_method:GOTYME"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="pay_cancel")]
    ])
    await update.message.reply_text(
        "➕ *Add Balance*\n\nChoose a payment method:",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )

async def pay_method_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    method = q.data.split(":", 1)[1]
    pt: PendingTopup = context.user_data.get("pending_topup") or PendingTopup()
    pt.method = method
    context.user_data["pending_topup"] = pt

    # Send QR image + instructions (editable later)
    instr = await get_setting(f"instr_{method}")
    file_id = GCASH_QR_FILE_ID if method == "GCASH" else GOTYME_QR_FILE_ID

    if file_id:
        await q.message.reply_photo(photo=file_id, caption=f"*{method} QR*\n\n{instr}", parse_mode=ParseMode.MARKDOWN)
    else:
        await q.message.reply_text(f"*{method}*\n\n{instr}", parse_mode=ParseMode.MARKDOWN)

    # Amount buttons
    rows = []
    row = []
    for amt in AMOUNTS:
        row.append(InlineKeyboardButton(money(amt), callback_data=f"pay_amount:{amt}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("⬅️ Change Method", callback_data="pay_back_method")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="pay_cancel")])

    await q.message.reply_text(
        "Now choose an amount:",
        reply_markup=InlineKeyboardMarkup(rows),
    )

async def pay_amount_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    amt = int(q.data.split(":", 1)[1])

    pt: PendingTopup = context.user_data.get("pending_topup") or PendingTopup()
    if not pt.method:
        await q.message.reply_text("Please choose a payment method first.")
        return
    pt.amount = amt
    context.user_data["pending_topup"] = pt
    context.user_data["waiting_proof"] = True

    await q.message.reply_text(
        f"✅ Amount selected: *{money(amt)}*\n\n"
        f"Please send your *proof of payment screenshot* now.\n\n"
        f"After you send it, you'll see: _waiting for approval_.",
        parse_mode=ParseMode.MARKDOWN
    )

async def pay_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.pop("pending_topup", None)
    context.user_data["waiting_proof"] = False
    await q.message.reply_text("Cancelled ✅")

async def pay_back_method_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["pending_topup"] = PendingTopup()
    context.user_data["waiting_proof"] = False
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💙 GCash", callback_data="pay_method:GCASH"),
            InlineKeyboardButton("🟦 GoTyme", callback_data="pay_method:GOTYME"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="pay_cancel")]
    ])
    await q.message.reply_text("Choose a payment method:", reply_markup=kb)

async def receive_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_proof"):
        return

    pt: PendingTopup = context.user_data.get("pending_topup") or PendingTopup()
    if not pt.method or not pt.amount:
        await update.message.reply_text("Please pick payment method + amount first (➕ Add Balance).")
        return

    # Accept photo or document image
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Please send a screenshot/photo of proof of payment.")
        return

    user_id = update.effective_user.id
    topup_id = str(uuid.uuid4())[:8].upper()  # short ID

    await adb_exec("""
      INSERT INTO topups(topup_id, user_id, amount, method, status, proof_file_id)
      VALUES (%s,%s,%s,%s,'PENDING',%s)
    """, (topup_id, user_id, int(pt.amount), pt.method, file_id))

    context.user_data["waiting_proof"] = False
    context.user_data.pop("pending_topup", None)

    await update.message.reply_text(
        "✅ Proof received!\n\n⏳ *Waiting for admin approval...*\nThank you 💖",
        parse_mode=ParseMode.MARKDOWN
    )

    # Notify admins
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"admin_topup:APPROVE:{topup_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"admin_topup:REJECT:{topup_id}"),
    ]])

    caption = (
        f"🔔 *New Top Up Request*\n\n"
        f"Topup ID: `{topup_id}`\n"
        f"User ID: `{user_id}`\n"
        f"Method: *{pt.method}*\n"
        f"Amount: *{money(int(pt.amount))}*\n"
        f"Status: *PENDING*"
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            log.exception("Failed to notify admin: %s", e)

async def admin_topup_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if update.effective_user.id not in ADMIN_IDS:
        await q.message.reply_text("Not authorized.")
        return

    _, action, topup_id = q.data.split(":", 2)

    row = await adb_fetchone("""
      SELECT user_id, amount, status
      FROM topups
      WHERE topup_id=%s
    """, (topup_id,))

    if not row:
        await q.message.reply_text("Topup not found.")
        return

    user_id, amount, status = row
    if status != "PENDING":
        await q.message.reply_text(f"Already decided: {status}")
        return

    if action == "APPROVE":
        # Approve: add balance + mark topup approved
        await adb_exec("""
          UPDATE users SET balance = COALESCE(balance,0) + %s WHERE user_id=%s
        """, (int(amount), int(user_id)))

        await adb_exec("""
          UPDATE topups
          SET status='APPROVED', decided_at=NOW(), admin_id=%s
          WHERE topup_id=%s
        """, (int(update.effective_user.id), topup_id))

        # Notify user
        new_bal = await get_balance(int(user_id))
        try:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    f"✅ Your top up `{topup_id}` was *APPROVED*.\n"
                    f"Added: *{money(int(amount))}*\n"
                    f"New Balance: *{money(new_bal)}* 💖"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

        await q.message.reply_text(f"Approved ✅ Topup `{topup_id}`", parse_mode=ParseMode.MARKDOWN)

    else:
        await adb_exec("""
          UPDATE topups
          SET status='REJECTED', decided_at=NOW(), admin_id=%s
          WHERE topup_id=%s
        """, (int(update.effective_user.id), topup_id))

        try:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(f"❌ Your top up `{topup_id}` was *REJECTED*.\nPlease contact admin if needed."),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

        await q.message.reply_text(f"Rejected ❌ Topup `{topup_id}`", parse_mode=ParseMode.MARKDOWN)

# ----------------------------
# Shop flow
# ----------------------------

async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await adb_fetchall("""
      SELECT DISTINCT COALESCE(category,'General')
      FROM products
      WHERE is_active=TRUE
      ORDER BY 1 ASC
    """)
    cats = [r[0] for r in rows] or ["General"]

    kb_rows = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(f"📦 {c}", callback_data=f"shop_cat:{c}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton("❌ Close", callback_data="shop_close")])

    await update.message.reply_text(
        "🛍️ *Shop*\nChoose a category:",
        reply_markup=InlineKeyboardMarkup(kb_rows),
        parse_mode=ParseMode.MARKDOWN
    )

async def shop_cat_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cat = q.data.split(":", 1)[1]

    prods = await adb_fetchall("""
      SELECT id, name, description, price
      FROM products
      WHERE is_active=TRUE AND COALESCE(category,'General')=%s
      ORDER BY id ASC
    """, (cat,))

    if not prods:
        await q.message.reply_text("No products in this category yet.")
        return

    kb = []
    for pid, name, desc, price in prods[:20]:
        kb.append([InlineKeyboardButton(f"{name} — {money(int(price))}", callback_data=f"shop_prod:{pid}")])
    kb.append([InlineKeyboardButton("⬅️ Back", callback_data="shop_back")])

    await q.message.reply_text(
        f"📦 *{cat}*\nSelect a product:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN
    )

async def shop_prod_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":", 1)[1])

    row = await adb_fetchone("""
      SELECT id, name, description, price, photo_file_id
      FROM products WHERE id=%s AND is_active=TRUE
    """, (pid,))
    if not row:
        await q.message.reply_text("Product not found.")
        return

    _id, name, desc, price, photo_file_id = row

    text = (
        f"✨ *{name}*\n"
        f"{desc or ''}\n\n"
        f"Price: *{money(int(price))}*\n\n"
        f"Confirm purchase?"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{pid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="shop_back")]
    ])

    if photo_file_id:
        await q.message.reply_photo(photo=photo_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await q.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def buy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = update.effective_user.id
    pid = int(q.data.split(":", 1)[1])

    prod = await adb_fetchone("""
      SELECT id, name, price, file_id
      FROM products WHERE id=%s AND is_active=TRUE
    """, (pid,))
    if not prod:
        await q.message.reply_text("Product not found.")
        return

    _, name, price, file_id = prod
    price = int(price)

    bal = await get_balance(user_id)
    if bal < price:
        await q.message.reply_text(
            f"❌ Not enough balance.\nYour balance: {money(bal)}\nPrice: {money(price)}\n\nUse ➕ Add Balance.",
        )
        return

    # Deduct balance + insert purchase in a single "logical" step (best-effort)
    tx = str(uuid.uuid4())[:10].upper()

    await adb_exec("UPDATE users SET balance = balance - %s WHERE user_id=%s", (price, user_id))
    await adb_exec("""
      INSERT INTO purchases(transaction_id, user_id, product_id, price)
      VALUES (%s,%s,%s,%s)
    """, (tx, user_id, pid, price))

    # Find purchase id
    purchase_row = await adb_fetchone("SELECT id FROM purchases WHERE transaction_id=%s", (tx,))
    purchase_id = int(purchase_row[0])

    await q.message.reply_text(f"✅ Purchase successful!\nTransaction: `{tx}`\nDelivering file…", parse_mode=ParseMode.MARKDOWN)

    # ---- DEDUP DELIVERY BLOCK ----
    # Only deliver if we can "claim" the purchase_id in deliveries table.
    claimed = await adb_fetchone("""
      INSERT INTO deliveries(purchase_id) VALUES (%s)
      ON CONFLICT (purchase_id) DO NOTHING
      RETURNING purchase_id
    """, (purchase_id,))
    if not claimed:
        # Already delivered (or delivery already claimed)
        await q.message.reply_text("ℹ️ This item was already delivered before. If you need help, contact admin.")
        return

    # Deliver file
    if not file_id:
        await q.message.reply_text("⚠️ Admin has not set the product file yet. Please contact admin.")
        # keep deliveries record to prevent repeated spam; admin can manually send later
        return

    try:
        await context.bot.send_document(
            chat_id=user_id,
            document=file_id,
            caption=f"📦 *{name}*\nThank you for your purchase! 💖\nTransaction: `{tx}`",
            parse_mode=ParseMode.MARKDOWN
        )
        await q.message.reply_text("✅ Delivered! Check your files/messages.")
    except Exception as e:
        # If send fails, we do NOT want double delivery spam; admin can resend manually.
        log.exception("Delivery failed: %s", e)
        await q.message.reply_text("⚠️ Delivery failed. Please contact admin with your transaction ID.")

async def shop_back_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # Just call shop again
    fake_update = Update(update.update_id, message=q.message)
    # Can't easily reuse; just send new
    await q.message.reply_text("Back to categories…")
    rows = await adb_fetchall("""
      SELECT DISTINCT COALESCE(category,'General')
      FROM products
      WHERE is_active=TRUE
      ORDER BY 1 ASC
    """)
    cats = [r[0] for r in rows] or ["General"]

    kb_rows = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(f"📦 {c}", callback_data=f"shop_cat:{c}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton("❌ Close", callback_data="shop_close")])
    await q.message.reply_text("Choose a category:", reply_markup=InlineKeyboardMarkup(kb_rows))

async def shop_close_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("Closed ✅")

# ----------------------------
# Chat Admin (user -> admin)
# ----------------------------

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 *Chat Admin*\n\nSend your message now and I will forward it to admins.\n\nType /cancel to stop.",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["chat_admin_mode"] = True

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("chat_admin_mode", None)
    context.user_data.pop("admin_mode", None)
    context.user_data.pop("admin_edit_key", None)
    context.user_data.pop("waiting_proof", None)
    context.user_data.pop("pending_topup", None)
    await update.message.reply_text("Cancelled ✅")

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("chat_admin_mode"):
        return
    user = update.effective_user
    text = update.message.text or ""
    if not text.strip():
        await update.message.reply_text("Please send text message.")
        return

    caption = f"💬 *Message from user*\nUser: `{user.id}`\nUsername: @{user.username or '-'}\n\n{text}"
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, caption, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    await update.message.reply_text("✅ Sent to admin. Thank you!")
    context.user_data["chat_admin_mode"] = False

# ----------------------------
# Admin Panel
# ----------------------------

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Not authorized.")
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ Pending Topups", callback_data="admin:pending_topups")],
        [InlineKeyboardButton("📦 Products", callback_data="admin:products")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin:purchases")],
        [InlineKeyboardButton("✏️ Edit Text", callback_data="admin:edit_text")],
    ])
    await update.message.reply_text("🛠️ *Admin Panel*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if update.effective_user.id not in ADMIN_IDS:
        await q.message.reply_text("Not authorized.")
        return

    action = q.data.split(":", 1)[1]

    if action == "pending_topups":
        rows = await adb_fetchall("""
          SELECT topup_id, user_id, amount, method, created_at
          FROM topups
          WHERE status='PENDING'
          ORDER BY created_at ASC
          LIMIT 20
        """)
        if not rows:
            await q.message.reply_text("No pending topups ✅")
            return

        msg = ["⏳ *Pending Topups* (max 20)\nTap a Topup ID to manage:"]
        kb = []
        for tid, uid, amt, method, created in rows:
            msg.append(f"• `{tid}` — `{uid}` — {money(int(amt))} — {method}")
            kb.append([InlineKeyboardButton(f"{tid} ({money(int(amt))} {method})", callback_data=f"admin_open_topup:{tid}")])
        await q.message.reply_text("\n".join(msg), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif action == "products":
        rows = await adb_fetchall("""
          SELECT id, name, price, COALESCE(category,'General'), is_active
          FROM products
          ORDER BY id ASC
          LIMIT 50
        """)
        msg = ["📦 *Products* (max 50)"]
        kb = [
            [InlineKeyboardButton("➕ Add Product", callback_data="admin_add_product")],
        ]
        if not rows:
            msg.append("_No products yet._")
        else:
            for pid, name, price, cat, active in rows:
                status = "✅" if active else "⛔"
                msg.append(f"{status} *{pid}* — {name} — {money(int(price))} — `{cat}`")
                kb.append([InlineKeyboardButton(f"Edit #{pid}: {name}", callback_data=f"admin_edit_product:{pid}")])

        await q.message.reply_text("\n".join(msg), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif action == "purchases":
        rows = await adb_fetchall("""
          SELECT p.transaction_id, p.user_id, pr.name, p.price, p.created_at
          FROM purchases p JOIN products pr ON pr.id=p.product_id
          ORDER BY p.created_at DESC
          LIMIT 20
        """)
        msg = ["🧾 *Purchases* (last 20)"]
        if not rows:
            msg.append("_No purchases yet._")
        else:
            for tx, uid, name, price, created in rows:
                msg.append(f"• `{tx}` — `{uid}` — {name} — {money(int(price))}")
        await q.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN)

    elif action == "edit_text":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Edit GCash Instructions", callback_data="admin_editkey:instr_GCASH")],
            [InlineKeyboardButton("Edit GoTyme Instructions", callback_data="admin_editkey:instr_GOTYME")],
            [InlineKeyboardButton("Edit Welcome Text", callback_data="admin_editkey:welcome")],
            [InlineKeyboardButton("Edit Help Text", callback_data="admin_editkey:help")],
        ])
        await q.message.reply_text("✏️ Choose which text to edit:", reply_markup=kb)

async def admin_open_topup_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return
    topup_id = q.data.split(":", 1)[1]

    row = await adb_fetchone("""
      SELECT topup_id, user_id, amount, method, status, proof_file_id
      FROM topups WHERE topup_id=%s
    """, (topup_id,))
    if not row:
        await q.message.reply_text("Topup not found.")
        return

    tid, uid, amt, method, status, proof_file_id = row
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"admin_topup:APPROVE:{tid}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"admin_topup:REJECT:{tid}"),
    ]])

    text = (
        f"Topup `{tid}`\n"
        f"User: `{uid}`\n"
        f"Method: *{method}*\n"
        f"Amount: *{money(int(amt))}*\n"
        f"Status: *{status}*"
    )
    if proof_file_id:
        await q.message.reply_photo(photo=proof_file_id, caption=text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await q.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def admin_editkey_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return
    key = q.data.split(":", 1)[1]
    context.user_data["admin_mode"] = "edit_text"
    context.user_data["admin_edit_key"] = key

    current = await get_setting(key)
    await q.message.reply_text(
        f"Send the *new text* for `{key}` now.\n\nCurrent:\n{current}\n\nType /cancel to stop.",
        parse_mode=ParseMode.MARKDOWN
    )

async def admin_text_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if context.user_data.get("admin_mode") != "edit_text":
        return

    key = context.user_data.get("admin_edit_key")
    new_text = (update.message.text or "").strip()
    if not key or not new_text:
        await update.message.reply_text("Send a text message.")
        return

    await adb_exec("""
      INSERT INTO settings(key,value) VALUES (%s,%s)
      ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
    """, (key, new_text))

    context.user_data.pop("admin_mode", None)
    context.user_data.pop("admin_edit_key", None)
    await update.message.reply_text(f"✅ Updated `{key}`", parse_mode=ParseMode.MARKDOWN)

# NOTE: Product add/edit flows are kept minimal so it doesn't break.
# Admin can still edit via DB or expand later.
async def admin_add_product_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return

    await q.message.reply_text(
        "➕ Add Product (quick)\n\n"
        "Send in ONE message like this:\n"
        "`Name | Price | Category | Description`\n\n"
        "Example:\n"
        "`Netflix 30 Days | 150 | Streaming | Premium account`",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["admin_mode"] = "add_product"

async def admin_edit_product_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return

    pid = int(q.data.split(":", 1)[1])
    row = await adb_fetchone("SELECT id,name,price,category,description,file_id,photo_file_id,is_active FROM products WHERE id=%s", (pid,))
    if not row:
        await q.message.reply_text("Product not found.")
        return

    context.user_data["admin_mode"] = "edit_product"
    context.user_data["edit_pid"] = pid

    _id, name, price, cat, desc, file_id, photo_file_id, active = row
    await q.message.reply_text(
        "✏️ Edit Product\n\nSend format:\n"
        "`Name | Price | Category | Description`\n\n"
        f"Current:\n"
        f"Name: {name}\nPrice: {price}\nCategory: {cat}\nActive: {active}\n\n"
        "After that, you can optionally send the product file (document) to set delivery file_id, "
        "or send a photo to set preview image.\n\nType /cancel to stop.",
        parse_mode=ParseMode.MARKDOWN
    )

async def admin_product_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    mode = context.user_data.get("admin_mode")
    if mode not in ("add_product", "edit_product"):
        return

    # If admin sends a document/photo while editing -> set file_id/photo_file_id
    if mode == "edit_product":
        pid = context.user_data.get("edit_pid")
        if pid and update.message.document:
            fid = update.message.document.file_id
            await adb_exec("UPDATE products SET file_id=%s WHERE id=%s", (fid, pid))
            await update.message.reply_text("✅ Set product delivery file_id.")
            return
        if pid and update.message.photo:
            pfid = update.message.photo[-1].file_id
            await adb_exec("UPDATE products SET photo_file_id=%s WHERE id=%s", (pfid, pid))
            await update.message.reply_text("✅ Set product preview photo.")
            return

    # Parse text format
    text = (update.message.text or "").strip()
    if "|" not in text:
        await update.message.reply_text("Use format: `Name | Price | Category | Description`", parse_mode=ParseMode.MARKDOWN)
        return

    parts = [p.strip() for p in text.split("|")]
    while len(parts) < 4:
        parts.append("")
    name, price_s, cat, desc = parts[0], parts[1], parts[2], parts[3]
    if not price_s.isdigit():
        await update.message.reply_text("Price must be a number.")
        return
    price = int(price_s)
    cat = cat or "General"
    desc = desc or ""

    if mode == "add_product":
        await adb_exec("""
          INSERT INTO products(name,price,category,description,is_active)
          VALUES (%s,%s,%s,%s,TRUE)
        """, (name, price, cat, desc))
        context.user_data.pop("admin_mode", None)
        await update.message.reply_text("✅ Product added.")
    else:
        pid = context.user_data.get("edit_pid")
        await adb_exec("""
          UPDATE products SET name=%s, price=%s, category=%s, description=%s
          WHERE id=%s
        """, (name, price, cat, desc, pid))
        await update.message.reply_text("✅ Product updated. (Send document/photo to set file_id/photo.)")

# ----------------------------
# Text button router (ReplyKeyboard buttons)
# ----------------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin edit text receiver
    if context.user_data.get("admin_mode") == "edit_text":
        await admin_text_receiver(update, context)
        return

    # Admin add/edit products receiver
    if context.user_data.get("admin_mode") in ("add_product", "edit_product"):
        await admin_product_receiver(update, context)
        return

    # Chat admin mode
    if context.user_data.get("chat_admin_mode"):
        await forward_to_admin(update, context)
        return

    txt = (update.message.text or "").strip()
    user = update.effective_user
    is_admin = user.id in ADMIN_IDS

    if txt == "🛍️ Shop":
        await shop_cmd(update, context)
    elif txt == "➕ Add Balance":
        await add_balance(update, context)
    elif txt == "💰 Balance":
        await balance_cmd(update, context)
    elif txt == "🧾 History":
        await history_cmd(update, context)
    elif txt == "❓ Help":
        await help_cmd(update, context)
    elif txt == "💬 Chat Admin":
        await chat_admin(update, context)
    elif txt == "🛠️ Admin" and is_admin:
        await admin_menu(update, context)
    else:
        await update.message.reply_text("Use the menu buttons below 👇")

# ----------------------------
# Main
# ----------------------------

def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing")
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL missing")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS is empty. Admin panel will not work.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Ensure schema at startup
    async def _startup(app_: Application):
        await ensure_schema()
        log.info("Schema ensured.")

    app.post_init = _startup

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("cancel", cancel))

    # Callback queries
    app.add_handler(CallbackQueryHandler(pay_method_cb, pattern=r"^pay_method:"))
    app.add_handler(CallbackQueryHandler(pay_amount_cb, pattern=r"^pay_amount:"))
    app.add_handler(CallbackQueryHandler(pay_cancel_cb, pattern=r"^pay_cancel$"))
    app.add_handler(CallbackQueryHandler(pay_back_method_cb, pattern=r"^pay_back_method$"))

    app.add_handler(CallbackQueryHandler(shop_cat_cb, pattern=r"^shop_cat:"))
    app.add_handler(CallbackQueryHandler(shop_prod_cb, pattern=r"^shop_prod:"))
    app.add_handler(CallbackQueryHandler(shop_back_cb, pattern=r"^shop_back$"))
    app.add_handler(CallbackQueryHandler(shop_close_cb, pattern=r"^shop_close$"))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern=r"^buy:"))

    # Admin callbacks
    app.add_handler(CallbackQueryHandler(admin_topup_cb, pattern=r"^admin_topup:"))
    app.add_handler(CallbackQueryHandler(admin_router, pattern=r"^admin:"))
    app.add_handler(CallbackQueryHandler(admin_open_topup_cb, pattern=r"^admin_open_topup:"))
    app.add_handler(CallbackQueryHandler(admin_editkey_cb, pattern=r"^admin_editkey:"))
    app.add_handler(CallbackQueryHandler(admin_add_product_cb, pattern=r"^admin_add_product$"))
    app.add_handler(CallbackQueryHandler(admin_edit_product_prompt, pattern=r"^admin_edit_product:"))

    # Proof of payment (photo/document)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_proof))

    # Text router (ReplyKeyboard buttons + admin inputs)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # IMPORTANT:
    # If you see Conflict error, you have TWO instances running.
    # Run only on Railway worker. Do NOT run locally at same time.
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
