import os
from datetime import datetime
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from db import (
    init_db,
    ensure_user,
    get_balance,
    add_balance,
    add_product,
    add_variant,
    add_stock_items,
    count_stock,
    set_variant_file,
    get_variant_file,
    list_products,
    list_variants
)

# ================= ENV =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

# ================= HELPERS =================
def money(n: int) -> str:
    return f"₱{n:,}"

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# ================= STARTUP =================
async def on_startup(app: Application):
    init_db()
    print("DB ready")

# ================= UI =================
MAIN_KB = ReplyKeyboardMarkup(
    [
        ["📦 List Produk", "💰 Balance"],
        ["💬 Chat Admin", "❌ Cancel"]
    ],
    resize_keyboard=True
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)

    await update.message.reply_text(
        "🌙 *Luna Shop*\n\nChoose an option:",
        reply_markup=MAIN_KB,
        parse_mode="Markdown"
    )

# ================= MENU =================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "💰 Balance":
        bal = get_balance(uid)
        await update.message.reply_text(f"💰 Balance: {money(bal)}")
        return

    if text == "💬 Chat Admin":
        await update.message.reply_text("📩 Contact admin: @lovebylunaa")
        return

    if text == "❌ Cancel":
        context.user_data.clear()
        await update.message.reply_text("❌ Order cancelled.")
        return

    if text == "📦 List Produk":
        products = list_products()
        if not products:
            await update.message.reply_text("No products yet.")
            return

        buttons = [
            [InlineKeyboardButton(p["name"], callback_data=f"prod:{p['id']}")]
            for p in products
        ]

        await update.message.reply_text(
            "📦 Products:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

# ================= CALLBACK =================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    uid = q.from_user.id

    if data.startswith("prod:"):
        pid = int(data.split(":")[1])
        variants = list_variants(pid)

        buttons = []
        for v in variants:
            stock = "∞" if v["file_id"] else count_stock(v["id"])
            buttons.append([
                InlineKeyboardButton(
                    f"{v['name']} — {money(v['price'])} (Stock {stock})",
                    callback_data=f"buy:{v['id']}"
                )
            ])

        await q.message.reply_text(
            "Choose package:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("buy:"):
        vid = int(data.split(":")[1])
        context.user_data["variant"] = vid
        await q.message.reply_text("Enter quantity:")

# ================= BUY =================
async def qty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "variant" not in context.user_data:
        return

    uid = update.effective_user.id
    vid = context.user_data.pop("variant")

    try:
        qty = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Enter a number.")
        return

    bal = get_balance(uid)
    variant = get_variant_file(vid)

    if variant:
        if qty != 1:
            await update.message.reply_text("File products only allow qty = 1")
            return

        price = variant["price"]
        if bal < price:
            await update.message.reply_text("Not enough balance.")
            return

        add_balance(uid, -price)
        await update.message.reply_document(
            document=variant["file_id"],
            caption="✅ Purchase successful"
        )
        return

    stock = count_stock(vid)
    if stock < qty:
        await update.message.reply_text("Not enough stock.")
        return

    total = variant["price"] * qty
    if bal < total:
        await update.message.reply_text("Not enough balance.")
        return

    add_balance(uid, -total)
    items = add_stock_items(vid, qty)

    await update.message.reply_text(
        "✅ Purchase successful:\n" + "\n".join(items)
    )

# ================= ADMIN =================
async def setfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not update.message.document:
        await update.message.reply_text("Usage: /setfile <variant_id> (attach file)")
        return

    vid = int(context.args[0])
    file_id = update.message.document.file_id
    set_variant_file(vid, file_id)

    await update.message.reply_text("✅ File set for auto delivery.")

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, menu_handler))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, qty_handler))
    app.add_handler(CommandHandler("setfile", setfile_cmd))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
