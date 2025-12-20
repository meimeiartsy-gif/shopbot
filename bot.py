import os
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

from db import (
    init_db, ensure_user, get_balance,
    set_setting, get_setting,
    create_topup, attach_topup_proof, get_topup, approve_topup
)

print("BOT.PY STARTED")

# ================== ENV ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
ADMIN_USERNAME = "@lovebylunaa"

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ================== STARTUP ==================
async def on_startup(app: Application):
    print("Initializing DB...")
    init_db()
    print("DB ready.")

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    bal = get_balance(user_id)

    # admin-editable welcome text (supports {balance})
    welcome_text = get_setting("WELCOME_TEXT")
    if not welcome_text:
        welcome_text = (
            "🌙 Luna’s Prem Shop\n"
            "━━━━━━━━━━━━━━\n"
            "💳 Balance: {balance}\n\n"
            "Choose an option:"
        )

    text = welcome_text.replace("{balance}", f"₱{bal}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Balance", callback_data="addbal")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("💬 Chat Admin", url="https://t.me/lovebylunaa")]
    ])

    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.callback_query.message.reply_text(text, reply_markup=kb)

# ================== ADMIN: SET WELCOME ==================
async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    text = update.message.text.replace("/setwelcome", "", 1).strip()
    if not text:
        await update.message.reply_text(
            "Usage:\n"
            "/setwelcome <your text>\n\n"
            "Tip: use {balance} to show user balance.\n"
            "Example:\n"
            "🌙✨ Luna Shop ✨🌙\n"
            "Balance: {balance}"
        )
        return

    set_setting("WELCOME_TEXT", text)
    await update.message.reply_text("✅ Welcome text updated!")

# ================== CALLBACKS ==================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    user_id = update.effective_user.id
    ensure_user(user_id)

    if data == "balance":
        bal = get_balance(user_id)
        await q.message.reply_text(f"💳 Your balance: ₱{bal}")
        return

    if data == "addbal":
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("₱50", callback_data="amt:50"),
                InlineKeyboardButton("₱100", callback_data="amt:100")
            ],
            [
                InlineKeyboardButton("₱300", callback_data="amt:300"),
                InlineKeyboardButton("₱500", callback_data="amt:500")
            ],
            [
                InlineKeyboardButton("₱1000", callback_data="amt:1000")
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="home")
            ]
        ])
        await q.message.reply_text("Select top-up amount:", reply_markup=kb)
        return

    if data == "home":
        await start(update, context)
        return

    if data.startswith("amt:"):
        amt = int(data.split(":")[1])
        context.user_data["topup_amount"] = amt

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📲 GCash", callback_data="method:GCASH"),
                InlineKeyboardButton("🏦 GoTyme", callback_data="method:GOTYME")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="home")]
        ])
        await q.message.reply_text(
            f"✅ Amount selected: ₱{amt}\nChoose payment method:",
            reply_markup=kb
        )
        return

    if data.startswith("method:"):
        method = data.split(":")[1]
        amt = context.user_data.get("topup_amount")

        if not amt:
            await q.message.reply_text("⚠️ Please start again: /start")
            return

        topup_id = create_topup(user_id, amt, method)

        qr_key = "QR_GCASH" if method == "GCASH" else "QR_GOTYME"
        qr_file_id = get_setting(qr_key)

        caption = (
            f"🧾 TOP UP REQUEST\n"
            f"━━━━━━━━━━━━━━\n"
            f"Amount: ₱{amt}\n"
            f"Method: {method}\n\n"
            f"1) Pay using the QR\n"
            f"2) Send screenshot proof here\n\n"
            f"✅ Send proof with caption:\n"
            f"/paid {topup_id}\n\n"
            f"Need help? Chat admin: {ADMIN_USERNAME}"
        )

        if qr_file_id:
            await q.message.reply_photo(photo=qr_file_id, caption=caption)
        else:
            await q.message.reply_text(caption + f"\n\n⚠️ Admin has not set {method} QR yet.")
        return

# ================== USER: /paid ==================
async def paid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("Usage: /paid <topup_id> (attach screenshot)")
        return

    try:
        topup_id = int(context.args[0])
    except:
        await update.message.reply_text("Topup id must be a number. Example: /paid 12")
        return

    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    if not proof_file_id:
        await update.message.reply_text("Please attach your payment screenshot (send as PHOTO).")
        return

    row = get_topup(topup_id)
    if not row:
        await update.message.reply_text("❌ Topup ID not found.")
        return

    _id, owner_id, amount, method, status, _proof = row

    if int(owner_id) != int(user_id):
        await update.message.reply_text("❌ That topup ID is not yours.")
        return

    if status != "PENDING":
        await update.message.reply_text(f"⚠️ This topup is already {status}.")
        return

    attach_topup_proof(topup_id, proof_file_id)
    await update.message.reply_text("✅ Proof received! Waiting for admin confirmation.")

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🧾 TOPUP PROOF SUBMITTED\n"
                f"Topup ID: {topup_id}\n"
                f"User: {user_id}\n"
                f"Amount: ₱{amount}\n"
                f"Method: {method}\n\n"
                f"Approve with: /approve {topup_id}"
            )
        )
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
        except:
            pass

# ================== ADMIN: /approve ==================
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /approve <topup_id>")
        return

    try:
        topup_id = int(context.args[0])
    except:
        await update.message.reply_text("Topup id must be a number. Example: /approve 12")
        return

    result = approve_topup(topup_id)
    if not result:
        await update.message.reply_text("❌ Not found or already approved.")
        return

    credited_user_id, amount = result

    await update.message.reply_text(f"✅ Approved topup #{topup_id}. Added ₱{amount}.")
    await context.bot.send_message(
        chat_id=credited_user_id,
        text=f"✅ Topup approved! +₱{amount} added to your balance."
    )

# ================== ADMIN: SET QR ==================
async def setqrgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not update.message.photo and not update.message.document:
        await update.message.reply_text("Send your GCash QR as PHOTO with caption: /setqrgcash")
        return

    file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
    set_setting("QR_GCASH", file_id)
    await update.message.reply_text("✅ GCash QR saved.")

async def setqrgotyme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not update.message.photo and not update.message.document:
        await update.message.reply_text("Send your GoTyme QR as PHOTO with caption: /setqrgotyme")
        return

    file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
    set_setting("QR_GOTYME", file_id)
    await update.message.reply_text("✅ GoTyme QR saved.")

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # user
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("paid", paid_cmd))

    # admin
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgotyme", setqrgotyme_cmd))
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    print("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
