import os
import uuid
import logging
from typing import Optional, List, Dict

import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Put your admin Telegram user IDs here (numbers)
ADMIN_IDS = {7719956917}  # <-- CHANGE THIS

# OPTIONAL: put your QR in Telegram once, then copy its file_id here (recommended).
GCASH_QR_FILE_ID = os.getenv("GCASH_QR_FILE_ID", "").strip()
GOTYME_QR_FILE_ID = os.getenv("GOTYME_QR_FILE_ID", "").strip()

# You can edit these texts anytime (or use Admin → Edit Payment Texts)
PAYMENT_INSTRUCTIONS: Dict[str, str] = {
    "gcash": "📌 *GCash Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here\n\n(You can edit this later.)",
    "gotyme": "📌 *GoTyme Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here\n\n(You can edit this later.)",
}

TOPUP_AMOUNTS = [50, 100, 300, 500, 1000]

# =========================
# DB
# =========================
def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Set it on Railway Variables.")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def ensure_schema():
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        balance INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE NOT NULL
    );

    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        price INTEGER NOT NULL DEFAULT 0,
        delivery_type TEXT NOT NULL DEFAULT 'text', -- 'text' or 'file'
        delivery_text TEXT NOT NULL DEFAULT '',
        delivery_file_id TEXT, -- Telegram file_id for auto delivery
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS purchases (
        id SERIAL PRIMARY KEY,
        purchase_token TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
        price INTEGER NOT NULL,
        delivered BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        delivered_at TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS topups (
        id SERIAL PRIMARY KEY,
        topup_id TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        amount INTEGER NOT NULL,
        method TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
        proof_file_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        decided_at TIMESTAMP,
        admin_id BIGINT,
        note TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_topups_status_created ON topups(status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
    """
    migrations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE topups ADD COLUMN IF NOT EXISTS topup_id TEXT;",
        "ALTER TABLE topups ADD COLUMN IF NOT EXISTS proof_file_id TEXT;",
        "ALTER TABLE topups ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'PENDING';",
        "ALTER TABLE topups ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP;",
        "ALTER TABLE topups ADD COLUMN IF NOT EXISTS admin_id BIGINT;",
        "ALTER TABLE topups ADD COLUMN IF NOT EXISTS note TEXT;",
    ]

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            for m in migrations:
                try:
                    cur.execute(m)
                    conn.commit()
                except Exception:
                    conn.rollback()

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO categories(name) VALUES
                ('Entertainment Prems'),
                ('Educational Prems'),
                ('Editing Prems'),
                ('VPN Prems'),
                ('Other Prems')
                ON CONFLICT (name) DO NOTHING;
            """)
            conn.commit()

def fetch_all(sql: str, params: tuple = ()) -> List[dict]:
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

def fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None

def exec_sql(sql: str, params: tuple = ()) -> None:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()

# =========================
# UI (Reply Keyboard)
# =========================
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Shop", "💳 Add Balance"],
        ["💰 Balance", "📜 History"],
        ["💬 Chat Admin", "🆘 Help"],
        ["🔐 Admin"],
    ],
    resize_keyboard=True
)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def fmt_money(n: int) -> str:
    return f"₱{n:,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    exec_sql("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
    await update.message.reply_text(
        "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
        reply_markup=MAIN_MENU,
        parse_mode=ParseMode.MARKDOWN
    )

# =========================
# BASIC MENU
# =========================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse and buy\n"
        "• Tap **Add Balance** to top up\n"
        "• After you send proof, wait for approval\n\n"
        "If something breaks, tap **Chat Admin** 💗",
        parse_mode=ParseMode.MARKDOWN
    )

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 **Chat Admin**\n\nMessage admin here:\n• @yourusername\n\n(Replace with your real username.)",
        parse_mode=ParseMode.MARKDOWN
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = fetch_one("SELECT COALESCE(balance,0) AS balance FROM users WHERE user_id=%s", (user_id,))
    bal = int(row["balance"]) if row else 0
    await update.message.reply_text(f"💰 Your balance: **{fmt_money(bal)}**", parse_mode=ParseMode.MARKDOWN)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = fetch_all("""
        SELECT p.created_at, pr.name, p.price, p.delivered
        FROM purchases p
        JOIN products pr ON pr.id = p.product_id
        WHERE p.user_id=%s
        ORDER BY p.created_at DESC
        LIMIT 15
    """, (user_id,))
    if not rows:
        await update.message.reply_text("📜 No purchases yet.")
        return

    lines = ["📜 **Recent Purchases**\n"]
    for r in rows:
        status = "✅ delivered" if r["delivered"] else "⏳ pending"
        lines.append(f"• {r['name']} — {fmt_money(int(r['price']))} — {status}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# =========================
# TOP-UP FLOW
# =========================
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("💙 GCash", callback_data="pay:gcash")],
        [InlineKeyboardButton("💜 GoTyme", callback_data="pay:gotyme")],
        [InlineKeyboardButton("⬅️ Back", callback_data="pay:back")],
    ]
    await update.message.reply_text(
        "💳 **Add Balance**\n\nChoose payment method:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if q.data == "pay:back":
        await q.message.reply_text("Back to menu ✅", reply_markup=MAIN_MENU)
        return

    method = q.data.split(":", 1)[1]
    context.user_data["topup_method"] = method

    # send QR + instruction
    file_id = GCASH_QR_FILE_ID if method == "gcash" else GOTYME_QR_FILE_ID
    instr = PAYMENT_INSTRUCTIONS.get(method, "")

    if file_id:
        try:
            await q.message.reply_photo(photo=file_id, caption=instr, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await q.message.reply_document(document=file_id, caption=instr, parse_mode=ParseMode.MARKDOWN)
    else:
        await q.message.reply_text(
            instr + "\n\n⚠️ Set GCASH_QR_FILE_ID / GOTYME_QR_FILE_ID in Railway Variables to show QR.",
            parse_mode=ParseMode.MARKDOWN
        )

    # amounts
    rows = [
        [InlineKeyboardButton("₱50", callback_data="amt:50"),
         InlineKeyboardButton("₱100", callback_data="amt:100")],
        [InlineKeyboardButton("₱300", callback_data="amt:300"),
         InlineKeyboardButton("₱500", callback_data="amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="amt:1000")],
        [InlineKeyboardButton("⬅️ Change method", callback_data="pay:back")],
    ]
    await q.message.reply_text("✨ Choose top-up amount:", reply_markup=InlineKeyboardMarkup(rows))

async def cb_choose_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if "topup_method" not in context.user_data:
        await q.message.reply_text("Tap **Add Balance** again and choose method first.", parse_mode=ParseMode.MARKDOWN)
        return

    amount = int(q.data.split(":", 1)[1])
    method = context.user_data["topup_method"]

    topup_id = f"shopnluna:{uuid.uuid4().hex[:10]}"
    context.user_data["topup_id"] = topup_id

    exec_sql(
        """INSERT INTO topups (topup_id, user_id, amount, method, status)
           VALUES (%s,%s,%s,%s,'PENDING')
           ON CONFLICT (topup_id) DO NOTHING;""",
        (topup_id, q.from_user.id, amount, method)
    )

    await q.message.reply_text(
        f"🆔 **Top-up ID:** `{topup_id}`\n"
        f"💵 Amount: **{fmt_money(amount)}**\n"
        f"💳 Method: **{method.upper()}**\n\n"
        "📸 Now send your **payment screenshot** here.\n"
        "⏳ Status: **Waiting for approval**",
        parse_mode=ParseMode.MARKDOWN
    )

async def proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "topup_id" not in context.user_data:
        return

    topup_id = context.user_data["topup_id"]
    user_id = update.effective_user.id
    file_id = update.message.photo[-1].file_id

    exec_sql("UPDATE topups SET proof_file_id=%s WHERE topup_id=%s", (file_id, topup_id))

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"admin:topup:approve:{topup_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin:topup:reject:{topup_id}")
        ]
    ])

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=(
                    "💳 **New Top-up Proof**\n\n"
                    f"Topup ID: `{topup_id}`\n"
                    f"User ID: `{user_id}`\n\n"
                    "Choose an action:"
                ),
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"💳 New Top-up Proof\nTopup ID: {topup_id}\nUser ID: {user_id}",
                reply_markup=kb
            )

    await update.message.reply_text("✅ Proof received.\n⏳ Waiting for admin approval.\n\nThank you 💗")

# =========================
# SHOP
# =========================
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id, name FROM categories ORDER BY id ASC")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="shop:back")])
    await update.message.reply_text(
        "🛍 **Shop Categories**",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if q.data == "shop:back":
        await q.message.reply_text("Back to menu ✅", reply_markup=MAIN_MENU)
        return

    parts = q.data.split(":")
    if parts[1] == "cat":
        cat_id = int(parts[2])
        prods = fetch_all("""
            SELECT id, name, price FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id ASC
        """, (cat_id,))
        if not prods:
            await q.message.reply_text("No products in this category yet 💗")
            return
        rows = [[InlineKeyboardButton(f"{p['name']} — {fmt_money(int(p['price']))}", callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅️ Categories", callback_data="shop:cats")])
        await q.message.reply_text("Select a product:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if parts[1] == "cats":
        await shop_from_callback(q, context)
        return

    if parts[1] == "prod":
        prod_id = int(parts[2])
        prod = fetch_one("SELECT id,name,description,price FROM products WHERE id=%s AND is_active=TRUE", (prod_id,))
        if not prod:
            await q.message.reply_text("Product not found.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Buy", callback_data=f"buy:{prod_id}")],
            [InlineKeyboardButton("⬅️ Categories", callback_data="shop:cats")],
        ])
        await q.message.reply_text(
            f"🧾 **{prod['name']}**\n\n{prod['description']}\n\n💵 Price: **{fmt_money(int(prod['price']))}**",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return

async def shop_from_callback(q, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id, name FROM categories ORDER BY id ASC")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="shop:back")])
    await q.message.reply_text("🛍 **Shop Categories**", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user_id = q.from_user.id
    prod_id = int(q.data.split(":")[1])
    prod = fetch_one("""
        SELECT id,name,price,delivery_type,delivery_text,delivery_file_id
        FROM products WHERE id=%s AND is_active=TRUE
    """, (prod_id,))
    if not prod:
        await q.message.reply_text("Product not found.")
        return

    price = int(prod["price"])

    # balance + deduct atomic
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
            cur.execute("SELECT balance FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
            bal = int(cur.fetchone()["balance"])
            if bal < price:
                conn.rollback()
                await q.message.reply_text(f"❌ Not enough balance.\nBalance: {fmt_money(bal)}\nPrice: {fmt_money(price)}")
                return
            cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (price, user_id))
            purchase_token = f"pur:{uuid.uuid4().hex}"
            cur.execute("""
                INSERT INTO purchases(purchase_token,user_id,product_id,price,delivered)
                VALUES(%s,%s,%s,%s,FALSE)
            """, (purchase_token, user_id, prod_id, price))
            conn.commit()

    await deliver_purchase_once(context, user_id, purchase_token, prod)
    await q.message.reply_text("✅ Purchase complete! 💗")

async def deliver_purchase_once(context: ContextTypes.DEFAULT_TYPE, user_id: int, purchase_token: str, prod: dict):
    # Prevent double delivery: lock purchase row + mark delivered first
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT delivered FROM purchases WHERE purchase_token=%s FOR UPDATE", (purchase_token,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return
            if row["delivered"]:
                conn.rollback()
                await context.bot.send_message(chat_id=user_id, text="ℹ️ Already delivered.")
                return
            cur.execute("UPDATE purchases SET delivered=TRUE, delivered_at=NOW() WHERE purchase_token=%s", (purchase_token,))
            conn.commit()

    try:
        if prod["delivery_type"] == "file" and prod.get("delivery_file_id"):
            await context.bot.send_document(
                chat_id=user_id,
                document=prod["delivery_file_id"],
                caption=f"📦 **Delivery:** {prod['name']}",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            text = prod.get("delivery_text") or "(No delivery text set yet.)"
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📦 **Delivery:** {prod['name']}\n\n{text}",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        log.exception("Delivery failed: %s", e)
        await context.bot.send_message(chat_id=user_id, text="⚠️ Delivery failed. Please contact admin.")

# =========================
# ADMIN
# =========================
ADMIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("⏳ Pending Top-ups", callback_data="admin:panel:pending_topups")],
    [InlineKeyboardButton("🧾 Purchases", callback_data="admin:panel:purchases")],
    [InlineKeyboardButton("👥 Users", callback_data="admin:panel:users")],
    [InlineKeyboardButton("✏️ Edit Payment Texts", callback_data="admin:panel:edit_texts")],
])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return
    await update.message.reply_text("🔐 **Admin Panel**", reply_markup=ADMIN_MENU, parse_mode=ParseMode.MARKDOWN)

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    data = q.data

    if data.startswith("admin:topup:"):
        _, _, action, topup_id = data.split(":", 3)
        await admin_decide_topup(q, context, action, topup_id)
        return

    if data == "admin:panel:pending_topups":
        rows = fetch_all("""
            SELECT topup_id,user_id,amount,method,created_at
            FROM topups WHERE status='PENDING'
            ORDER BY created_at DESC LIMIT 10
        """)
        if not rows:
            await q.message.reply_text("✅ No pending top-ups.")
            return
        lines = ["⏳ **Pending Top-ups**\n"]
        for r in rows:
            lines.append(f"• `{r['topup_id']}` — user `{r['user_id']}` — {fmt_money(int(r['amount']))} — {r['method']}")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if data == "admin:panel:purchases":
        rows = fetch_all("""
            SELECT p.created_at,p.user_id,pr.name,p.price,p.delivered
            FROM purchases p JOIN products pr ON pr.id=p.product_id
            ORDER BY p.created_at DESC LIMIT 15
        """)
        if not rows:
            await q.message.reply_text("No purchases yet.")
            return
        lines = ["🧾 **Recent Purchases**\n"]
        for r in rows:
            status = "✅" if r["delivered"] else "⏳"
            lines.append(f"{status} user `{r['user_id']}` — {r['name']} — {fmt_money(int(r['price']))}")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if data == "admin:panel:users":
        rows = fetch_all("SELECT user_id,balance FROM users ORDER BY balance DESC LIMIT 20")
        if not rows:
            await q.message.reply_text("No users yet.")
            return
        lines = ["👥 **Top Users**\n"]
        for r in rows:
            lines.append(f"• `{r['user_id']}` — {fmt_money(int(r['balance']))}")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if data == "admin:panel:edit_texts":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Edit GCash text", callback_data="admin:edittext:gcash")],
            [InlineKeyboardButton("Edit GoTyme text", callback_data="admin:edittext:gotyme")],
        ])
        await q.message.reply_text("✏️ Choose which text to edit:", reply_markup=kb)
        return

    if data.startswith("admin:edittext:"):
        method = data.split(":", 2)[2]
        context.user_data["edit_text_method"] = method
        await q.message.reply_text(f"Send the new text for **{method.upper()}** now:", parse_mode=ParseMode.MARKDOWN)
        return

async def admin_edit_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    method = context.user_data.get("edit_text_method")
    if not method:
        return
    PAYMENT_INSTRUCTIONS[method] = update.message.text
    context.user_data.pop("edit_text_method", None)
    await update.message.reply_text(f"✅ Updated {method.upper()} instructions.")

async def admin_decide_topup(q, context: ContextTypes.DEFAULT_TYPE, action: str, topup_id: str):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM topups WHERE topup_id=%s FOR UPDATE", (topup_id,))
            t = cur.fetchone()
            if not t:
                conn.rollback()
                await q.message.reply_text("Top-up not found.")
                return
            if t["status"] != "PENDING":
                conn.rollback()
                await q.message.reply_text(f"Already decided: {t['status']}")
                return

            if action == "approve":
                cur.execute("UPDATE topups SET status='APPROVED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s",
                            (q.from_user.id, topup_id))
                cur.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (t["user_id"],))
                cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (t["amount"], t["user_id"]))
                conn.commit()

                await context.bot.send_message(
                    chat_id=t["user_id"],
                    text=(
                        "✅ **Top-up Approved!**\n\n"
                        f"Topup ID: `{topup_id}`\n"
                        f"Amount: **{fmt_money(int(t['amount']))}**\n\n"
                        "Your balance has been updated 💗"
                    ),
                    parse_mode=ParseMode.MARKDOWN
                )
                await q.message.reply_text("✅ Approved.")
                return

            if action == "reject":
                cur.execute("UPDATE topups SET status='REJECTED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s",
                            (q.from_user.id, topup_id))
                conn.commit()

                await context.bot.send_message(
                    chat_id=t["user_id"],
                    text=(
                        "❌ **Top-up Rejected**\n\n"
                        f"Topup ID: `{topup_id}`\n"
                        "If you think this is a mistake, contact admin."
                    ),
                    parse_mode=ParseMode.MARKDOWN
                )
                await q.message.reply_text("❌ Rejected.")
                return

    await q.message.reply_text("Unknown action.")

# =========================
# BOOT
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it on Railway Variables.")
    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))

    # Menu
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop))
    app.add_handler(MessageHandler(filters.Regex(r"^💳 Add Balance$"), add_balance))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 Balance$"), balance))
    app.add_handler(MessageHandler(filters.Regex(r"^📜 History$"), history))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Chat Admin$"), chat_admin))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_payment_method, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(cb_choose_amount, pattern=r"^amt:"))
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))

    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, proof_photo))

    # Admin edit text (only if in edit mode)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_text_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
