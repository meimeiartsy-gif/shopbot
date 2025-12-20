import os
import re
import uuid
import asyncio
import logging
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
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shopbot")

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()

SHOP_PREFIX = os.getenv("SHOP_PREFIX", "shopnluna").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa").strip()
ADMIN_TME = os.getenv("ADMIN_TME", "https://t.me/lovebylunaa").strip()

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")

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
# DB helpers (sync) -> run in thread
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
# Safe edit fallback (fixes "button click -> error")
# =========================
async def safe_edit_or_send(q, text: str, reply_markup=None, parse_mode=None):
    try:
        await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.warning("Edit failed fallback send: %s", e)
        await q.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

# =========================
# SETTINGS / TEXTS
# =========================
DEFAULT_TEXTS = {
    "welcome_text": "🌙 *Luna’s Prem Shop*\n\nChoose an option below:",
    "help_text": f"Need help? Tap *Chat Admin* or DM {ADMIN_USERNAME}",

    "topup_menu_text": "💳 *Add Balance*\nChoose payment method:",
    "gcash_text": "💳 *GCash Payment*\nScan QR and pay exact amount.\n\nThen choose amount and send screenshot proof.",
    "gotyme_text": "🏦 *GoTyme Payment*\nScan QR and pay exact amount.\n\nThen choose amount and send screenshot proof.",

    "topup_send_proof_text": f"📸 Now send your *payment screenshot* (Photo or Document).\n\nFor fast approval, DM admin: {ADMIN_USERNAME}",
    "topup_received_text": f"✅ Thank you! Proof received.\n🧾 TopUp ID: `{{topup_id}}`\n⏳ Waiting for admin approval.\n\nFor fast approval, DM admin: {ADMIN_USERNAME}",
    "topup_approved_text": "✅ Top up approved!\n🧾 TopUp ID: `{topup_id}`\nAdded: *₱{amount}*\n💰 New Balance: *₱{balance}*",
    "topup_rejected_text": "❌ Top up rejected.\n🧾 TopUp ID: `{topup_id}`\n💰 Your Balance: *₱{balance}*",

    "insufficient_balance_text": "❌ Not enough balance. Please top up first.",
    "purchase_success_text": "✅ Purchase complete!\nTransaction ID: `{transaction_id}`\n💰 Remaining Balance: *₱{balance}*",
    "delivery_not_set_text": f"⚠️ Delivery is not set yet for this product.\nPlease DM admin: {ADMIN_USERNAME}",
}

SCHEMA_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  balance INTEGER NOT NULL DEFAULT 0,
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

async def init_db():
    await adb_exec(SCHEMA_CREATE_SQL)

    # default texts
    for k, v in DEFAULT_TEXTS.items():
        await adb_exec(
            "INSERT INTO settings(key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (k, v),
        )

    # QR keys
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

# =========================
# Utilities
# =========================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def make_id(prefix: str) -> str:
    return f"{SHOP_PREFIX}:{prefix}{uuid.uuid4().hex[:8].upper()}"

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

async def get_balance(uid: int) -> int:
    row = await adb_fetchone("SELECT balance FROM users WHERE user_id=%s", (uid,))
    return int(row["balance"]) if row else 0

def main_menu_kb(user_id: int):
    rows = [
        [KeyboardButton("🛒 Shop"), KeyboardButton("💳 Add Balance")],
        [KeyboardButton("💰 Balance"), KeyboardButton("🧾 History")],
        [KeyboardButton("💬 Chat Admin"), KeyboardButton("❓ Help")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton("🛠 Admin")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def chat_admin_inline_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("💬 Chat Admin", url=ADMIN_TME)]])

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="admin_products")],
        [InlineKeyboardButton("💳 Payment QR", callback_data="admin_pay")],
        [InlineKeyboardButton("💳 Pending Topups", callback_data="admin_topups_pending")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin_purchases")],
        [InlineKeyboardButton("⬅️ Close", callback_data="admin_close")],
    ])

def topup_method_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💳 GCash", callback_data="topup_method:gcash"),
            InlineKeyboardButton("🏦 GoTyme", callback_data="topup_method:gotyme"),
        ],
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
    return InlineKeyboardMarkup(rows)

# =========================
# Startup
# =========================
async def on_startup(app: Application):
    await init_db()
    logger.info("DB ready.")

# =========================
# Commands / Screens
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    welcome = await get_text("welcome_text")

    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(update.effective_user.id),
    )
    await show_categories(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await get_text("help_text")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=chat_admin_inline_kb())

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    bal = await get_balance(update.effective_user.id)
    await update.message.reply_text(
        f"💰 Balance: *₱{bal}*\n\nTap *Add Balance* to top up.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(update.effective_user.id),
    )

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    topups = await adb_fetchall(
        "SELECT topup_id, amount, method, status FROM topups WHERE user_id=%s ORDER BY id DESC LIMIT 10",
        (uid,),
    )
    purchases = await adb_fetchall(
        "SELECT transaction_id, price FROM purchases WHERE user_id=%s ORDER BY id DESC LIMIT 10",
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

    await update.message.reply_text(
        "\n".join(msg),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(uid),
    )

# =========================
# SHOP
# =========================
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = await adb_fetchall("SELECT DISTINCT category FROM products WHERE is_active=TRUE ORDER BY category ASC")
    if not cats:
        await update.message.reply_text("No products yet.")
        return

    buttons = [[InlineKeyboardButton(f"✨ {c['category']}", callback_data=f"cat:{c['category']}")] for c in cats]
    kb = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("🛒 *Shop Categories*\nChoose category:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    await show_categories(update, context)

async def shop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        products = await adb_fetchall(
            "SELECT id, name, price FROM products WHERE is_active=TRUE AND category=%s ORDER BY id DESC",
            (category,),
        )
        buttons = [[InlineKeyboardButton(f"{p['name']} — ₱{p['price']}", callback_data=f"prod:{p['id']}")] for p in products]
        buttons.append([InlineKeyboardButton("⬅️ Back to Categories", callback_data="cats_back")])

        await safe_edit_or_send(
            q,
            f"✨ *{category}*\nChoose product:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "cats_back":
        cats = await adb_fetchall("SELECT DISTINCT category FROM products WHERE is_active=TRUE ORDER BY category ASC")
        buttons = [[InlineKeyboardButton(f"✨ {c['category']}", callback_data=f"cat:{c['category']}")] for c in cats]
        await safe_edit_or_send(q, "🛒 *Shop Categories*\nChoose category:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        product = await adb_fetchone(
            "SELECT id, category, name, description, price, photo_file_id FROM products WHERE id=%s AND is_active=TRUE",
            (pid,),
        )
        if not product:
            await q.message.reply_text("Product not found.")
            return

        confirm_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Buy", callback_data=f"buy_confirm:{pid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="buy_cancel")],
        ])

        caption = (
            f"*{product['name']}*\n"
            f"Price: *₱{int(product['price'])}*\n\n"
            f"{product.get('description') or ''}\n\n"
            "Tap ✅ Confirm to pay and receive delivery."
        )

        if (product.get("photo_file_id") or "").strip():
            await q.message.reply_photo(
                photo=product["photo_file_id"],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=confirm_kb,
            )
        else:
            await q.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=confirm_kb)
        return

    if data == "buy_cancel":
        await q.message.reply_text("✅ Cancelled. No payment deducted.")
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
        balance = await get_balance(uid)

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

        new_balance = await get_balance(uid)
        done_tpl = await get_text("purchase_success_text")
        done = done_tpl.format(transaction_id=txid, balance=new_balance)
        await q.message.reply_text(done, parse_mode=ParseMode.MARKDOWN)

        delivery_file_id = (product.get("delivery_file_id") or "").strip()
        delivery_caption = (product.get("delivery_caption") or "").strip()

        if delivery_file_id:
            cap = delivery_caption if delivery_caption else f"📦 *Delivery*\nProduct: *{product['name']}*\nTransaction: `{txid}`"
            await q.message.reply_document(document=delivery_file_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
        else:
            warn = await get_text("delivery_not_set_text")
            await q.message.reply_text(warn)
        return

# =========================
# TOPUP FLOW
# =========================
async def topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await get_text("topup_menu_text")
    if update.message:
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=topup_method_kb())
    else:
        q = update.callback_query
        await safe_edit_or_send(q, txt, reply_markup=topup_method_kb(), parse_mode=ParseMode.MARKDOWN)

async def topup_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data == "topup_menu":
        await topup_menu(update, context)
        return

    if data == "topup_back":
        await q.message.reply_text("Back.")
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
        )
        return

async def proof_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_proof"):
        return

    topup_id = context.user_data.get("pending_topup_id")
    if not topup_id:
        context.user_data["awaiting_proof"] = False
        return

    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    if not proof_file_id:
        await update.message.reply_text("Please send proof as PHOTO or DOCUMENT.")
        return

    row = await adb_fetchone("SELECT * FROM topups WHERE topup_id=%s", (topup_id,))
    if not row or row["status"] != "pending":
        await update.message.reply_text("Topup not found or already handled.")
        context.user_data["awaiting_proof"] = False
        context.user_data["pending_topup_id"] = None
        return

    await adb_exec("UPDATE topups SET proof_file_id=%s WHERE topup_id=%s", (proof_file_id, topup_id))

    txt = (await get_text("topup_received_text")).format(topup_id=topup_id)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    uid = int(row["user_id"])
    amount = int(row["amount"])
    method = (row["method"] or "").upper()

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"tu_approve:{topup_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"tu_reject:{topup_id}"),
        ]
    ])

    caption = (
        f"💳 *TOPUP PROOF SUBMITTED*\n"
        f"TopUp ID: `{topup_id}`\n"
        f"User ID: `{uid}`\n"
        f"User: @{update.effective_user.username}\n"
        f"Method: *{method}*\n"
        f"Amount: *₱{amount}*\n\n"
        f"Approve/Reject below:"
    )

    for admin_id in ADMIN_IDS:
        try:
            if update.message.photo:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=proof_file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=admin_kb,
                )
            else:
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=proof_file_id,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=admin_kb,
                )
        except Exception as e:
            logger.warning("Admin notify failed: %s", e)

    context.user_data["awaiting_proof"] = False
    context.user_data["pending_topup_id"] = None

async def topup_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ You are not admin.")
        return

    action, topup_id = q.data.split(":", 1)

    row = await adb_fetchone("SELECT * FROM topups WHERE topup_id=%s", (topup_id,))
    if not row:
        await q.message.reply_text("Topup not found.")
        return
    if row["status"] != "pending":
        await q.message.reply_text(f"Already handled: {row['status']}")
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
        new_balance = await get_balance(uid)

        txt = (await get_text("topup_approved_text")).format(topup_id=topup_id, amount=amount, balance=new_balance)
        try:
            await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass

        await q.message.reply_text(f"✅ Approved. User new balance: ₱{new_balance}")

    elif action == "tu_reject":
        await adb_exec(
            "UPDATE topups SET status='rejected', admin_id=%s, decided_at=%s WHERE topup_id=%s",
            (q.from_user.id, now, topup_id),
        )
        bal = await get_balance(uid)
        txt = (await get_text("topup_rejected_text")).format(topup_id=topup_id, balance=bal)
        try:
            await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass
        await q.message.reply_text("❌ Rejected.")

# =========================
# ADMIN PANEL
# =========================
async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not admin.")
        return
    await update.message.reply_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())

def products_list_kb(products: List[Dict[str, Any]]):
    btns = []
    btns.append([InlineKeyboardButton("➕ Add Product", callback_data="prod_add")])
    for p in products[:30]:
        status = "✅" if p["is_active"] else "❌"
        btns.append([InlineKeyboardButton(
            f"{status} #{p['id']} {p['category']} | {p['name']} (₱{p['price']})",
            callback_data=f"prod_open:{p['id']}"
        )])
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
    data = q.data

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ You are not admin.")
        return

    if data == "admin_close":
        await safe_edit_or_send(q, "Closed.")
        return

    if data == "admin_back":
        await safe_edit_or_send(q, "🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())
        return

    if data == "admin_pay":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Set GCash QR", callback_data="pay_setqr:gcash")],
            [InlineKeyboardButton("Set GoTyme QR", callback_data="pay_setqr:gotyme")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        ])
        await safe_edit_or_send(q, "💳 *Payment QR*\nTap one then send QR as PHOTO.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data == "admin_products":
        products = await adb_fetchall("SELECT id, category, name, price, is_active FROM products ORDER BY id DESC")
        await safe_edit_or_send(q, "📦 *Products*", parse_mode=ParseMode.MARKDOWN, reply_markup=products_list_kb(products))
        return

    if data == "admin_topups_pending":
        rows = await adb_fetchall(
            "SELECT topup_id, user_id, amount, method FROM topups WHERE status='pending' ORDER BY id DESC LIMIT 30"
        )
        if not rows:
            await safe_edit_or_send(
                q,
                "✅ No pending topups.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]),
            )
            return
        msg = ["💳 *Pending Topups (last 30)*"]
        for r in rows:
            msg.append(f"• `{r['topup_id']}` | +₱{r['amount']} | {str(r['method']).upper()} | user `{r['user_id']}`")
        await safe_edit_or_send(
            q,
            "\n".join(msg),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]),
        )
        return

    if data == "admin_purchases":
        rows = await adb_fetchall(
            """
            SELECT p.transaction_id, p.price, p.user_id, pr.name AS product_name
            FROM purchases p
            LEFT JOIN products pr ON pr.id = p.product_id
            ORDER BY p.id DESC LIMIT 30
            """
        )
        msg = ["🧾 *Purchases (last 30)*"]
        if not rows:
            msg.append("— none")
        else:
            for r in rows:
                pname = r["product_name"] or "deleted-product"
                msg.append(f"• `{r['transaction_id']}` | {pname} | -₱{r['price']} | user `{r['user_id']}`")
        await safe_edit_or_send(
            q,
            "\n".join(msg),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]),
        )
        return

    if data == "prod_add":
        await adb_exec(
            "INSERT INTO products(category, name, description, price, is_active) VALUES (%s,%s,%s,%s,TRUE)",
            ("General", "New Product", "", 0),
        )
        products = await adb_fetchall("SELECT id, category, name, price, is_active FROM products ORDER BY id DESC")
        await safe_edit_or_send(q, "✅ Product created. Select it to edit:", parse_mode=ParseMode.MARKDOWN, reply_markup=products_list_kb(products))
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
        await safe_edit_or_send(q, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=product_manage_kb(pid, bool(p["is_active"])))
        return

    if data.startswith("prod_toggle:"):
        pid = int(data.split(":")[1])
        await adb_exec("UPDATE products SET is_active = NOT is_active WHERE id=%s", (pid,))
        await q.message.reply_text("✅ Toggled. Re-open product to see status.")
        return

    for prefix, field in [
        ("prod_edit_cat:", "category"),
        ("prod_edit_name:", "name"),
        ("prod_edit_price:", "price"),
        ("prod_edit_desc:", "description"),
    ]:
        if data.startswith(prefix):
            pid = int(data.split(":")[1])
            context.user_data["awaiting_prod_edit"] = {"pid": pid, "field": field}
            label = {
                "category": "category (example: Editing Apps)",
                "name": "name",
                "price": "price (number)",
                "description": "description",
            }[field]
            await q.message.reply_text(f"✏️ Send NEW *{label}* for product #{pid}:", parse_mode=ParseMode.MARKDOWN)
            return

    if data.startswith("prod_set_photo:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_photo"] = pid
        await q.message.reply_text(f"🖼 Send product photo as *PHOTO* for product #{pid}:", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_set_file:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_file"] = pid
        await q.message.reply_text(f"📦 Send delivery file as *DOCUMENT* for product #{pid}:", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("pay_setqr:"):
        method = data.split(":", 1)[1]
        context.user_data["awaiting_qr_set"] = method
        await q.message.reply_text(f"✅ Now send the *{method.upper()} QR* as a PHOTO.", parse_mode=ParseMode.MARKDOWN)
        return

# =========================
# ADMIN capture actions
# =========================
async def admin_capture_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    method = context.user_data.get("awaiting_qr_set")
    if not method:
        return
    if not update.message.photo:
        await update.message.reply_text("Please send QR as a PHOTO.")
        return

    file_id = update.message.photo[-1].file_id
    if method == "gcash":
        await set_setting("GCASH_QR_FILE_ID", file_id)
    else:
        await set_setting("GOTYME_QR_FILE_ID", file_id)

    context.user_data["awaiting_qr_set"] = None
    await update.message.reply_text(f"✅ {method.upper()} QR saved! Customers can now see it in Add Balance.")

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
        await update.message.reply_text("Please send delivery file as DOCUMENT.")
        return

    file_id = update.message.document.file_id
    await adb_exec("UPDATE products SET delivery_file_id=%s WHERE id=%s", (file_id, int(pid)))
    context.user_data["awaiting_prod_file"] = None
    await update.message.reply_text(f"✅ Delivery file saved for product #{pid}. Auto-delivery is ON.")

async def admin_edit_product_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    edit = context.user_data.get("awaiting_prod_edit")
    if not edit:
        return

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
    else:
        if field in ("category", "name") and not val:
            await update.message.reply_text("❌ This can't be empty. Send again:")
            return
        await adb_exec(f"UPDATE products SET {field}=%s WHERE id=%s", (val, pid))
        await update.message.reply_text(f"✅ Updated {field} for product #{pid}.")

    context.user_data["awaiting_prod_edit"] = None

# =========================
# MENU Router
# =========================
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🛒 Shop":
        await shop_cmd(update, context)
    elif text == "💳 Add Balance":
        await topup_menu(update, context)
    elif text == "💰 Balance":
        await balance_cmd(update, context)
    elif text == "🧾 History":
        await history_cmd(update, context)
    elif text == "💬 Chat Admin":
        await update.message.reply_text(f"Tap below to chat admin: {ADMIN_USERNAME}", reply_markup=chat_admin_inline_kb())
    elif text == "❓ Help":
        await help_cmd(update, context)
    elif text == "🛠 Admin":
        await admin_entry(update, context)
    else:
        await update.message.reply_text("Use the menu buttons.", reply_markup=main_menu_kb(update.effective_user.id))

# =========================
# SINGLE CALLBACK ROUTER (IMPORTANT FIX)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
        data = q.data or ""
        logger.info("Callback: %s", data)

        # SHOP
        if data.startswith(("cat:", "prod:", "buy_confirm:")) or data in ("cats_back", "buy_cancel"):
            await shop_cb(update, context)
            return

        # TOPUP
        if data.startswith(("topup_", "topup_method:", "topup_amount:")):
            await topup_cb(update, context)
            return

        # ADMIN approve/reject
        if data.startswith(("tu_approve:", "tu_reject:")):
            await topup_admin_cb(update, context)
            return

        # ADMIN panel
        if data.startswith(("admin_", "prod_", "pay_setqr:")):
            await admin_menu_cb(update, context)
            return

        await q.message.reply_text("Unknown button. Type /start again.")

    except Exception as e:
        logger.exception("Callback crashed: %s", e)
        await q.message.reply_text("⚠️ Bot error happened. Try again in a moment.")

# =========================
# ERROR handler
# =========================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, "⚠️ Bot error happened. Try again in a moment.")
    except:
        pass

# =========================
# BUILD APP
# =========================
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # ✅ ONE callback handler only
    app.add_handler(CallbackQueryHandler(on_callback))

    # admin edits (text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_product_message), group=0)

    # admin captures
    app.add_handler(MessageHandler(filters.PHOTO, admin_capture_qr_photo), group=1)
    app.add_handler(MessageHandler(filters.PHOTO, admin_capture_product_photo), group=2)
    app.add_handler(MessageHandler(filters.Document.ALL, admin_capture_product_file), group=3)

    # customer proof capture
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL), proof_handler), group=4)

    # menu router last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router), group=10)

    return app

def main():
    app = build_app()
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
