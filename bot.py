import os
import re
import uuid
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

import psycopg2
import psycopg2.extras

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
SHOP_PREFIX = os.getenv("SHOP_PREFIX", "shopnluna").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "lovebylunaa").lstrip("@")  # your admin username

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")

ADMIN_CHAT_URL = f"https://t.me/{ADMIN_USERNAME}"

def parse_admin_ids(s: str) -> set[int]:
    ids = set()
    for part in re.split(r"[,\s]+", s.strip()):
        if not part:
            continue
        try:
            ids.add(int(part))
        except:
            pass
    return ids

ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)

# =========================
# DB (sync) helpers -> run in thread
# =========================
def db_connect():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def db_exec(sql: str, params: tuple = ()) -> None:
    conn = db_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    finally:
        conn.close()

def db_fetchone(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()

def db_fetchall(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(r) for r in rows]
    finally:
        conn.close()

async def adb_exec(sql: str, params: tuple = ()) -> None:
    await asyncio.to_thread(db_exec, sql, params)

async def adb_fetchone(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(db_fetchone, sql, params)

async def adb_fetchall(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(db_fetchall, sql, params)

# =========================
# Defaults / Settings
# =========================
DEFAULT_TEXTS = {
    "welcome_text": "🌙 *Luna’s Prem Shop*\n\nChoose an option below:",
    "shop_text": "🛒 *Shop*\nPick a category:",
    "help_text": "Need help? Tap *Chat Admin* or DM admin: @lovebylunaa",

    "topup_menu_text": "💳 *Add Balance*\nChoose payment method:",
    "gcash_text": "💳 *GCash Payment*\nScan the QR and pay the exact amount.\n\nAfter paying, choose amount below then upload screenshot proof.",
    "gotyme_text": "🏦 *GoTyme Payment*\nScan the QR and pay the exact amount.\n\nAfter paying, choose amount below then upload screenshot proof.",
    "topup_send_proof_text": "📸 Now send your *payment screenshot* (photo).\n\n✅ After you send it, I will submit it for admin approval.\nFor fast approval, tap *Chat Admin*.",
    "topup_received_text": "✅ Thank you! Proof received.\n🧾 TopUp ID: `{topup_id}`\n⏳ Waiting for admin approval.\n\nFor fast approval, tap *Chat Admin*.",
    "topup_approved_text": "✅ Top up approved!\n🧾 TopUp ID: `{topup_id}`\nAdded: *₱{amount}*",
    "topup_rejected_text": "❌ Top up rejected.\n🧾 TopUp ID: `{topup_id}`",

    "insufficient_balance_text": "❌ Not enough balance. Please top up first.",
    "purchase_success_text": "✅ Purchase complete!\nTransaction ID: `{transaction_id}`",
    "delivery_not_set_text": "⚠️ Delivery is not set yet for this product. Please tap *Chat Admin*.",
}

# =========================
# Schema + Migrations
# =========================
SCHEMA_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  balance INTEGER NOT NULL DEFAULT 0,
  is_banned BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  category TEXT NOT NULL DEFAULT 'General',
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  price INTEGER NOT NULL DEFAULT 0,
  photo_file_id TEXT,
  delivery_file_id TEXT,
  delivery_caption TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topups (
  id SERIAL PRIMARY KEY,
  topup_id TEXT UNIQUE NOT NULL,
  user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  amount INTEGER NOT NULL,
  method TEXT NOT NULL,
  proof_file_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  admin_id BIGINT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS purchases (
  id SERIAL PRIMARY KEY,
  transaction_id TEXT UNIQUE NOT NULL,
  user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
  price INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

MIGRATIONS_SQL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE products ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'General';
ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN IF NOT EXISTS price INTEGER NOT NULL DEFAULT 0;
ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_file_id TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS delivery_file_id TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS delivery_caption TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE topups ADD COLUMN IF NOT EXISTS method TEXT;
ALTER TABLE topups ADD COLUMN IF NOT EXISTS proof_file_id TEXT;
ALTER TABLE topups ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE topups ADD COLUMN IF NOT EXISTS admin_id BIGINT;
ALTER TABLE topups ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE topups ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP;
"""

async def init_db():
    await adb_exec(SCHEMA_CREATE_SQL)
    await adb_exec(MIGRATIONS_SQL)

    for k, v in DEFAULT_TEXTS.items():
        await adb_exec(
            "INSERT INTO settings(key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (k, v),
        )

    for k in ["GCASH_QR_FILE_ID", "GOTYME_QR_FILE_ID"]:
        await adb_exec(
            "INSERT INTO settings(key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (k, ""),
        )

async def get_setting(key: str) -> str:
    row = await adb_fetchone("SELECT value FROM settings WHERE key=%s", (key,))
    return str(row["value"]) if row and row.get("value") is not None else ""

async def set_setting(key: str, value: str) -> None:
    await adb_exec(
        "INSERT INTO settings(key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        (key, value),
    )

async def get_text(key: str) -> str:
    row = await adb_fetchone("SELECT value FROM settings WHERE key=%s", (key,))
    if row and row.get("value"):
        return str(row["value"])
    return DEFAULT_TEXTS.get(key, "")

async def set_text(key: str, value: str) -> None:
    await set_setting(key, value)

# =========================
# Utils
# =========================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def make_id(prefix: str) -> str:
    return f"{SHOP_PREFIX}:{prefix}{uuid.uuid4().hex[:6].upper()}"

async def upsert_user(u) -> None:
    await adb_exec(
        """
        INSERT INTO users(user_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
          username = EXCLUDED.username,
          first_name = EXCLUDED.first_name
        """,
        (u.id, u.username, u.first_name),
    )

def main_menu_kb(user_id: int):
    rows = [
        [KeyboardButton("🛒 Shop"), KeyboardButton("💳 Add Balance")],
        [KeyboardButton("💰 Balance"), KeyboardButton("💬 Chat Admin")],
        [KeyboardButton("🧾 History"), KeyboardButton("❓ Help")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton("🛠 Admin")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def chat_admin_inline_btn():
    return InlineKeyboardButton("💬 Chat Admin", url=ADMIN_CHAT_URL)

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="admin_products")],
        [InlineKeyboardButton("💳 Payment Settings", callback_data="admin_pay")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin_purchases")],
        [InlineKeyboardButton("💳 Topups", callback_data="admin_topups")],
        [InlineKeyboardButton("✏️ Edit Texts", callback_data="admin_texts")],
        [InlineKeyboardButton("⬅️ Close", callback_data="admin_close")],
    ])

def topup_method_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💳 GCash", callback_data="topup_method:gcash"),
            InlineKeyboardButton("🏦 GoTyme", callback_data="topup_method:gotyme"),
        ],
        [chat_admin_inline_btn()],
        [InlineKeyboardButton("⬅️ Back", callback_data="topup_back")],
    ])

def amount_kb(method: str):
    amounts = [50, 100, 300, 500, 1000]
    rows = []
    row = []
    for a in amounts:
        row.append(InlineKeyboardButton(f"₱{a}", callback_data=f"topup_amount:{method}:{a}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Change Method", callback_data="topup_menu")])
    rows.append([chat_admin_inline_btn()])
    return InlineKeyboardMarkup(rows)

# =========================
# STARTUP
# =========================
async def on_startup(app: Application):
    await init_db()

# =========================
# BASIC
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    welcome = await get_text("welcome_text")

    # show welcome + shop categories directly
    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(update.effective_user.id),
    )
    await shop_categories_screen(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await get_text("help_text")
    kb = InlineKeyboardMarkup([[chat_admin_inline_btn()]])
    await update.message.reply_text(txt, reply_markup=kb)

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    row = await adb_fetchone("SELECT balance FROM users WHERE user_id=%s", (update.effective_user.id,))
    bal = int(row["balance"]) if row else 0

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Add Balance", callback_data="topup_menu")],
        [chat_admin_inline_btn()],
    ])
    await update.message.reply_text(
        f"💰 Balance: *₱{bal}*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    topups = await adb_fetchall(
        "SELECT topup_id, amount, method, status FROM topups WHERE user_id=%s ORDER BY id DESC LIMIT 10",
        (uid,),
    )
    purchases = await adb_fetchall(
        "SELECT transaction_id, price, created_at FROM purchases WHERE user_id=%s ORDER BY id DESC LIMIT 10",
        (uid,),
    )

    msg = ["🧾 *Your History*"]
    msg.append("\n*Topups (last 10)*")
    if not topups:
        msg.append("— none")
    else:
        for t in topups:
            msg.append(f"• `{t['topup_id']}` | +₱{t['amount']} | {str(t['method']).upper()} | {t['status']}")

    msg.append("\n*Purchases (last 10)*")
    if not purchases:
        msg.append("— none")
    else:
        for p in purchases:
            msg.append(f"• `{p['transaction_id']}` | -₱{p['price']}")

    await update.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))

async def chat_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[chat_admin_inline_btn()]])
    await update.message.reply_text(f"💬 Tap to chat admin: @{ADMIN_USERNAME}", reply_markup=kb)

# =========================
# SHOP: Categories -> Products -> Buy -> Delivery
# =========================
async def shop_categories_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # categories from active products only
    rows = await adb_fetchall(
        "SELECT DISTINCT category FROM products WHERE is_active=TRUE ORDER BY category ASC"
    )
    cats = [r["category"] for r in rows if r.get("category")]

    txt = await get_text("shop_text")

    if not cats:
        await update.message.reply_text("No products yet.", reply_markup=main_menu_kb(update.effective_user.id))
        return

    buttons = []
    row_btns = []
    for c in cats:
        row_btns.append(InlineKeyboardButton(f"✨ {c}", callback_data=f"shop_cat:{c}"))
        if len(row_btns) == 2:
            buttons.append(row_btns)
            row_btns = []
    if row_btns:
        buttons.append(row_btns)

    buttons.append([chat_admin_inline_btn()])
    kb = InlineKeyboardMarkup(buttons)

    # when /start calls this with update.message
    if update.message:
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        q = update.callback_query
        await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def shop_products_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    products = await adb_fetchall(
        "SELECT id, name, price FROM products WHERE is_active=TRUE AND category=%s ORDER BY id DESC",
        (category,),
    )

    if not products:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="shop_home")],
            [chat_admin_inline_btn()],
        ])
        await update.callback_query.edit_message_text(
            f"✨ *{category}*\n\nNo products yet.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return

    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(f"{p['name']} — ₱{p['price']}", callback_data=f"buy:{p['id']}")])

    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="shop_home")])
    buttons.append([chat_admin_inline_btn()])
    kb = InlineKeyboardMarkup(buttons)

    await update.callback_query.edit_message_text(
        f"✨ *{category}*\nPick a product:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

async def shop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "shop_home":
        await shop_categories_screen(update, context)
        return

    if data.startswith("shop_cat:"):
        category = data.split(":", 1)[1]
        await shop_products_screen(update, context, category)
        return

    if data.startswith("buy:"):
        pid = int(data.split(":")[1])

        product = await adb_fetchone(
            "SELECT id, category, name, description, price, photo_file_id FROM products WHERE id=%s AND is_active=TRUE",
            (pid,),
        )
        if not product:
            await q.message.reply_text("Product not found or inactive.")
            return

        confirm_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Buy", callback_data=f"buy_confirm:{pid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="buy_cancel")],
            [chat_admin_inline_btn()],
        ])

        caption = (
            f"*{product['name']}*\n"
            f"Price: *₱{int(product['price'])}*\n\n"
            f"{product.get('description') or ''}\n\n"
            "Tap ✅ Confirm to pay and receive delivery."
        )

        try:
            if product.get("photo_file_id"):
                await q.message.reply_photo(
                    product["photo_file_id"],
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=confirm_kb
                )
            else:
                await q.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=confirm_kb)
        except:
            await q.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=confirm_kb)
        return

    if data == "buy_cancel":
        await q.edit_message_text("✅ Cancelled. No payment deducted.")
        return

    if data.startswith("buy_confirm:"):
        pid = int(data.split(":")[1])
        uid = q.from_user.id
        await upsert_user(q.from_user)

        product = await adb_fetchone(
            "SELECT id, name, price, delivery_file_id, delivery_caption FROM products WHERE id=%s AND is_active=TRUE",
            (pid,),
        )
        if not product:
            await q.message.reply_text("Product not found.")
            return

        price = int(product["price"])
        user = await adb_fetchone("SELECT balance FROM users WHERE user_id=%s", (uid,))
        balance = int(user["balance"]) if user else 0

        if balance < price:
            txt = await get_text("insufficient_balance_text")
            await q.message.reply_text(txt)
            return

        txid = make_id("TX")
        await adb_exec("UPDATE users SET balance = balance - %s WHERE user_id=%s", (price, uid))
        await adb_exec(
            "INSERT INTO purchases(transaction_id, user_id, product_id, price) VALUES (%s, %s, %s, %s)",
            (txid, uid, pid, price),
        )

        done = (await get_text("purchase_success_text")).format(transaction_id=txid)
        await q.message.reply_text(done, parse_mode=ParseMode.MARKDOWN)

        delivery_file_id = (product.get("delivery_file_id") or "").strip()
        delivery_caption = (product.get("delivery_caption") or "").strip()

        if delivery_file_id:
            cap = delivery_caption if delivery_caption else f"📦 *Delivery*\nProduct: *{product['name']}*\nTransaction: `{txid}`"
            try:
                await q.message.reply_document(document=delivery_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
            except:
                await q.message.reply_document(document=delivery_file_id, caption="📦 Delivery")
        else:
            warn = await get_text("delivery_not_set_text")
            await q.message.reply_text(warn, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[chat_admin_inline_btn()]]))
        return

# =========================
# TOP UP UI
# =========================
async def topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await get_text("topup_menu_text")
    if update.message:
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=topup_method_kb())
    else:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=topup_method_kb())

async def topup_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "topup_menu":
        await topup_menu(update, context)
        return

    if data == "topup_back":
        await q.edit_message_text("Back to menu.")
        return

    if data.startswith("topup_method:"):
        method = data.split(":", 1)[1]
        if method == "gcash":
            txt = await get_text("gcash_text")
            qr = await get_setting("GCASH_QR_FILE_ID")
        else:
            txt = await get_text("gotyme_text")
            qr = await get_setting("GOTYME_QR_FILE_ID")

        if qr.strip():
            await q.message.reply_photo(photo=qr.strip(), caption=txt, parse_mode=ParseMode.MARKDOWN)
        else:
            await q.message.reply_text(txt + "\n\n⚠️ Admin has not set the QR yet.", parse_mode=ParseMode.MARKDOWN)

        await q.message.reply_text("✨ Choose amount:", reply_markup=amount_kb(method))
        return

    if data.startswith("topup_amount:"):
        _, method, amt_s = data.split(":")
        amount = int(amt_s)

        uid = q.from_user.id
        await upsert_user(q.from_user)

        topup_id = make_id("TU")
        await adb_exec(
            "INSERT INTO topups(topup_id, user_id, amount, method, status) VALUES (%s, %s, %s, %s, 'pending')",
            (topup_id, uid, amount, method),
        )

        context.user_data["awaiting_proof"] = True
        context.user_data["pending_topup_id"] = topup_id

        ask = await get_text("topup_send_proof_text")
        await q.message.reply_text(
            f"🧾 TopUp ID: `{topup_id}`\nAmount: *₱{amount}*\nMethod: *{method.upper()}*\n\n{ask}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[chat_admin_inline_btn()]])
        )
        return

async def proof_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_proof"):
        return

    topup_id = context.user_data.get("pending_topup_id")
    if not topup_id:
        context.user_data["awaiting_proof"] = False
        return

    if not update.message.photo:
        await update.message.reply_text("Please send the proof as a PHOTO screenshot.")
        return

    proof_file_id = update.message.photo[-1].file_id
    row = await adb_fetchone("SELECT * FROM topups WHERE topup_id=%s", (topup_id,))
    if not row or row["status"] != "pending":
        await update.message.reply_text("Topup not found or already handled.")
        context.user_data["awaiting_proof"] = False
        context.user_data["pending_topup_id"] = None
        return

    await adb_exec("UPDATE topups SET proof_file_id=%s WHERE topup_id=%s", (proof_file_id, topup_id))

    txt = (await get_text("topup_received_text")).format(topup_id=topup_id)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[chat_admin_inline_btn()]]))

    uid = int(row["user_id"])
    amount = int(row["amount"])
    method = (row["method"] or "").upper()

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"tu_approve:{topup_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"tu_reject:{topup_id}"),
        ],
        [chat_admin_inline_btn()],
    ])

    caption = (
        f"💳 *TOPUP REQUEST*\n"
        f"TopUp ID: `{topup_id}`\n"
        f"User ID: `{uid}`\n"
        f"User: @{update.effective_user.username}\n"
        f"Method: *{method}*\n"
        f"Amount: *₱{amount}*\n"
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=proof_file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_kb,
            )
        except:
            pass

    context.user_data["awaiting_proof"] = False
    context.user_data["pending_topup_id"] = None

async def topup_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ You are not admin.")
        return

    action, topup_id = q.data.split(":", 1)
    row = await adb_fetchone("SELECT * FROM topups WHERE topup_id=%s", (topup_id,))
    if not row:
        await q.edit_message_text("Topup not found.")
        return
    if row["status"] != "pending":
        await q.edit_message_text(f"Already handled: {row['status']}")
        return

    uid = int(row["user_id"])
    amount = int(row["amount"])
    now = datetime.utcnow()

    if action == "tu_approve":
        await adb_exec(
            "UPDATE topups SET status='approved', admin_id=%s, decided_at=%s WHERE topup_id=%s",
            (q.from_user.id, now, topup_id),
        )
        await adb_exec("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, uid))
        txt = (await get_text("topup_approved_text")).format(topup_id=topup_id, amount=amount)
        try:
            await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass
        try:
            await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n✅ *APPROVED*", parse_mode=ParseMode.MARKDOWN)
        except:
            await q.edit_message_text("✅ Approved.")
        return

    if action == "tu_reject":
        await adb_exec(
            "UPDATE topups SET status='rejected', admin_id=%s, decided_at=%s WHERE topup_id=%s",
            (q.from_user.id, now, topup_id),
        )
        txt = (await get_text("topup_rejected_text")).format(topup_id=topup_id)
        try:
            await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass
        try:
            await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n❌ *REJECTED*", parse_mode=ParseMode.MARKDOWN)
        except:
            await q.edit_message_text("❌ Rejected.")
        return

# =========================
# ADMIN PANEL
# =========================
async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not admin.")
        return
    await update.message.reply_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text("Choose:", reply_markup=admin_menu_kb())

def products_list_kb(products: List[Dict[str, Any]]):
    btns = []
    btns.append([InlineKeyboardButton("➕ Add Product", callback_data="prod_add")])
    for p in products[:30]:
        status = "✅" if p["is_active"] else "❌"
        btns.append([InlineKeyboardButton(f"{status} #{p['id']} [{p['category']}] {p['name']} (₱{p['price']})", callback_data=f"prod_open:{p['id']}")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    return InlineKeyboardMarkup(btns)

def product_manage_kb(pid: int, active: bool):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏷 Edit Category", callback_data=f"prod_edit_cat:{pid}")],
        [InlineKeyboardButton("✏️ Edit Name", callback_data=f"prod_edit_name:{pid}")],
        [InlineKeyboardButton("💵 Edit Price", callback_data=f"prod_edit_price:{pid}")],
        [InlineKeyboardButton("📝 Edit Description", callback_data=f"prod_edit_desc:{pid}")],
        [InlineKeyboardButton("🖼 Set Photo", callback_data=f"prod_set_photo:{pid}")],
        [InlineKeyboardButton("📦 Set Delivery File", callback_data=f"prod_set_file:{pid}")],
        [InlineKeyboardButton(("✅ Active" if active else "❌ Inactive") + " (toggle)", callback_data=f"prod_toggle:{pid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_products")],
    ])

async def admin_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ You are not admin.")
        return

    data = q.data

    if data == "admin_close":
        await q.edit_message_text("Closed.")
        return

    if data == "admin_back":
        await q.edit_message_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())
        return

    if data == "admin_pay":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Set GCash QR", callback_data="pay_setqr:gcash")],
            [InlineKeyboardButton("Set GoTyme QR", callback_data="pay_setqr:gotyme")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        ])
        await q.edit_message_text("💳 *Payment Settings*\n\nTap an option then send QR as PHOTO.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data == "admin_texts":
        keys = [
            "welcome_text", "shop_text", "help_text",
            "topup_menu_text", "gcash_text", "gotyme_text",
            "topup_send_proof_text", "topup_received_text",
            "topup_approved_text", "topup_rejected_text",
            "insufficient_balance_text", "purchase_success_text",
            "delivery_not_set_text",
        ]
        btns = [[InlineKeyboardButton(k, callback_data=f"text_pick:{k}")] for k in keys]
        btns.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
        await q.edit_message_text("✏️ *Pick a text to edit:*", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(btns))
        return

    if data == "admin_purchases":
        rows = await adb_fetchall(
            """
            SELECT p.transaction_id, p.price, p.user_id, pr.name AS product_name
            FROM purchases p
            LEFT JOIN products pr ON pr.id = p.product_id
            ORDER BY p.id DESC LIMIT 20
            """
        )
        msg = ["🧾 *Purchases (last 20)*"]
        if not rows:
            msg.append("— none")
        else:
            for r in rows:
                pname = r["product_name"] or "deleted-product"
                msg.append(f"• `{r['transaction_id']}` | {pname} | -₱{r['price']} | user `{r['user_id']}`")
        await q.edit_message_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]))
        return

    if data == "admin_topups":
        rows = await adb_fetchall(
            "SELECT topup_id, user_id, amount, method, status FROM topups ORDER BY id DESC LIMIT 20"
        )
        msg = ["💳 *Topups (last 20)*"]
        if not rows:
            msg.append("— none")
        else:
            for r in rows:
                msg.append(f"• `{r['topup_id']}` | +₱{r['amount']} | {str(r['method']).upper()} | {r['status']} | user `{r['user_id']}`")
        await q.edit_message_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]))
        return

    # PRODUCTS UI
    if data == "admin_products":
        products = await adb_fetchall("SELECT id, category, name, price, is_active FROM products ORDER BY id DESC")
        await q.edit_message_text("📦 *Products*", parse_mode=ParseMode.MARKDOWN, reply_markup=products_list_kb(products))
        return

    if data == "prod_add":
        await adb_exec(
            "INSERT INTO products(category, name, description, price, is_active) VALUES (%s,%s,%s,%s,TRUE)",
            ("General", "New Product", "", 0),
        )
        products = await adb_fetchall("SELECT id, category, name, price, is_active FROM products ORDER BY id DESC")
        await q.edit_message_text("✅ Product created. Select it to edit:", parse_mode=ParseMode.MARKDOWN, reply_markup=products_list_kb(products))
        return

    if data.startswith("prod_open:"):
        pid = int(data.split(":")[1])
        p = await adb_fetchone("SELECT * FROM products WHERE id=%s", (pid,))
        if not p:
            await q.message.reply_text("Product not found.")
            return
        delivery = "✅ set" if (p.get("delivery_file_id") or "").strip() else "❌ not set"
        photo = "✅ set" if (p.get("photo_file_id") or "").strip() else "❌ not set"
        txt = (
            f"📦 *Product #{pid}*\n\n"
            f"*Category:* {p['category']}\n"
            f"*Name:* {p['name']}\n"
            f"*Price:* ₱{int(p['price'])}\n"
            f"*Active:* {'YES' if p['is_active'] else 'NO'}\n"
            f"*Photo:* {photo}\n"
            f"*Delivery File:* {delivery}\n\n"
            f"*Description:*\n{p.get('description') or ''}"
        )
        await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=product_manage_kb(pid, bool(p["is_active"])))
        return

    if data.startswith("prod_toggle:"):
        pid = int(data.split(":")[1])
        await adb_exec("UPDATE products SET is_active = NOT is_active WHERE id=%s", (pid,))
        await q.message.reply_text("✅ Toggled.")
        return

    if data.startswith("prod_edit_cat:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_edit"] = {"pid": pid, "field": "category"}
        await q.message.reply_text(f"🏷 Send NEW *category* for product #{pid} (example: Editing Apps):", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_edit_name:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_edit"] = {"pid": pid, "field": "name"}
        await q.message.reply_text(f"✏️ Send NEW *name* for product #{pid}:", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_edit_price:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_edit"] = {"pid": pid, "field": "price"}
        await q.message.reply_text(f"💵 Send NEW *price* (number) for product #{pid}:", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_edit_desc:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_edit"] = {"pid": pid, "field": "description"}
        await q.message.reply_text(f"📝 Send NEW *description* for product #{pid}:", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_set_photo:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_photo"] = pid
        await q.message.reply_text(f"🖼 Send product photo as *PHOTO* for product #{pid}:", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_set_file:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_file"] = pid
        await q.message.reply_text(
            f"📦 Send the delivery file as *DOCUMENT* for product #{pid}.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Payment QR set
    if data.startswith("pay_setqr:"):
        method = data.split(":", 1)[1]
        context.user_data["awaiting_qr_set"] = method
        await q.message.reply_text(f"✅ Now send the *{method.upper()} QR* as a PHOTO.", parse_mode=ParseMode.MARKDOWN)
        return

async def text_pick_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ You are not admin.")
        return
    key = q.data.split(":", 1)[1]
    context.user_data["edit_text_key"] = key
    current = await get_text(key)
    await q.edit_message_text(
        f"✏️ Editing: *{key}*\n\nCurrent:\n{current}\n\nSend NEW text now:",
        parse_mode=ParseMode.MARKDOWN,
    )

# =========================
# ADMIN capture photo/document states
# =========================
async def admin_capture_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    method = context.user_data.get("awaiting_qr_set")
    if not method:
        return
    if not update.message.photo:
        await update.message.reply_text("Please send it as a PHOTO.")
        return
    file_id = update.message.photo[-1].file_id
    if method == "gcash":
        await set_setting("GCASH_QR_FILE_ID", file_id)
    else:
        await set_setting("GOTYME_QR_FILE_ID", file_id)
    context.user_data["awaiting_qr_set"] = None
    await update.message.reply_text(f"✅ {method.upper()} QR saved! Users can now see it in Add Balance.")

async def admin_capture_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    pid = context.user_data.get("awaiting_prod_photo")
    if not pid:
        return
    if not update.message.photo:
        await update.message.reply_text("Please send product photo as PHOTO.")
        return
    file_id = update.message.photo[-1].file_id
    await adb_exec("UPDATE products SET photo_file_id=%s WHERE id=%s", (file_id, int(pid)))
    context.user_data["awaiting_prod_photo"] = None
    await update.message.reply_text(f"✅ Photo saved for product #{pid}.")

async def admin_capture_product_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    pid = context.user_data.get("awaiting_prod_file")
    if not pid:
        return
    if not update.message.document:
        await update.message.reply_text("Please send the delivery file as DOCUMENT.")
        return
    file_id = update.message.document.file_id
    await adb_exec("UPDATE products SET delivery_file_id=%s WHERE id=%s", (file_id, int(pid)))
    context.user_data["awaiting_prod_file"] = None
    await update.message.reply_text(f"✅ Delivery file saved for product #{pid}. Auto-delivery is now ON.")

async def admin_edit_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    key = context.user_data.get("edit_text_key")
    if key:
        new_text = update.message.text or ""
        await set_text(key, new_text)
        context.user_data["edit_text_key"] = None
        await update.message.reply_text("✅ Text updated!")
        await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())
        return

    edit = context.user_data.get("awaiting_prod_edit")
    if edit:
        pid = int(edit["pid"])
        field = edit["field"]
        val = (update.message.text or "").strip()

        if field == "price":
            try:
                price = int(val)
                if price < 0:
                    raise ValueError()
            except:
                await update.message.reply_text("❌ Price must be a number (example: 100). Send again:")
                return
            await adb_exec("UPDATE products SET price=%s WHERE id=%s", (price, pid))
            await update.message.reply_text(f"✅ Price updated for product #{pid}.")
        elif field == "name":
            if not val:
                await update.message.reply_text("❌ Name can't be empty. Send again:")
                return
            await adb_exec("UPDATE products SET name=%s WHERE id=%s", (val, pid))
            await update.message.reply_text(f"✅ Name updated for product #{pid}.")
        elif field == "description":
            await adb_exec("UPDATE products SET description=%s WHERE id=%s", (val, pid))
            await update.message.reply_text(f"✅ Description updated for product #{pid}.")
        elif field == "category":
            if not val:
                await update.message.reply_text("❌ Category can't be empty. Send again:")
                return
            await adb_exec("UPDATE products SET category=%s WHERE id=%s", (val, pid))
            await update.message.reply_text(f"✅ Category updated for product #{pid}.")

        context.user_data["awaiting_prod_edit"] = None
        return

# =========================
# Menu router (keyboard)
# =========================
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🛒 Shop":
        await shop_categories_screen(update, context)
    elif text == "💳 Add Balance":
        await topup_menu(update, context)
    elif text == "💰 Balance":
        await balance_cmd(update, context)
    elif text == "🧾 History":
        await history_cmd(update, context)
    elif text == "❓ Help":
        await help_cmd(update, context)
    elif text == "💬 Chat Admin":
        await chat_admin_cmd(update, context)
    elif text == "🛠 Admin":
        await admin_entry(update, context)
    else:
        await update.message.reply_text("Use the menu buttons.", reply_markup=main_menu_kb(update.effective_user.id))

# =========================
# Build app
# =========================
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(shop_cb, pattern=r"^(shop_cat:|shop_home$|buy:|buy_confirm:|buy_cancel$)"))
    app.add_handler(CallbackQueryHandler(topup_cb, pattern=r"^(topup_|topup_method:|topup_amount:)"))
    app.add_handler(CallbackQueryHandler(topup_admin_cb, pattern=r"^(tu_approve:|tu_reject:)"))
    app.add_handler(CallbackQueryHandler(admin_menu_cb, pattern=r"^admin_|^prod_|^pay_setqr:"))
    app.add_handler(CallbackQueryHandler(text_pick_cb, pattern=r"^text_pick:"))

    # admin edit text / product edit field
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_text_message), group=0)

    # photos/documents:
    app.add_handler(MessageHandler(filters.PHOTO, admin_capture_qr_photo), group=1)
    app.add_handler(MessageHandler(filters.PHOTO, admin_capture_product_photo), group=2)
    app.add_handler(MessageHandler(filters.Document.ALL, admin_capture_product_file), group=3)

    # user proof photo
    app.add_handler(MessageHandler(filters.PHOTO, proof_photo_handler), group=4)

    # menu router last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router), group=10)

    return app

def main():
    app = build_app()
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
