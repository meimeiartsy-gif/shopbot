import os
import io
import zipfile
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

from db import (
    connect,
    init_db,
    ensure_user,
    get_balance,
    add_balance,
    deduct_balance_if_enough,
)

# ================== VERSION (DEBUG) ==================
BOT_VERSION = "v1-buttons-2025-12-20"

# ================== CONFIG ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

# Your admin username (for "Chat Admin" button)
ADMIN_USERNAME = "@lovebylunaa"

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

def parse_product(name: str):
    # "Category | Product Name"
    if "|" in name:
        a, b = name.split("|", 1)
        return a.strip(), b.strip()
    return "All", name.strip()

# ================== KEYBOARDS ==================
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📦 List Produk"), KeyboardButton("💰 Balance")],
        [KeyboardButton("💬 Chat Admin")],
    ],
    resize_keyboard=True
)

CANCEL_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("❌ Cancel Order")]],
    resize_keyboard=True
)

# ================== DB QUERY HELPERS ==================
def list_products():
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT id, name FROM products ORDER BY id ASC")
        return cur.fetchall()

def list_variants(product_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT id, name, price, telegram_file_id
            FROM variants
            WHERE product_id=%s
            ORDER BY id ASC
        """, (product_id,))
        return cur.fetchall()

def get_variant(variant_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT v.id, v.name, v.price, v.telegram_file_id, p.name
            FROM variants v
            JOIN products p ON p.id=v.product_id
            WHERE v.id=%s
        """, (variant_id,))
        return cur.fetchone()

def count_stock(variant_id: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT COUNT(*)
            FROM inventory_items
            WHERE variant_id=%s AND status='available'
        """, (variant_id,))
        return int(cur.fetchone()[0])

def take_stock_payloads(variant_id: int, qty: int, user_id: int):
    """
    Take qty stock items atomically and mark sold.
    """
    with connect() as db:
        cur = db.cursor()
        cur.execute("BEGIN;")
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
            """, (user_id, now, iid))

        db.commit()
        return [p for (_id, p) in rows]

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

# ================== COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    await update.message.reply_text(
        f"🌙 Luna’s Prem Shop\n\n💳 Balance: {money(bal)}\n\nChoose an option:",
        reply_markup=MAIN_MENU
    )

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)
    await update.message.reply_text(
        f"📌 Menu\n💳 Balance: {money(bal)}",
        reply_markup=MAIN_MENU
    )

async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"✅ Running: {BOT_VERSION}", reply_markup=MAIN_MENU)

# ================== MENU HANDLER ==================
async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    user_id = update.effective_user.id
    ensure_user(user_id)

    if txt == "💰 Balance":
        bal = get_balance(user_id)
        await update.message.reply_text(f"💳 Balance: {money(bal)}", reply_markup=MAIN_MENU)
        return

    if txt == "💬 Chat Admin":
        await update.message.reply_text(
            f"Message admin here: {ADMIN_USERNAME}\n👉 https://t.me/{ADMIN_USERNAME.replace('@','')}",
            reply_markup=MAIN_MENU
        )
        return

    if txt == "❌ Cancel Order":
        context.user_data.pop("pending_order", None)
        context.user_data.pop("selected_variant", None)
        await update.message.reply_text("✅ Order cancelled. No payment deducted.", reply_markup=MAIN_MENU)
        return

    if txt == "📦 List Produk":
        products = list_products()
        if not products:
            await update.message.reply_text("No products yet.", reply_markup=MAIN_MENU)
            return

        # numbered list buttons (1..n)
        buttons = []
        row = []
        text_lines = ["📦 Product List\n━━━━━━━━━━━━━━"]
        for idx, (pid, pname) in enumerate(products, start=1):
            _cat, title = parse_product(pname)
            text_lines.append(f"{idx}. {title}")
            row.append(InlineKeyboardButton(str(idx), callback_data=f"p:{pid}"))
            if len(row) == 6:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        await update.message.reply_text(
            "\n".join(text_lines),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

# ================== CALLBACKS ==================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id
    ensure_user(user_id)

    # select product
    if data.startswith("p:"):
        product_id = int(data.split(":")[1])
        variants = list_variants(product_id)
        if not variants:
            await q.message.reply_text("No packages yet.")
            return

        buttons = []
        for vid, vname, price, file_id in variants:
            stock_label = "∞" if file_id else str(count_stock(vid))
            buttons.append([InlineKeyboardButton(
                f"{vname} — {money(price)} (Stock: {stock_label})",
                callback_data=f"v:{vid}"
            )])

        await q.message.reply_text(
            "Choose package:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # select variant => ask quantity
    if data.startswith("v:"):
        variant_id = int(data.split(":")[1])
        context.user_data["selected_variant"] = variant_id
        await q.message.reply_text("Enter quantity (1–50):", reply_markup=CANCEL_MENU)
        return
# confirm order
    if data.startswith("confirm:"):
        _, variant_id_s, qty_s = data.split(":")
        variant_id = int(variant_id_s)
        qty = int(qty_s)

        context.user_data.pop("pending_order", None)

        row = get_variant(variant_id)
        if not row:
            await q.message.reply_text("Package not found.", reply_markup=MAIN_MENU)
            return

        _vid, vname, price, file_id, product_full = row
        _cat, product_title = parse_product(product_full)

        total = int(price) * qty

        # Check stock for non-file products
        if not file_id:
            if count_stock(variant_id) < qty:
                await q.message.reply_text("❌ Not enough stock.", reply_markup=MAIN_MENU)
                return

        # Deduct only on confirm
        ok = deduct_balance_if_enough(user_id, total)
        if not ok:
            bal = get_balance(user_id)
            await q.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.", reply_markup=MAIN_MENU)
            return

        # Deliver
        caption = (
            "✅ Purchase Successful\n"
            f"📦 {product_title} — {vname}\n"
            f"Qty: {qty}\n"
            f"Total: {money(total)}\n\n"
            f"Need help? {ADMIN_USERNAME}"
        )

        if file_id:
            if qty == 1:
                await q.message.reply_document(document=file_id, caption=caption)
                await q.message.reply_text("Back to menu:", reply_markup=MAIN_MENU)
                return

            # ZIP bundle
            tg_file = await context.bot.get_file(file_id)
            data_bytes = await tg_file.download_as_bytearray()

            mem = io.BytesIO()
            with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
                for i in range(1, qty + 1):
                    z.writestr(f"{product_title}_{vname}_{i}.bin", bytes(data_bytes))

            mem.seek(0)
            await q.message.reply_document(
                document=mem,
                filename=f"{product_title}_{vname}_x{qty}.zip",
                caption=caption + "\n\n📦 Sent as ZIP bundle."
            )
            await q.message.reply_text("Back to menu:", reply_markup=MAIN_MENU)
            return

        # Stock item delivery
        payloads = take_stock_payloads(variant_id, qty, user_id)
        if not payloads:
            add_balance(user_id, total)
            await q.message.reply_text("❌ Stock changed. Payment refunded. Try again.", reply_markup=MAIN_MENU)
            return

        await q.message.reply_text(
            caption + "\n\n📦 Delivery:\n" + "\n".join(payloads),
            reply_markup=MAIN_MENU
        )
        return

    # cancel confirm
    if data == "cancel_confirm":
        context.user_data.pop("pending_order", None)
        await q.message.reply_text("✅ Order cancelled. No payment deducted.", reply_markup=MAIN_MENU)
        return

# ================== QTY INPUT ==================
async def buy_qty_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    # ignore menu texts
    if txt in ["📦 List Produk", "💰 Balance", "💬 Chat Admin", "❌ Cancel Order"]:
        return

    if "selected_variant" not in context.user_data:
        return

    user_id = update.effective_user.id
    ensure_user(user_id)

    try:
        qty = int(txt)
        if qty < 1 or qty > 50:
            await update.message.reply_text("Qty must be 1–50.", reply_markup=CANCEL_MENU)
            return
    except:
        await update.message.reply_text("Please enter a number (1–50).", reply_markup=CANCEL_MENU)
        return

    variant_id = int(context.user_data.pop("selected_variant"))

    row = get_variant(variant_id)
    if not row:
        await update.message.reply_text("Package not found.", reply_markup=MAIN_MENU)
        return

    _vid, vname, price, file_id, product_full = row
    _cat, product_title = parse_product(product_full)

    total = int(price) * qty
    bal = get_balance(user_id)
if bal < total:
        await update.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.", reply_markup=MAIN_MENU)
        return

    if not file_id:
        stock = count_stock(variant_id)
        if stock < qty:
            await update.message.reply_text(f"❌ Not enough stock. Available: {stock}", reply_markup=MAIN_MENU)
            return

    # pending confirm (NO DEDUCT YET)
    context.user_data["pending_order"] = {"variant_id": variant_id, "qty": qty}

    buttons = [
        [InlineKeyboardButton("✅ Confirm", callback_data=f"confirm:{variant_id}:{qty}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_confirm")],
    ]

    await update.message.reply_text(
        "🧾 Confirm Order\n"
        "━━━━━━━━━━━━━━\n"
        f"📦 {product_title} — {vname}\n"
        f"Qty: {qty}\n"
        f"Total: {money(total)}\n\n"
        "Payment will be deducted ONLY after you confirm.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ================== ADMIN COMMANDS ==================
async def addproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Usage: /addproduct <Category | Product Name>")
        return

    with connect() as db:
        cur = db.cursor()
        cur.execute("INSERT INTO products(name) VALUES(%s) RETURNING id", (name,))
        pid = cur.fetchone()[0]
        db.commit()

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

    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO variants(product_id,name,price) VALUES(%s,%s,%s) RETURNING id",
            (product_id, name, price)
        )
        vid = cur.fetchone()[0]
        db.commit()

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
    if not items:
        await update.message.reply_text("No items found.")
        return

    with connect() as db:
        cur = db.cursor()
        for it in items:
            cur.execute(
                "INSERT INTO inventory_items(variant_id,payload,status) VALUES(%s,%s,'available')",
                (variant_id, it)
            )
        db.commit()

    await update.message.reply_text(f"✅ Added {len(items)} stock items.")

async def setfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /setfile <variant_id> (attach file as Document)")
        return

    if not update.message.document:
        await update.message.reply_text("Attach a file as Document with caption: /setfile <variant_id>")
        return

    variant_id = int(context.args[0])
    file_id = update.message.document.file_id
    set_variant_file(variant_id, file_id)
    await update.message.reply_text(f"✅ Auto-delivery file set for variant {variant_id}.")
# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("version", version_cmd))

    # admin
    app.add_handler(CommandHandler("addproduct", addproduct_cmd))
    app.add_handler(CommandHandler("addvariant", addvariant_cmd))
    app.add_handler(CommandHandler("addstock", addstock_cmd))
    app.add_handler(CommandHandler("setfile", setfile_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # menu + qty input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty_message))

    print("Starting bot...")
    app.run_polling()

if name == "main":
    main()