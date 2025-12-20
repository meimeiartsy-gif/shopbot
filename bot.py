import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
    init_db,
    ensure_user,
    get_balance,
    add_balance,
    deduct_balance,
    set_setting,
    get_setting,
    create_topup,
    attach_topup_proof,
    get_topup,
    approve_topup,
)

print("BOT.PY STARTED")

# ---------------- ENV ----------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa").strip()  # your username

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{n:,}"

# Settings keys
QR_GCASH_KEY = "QR_GCASH_FILE_ID"
QR_GOTYME_KEY = "QR_GOTYME_FILE_ID"
PAY_GCASH_TEXT_KEY = "PAY_TEXT_GCASH"
PAY_GOTYME_TEXT_KEY = "PAY_TEXT_GOTYME"

# ---------------- STARTUP ----------------
async def on_startup(app: Application):
    print("Initializing DB...")
    init_db()
    print("DB ready.")

# ---------------- MENUS ----------------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 List Products", callback_data="products")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="addbal")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("💬 Chat Admin", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")],
    ])

def add_balance_amount_kb() -> InlineKeyboardMarkup:
    amounts = [50, 100, 300, 500, 1000]
    rows = []
    row = []
    for a in amounts:
        row.append(InlineKeyboardButton(money(a), callback_data=f"amt:{a}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("GCash", callback_data="method:gcash"),
         InlineKeyboardButton("GoTyme", callback_data="method:gotyme")],
        [InlineKeyboardButton("⬅ Back", callback_data="addbal")]
    ])

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    text = (
        "🌙 Luna’s Prem Shop\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 Balance: {money(bal)}\n\n"
        "Choose an option:"
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb())

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ---------------- CALLBACKS ----------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id

    # always ensure user exists
    ensure_user(user_id)

    if data == "home":
        bal = get_balance(user_id)
        await q.message.reply_text(
            f"💳 Balance: {money(bal)}\n\nChoose an option:",
            reply_markup=main_menu_kb()
        )
        return

    if data == "balance":
        bal = get_balance(user_id)
        # show balance + quick add balance button
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Balance", callback_data="addbal")],
            [InlineKeyboardButton("⬅ Back", callback_data="home")]
        ])
        await q.message.reply_text(f"💳 Your balance: {money(bal)}", reply_markup=kb)
        return

    if data == "addbal":
        await q.message.reply_text("Select top up amount:", reply_markup=add_balance_amount_kb())
        return

    if data.startswith("amt:"):
        amount = int(data.split(":")[1])
        context.user_data["topup_amount"] = amount
        await q.message.reply_text(
            f"Selected: {money(amount)}\nChoose payment method:",
            reply_markup=method_kb()
        )
        return

    if data.startswith("method:"):
        method = data.split(":")[1]  # gcash / gotyme
        amount = int(context.user_data.get("topup_amount", 0))
        if amount <= 0:
            await q.message.reply_text("❌ Please pick amount again.", reply_markup=add_balance_amount_kb())
            return

        # Create topup record now (so proof can be linked)
        topup_id = create_topup(user_id, amount, method)

        if method == "gcash":
            qr_file_id = get_setting(QR_GCASH_KEY)
            guide = get_setting(PAY_GCASH_TEXT_KEY) or (
                "💸 GCASH PAYMENT GUIDE\n\n"
                "1) Scan the QR\n"
                "2) Pay exact amount, no refund policy\n"
                "3) Send screenshot proof here\n\n"
                f"Then send:\n/paid {topup_id}\n\n"
                f"Admin: {ADMIN_USERNAME}"
            )
        else:
            qr_file_id = get_setting(QR_GOTYME_KEY)
            guide = get_setting(PAY_GOTYME_TEXT_KEY) or (
                "🏦 GOTYME PAYMENT GUIDE\n\n"
                "1) Scan the QR\n"
                "2) Pay exact amount\n"
                "3) Send screenshot proof here\n\n"
                f"Then send:\n/paid {topup_id}\n\n"
                f"Admin: {ADMIN_USERNAME}"
            )

        if not qr_file_id:
            await q.message.reply_text(
                f"❌ QR for {method.upper()} not set yet.\nAdmin must set it first:\n"
                f"/setqrgcash or /setqrgotyme"
            )
            return

        await q.message.reply_photo(photo=qr_file_id, caption=guide)
        return

    if data == "products":
        # Placeholder: keep it simple for now
        await q.message.reply_text("🛍 Products list coming next. (Tell me if you want categories + variants UI.)")
        return

# ---------------- TOPUP PROOF ----------------
async def paid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    if not context.args:
        await update.message.reply_text("Usage: /paid <topup_id> (attach screenshot/photo)")
        return

    try:
        topup_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid topup id.")
        return

    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    if not proof_file_id:
        await update.message.reply_text("❌ Please attach your screenshot as PHOTO (or document).")
        return

    row = get_topup(topup_id)
    if not row:
        await update.message.reply_text("❌ Topup not found.")
        return

    _id, owner_id, status, amount, method, _proof = row
    if int(owner_id) != int(user_id):
        await update.message.reply_text("❌ That topup ID is not yours.")
        return
    if status != "PENDING":
        await update.message.reply_text(f"❌ Topup already {status}.")
        return

    attach_topup_proof(topup_id, proof_file_id)
    await update.message.reply_text("✅ Proof received. Waiting for admin approval.")

    # notify admins
    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "🧾 TOPUP PROOF SUBMITTED\n"
                f"Topup ID: {topup_id}\n"
                f"User: {user_id}\n"
                f"Method: {method}\n"
                f"Amount: {money(int(amount))}\n\n"
                f"Approve with:\n/approve {topup_id}"
            ),
        )
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
        except:
            pass

# ---------------- ADMIN APPROVE ----------------
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /approve <topup_id>")
        return

    try:
        topup_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid topup id.")
        return

    credited_user_id = approve_topup(topup_id)
    if not credited_user_id:
        await update.message.reply_text("❌ Topup not found or already processed.")
        return

    # tell user
    row = get_topup(topup_id)
    amount = row[3] if row else 0

    await update.message.reply_text(f"✅ Approved topup #{topup_id} (+{money(int(amount))}).")
    await context.bot.send_message(
        chat_id=credited_user_id,
        text=f"✅ Your topup is approved! +{money(int(amount))}\nType /start to refresh."
    )

# ---------------- ADMIN: SET QR (2 ways supported) ----------------
async def setqrgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_qr"] = "gcash"
    await update.message.reply_text("✅ Send your *GCash QR* now as PHOTO (no need caption).", parse_mode="Markdown")

async def setqrgotyme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_qr"] = "gotyme"
    await update.message.reply_text("✅ Send your *GoTyme QR* now as PHOTO (no need caption).", parse_mode="Markdown")

async def capture_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin only
    if not is_admin(update.effective_user.id):
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    # method A: admin previously typed /setqrgcash or /setqrgotyme
    awaiting = context.user_data.get("await_qr")

    # method B: photo caption contains /setqrgcash or /setqrgotyme
    caption = (update.message.caption or "").lower()

    if not file_id:
        return

    if " /setqrgcash" in f" {caption}" or caption.strip() == "/setqrgcash":
        set_setting(QR_GCASH_KEY, file_id)
        await update.message.reply_text("✅ GCash QR saved!")
        return

    if " /setqrgotyme" in f" {caption}" or caption.strip() == "/setqrgotyme":
        set_setting(QR_GOTYME_KEY, file_id)
        await update.message.reply_text("✅ GoTyme QR saved!")
        return

    if awaiting == "gcash":
        set_setting(QR_GCASH_KEY, file_id)
        context.user_data["await_qr"] = None
        await update.message.reply_text("✅ GCash QR saved!")
        return

    if awaiting == "gotyme":
        set_setting(QR_GOTYME_KEY, file_id)
        context.user_data["await_qr"] = None
        await update.message.reply_text("✅ GoTyme QR saved!")
        return

# ---------------- ADMIN: SET CAPTION GUIDES ----------------
async def setpaygcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    text = update.message.text.replace("/setpaygcash", "").strip()
    if not text:
        await update.message.reply_text("Usage:\n/setpaygcash <caption guide text>")
        return
    set_setting(PAY_GCASH_TEXT_KEY, text)
    await update.message.reply_text("✅ GCash payment caption updated.")

async def setpaygotyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    text = update.message.text.replace("/setpaygotyme", "").strip()
    if not text:
        await update.message.reply_text("Usage:\n/setpaygotyme <caption guide text>")
        return
    set_setting(PAY_GOTYME_TEXT_KEY, text)
    await update.message.reply_text("✅ GoTyme payment caption updated.")

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("paid", paid_cmd))

    # Admin commands
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgotyme", setqrgotyme_cmd))
    app.add_handler(CommandHandler("setpaygcash", setpaygcash))
    app.add_handler(CommandHandler("setpaygotyme", setpaygotyme))

    # Buttons
    app.add_handler(CallbackQueryHandler(on_callback))

    # Capture photos/documents (QR or proofs)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, capture_qr_photo))

    print("Starting bot polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
