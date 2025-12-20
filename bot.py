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
    ConversationHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("botTOKEN") or os.getenv("botToken")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()  # comma-separated
SHOP_PREFIX = os.getenv("SHOP_PREFIX", "shopnluna").strip()  # for transaction/topup ids

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
# DB helpers (sync, run in thread)
# =========================
def db_connect():
    # Railway Postgres often requires SSL
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
# Schema + settings
# =========================
DEFAULT_TEXTS = {
    "welcome_text": "Welcome! 🛍️\nChoose an option below.",
    "shop_text": "🛒 *Shop*\nPick a product to buy.",
    "topup_text": "💳 *Top Up*\nSend your payment proof screenshot after choosing amount.",
    "help_text": "Need help? Contact admin.",
    "insufficient_balance_text": "❌ Not enough balance. Please top up first.",
    "purchase_success_text": "✅ Purchase complete!\nTransaction ID: `{transaction_id}`",
    "topup_received_text": "✅ Proof received!\nTop-Up ID: `{topup_id}`\nStatus: *PENDING*",
    "topup_approved_text": "✅ Top up approved!\nTop-Up ID: `{topup_id}`\nAdded: *{amount}*",
    "topup_rejected_text": "❌ Top up rejected.\nTop-Up ID: `{topup_id}`",
}

SCHEMA_SQL = """
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
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  price INTEGER NOT NULL DEFAULT 0,
  photo_file_id TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topups (
  id SERIAL PRIMARY KEY,
  topup_id TEXT UNIQUE NOT NULL,
  user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  amount INTEGER NOT NULL,
  method TEXT,
  proof_file_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending', -- pending/approved/rejected
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

CREATE TABLE IF NOT EXISTS announcements (
  id SERIAL PRIMARY KEY,
  admin_id BIGINT,
  message TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

async def init_db():
    await adb_exec(SCHEMA_SQL, ())
    # Insert default texts if missing
    for k, v in DEFAULT_TEXTS.items():
        await adb_exec(
            "INSERT INTO settings(key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (k, v),
        )

async def get_text(key: str) -> str:
    row = await adb_fetchone("SELECT value FROM settings WHERE key=%s", (key,))
    if row and row.get("value") is not None:
        return str(row["value"])
    return DEFAULT_TEXTS.get(key, "")

async def set_text(key: str, value: str) -> None:
    await adb_exec(
        "INSERT INTO settings(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        (key, value),
    )

# =========================
# Utils
# =========================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def make_id(prefix: str) -> str:
    # short unique
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
        [KeyboardButton("🛒 Shop"), KeyboardButton("💳 Top Up")],
        [KeyboardButton("💰 My Balance"), KeyboardButton("🧾 History")],
        [KeyboardButton("❓ Help")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton("🛠 Admin")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="admin_products")],
        [InlineKeyboardButton("👤 Users", callback_data="admin_users")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin_purchases")],
        [InlineKeyboardButton("💳 Topups", callback_data="admin_topups")],
        [InlineKeyboardButton("✏️ Edit Texts", callback_data="admin_texts")],
        [InlineKeyboardButton("📢 Announcement", callback_data="admin_announce")],
        [InlineKeyboardButton("⬅️ Close", callback_data="admin_close")],
    ])

# =========================
# Conversations (states)
# =========================
TOPUP_AMOUNT, TOPUP_PROOF = range(2)

PROD_CHOOSE_ACTION, PROD_ADD_NAME, PROD_ADD_PRICE, PROD_ADD_DESC, PROD_ADD_PHOTO = range(10, 15)
PROD_EDIT_PICK, PROD_EDIT_FIELD, PROD_EDIT_VALUE, PROD_EDIT_PHOTO = range(15, 19)

USER_EDIT_ASK_ID, USER_EDIT_ASK_BAL = range(30, 32)

TEXT_EDIT_PICK, TEXT_EDIT_VALUE = range(40, 42)

ANNOUNCE_TEXT = 50

# =========================
# Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    welcome = await get_text("welcome_text")
    await update.message.reply_text(welcome, reply_markup=main_menu_kb(update.effective_user.id))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await get_text("help_text")
    await update.message.reply_text(txt, reply_markup=main_menu_kb(update.effective_user.id))

async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    row = await adb_fetchone("SELECT balance FROM users WHERE user_id=%s", (update.effective_user.id,))
    bal = int(row["balance"]) if row else 0
    await update.message.reply_text(f"💰 Your balance: *{bal}*", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(update.effective_user.id))

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    topups = await adb_fetchall(
        "SELECT topup_id, amount, status, created_at FROM topups WHERE user_id=%s ORDER BY id DESC LIMIT 10",
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
            msg.append(f"• `{t['topup_id']}` | +{t['amount']} | {t['status']} | {t['created_at']:%Y-%m-%d %H:%M}")

    msg.append("\n*Purchases (last 10)*")
    if not purchases:
        msg.append("— none")
    else:
        for p in purchases:
            msg.append(f"• `{p['transaction_id']}` | -{p['price']} | {p['created_at']:%Y-%m-%d %H:%M}")

    await update.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))

# -------- SHOP --------
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    products = await adb_fetchall(
        "SELECT id, name, price FROM products WHERE is_active=TRUE ORDER BY id DESC LIMIT 50"
    )
    if not products:
        await update.message.reply_text("No products yet.", reply_markup=main_menu_kb(update.effective_user.id))
        return

    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(f"{p['name']} — {p['price']}", callback_data=f"buy:{p['id']}")])
    kb = InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("⬅️ Back", callback_data="shop_back")]])
    txt = await get_text("shop_text")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def shop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "shop_back":
        await q.edit_message_text("Back to menu.", reply_markup=None)
        return

    if data.startswith("buy:"):
        pid = int(data.split(":")[1])
        uid = q.from_user.id

        user = await adb_fetchone("SELECT balance FROM users WHERE user_id=%s", (uid,))
        if not user:
            await upsert_user(q.from_user)
            user = {"balance": 0}
        balance = int(user["balance"])

        product = await adb_fetchone("SELECT id, name, description, price, photo_file_id FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not product:
            await q.edit_message_text("Product not found or inactive.")
            return

        price = int(product["price"])
        if balance < price:
            txt = await get_text("insufficient_balance_text")
            await q.edit_message_text(txt)
            return

        # Deduct + create purchase
        txid = make_id("TX")
        await adb_exec("UPDATE users SET balance = balance - %s WHERE user_id=%s", (price, uid))
        await adb_exec(
            "INSERT INTO purchases(transaction_id, user_id, product_id, price) VALUES (%s, %s, %s, %s)",
            (txid, uid, pid, price),
        )

        msg = await get_text("purchase_success_text")
        msg = msg.format(transaction_id=txid)

        # Show product details + tx
        details = f"*{product['name']}*\nPrice: *{price}*\n\n{product['description']}\n\n{msg}"
        try:
            if product.get("photo_file_id"):
                await q.message.reply_photo(product["photo_file_id"], caption=details, parse_mode=ParseMode.MARKDOWN)
            else:
                await q.message.reply_text(details, parse_mode=ParseMode.MARKDOWN)
        except:
            await q.message.reply_text(details, parse_mode=ParseMode.MARKDOWN)

        await q.edit_message_text("✅ Done. Back to menu.", reply_markup=None)

# -------- TOPUP (user flow) --------
async def topup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await upsert_user(update.effective_user)
    txt = await get_text("topup_text")
    await update.message.reply_text(
        txt + "\n\nSend amount now (numbers only). Example: `50`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_kb(update.effective_user.id),
    )
    return TOPUP_AMOUNT

async def topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (update.message.text or "").strip()
    if not msg.isdigit():
        await update.message.reply_text("Please send a number only. Example: 50")
        return TOPUP_AMOUNT

    amount = int(msg)
    if amount <= 0:
        await update.message.reply_text("Amount must be > 0.")
        return TOPUP_AMOUNT

    context.user_data["topup_amount"] = amount
    await update.message.reply_text("Now send your payment proof screenshot/photo.")
    return TOPUP_PROOF

async def topup_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = int(context.user_data.get("topup_amount", 0))
    if amount <= 0:
        await update.message.reply_text("Please start again: press 💳 Top Up")
        return ConversationHandler.END

    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        await update.message.reply_text("Please send a PHOTO screenshot proof.")
        return TOPUP_PROOF

    uid = update.effective_user.id
    topup_id = make_id("TU")
    file_id = photo.file_id

    await adb_exec(
        "INSERT INTO topups(topup_id, user_id, amount, proof_file_id, status) VALUES (%s, %s, %s, %s, 'pending')",
        (topup_id, uid, amount, file_id),
    )

    # Notify admins with approve/reject buttons
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"tu_approve:{topup_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"tu_reject:{topup_id}"),
        ]
    ])

    caption = (
        f"💳 *TOPUP REQUEST*\n"
        f"User: `{uid}` (@{update.effective_user.username})\n"
        f"Amount: *{amount}*\n"
        f"TopUp ID: `{topup_id}`"
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(admin_id, file_id, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except:
            pass

    txt = await get_text("topup_received_text")
    txt = txt.format(topup_id=topup_id)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
    return ConversationHandler.END

# -------- ADMIN TOPUP APPROVE/REJECT --------
async def topup_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_caption(caption="❌ You are not admin.", reply_markup=None)
        return

    data = q.data
    action, topup_id = data.split(":", 1)

    row = await adb_fetchone("SELECT * FROM topups WHERE topup_id=%s", (topup_id,))
    if not row:
        try:
            await q.edit_message_caption(caption="Topup not found.", reply_markup=None)
        except:
            await q.edit_message_text("Topup not found.")
        return

    if row["status"] != "pending":
        try:
            await q.edit_message_caption(caption=f"Already handled: {row['status']}", reply_markup=None)
        except:
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

        txt = await get_text("topup_approved_text")
        txt = txt.format(topup_id=topup_id, amount=amount)

        try:
            await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass

        new_cap = (q.message.caption or "") + "\n\n✅ *APPROVED*"
        try:
            await q.edit_message_caption(caption=new_cap, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
        except:
            await q.edit_message_text("✅ Approved.", reply_markup=None)

    elif action == "tu_reject":
        await adb_exec(
            "UPDATE topups SET status='rejected', admin_id=%s, decided_at=%s WHERE topup_id=%s",
            (q.from_user.id, now, topup_id),
        )
        txt = await get_text("topup_rejected_text")
        txt = txt.format(topup_id=topup_id)

        try:
            await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(uid))
        except:
            pass

        new_cap = (q.message.caption or "") + "\n\n❌ *REJECTED*"
        try:
            await q.edit_message_caption(caption=new_cap, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
        except:
            await q.edit_message_text("❌ Rejected.", reply_markup=None)

# -------- ADMIN MAIN --------
async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not admin.")
        return
    await update.message.reply_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb(update.effective_user.id))
    await update.message.reply_text("Choose:", reply_markup=admin_menu_kb())

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

    if data == "admin_products":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Product", callback_data="prod_add")],
            [InlineKeyboardButton("✏️ Edit Product", callback_data="prod_edit")],
            [InlineKeyboardButton("🗑 Delete Product", callback_data="prod_del")],
            [InlineKeyboardButton("📃 List Products", callback_data="prod_list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        ])
        await q.edit_message_text("📦 *Products*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data == "admin_users":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📃 List Users", callback_data="user_list")],
            [InlineKeyboardButton("💰 Edit User Balance", callback_data="user_edit")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        ])
        await q.edit_message_text("👤 *Users*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data == "admin_purchases":
        rows = await adb_fetchall(
            """
            SELECT p.transaction_id, p.price, p.created_at, p.user_id, pr.name AS product_name
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
                msg.append(f"• `{r['transaction_id']}` | {pname} | -{r['price']} | user `{r['user_id']}` | {r['created_at']:%Y-%m-%d %H:%M}")
        await q.edit_message_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]))
        return

    if data == "admin_topups":
        rows = await adb_fetchall(
            "SELECT topup_id, user_id, amount, status, created_at FROM topups ORDER BY id DESC LIMIT 20"
        )
        msg = ["💳 *Topups (last 20)*"]
        if not rows:
            msg.append("— none")
        else:
            for r in rows:
                msg.append(f"• `{r['topup_id']}` | +{r['amount']} | {r['status']} | user `{r['user_id']}` | {r['created_at']:%Y-%m-%d %H:%M}")
        await q.edit_message_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]]))
        return

    if data == "admin_texts":
        keys = list(DEFAULT_TEXTS.keys())
        btns = [[InlineKeyboardButton(k, callback_data=f"text_pick:{k}")] for k in keys]
        btns.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_back")])
        await q.edit_message_text("✏️ *Pick a text to edit:*", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(btns))
        return

    if data == "admin_announce":
        await q.edit_message_text("📢 Send the announcement text now (it will be sent to all users).")
        context.user_data["announce_mode"] = True
        return

    if data == "admin_back":
        await q.edit_message_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())
        return

# -------- PRODUCTS (callbacks + message steps) --------
async def products_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ You are not admin.")
        return ConversationHandler.END

    data = q.data

    if data == "prod_list":
        rows = await adb_fetchall("SELECT id, name, price, is_active FROM products ORDER BY id DESC LIMIT 50")
        msg = ["📃 *Products*"]
        if not rows:
            msg.append("— none")
        else:
            for r in rows:
                status = "✅" if r["is_active"] else "❌"
                msg.append(f"{status} ID *{r['id']}* — {r['name']} — *{r['price']}*")
        await q.edit_message_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN,
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_products")]]))
        return ConversationHandler.END

    if data == "prod_add":
        await q.edit_message_text("➕ Send *product name*:", parse_mode=ParseMode.MARKDOWN)
        return PROD_ADD_NAME

    if data == "prod_edit":
        await q.edit_message_text("✏️ Send the *Product ID* you want to edit:")
        return PROD_EDIT_PICK

    if data == "prod_del":
        await q.edit_message_text("🗑 Send the *Product ID* to delete:")
        context.user_data["prod_delete_mode"] = True
        return PROD_EDIT_PICK

    return ConversationHandler.END

async def prod_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prod_name"] = (update.message.text or "").strip()
    if not context.user_data["prod_name"]:
        await update.message.reply_text("Name cannot be empty. Send product name:")
        return PROD_ADD_NAME
    await update.message.reply_text("Send *price* (number):", parse_mode=ParseMode.MARKDOWN)
    return PROD_ADD_PRICE

async def prod_add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if not txt.isdigit():
        await update.message.reply_text("Price must be a number. Send price:")
        return PROD_ADD_PRICE
    context.user_data["prod_price"] = int(txt)
    await update.message.reply_text("Send *description*:", parse_mode=ParseMode.MARKDOWN)
    return PROD_ADD_DESC

async def prod_add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prod_desc"] = (update.message.text or "").strip()
    await update.message.reply_text("Send *photo* (or type `skip`):", parse_mode=ParseMode.MARKDOWN)
    return PROD_ADD_PHOTO

async def prod_add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file_id = None
    if update.message.text and update.message.text.strip().lower() == "skip":
        photo_file_id = None
    else:
        if not update.message.photo:
            await update.message.reply_text("Please send a photo, or type `skip`.")
            return PROD_ADD_PHOTO
        photo_file_id = update.message.photo[-1].file_id

    name = context.user_data["prod_name"]
    price = int(context.user_data["prod_price"])
    desc = context.user_data.get("prod_desc", "")

    await adb_exec(
        "INSERT INTO products(name, description, price, photo_file_id) VALUES (%s, %s, %s, %s)",
        (name, desc, price, photo_file_id),
    )

    await update.message.reply_text("✅ Product added!", reply_markup=main_menu_kb(update.effective_user.id))
    # show admin menu again
    await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())
    return ConversationHandler.END

async def prod_edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if not txt.isdigit():
        await update.message.reply_text("Send a numeric Product ID.")
        return PROD_EDIT_PICK
    pid = int(txt)

    if context.user_data.get("prod_delete_mode"):
        context.user_data["prod_delete_mode"] = False
        await adb_exec("DELETE FROM products WHERE id=%s", (pid,))
        await update.message.reply_text("🗑 Deleted (if it existed).", reply_markup=main_menu_kb(update.effective_user.id))
        await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())
        return ConversationHandler.END

    prod = await adb_fetchone("SELECT * FROM products WHERE id=%s", (pid,))
    if not prod:
        await update.message.reply_text("Product not found. Send Product ID:")
        return PROD_EDIT_PICK

    context.user_data["edit_pid"] = pid
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Name", callback_data="pf:name"),
         InlineKeyboardButton("Price", callback_data="pf:price")],
        [InlineKeyboardButton("Description", callback_data="pf:desc"),
         InlineKeyboardButton("Photo", callback_data="pf:photo")],
        [InlineKeyboardButton("Toggle Active", callback_data="pf:toggle")],
        [InlineKeyboardButton("Cancel", callback_data="pf:cancel")],
    ])
    await update.message.reply_text(
        f"Editing ID *{pid}* — *{prod['name']}*\nChoose field:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )
    return PROD_EDIT_FIELD

async def prod_edit_field_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ You are not admin.")
        return ConversationHandler.END

    field = q.data.split(":", 1)[1]
    pid = context.user_data.get("edit_pid")

    if field == "cancel":
        await q.edit_message_text("Cancelled.")
        return ConversationHandler.END

    if field == "toggle":
        await adb_exec("UPDATE products SET is_active = NOT is_active WHERE id=%s", (pid,))
        await q.edit_message_text("✅ Toggled active status.")
        return ConversationHandler.END

    context.user_data["edit_field"] = field
    if field == "photo":
        await q.edit_message_text("Send NEW product photo now.")
        return PROD_EDIT_PHOTO

    await q.edit_message_text("Send NEW value now:")
    return PROD_EDIT_VALUE

async def prod_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = int(context.user_data.get("edit_pid"))
    field = context.user_data.get("edit_field")

    val = (update.message.text or "").strip()
    if field == "price":
        if not val.isdigit():
            await update.message.reply_text("Price must be a number. Send again:")
            return PROD_EDIT_VALUE
        await adb_exec("UPDATE products SET price=%s WHERE id=%s", (int(val), pid))
    elif field == "name":
        if not val:
            await update.message.reply_text("Name cannot be empty. Send again:")
            return PROD_EDIT_VALUE
        await adb_exec("UPDATE products SET name=%s WHERE id=%s", (val, pid))
    elif field == "desc":
        await adb_exec("UPDATE products SET description=%s WHERE id=%s", (val, pid))

    await update.message.reply_text("✅ Updated.")
    await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())
    return ConversationHandler.END

async def prod_edit_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = int(context.user_data.get("edit_pid"))
    if not update.message.photo:
        await update.message.reply_text("Please send a photo.")
        return PROD_EDIT_PHOTO
    fid = update.message.photo[-1].file_id
    await adb_exec("UPDATE products SET photo_file_id=%s WHERE id=%s", (fid, pid))
    await update.message.reply_text("✅ Photo updated.")
    await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())
    return ConversationHandler.END

# -------- USERS (callbacks + message steps) --------
async def users_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ You are not admin.")
        return ConversationHandler.END

    data = q.data
    if data == "user_list":
        rows = await adb_fetchall("SELECT user_id, username, balance FROM users ORDER BY created_at DESC LIMIT 30")
        msg = ["👤 *Users (last 30)*"]
        for r in rows:
            uname = r["username"] or "-"
            msg.append(f"• `{r['user_id']}` @{uname} | balance *{r['balance']}*")
        await q.edit_message_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN,
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_users")]]))
        return ConversationHandler.END

    if data == "user_edit":
        await q.edit_message_text("Send the *User ID* to edit balance:")
        return USER_EDIT_ASK_ID

    return ConversationHandler.END

async def user_edit_ask_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if not txt.isdigit():
        await update.message.reply_text("Send numeric User ID.")
        return USER_EDIT_ASK_ID
    uid = int(txt)
    row = await adb_fetchone("SELECT user_id, balance FROM users WHERE user_id=%s", (uid,))
    if not row:
        await update.message.reply_text("User not found.")
        return ConversationHandler.END
    context.user_data["edit_uid"] = uid
    await update.message.reply_text(f"Current balance: {row['balance']}\nSend NEW balance (number):")
    return USER_EDIT_ASK_BAL

async def user_edit_ask_bal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if not re.fullmatch(r"-?\d+", txt):
        await update.message.reply_text("Send a valid number (can be negative).")
        return USER_EDIT_ASK_BAL
    bal = int(txt)
    uid = int(context.user_data["edit_uid"])
    await adb_exec("UPDATE users SET balance=%s WHERE user_id=%s", (bal, uid))
    await update.message.reply_text("✅ Balance updated.")
    try:
        await update.get_bot().send_message(uid, f"Admin updated your balance. New balance: {bal}", reply_markup=main_menu_kb(uid))
    except:
        pass
    await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())
    return ConversationHandler.END

# -------- TEXT EDIT (callbacks + message steps) --------
async def text_pick_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ You are not admin.")
        return ConversationHandler.END

    key = q.data.split(":", 1)[1]
    context.user_data["edit_text_key"] = key
    current = await get_text(key)
    await q.edit_message_text(
        f"✏️ Editing: *{key}*\n\nCurrent:\n{current}\n\nSend NEW text now:",
        parse_mode=ParseMode.MARKDOWN
    )
    return TEXT_EDIT_VALUE

async def text_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("edit_text_key")
    new_text = update.message.text or ""
    await set_text(key, new_text)
    await update.message.reply_text("✅ Text updated!")
    await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())
    return ConversationHandler.END

# -------- ANNOUNCEMENTS --------
async def announce_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.user_data.get("announce_mode"):
        return

    context.user_data["announce_mode"] = False
    text = update.message.text or ""
    if not text.strip():
        await update.message.reply_text("Announcement cancelled (empty).")
        return

    await adb_exec("INSERT INTO announcements(admin_id, message) VALUES (%s, %s)", (update.effective_user.id, text))

    users = await adb_fetchall("SELECT user_id FROM users WHERE is_banned=FALSE")
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(int(u["user_id"]), f"📢 *Announcement*\n\n{text}", parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except:
            pass

    await update.message.reply_text(f"✅ Announcement sent to ~{sent} users.")
    await update.message.reply_text("🛠 Admin Panel:", reply_markup=admin_menu_kb())

# -------- Router for menu buttons --------
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == "🛒 Shop":
        await shop(update, context)
    elif text == "💳 Top Up":
        await topup_start(update, context)
    elif text == "💰 My Balance":
        await my_balance(update, context)
    elif text == "🧾 History":
        await history(update, context)
    elif text == "❓ Help":
        await help_cmd(update, context)
    elif text == "🛠 Admin":
        await admin_entry(update, context)
    else:
        # fallback
        await update.message.reply_text("Use the menu buttons.", reply_markup=main_menu_kb(update.effective_user.id))

# =========================
# Main
# =========================
async def on_startup(app: Application):
    await init_db()

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.post_init = on_startup

    # Basic
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # Menu text buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    # Shop callbacks
    app.add_handler(CallbackQueryHandler(shop_cb, pattern=r"^(buy:|shop_back$)"))

    # Topup approve/reject callbacks
    app.add_handler(CallbackQueryHandler(topup_admin_cb, pattern=r"^(tu_approve:|tu_reject:)"))

    # Admin menu callbacks
    app.add_handler(CallbackQueryHandler(admin_menu_cb, pattern=r"^admin_"))

    # Admin sub-callbacks
    app.add_handler(CallbackQueryHandler(products_cb, pattern=r"^prod_"))
    app.add_handler(CallbackQueryHandler(users_cb, pattern=r"^user_"))
    app.add_handler(CallbackQueryHandler(text_pick_cb, pattern=r"^text_pick:"))

    # Product edit field chooser
    app.add_handler(CallbackQueryHandler(prod_edit_field_cb, pattern=r"^pf:"))

    # Topup conversation
    topup_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^💳 Top Up$"), topup_start)],
        states={
            TOPUP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_amount)],
            TOPUP_PROOF: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), topup_proof)],
        },
        fallbacks=[],
        allow_reentry=True,
    )
    app.add_handler(topup_conv)

    # Products conversation (admin)
    prod_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(products_cb, pattern=r"^prod_(add|edit|del)$")],
        states={
            PROD_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_add_name)],
            PROD_ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_add_price)],
            PROD_ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_add_desc)],
            PROD_ADD_PHOTO: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), prod_add_photo)],
            PROD_EDIT_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_edit_pick)],
            PROD_EDIT_FIELD: [CallbackQueryHandler(prod_edit_field_cb, pattern=r"^pf:")],
            PROD_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, prod_edit_value)],
            PROD_EDIT_PHOTO: [MessageHandler(filters.PHOTO, prod_edit_photo)],
        },
        fallbacks=[],
        allow_reentry=True,
    )
    app.add_handler(prod_conv)

    # User edit conversation
    user_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(users_cb, pattern=r"^user_edit$")],
        states={
            USER_EDIT_ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_edit_ask_id)],
            USER_EDIT_ASK_BAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_edit_ask_bal)],
        },
        fallbacks=[],
        allow_reentry=True,
    )
    app.add_handler(user_conv)

    # Text edit conversation
    text_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(text_pick_cb, pattern=r"^text_pick:")],
        states={
            TEXT_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_edit_value)],
        },
        fallbacks=[],
        allow_reentry=True,
    )
    app.add_handler(text_conv)

    # Announce (admin) message catcher
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, announce_text), group=1)

    return app

def main():
    app = build_app()
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
