import os
import uuid
import logging
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import db

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("botTOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")  # comma-separated telegram user ids, example: "123,456"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa")

def admin_id_set() -> set[int]:
    ids = set()
    for x in ADMIN_IDS.split(","):
        x = x.strip()
        if x.isdigit():
            ids.add(int(x))
    return ids

ADMINS = admin_id_set()

# --- Keyboards ---
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📦 List Produk", "💰 Balance"],
            ["➕ Add Balance", "💬 Chat Admin"],
            ["❌ Cancel"],
        ],
        resize_keyboard=True,
    )

def amount_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("₱50", callback_data="amt:50"),
         InlineKeyboardButton("₱100", callback_data="amt:100"),
         InlineKeyboardButton("₱300", callback_data="amt:300")],
        [InlineKeyboardButton("₱500", callback_data="amt:500"),
         InlineKeyboardButton("₱1000", callback_data="amt:1000")],
        [InlineKeyboardButton("⬅ Back", callback_data="back:menu")]
    ])

def method_keyboard(amount: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("GCash", callback_data=f"method:gcash:{amount}"),
         InlineKeyboardButton("GoTyme", callback_data=f"method:gotyme:{amount}")],
        [InlineKeyboardButton("⬅ Back", callback_data="back:amount")]
    ])

# --- Helpers ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def make_topup_id():
    # example: SHOPNLUNA-6-TU3SM4 (similar to your screenshot)
    short = uuid.uuid4().hex[:6].upper()
    return f"SHOPNLUNA-{len(short)}-{short}"

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled exception", exc_info=context.error)

# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    db.upsert_user(user.id, user.username)

    bal = db.get_balance(user.id)
    await msg.reply_text(
        f"🌙 Luna’s Prem Shop\n"
        f"━━━━━━━━━━━━━━\n"
        f"💳 Balance: ₱{bal}\n\n"
        f"Choose an option:",
        reply_markup=main_menu_keyboard()
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username)
    bal = db.get_balance(user.id)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Balance", callback_data="open:addbalance")],
    ])

    await update.effective_message.reply_text(
        f"💰 Your balance: ₱{bal}",
        reply_markup=kb
    )

async def list_produk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # placeholder (your product system can be plugged later)
    await update.effective_message.reply_text("No products yet.", reply_markup=main_menu_keyboard())

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"💬 Chat Admin: {ADMIN_USERNAME}",
        reply_markup=main_menu_keyboard()
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("✅ Cancelled.", reply_markup=main_menu_keyboard())

# --- Add Balance Flow ---
async def open_add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Choose top up amount:",
        reply_markup=amount_keyboard()
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "open:addbalance":
        await query.message.reply_text("Choose top up amount:", reply_markup=amount_keyboard())
        return

    if data.startswith("back:menu"):
        await query.message.reply_text("Choose an option:", reply_markup=main_menu_keyboard())
        return

    if data.startswith("back:amount"):
        await query.message.reply_text("Choose top up amount:", reply_markup=amount_keyboard())
        return

    if data.startswith("amt:"):
        amount = int(data.split(":")[1])
        context.user_data["topup_amount"] = amount
        await query.message.reply_text(
            f"Amount selected: ₱{amount}\nChoose payment method:",
            reply_markup=method_keyboard(amount)
        )
        return

    if data.startswith("method:"):
        _, method, amount_str = data.split(":")
        amount = int(amount_str)

        context.user_data["topup_amount"] = amount
        context.user_data["topup_method"] = method
        context.user_data["awaiting_proof"] = True

        # show QR if set
        qr_key = f"qr_{method}"
        qr_file_id = db.get_setting(qr_key)
        caption = db.get_setting(f"{qr_key}_caption") or ""

        topup_id = make_topup_id()
        context.user_data["topup_id"] = topup_id

        text = (
            f"Topup ID: {topup_id}\n\n"
            f"✅ After payment, just send your screenshot here.\n"
            f"⏳ Waiting for admin approval.\n"
            f"Admin: {ADMIN_USERNAME}"
        )

        if qr_file_id:
            await query.message.reply_photo(photo=qr_file_id, caption=(caption.strip() or f"{method.upper()} QR"))
        else:
            await query.message.reply_text(
                f"⚠️ Admin has not set {method.upper()} QR yet.\n"
                f"Tell admin to run /setqr{method}"
            )

        await query.message.reply_text(text)
        return

    # Admin approve/reject
    if data.startswith("approve:"):
        if not is_admin(query.from_user.id):
            await query.message.reply_text("❌ Admin only.")
            return
        topup_id = data.split(":", 1)[1]
        ok = db.approve_topup(topup_id)
        await query.message.reply_text("✅ Approved." if ok else "⚠️ Not found / already handled.")
        return

    if data.startswith("reject:"):
        if not is_admin(query.from_user.id):
            await query.message.reply_text("❌ Admin only.")
            return
        topup_id = data.split(":", 1)[1]
        ok = db.reject_topup(topup_id)
        await query.message.reply_text("✅ Rejected." if ok else "⚠️ Not found / already handled.")
        return

# --- Proof Upload (Customer sends screenshot/photo) ---
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    db.upsert_user(user.id, user.username)

    if not context.user_data.get("awaiting_proof"):
        await msg.reply_text("📌 If you want to top up, click ➕ Add Balance first.")
        return

    topup_id = context.user_data.get("topup_id") or make_topup_id()
    amount = int(context.user_data.get("topup_amount") or 0)
    method = context.user_data.get("topup_method") or "unknown"

    # get highest quality photo
    photo = msg.photo[-1]
    proof_file_id = photo.file_id

    # save topup
    db.create_topup(topup_id, user.id, amount, method, proof_file_id)

    # confirm to customer
    await msg.reply_text(
        f"✅ Proof received!\n"
        f"Topup ID: {topup_id}\n"
        f"Amount: ₱{amount}\n"
        f"Method: {method.upper()}\n\n"
        f"⏳ Waiting for admin approval.\n"
        f"For fast approval, DM admin: {ADMIN_USERNAME}"
    )

    # notify admin with approve buttons
    if ADMINS:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve:{topup_id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject:{topup_id}")]
        ])

        text = (
            f"🧾 NEW TOPUP\n"
            f"Topup ID: {topup_id}\n"
            f"User: @{user.username or 'no_username'} ({user.id})\n"
            f"Amount: ₱{amount}\n"
            f"Method: {method.upper()}"
        )

        for admin_id in ADMINS:
            try:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=proof_file_id,
                    caption=text,
                    reply_markup=kb
                )
            except Exception:
                # fallback: send text only
                await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb)

    # clear state
    context.user_data["awaiting_proof"] = False

# --- Admin: set QR commands ---
async def setqrgcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    context.user_data["awaiting_setqr"] = "gcash"
    await update.effective_message.reply_text("Send your GCash QR as PHOTO now (no document). You can add caption text after by /setqrcaptiongcash")

async def setqrgotyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    context.user_data["awaiting_setqr"] = "gotyme"
    await update.effective_message.reply_text("Send your GoTyme QR as PHOTO now (no document). You can add caption text after by /setqrcaptiongotyme")

async def on_admin_qr_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    method = context.user_data.get("awaiting_setqr")
    if not method:
        return

    photo = update.effective_message.photo[-1]
    file_id = photo.file_id

    db.set_setting(f"qr_{method}", file_id)
    context.user_data["awaiting_setqr"] = None
    await update.effective_message.reply_text(f"✅ {method.upper()} QR saved!")

async def setqrcaptiongcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    caption = " ".join(context.args).strip()
    db.set_setting("qr_gcash_caption", caption)
    await update.effective_message.reply_text("✅ GCash caption updated.")

async def setqrcaptiongotyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    caption = " ".join(context.args).strip()
    db.set_setting("qr_gotyme_caption", caption)
    await update.effective_message.reply_text("✅ GoTyme caption updated.")

# Admin: list pending
async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    rows = db.list_pending_topups(20)
    if not rows:
        await update.effective_message.reply_text("✅ No pending topups.")
        return
    lines = ["🕒 Pending topups:"]
    for r in rows:
        lines.append(f"- {r['topup_id']} | ₱{r['amount']} | {r['method']} | user {r['user_id']}")
    await update.effective_message.reply_text("\n".join(lines))

# --- Text router for menu buttons ---
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()

    if text in ("/start", "/menu"):
        await start(update, context)
        return

    if text == "💰 Balance":
        await balance(update, context)
        return

    if text == "📦 List Produk":
        await list_produk(update, context)
        return

    if text == "💬 Chat Admin":
        await chat_admin(update, context)
        return

    if text == "➕ Add Balance":
        await open_add_balance(update, context)
        return

    if text == "❌ Cancel":
        await cancel(update, context)
        return

    await update.effective_message.reply_text("Use the menu buttons below 👇", reply_markup=main_menu_keyboard())

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing in Railway Variables.")

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("balance", balance))

    app.add_handler(CommandHandler("setqrgcash", setqrgcash))
    app.add_handler(CommandHandler("setqrgotyme", setqrgotyme))
    app.add_handler(CommandHandler("setqrcaptiongcash", setqrcaptiongcash))
    app.add_handler(CommandHandler("setqrcaptiongotyme", setqrcaptiongotyme))
    app.add_handler(CommandHandler("pending", pending))

    app.add_handler(CallbackQueryHandler(on_callback))

    # IMPORTANT: admin set-QR photo must be checked before normal proof flow
    app.add_handler(MessageHandler(filters.PHOTO & filters.User(user_id=list(ADMINS)), on_admin_qr_photo))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling()

if __name__ == "__main__":
    main()
