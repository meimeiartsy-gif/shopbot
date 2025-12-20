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
DATABASE_URL = os.getenv("DATABASE_URL")  # Railway Postgres
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()

SHOP_PREFIX = os.getenv("SHOP_PREFIX", "shopnluna").strip()  # topup id prefix
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

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def make_id(prefix: str) -> str:
    # example: shopnluna:TUAB12CD34
    return f"{SHOP_PREFIX}:{prefix}{uuid.uuid4().hex[:10].upper()}"

# =========================
# DB (sync) wrappers -> run in thread
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
# DB schema + settings
# =========================
DEFAULT_SETTINGS = {
    # user-facing texts you can edit later
    "welcome_text": "🌙 *Luna’s Prem Shop*\n━━━━━━━━━━━━━━\nChoose an option below.",
    "help_text": "If you need help, tap *Chat Admin*.",
    "topup_menu_text": "💳 *Add Balance*\nChoose payment method:",
    "gcash_text": "💳 *GCash Payment*\nScan QR and pay the exact amount.\nThen select amount and send proof.",
    "gotyme_text": "🏦 *GoTyme Payment*\nScan QR and pay the exact amount.\nThen select amount and send proof.",
    "topup_wait_text": "✅ Thank you! Proof received.\n🧾 TopUp ID: `{topup_id}`\n⏳ Waiting for admin approval.\n\nFor fast approval DM admin: " + ADMIN_USERNAME,

    "purchase_done_text": "✅ Purchase complete!\nTransaction: `{tx}`\n💰 New Balance: *₱{bal}*",
    "no_balance_text": "❌ Not enough balance. Please top up first.",
    "delivery_missing_text": "⚠️ Delivery is not set yet. Please DM admin: " + ADMIN_USERNAME,

    # QR storage
    "GCASH_QR_FILE_ID": "",
    "GOTYME_QR_FILE_ID": "",
}

SCHEMA_SQL = """
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
  tx_id TEXT UNIQUE NOT NULL,
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
    await adb_exec(SCHEMA_SQL)
    for k, v in DEFAULT_SETTINGS.items():
        await adb_exec(
            "INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT (key) DO NOTHING",
            (k, v),
        )

async def get_setting(key: str) -> str:
    row = await adb_fetchone("SELECT value FROM settings WHERE key=%s", (key,))
    return str(row["value"]) if row else ""

async def set_setting(key: str, value: str) -> None:
    await adb_exec(
        "INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        (key, value),
    )

# =========================
# Safe edit fallback
# =========================
async def safe_edit_or_send(q, text: str, reply_markup=None, parse_mode=None):
    try:
        await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        await q.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

# =========================
# UI keyboards
# =========================
def main_menu_kb(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("🛒 Shop"), KeyboardButton("💳 Add Balance")],
        [KeyboardButton("💰 Balance"), KeyboardButton("🧾 History")],
        [KeyboardButton("💬 Chat Admin"), KeyboardButton("❓ Help")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton("🛠 Admin")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def chat_admin_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("💬 Chat Admin", url=ADMIN_TME)]])

def topup_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💳 GCash", callback_data="topup_method:gcash"),
            InlineKeyboardButton("🏦 GoTyme", callback_data="topup_method:gotyme"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="topup_back")],
    ])

def topup_amount_kb(method: str) -> InlineKeyboardMarkup:
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

def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="admin_products")],
        [InlineKeyboardButton("💳 Pending Topups", callback_data="admin_topups")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin_purchases")],
        [InlineKeyboardButton("💳 Set QR", callback_data="admin_pay")],
        [InlineKeyboardButton("✏️ Edit Texts", callback_data="admin_texts")],
        [InlineKeyboardButton("⬅️ Close", callback_data="admin_close")],
    ])

def admin_pay_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Set GCash QR", callback_data="pay_setqr:gcash")],
        [InlineKeyboardButton("Set GoTyme QR", callback_data="pay_setqr:gotyme")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])

def admin_texts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Edit Welcome", callback_data="txt_edit:welcome_text")],
        [InlineKeyboardButton("Edit Help", callback_data="txt_edit:help_text")],
        [InlineKeyboardButton("Edit Topup Menu", callback_data="txt_edit:topup_menu_text")],
        [InlineKeyboardButton("Edit GCash Text", callback_data="txt_edit:gcash_text")],
        [InlineKeyboardButton("Edit GoTyme Text", callback_data="txt_edit:gotyme_text")],
        [InlineKeyboardButton("Edit Proof Received Text", callback_data="txt_edit:topup_wait_text")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])

def products_list_kb(products: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    btns = [[InlineKeyboardButton("➕ Add Product", callback_data="prod_add")]]
    for p in products[:30]:
        status = "✅" if p["is_active"] else "❌"
        btns.append([InlineKeyboardButton(
            f"{status} #{p['id']} {p['category']} | {p['name']} (₱{p['price']})",
            callback_data=f"prod_open:{p['id']}"
        )])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
    return InlineKeyboardMarkup(btns)

def product_manage_kb(pid: int, active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏷 Edit Category", callback_data=f"prod_edit:category:{pid}")],
        [InlineKeyboardButton("✏️ Edit Name", callback_data=f"prod_edit:name:{pid}")],
        [InlineKeyboardButton("💵 Edit Price", callback_data=f"prod_edit:price:{pid}")],
        [InlineKeyboardButton("📝 Edit Description", callback_data=f"prod_edit:description:{pid}")],
        [InlineKeyboardButton("🖼 Set Photo", callback_data=f"prod_set_photo:{pid}")],
        [InlineKeyboardButton("📦 Set Delivery File", callback_data=f"prod_set_file:{pid}")],
        [InlineKeyboardButton(("✅ Active" if active else "❌ Inactive") + " (toggle)", callback_data=f"prod_toggle:{pid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_products")],
    ])

# =========================
# User helpers
# =========================
async def upsert_user(u) -> None:
    await adb_exec(
        """
        INSERT INTO users(user_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
          username=EXCLUDED.username,
          first_name=EXCLUDED.first_name
        """,
        (u.id, u.username, u.first_name),
    )

async def get_balance(uid: int) -> int:
    row = await adb_fetchone("SELECT balance FROM users WHERE user_id=%s", (uid,))
    return int(row["balance"]) if row else 0

# =========================
# Startup
# =========================
async def on_startup(app: Application):
    await init_db()
    logger.info("DB initialized/ready.")

# =========================
# /start and menu
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    welcome = await get_setting("welcome_text")
    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(update.effective_user.id),
    )
    await show_categories(update, context)

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    uid = update.effective_user.id

    if txt == "🛒 Shop":
        await show_categories(update, context)
        return

    if txt == "💳 Add Balance":
        await topup_menu(update, context)
        return

    if txt == "💰 Balance":
        bal = await get_balance(uid)
        await update.message.reply_text(
            f"💰 Balance: *₱{bal}*\n\nTap *Add Balance* to top up.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_kb(uid),
        )
        return

    if txt == "🧾 History":
        await history_cmd(update, context)
        return

    if txt == "💬 Chat Admin":
        await update.message.reply_text(
            f"DM admin: {ADMIN_USERNAME}",
            reply_markup=chat_admin_inline_kb(),
        )
        return

    if txt == "❓ Help":
        help_text = await get_setting("help_text")
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN, reply_markup=chat_admin_inline_kb())
        return

    if txt == "🛠 Admin":
        if not is_admin(uid):
            await update.message.reply_text("❌ You are not admin.")
            return
        await update.message.reply_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())
        return

    await update.message.reply_text("Use the buttons below.", reply_markup=main_menu_kb(uid))

# =========================
# History
# =========================
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    topups = await adb_fetchall(
        "SELECT topup_id, amount, method, status FROM topups WHERE user_id=%s ORDER BY id DESC LIMIT 10",
        (uid,),
    )
    purchases = await adb_fetchall(
        "SELECT tx_id, price FROM purchases WHERE user_id=%s ORDER BY id DESC LIMIT 10",
        (uid,),
    )

    msg = ["🧾 *History*"]
    msg.append("\n*Topups*")
    if not topups:
        msg.append("— none")
    else:
        for t in topups:
            msg.append(f"• `{t['topup_id']}` | +₱{t['amount']} | {str(t['method']).upper()} | {t['status']}")

    msg.append("\n*Purchases*")
    if not purchases:
        msg.append("— none")
    else:
        for p in purchases:
            msg.append(f"• `{p['tx_id']}` | -₱{p['price']}")

    await update.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))

# =========================
# Shop (categories → products → buy)
# =========================
async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = await adb_fetchall(
        "SELECT DISTINCT category FROM products WHERE is_active=TRUE ORDER BY category ASC"
    )
    if not cats:
        await update.message.reply_text("No products yet.")
        return
    buttons = [[InlineKeyboardButton(f"✨ {c['category']}", callback_data=f"cat:{c['category']}")] for c in cats]
    await update.message.reply_text(
        "🛒 *Shop*\nChoose category:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def shop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        products = await adb_fetchall(
            "SELECT id, name, price FROM products WHERE is_active=TRUE AND category=%s ORDER BY id DESC",
            (cat,),
        )
        btns = [[InlineKeyboardButton(f"{p['name']} — ₱{p['price']}", callback_data=f"prod:{p['id']}")] for p in products]
        btns.append([InlineKeyboardButton("⬅️ Back", callback_data="cats_back")])
        await safe_edit_or_send(q, f"✨ *{cat}*\nSelect product:", InlineKeyboardMarkup(btns), ParseMode.MARKDOWN)
        return

    if data == "cats_back":
        await show_categories(update, context)
        return

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        p = await adb_fetchone("SELECT * FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not p:
            await q.message.reply_text("Product not found.")
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{pid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="buy_cancel")],
        ])

        text = (
            f"*{p['name']}*\n"
            f"Price: *₱{int(p['price'])}*\n\n"
            f"{p.get('description') or ''}"
        )

        if (p.get("photo_file_id") or "").strip():
            await q.message.reply_photo(photo=p["photo_file_id"], caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        else:
            await q.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data == "buy_cancel":
        await q.message.reply_text("✅ Cancelled. No payment deducted.")
        return

    if data.startswith("buy:"):
        pid = int(data.split(":")[1])
        uid = q.from_user.id
        await upsert_user(q.from_user)

        p = await adb_fetchone("SELECT * FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not p:
            await q.message.reply_text("Product not found.")
            return

        price = int(p["price"])
        bal = await get_balance(uid)
        if bal < price:
            await q.message.reply_text(await get_setting("no_balance_text"))
            return

        tx = make_id("TX")
        await adb_exec("UPDATE users SET balance = balance - %s WHERE user_id=%s", (price, uid))
        await adb_exec("INSERT INTO purchases(tx_id,user_id,product_id,price) VALUES(%s,%s,%s,%s)", (tx, uid, pid, price))

        new_bal = await get_balance(uid)
        done_tpl = await get_setting("purchase_done_text")
        await q.message.reply_text(done_tpl.format(tx=tx, bal=new_bal), parse_mode=ParseMode.MARKDOWN)

        delivery_file = (p.get("delivery_file_id") or "").strip()
        delivery_caption = (p.get("delivery_caption") or "").strip()
        if not delivery_file:
            await q.message.reply_text(await get_setting("delivery_missing_text"))
            return

        cap = delivery_caption if delivery_caption else f"📦 *Delivery*\nProduct: *{p['name']}*\nTransaction: `{tx}`"
        await q.message.reply_document(document=delivery_file, caption=cap, parse_mode=ParseMode.MARKDOWN)
        return

# =========================
# Topup (Add Balance)
# =========================
async def topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await get_setting("topup_menu_text")
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=topup_method_kb())
    else:
        await safe_edit_or_send(update.callback_query, text, topup_method_kb(), ParseMode.MARKDOWN)

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
            txt = await get_setting("gcash_text")
            qr = await get_setting("GCASH_QR_FILE_ID")
        else:
            txt = await get_setting("gotyme_text")
            qr = await get_setting("GOTYME_QR_FILE_ID")

        if qr.strip():
            await q.message.reply_photo(photo=qr.strip(), caption=txt, parse_mode=ParseMode.MARKDOWN)
        else:
            await q.message.reply_text(txt + "\n\n⚠️ Admin has not set QR yet.", parse_mode=ParseMode.MARKDOWN)

        await q.message.reply_text("✨ Choose amount:", reply_markup=topup_amount_kb(method))
        return

    if data.startswith("topup_amount:"):
        _, method, amt_s = data.split(":")
        amount = int(amt_s)
        uid = q.from_user.id
        await upsert_user(q.from_user)

        topup_id = make_id("TU")
        await adb_exec(
            "INSERT INTO topups(topup_id,user_id,amount,method,status) VALUES(%s,%s,%s,%s,'pending')",
            (topup_id, uid, amount, method),
        )

        # now wait for proof
        context.user_data["awaiting_proof"] = True
        context.user_data["pending_topup_id"] = topup_id

        await q.message.reply_text(
            f"🧾 TopUp ID: `{topup_id}`\nAmount: *₱{amount}*\nMethod: *{method.upper()}*\n\n📸 Now send your screenshot proof (photo or document).",
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

    wait_tpl = await get_setting("topup_wait_text")
    await update.message.reply_text(wait_tpl.format(topup_id=topup_id), parse_mode=ParseMode.MARKDOWN)

    # notify admins with approve/reject buttons
    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"tu_approve:{topup_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"tu_reject:{topup_id}"),
        ]
    ])
    caption = (
        f"💳 *TOPUP PROOF*\n"
        f"TopUp ID: `{topup_id}`\n"
        f"User ID: `{int(row['user_id'])}`\n"
        f"User: @{update.effective_user.username}\n"
        f"Method: *{str(row['method']).upper()}*\n"
        f"Amount: *₱{int(row['amount'])}*\n\n"
        "Approve/Reject below:"
    )

    for admin_id in ADMIN_IDS:
        try:
            if update.message.photo:
                await context.bot.send_photo(admin_id, proof_file_id, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_kb)
            else:
                await context.bot.send_document(admin_id, proof_file_id, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_kb)
        except Exception as e:
            logger.warning("Admin notify failed: %s", e)

    context.user_data["awaiting_proof"] = False
    context.user_data["pending_topup_id"] = None

async def topup_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Not admin.")
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
        await adb_exec("UPDATE topups SET status='approved', admin_id=%s, decided_at=%s WHERE topup_id=%s",
                       (q.from_user.id, now, topup_id))
        await adb_exec("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, uid))
        bal = await get_balance(uid)
        await q.message.reply_text(f"✅ Approved. New user balance: ₱{bal}")
        try:
            await context.bot.send_message(uid, f"✅ Top up approved!\n🧾 `{topup_id}`\n+₱{amount}\n💰 Balance: *₱{bal}*",
                                           parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass
        return

    if action == "tu_reject":
        await adb_exec("UPDATE topups SET status='rejected', admin_id=%s, decided_at=%s WHERE topup_id=%s",
                       (q.from_user.id, now, topup_id))
        bal = await get_balance(uid)
        await q.message.reply_text("❌ Rejected.")
        try:
            await context.bot.send_message(uid, f"❌ Top up rejected.\n🧾 `{topup_id}`\n💰 Balance: *₱{bal}*",
                                           parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass
        return

# =========================
# Admin panel
# =========================
async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Not admin.")
        return

    if data == "admin_close":
        await safe_edit_or_send(q, "Closed.")
        return

    if data == "admin_back":
        await safe_edit_or_send(q, "🛠 *Admin Panel*", admin_menu_kb(), ParseMode.MARKDOWN)
        return

    if data == "admin_pay":
        await safe_edit_or_send(q, "💳 *Set QR*\nTap one then send QR as PHOTO.", admin_pay_kb(), ParseMode.MARKDOWN)
        return

    if data == "admin_texts":
        await safe_edit_or_send(q, "✏️ *Edit Texts*\nTap which text to edit:", admin_texts_kb(), ParseMode.MARKDOWN)
        return

    if data.startswith("txt_edit:"):
        key = data.split(":", 1)[1]
        context.user_data["awaiting_text_key"] = key
        current = await get_setting(key)
        await q.message.reply_text(f"Send new text for `{key}`.\n\nCurrent:\n{current}", parse_mode=ParseMode.MARKDOWN)
        return

    if data == "admin_products":
        products = await adb_fetchall("SELECT id,category,name,price,is_active FROM products ORDER BY id DESC")
        await safe_edit_or_send(q, "📦 *Products*", products_list_kb(products), ParseMode.MARKDOWN)
        return

    if data == "admin_topups":
        rows = await adb_fetchall("SELECT topup_id,user_id,amount,method,status FROM topups ORDER BY id DESC LIMIT 30")
        lines = ["💳 *Topups (last 30)*"]
        for r in rows:
            lines.append(f"• `{r['topup_id']}` | +₱{r['amount']} | {str(r['method']).upper()} | {r['status']} | user `{r['user_id']}`")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]])
        await safe_edit_or_send(q, "\n".join(lines) if rows else "No topups.", kb, ParseMode.MARKDOWN)
        return

    if data == "admin_purchases":
        rows = await adb_fetchall("""
            SELECT p.tx_id, p.price, p.user_id, pr.name AS product_name
            FROM purchases p
            LEFT JOIN products pr ON pr.id=p.product_id
            ORDER BY p.id DESC LIMIT 30
        """)
        lines = ["🧾 *Purchases (last 30)*"]
        for r in rows:
            lines.append(f"• `{r['tx_id']}` | {r['product_name'] or 'deleted'} | -₱{r['price']} | user `{r['user_id']}`")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]])
        await safe_edit_or_send(q, "\n".join(lines) if rows else "No purchases.", kb, ParseMode.MARKDOWN)
        return

    if data == "prod_add":
        await adb_exec("INSERT INTO products(category,name,description,price,is_active) VALUES(%s,%s,%s,%s,TRUE)",
                       ("General", "New Product", "", 0))
        products = await adb_fetchall("SELECT id,category,name,price,is_active FROM products ORDER BY id DESC")
        await safe_edit_or_send(q, "✅ Product created. Click it to edit.", products_list_kb(products), ParseMode.MARKDOWN)
        return

    if data.startswith("prod_open:"):
        pid = int(data.split(":")[1])
        p = await adb_fetchone("SELECT * FROM products WHERE id=%s", (pid,))
        if not p:
            await q.message.reply_text("Product not found.")
            return
        txt = (
            f"📦 *Product #{pid}*\n\n"
            f"*Category:* {p['category']}\n"
            f"*Name:* {p['name']}\n"
            f"*Price:* ₱{int(p['price'])}\n"
            f"*Active:* {'YES' if p['is_active'] else 'NO'}\n"
            f"*Photo:* {'✅' if (p.get('photo_file_id') or '').strip() else '❌'}\n"
            f"*Delivery:* {'✅' if (p.get('delivery_file_id') or '').strip() else '❌'}\n\n"
            f"*Description:*\n{p.get('description') or ''}"
        )
        await safe_edit_or_send(q, txt, product_manage_kb(pid, bool(p["is_active"])), ParseMode.MARKDOWN)
        return

    if data.startswith("prod_toggle:"):
        pid = int(data.split(":")[1])
        await adb_exec("UPDATE products SET is_active = NOT is_active WHERE id=%s", (pid,))
        await q.message.reply_text("✅ Toggled. Open product again to see changes.")
        return

    if data.startswith("prod_edit:"):
        _, field, pid_s = data.split(":")
        pid = int(pid_s)
        context.user_data["awaiting_prod_edit"] = {"pid": pid, "field": field}
        await q.message.reply_text(f"Send new value for *{field}* (product #{pid}):", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_set_photo:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_photo"] = pid
        await q.message.reply_text(f"Send PHOTO for product #{pid}.", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("prod_set_file:"):
        pid = int(data.split(":")[1])
        context.user_data["awaiting_prod_file"] = pid
        await q.message.reply_text(f"Send DOCUMENT delivery file for product #{pid}.", parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("pay_setqr:"):
        method = data.split(":")[1]
        context.user_data["awaiting_qr_set"] = method
        await q.message.reply_text(f"Now send *{method.upper()} QR* as PHOTO.", parse_mode=ParseMode.MARKDOWN)
        return

# =========================
# Admin capture messages
# =========================
async def admin_text_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    key = context.user_data.get("awaiting_text_key")
    if not key:
        return
    new_text = (update.message.text or "").strip()
    await set_setting(key, new_text)
    context.user_data["awaiting_text_key"] = None
    await update.message.reply_text(f"✅ Updated `{key}`.", parse_mode=ParseMode.MARKDOWN)

async def admin_product_edit_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        except:
            await update.message.reply_text("❌ Price must be a number. Send again:")
            return
        await adb_exec("UPDATE products SET price=%s WHERE id=%s", (price, pid))
    elif field in ("category", "name", "description"):
        await adb_exec(f"UPDATE products SET {field}=%s WHERE id=%s", (val, pid))
    else:
        await update.message.reply_text("Unknown field.")
        context.user_data["awaiting_prod_edit"] = None
        return

    context.user_data["awaiting_prod_edit"] = None
    await update.message.reply_text(f"✅ Updated {field} for product #{pid}.")

async def admin_capture_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    method = context.user_data.get("awaiting_qr_set")
    if not method:
        return
    if not update.message.photo:
        await update.message.reply_text("Send QR as PHOTO.")
        return
    file_id = update.message.photo[-1].file_id
    if method == "gcash":
        await set_setting("GCASH_QR_FILE_ID", file_id)
    else:
        await set_setting("GOTYME_QR_FILE_ID", file_id)
    context.user_data["awaiting_qr_set"] = None
    await update.message.reply_text(f"✅ Saved {method.upper()} QR.")

async def admin_capture_product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    pid = context.user_data.get("awaiting_prod_photo")
    if not pid:
        return
    if not update.message.photo:
        return
    file_id = update.message.photo[-1].file_id
    await adb_exec("UPDATE products SET photo_file_id=%s WHERE id=%s", (file_id, int(pid)))
    context.user_data["awaiting_prod_photo"] = None
    await update.message.reply_text(f"✅ Product photo saved for #{pid}.")

async def admin_capture_product_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    pid = context.user_data.get("awaiting_prod_file")
    if not pid:
        return
    if not update.message.document:
        await update.message.reply_text("Send delivery as DOCUMENT.")
        return
    file_id = update.message.document.file_id
    await adb_exec("UPDATE products SET delivery_file_id=%s WHERE id=%s", (file_id, int(pid)))
    context.user_data["awaiting_prod_file"] = None
    await update.message.reply_text(f"✅ Delivery file saved for #{pid} (auto-delivery ON).")

# =========================
# SINGLE CALLBACK ROUTER (fixes your button errors)
# =========================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
        data = q.data or ""
        logger.info("Callback: %s", data)

        # shop
        if data.startswith(("cat:", "prod:", "buy:")) or data in ("cats_back", "buy_cancel"):
            await shop_cb(update, context)
            return

        # topup
        if data.startswith(("topup_", "topup_method:", "topup_amount:")):
            await topup_cb(update, context)
            return

        # approve/reject
        if data.startswith(("tu_approve:", "tu_reject:")):
            await topup_admin_cb(update, context)
            return

        # admin
        if data.startswith(("admin_", "prod_", "pay_setqr:", "txt_edit:")):
            await admin_cb(update, context)
            return

        await q.message.reply_text("Unknown button. Type /start again.")

    except Exception as e:
        logger.exception("Callback crashed: %s", e)
        await q.message.reply_text("⚠️ Bot error happened. Try again in a moment.")

# =========================
# Error handler
# =========================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)

# =========================
# Build App
# =========================
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_callback))

    # proof capture (customer) - photo/doc
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, proof_handler), group=0)

    # admin captures
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_capture), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_product_edit_capture), group=2)
    app.add_handler(MessageHandler(filters.PHOTO, admin_capture_qr), group=3)
    app.add_handler(MessageHandler(filters.PHOTO, admin_capture_product_photo), group=4)
    app.add_handler(MessageHandler(filters.Document.ALL, admin_capture_product_file), group=5)

    # menu router last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router), group=10)

    return app

def main():
    app = build_app()
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
