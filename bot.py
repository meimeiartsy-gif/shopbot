import os
import uuid
import psycopg2
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_IDS = {7719956917}  # 🔁 PUT YOUR REAL TELEGRAM USER ID HERE

def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Shop", "💳 Add Balance"],
        ["💰 Balance", "📜 History"],
        ["🆘 Help", "🔐 Admin"]
    ],
    resize_keyboard=True
)

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users (user_id)
                   VALUES (%s)
                   ON CONFLICT (user_id) DO NOTHING""",
                (user.id,)
            )
    await update.message.reply_text(
        "Welcome to **Luna’s Prem Shop** 💗",
        reply_markup=MAIN_MENU
    )

# ---------------- ADD BALANCE ----------------
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("GCash", callback_data="pay_gcash")],
        [InlineKeyboardButton("GoTyme", callback_data="pay_gotyme")]
    ]
    await update.message.reply_text(
        "Choose payment method:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ---------------- PAYMENT METHOD ----------------
async def payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    method = q.data.replace("pay_", "")
    context.user_data["method"] = method

    amounts = [
        ["₱50", "₱100"],
        ["₱300", "₱500"],
        ["₱1000"]
    ]

    kb = [
        [InlineKeyboardButton(a, callback_data=f"amt_{a[1:]}")]
        for row in amounts for a in row
    ]

    await q.message.reply_text(
        f"Selected **{method.upper()}**\nChoose amount:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ---------------- AMOUNT ----------------
async def choose_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    amount = int(q.data.replace("amt_", ""))
    context.user_data["amount"] = amount

    topup_id = f"shopnluna:{uuid.uuid4().hex[:8]}"
    context.user_data["topup_id"] = topup_id

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO topups (topup_id, user_id, amount, method, status)
                   VALUES (%s,%s,%s,%s,'PENDING')""",
                (topup_id, q.from_user.id, amount, context.user_data["method"])
            )

    await q.message.reply_text(
        f"🆔 Top-up ID: `{topup_id}`\n"
        "📸 Please send your payment screenshot.\n"
        "⏳ Waiting for admin approval."
    )

# ---------------- PROOF ----------------
async def proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "topup_id" not in context.user_data:
        return

    topup_id = context.user_data["topup_id"]

    for admin in ADMIN_IDS:
        await context.bot.send_message(
            admin,
            f"💳 New Top-up\nID: {topup_id}\nUser: {update.effective_user.id}"
        )

    await update.message.reply_text(
        "✅ Proof received.\n⏳ Please wait for admin confirmation."
    )

# ---------------- ADMIN ----------------
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Access denied.")
        return

    await update.message.reply_text(
        "🔐 Admin Panel\n\n(Approvals & management coming next)"
    )

# ---------------- APP ----------------
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.Regex("^💳 Add Balance$"), add_balance))
app.add_handler(MessageHandler(filters.Regex("^🔐 Admin$"), admin))
app.add_handler(CallbackQueryHandler(payment_method, pattern="^pay_"))
app.add_handler(CallbackQueryHandler(choose_amount, pattern="^amt_"))
app.add_handler(MessageHandler(filters.PHOTO, proof))

app.run_polling()
