import os
import random
import string
from datetime import datetime

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
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
    connect,
    init_db,
    ensure_user,
    get_balance,
    set_setting,
    get_setting,
)

# ================== ENV ==================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa").strip()

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN missing in Railway Variables")

SHOP_NAME = "🌙 Luna’s Prem Shop"

# ================== HELPERS ==================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📦 List Produk"), KeyboardButton("💰 Balance")],
            [KeyboardButton("➕ Add Balance"), KeyboardButton("💬 Chat Admin")],
            [KeyboardButton("❌ Cancel")],
        ],
        resize_keyboard=True
    )

def random_code(n=6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

def make_topup_code(topup_id: int) -> str:
    return f"SHOPNLUNA-{topup_id}-{random_code(6)}"

# ================== DB: TOPUPS ==================
def create_topup(user_id: int, amount: int, method: str) -> tuple[int, str]:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO topups(user_id,status,proof_file_id) VALUES(%s,'PENDING','') RETURNING id",
            (user_id,)
        )
        topup_id = int(cur.fetchone()[0])
        topup_code = make_topup_code(topup_id)

        set_setting(f"TOPUP:{topup_id}:CODE", topup_code)
        set_setting(f"TOPUP:{topup_id}:AMOUNT", str(int(amount)))
        set_setting(f"TOPUP:{topup_id}:METHOD", method)

        db.commit()
        return topup_id, topup_code

def set_topup_proof(topup_id: int, proof_file_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET proof_file_id=%s WHERE id=%s", (proof_file_id, topup_id))
        db.commit()

def get_topup_row(topup_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT id, user_id, status, proof_file_id FROM topups WHERE id=%s", (topup_id,))
        return cur.fetchone()

def list_pending_topups(limit: int = 20):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, user_id, proof_file_id FROM topups WHERE status='PENDING' ORDER BY id DESC LIMIT %s",
            (limit,)
        )
        return cur.fetchall()

def approve_topup_db(topup_id: int) -> tuple[int, int, str] | None:
    row = get_topup_row(topup_id)
    if not row:
        return None
    _id, user_id, status, _proof = row
    if status != "PENDING":
        return None

    amount = int(get_setting(f"TOPUP:{topup_id}:AMOUNT") or "0")
    code = get_setting(f"TOPUP:{topup_id}:CODE") or f"TOPUP-{topup_id}"
    if amount <= 0:
        return None

    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET status='APPROVED' WHERE id=%s", (topup_id,))
        cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
        db.commit()

    return int(user_id), amount, code

def reject_topup_db(topup_id: int) -> tuple[int, str] | None:
    row = get_topup_row(topup_id)
    if not row:
        return None
    _id, user_id, status, _proof = row
    if status != "PENDING":
        return None
    code = get_setting(f"TOPUP:{topup_id}:CODE") or f"TOPUP-{topup_id}"
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET status='REJECTED' WHERE id=%s", (topup_id,))
        db.commit()
    return int(user_id), code

# ================== STARTUP ==================
async def on_startup(app: Application):
    init_db()

# ================== BASIC ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    text = (
        f"{SHOP_NAME}\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 Balance: {money(bal)}\n\n"
        "Choose an option:"
    )
    await update.message.reply_text(text, reply_markup=main_menu_kb())

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Balance", callback_data="add_balance")],
    ])
    await update.message.reply_text(f"💳 Your balance: {money(bal)}", reply_markup=kb)

# ================== ADD BALANCE FLOW ==================
async def add_balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("₱50", callback_data="amt:50"),
         InlineKeyboardButton("₱100", callback_data="amt:100")],
        [InlineKeyboardButton("₱300", callback_data="amt:300"),
         InlineKeyboardButton("₱500", callback_data="amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="amt:1000")],
    ])
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text("Choose top up amount:", reply_markup=kb)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = update.effective_user.id

    if data == "add_balance":
        await add_balance_menu(update, context)
        return

    if data.startswith("amt:"):
        amount = int(data.split(":")[1])
        context.user_data["topup_amount"] = amount
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟦 GCash", callback_data="paymethod:GCASH")],
            [InlineKeyboardButton("🟩 GoTyme", callback_data="paymethod:GOTYME")],
        ])
        await q.message.reply_text(f"Choose payment method for {money(amount)}:", reply_markup=kb)
        return

    if data.startswith("paymethod:"):
        method = data.split(":", 1)[1]
        amount = int(context.user_data.get("topup_amount") or 0)
        if amount <= 0:
            await q.message.reply_text("Please choose amount again.")
            await add_balance_menu(update, context)
            return

        topup_id, topup_code = create_topup(user_id, amount, method)
        context.user_data["awaiting_proof"] = topup_id

        if method == "GCASH":
            qr_file_id = get_setting("PAYMENT_QR_GCASH_FILE_ID")
            caption = get_setting("PAYMENT_QR_GCASH_CAPTION") or "Scan to pay via GCash."
        else:
            qr_file_id = get_setting("PAYMENT_QR_GOTYME_FILE_ID")
            caption = get_setting("PAYMENT_QR_GOTYME_CAPTION") or "Scan to pay via GoTyme."

        guide = (
            f"🧾 TOP UP REQUEST\n"
            "━━━━━━━━━━━━━━\n"
            f"Amount: {money(amount)}\n"
            f"Method: {method}\n"
            f"Topup ID: {topup_code}\n\n"
            "✅ After payment, send your screenshot here.\n"
            "⏳ Waiting for admin approval.\n"
            f"For faster approval, DM admin: {ADMIN_USERNAME}"
        )

        if qr_file_id:
            await q.message.reply_photo(photo=qr_file_id, caption=f"{caption}\n\n{guide}")
        else:
            await q.message.reply_text(
                f"⚠️ QR for {method} is not set yet.\nAdmin must set it first.\n\n{guide}"
            )
        return

    # ADMIN inline approve/reject
    if data.startswith("approve:"):
        if not is_admin(user_id):
            await q.message.reply_text("❌ Admin only.")
            return
        topup_id = int(data.split(":")[1])
        approved = approve_topup_db(topup_id)
        if not approved:
            await q.message.reply_text("❌ Cannot approve (already processed / missing).")
            return
        pay_user_id, amount, code = approved
        await q.message.reply_text(f"✅ Approved {code} (+{money(amount)})")
        await context.bot.send_message(
            chat_id=pay_user_id,
            text=f"✅ Top up approved!\nTopup ID: {code}\nAdded: {money(amount)}"
        )
        return

    if data.startswith("reject:"):
        if not is_admin(user_id):
            await q.message.reply_text("❌ Admin only.")
            return
        topup_id = int(data.split(":")[1])
        rejected = reject_topup_db(topup_id)
        if not rejected:
            await q.message.reply_text("❌ Cannot reject (already processed).")
            return
        pay_user_id, code = rejected
        await q.message.reply_text(f"❌ Rejected {code}")
        await context.bot.send_message(
            chat_id=pay_user_id,
            text=f"❌ Top up rejected.\nIf this is wrong, message admin: {ADMIN_USERNAME}"
        )
        return

# ================== ADMIN: PENDING LIST ==================
async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    pending = list_pending_topups(30)
    if not pending:
        await update.message.reply_text("✅ No pending topups.")
        return

    lines = ["🧾 Pending Topups:"]
    for tid, uid, proof in pending:
        code = get_setting(f"TOPUP:{tid}:CODE") or f"TOPUP-{tid}"
        amt = get_setting(f"TOPUP:{tid}:AMOUNT") or "?"
        method = get_setting(f"TOPUP:{tid}:METHOD") or "?"
        lines.append(f"- {code} | user:{uid} | {money(int(amt))} | {method}")

    await update.message.reply_text("\n".join(lines))

# ================== ADMIN: SET QR ==================
async def setqrgcash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["awaiting_qr"] = "GCASH"
    await update.message.reply_text("✅ Send your GCash QR as PHOTO now.")

async def setqrgoytme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["awaiting_qr"] = "GOTYME"
    await update.message.reply_text("✅ Send your GoTyme QR as PHOTO now.")

async def setqrcaption_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setqrcaption gcash|gotyme <caption text>")
        return
    which = context.args[0].lower()
    text = " ".join(context.args[1:]).strip()
    if which == "gcash":
        set_setting("PAYMENT_QR_GCASH_CAPTION", text)
        await update.message.reply_text("✅ GCash caption saved.")
    elif which == "gotyme":
        set_setting("PAYMENT_QR_GOTYME_CAPTION", text)
        await update.message.reply_text("✅ GoTyme caption saved.")
    else:
        await update.message.reply_text("First arg must be gcash or gotyme.")

# ================== ONE PHOTO HANDLER (FIX) ==================
async def on_any_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    This fixes your issue:
    - if admin is setting QR -> save QR
    - else if user is awaiting proof -> treat as payment proof
    - else ignore
    """
    user_id = update.effective_user.id

    # 1) Admin QR upload
    which = context.user_data.get("awaiting_qr")
    if which and is_admin(user_id):
        if not update.message.photo:
            await update.message.reply_text("❌ Please send as PHOTO.")
            return
        file_id = update.message.photo[-1].file_id
        if which == "GCASH":
            set_setting("PAYMENT_QR_GCASH_FILE_ID", file_id)
            await update.message.reply_text("✅ GCash QR saved!")
        else:
            set_setting("PAYMENT_QR_GOTYME_FILE_ID", file_id)
            await update.message.reply_text("✅ GoTyme QR saved!")
        context.user_data["awaiting_qr"] = None
        return

    # 2) Customer proof upload
    topup_id = context.user_data.get("awaiting_proof")
    if topup_id:
        proof_file_id = update.message.photo[-1].file_id if update.message.photo else None
        if not proof_file_id:
            await update.message.reply_text("❌ Please send screenshot as PHOTO.")
            return

        row = get_topup_row(int(topup_id))
        if not row:
            await update.message.reply_text("Topup not found. Please create new top up.")
            context.user_data["awaiting_proof"] = None
            return

        _id, owner_id, status, _old = row
        if int(owner_id) != int(user_id):
            await update.message.reply_text("This topup is not yours.")
            return
        if status != "PENDING":
            await update.message.reply_text(f"Topup already {status}.")
            context.user_data["awaiting_proof"] = None
            return

        set_topup_proof(int(topup_id), proof_file_id)

        code = get_setting(f"TOPUP:{topup_id}:CODE") or f"TOPUP-{topup_id}"
        amount = int(get_setting(f"TOPUP:{topup_id}:AMOUNT") or "0")
        method = get_setting(f"TOPUP:{topup_id}:METHOD") or "UNKNOWN"

        # ✅ Customer follow-up message
        await update.message.reply_text(
            "✅ Thank you! Proof of payment received.\n"
            f"🧾 Topup ID: {code}\n"
            f"💵 Amount: {money(amount)}\n"
            f"💳 Method: {method}\n\n"
            "⏳ Waiting for admin approval.\n"
            f"⚡ For faster approval, DM admin: {ADMIN_USERNAME}"
        )

        # ✅ Notify admins with buttons + screenshot
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{topup_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{topup_id}"),
            ]
        ])

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "🧾 New Topup Proof Received\n"
                        f"Topup ID: {code}\n"
                        f"User ID: {user_id}\n"
                        f"Amount: {money(amount)}\n"
                        f"Method: {method}\n"
                    ),
                    reply_markup=kb
                )
                await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
            except Exception as e:
                print("Admin notify failed:", e)

        context.user_data["awaiting_proof"] = None
        return

    # 3) Otherwise ignore
    return

# ================== TEXT MENU ==================
async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "💰 Balance":
        await show_balance(update, context)
        return

    if text == "➕ Add Balance":
        await add_balance_menu(update, context)
        return

    if text == "💬 Chat Admin":
        await update.message.reply_text(f"Message admin: {ADMIN_USERNAME}")
        return

    if text == "❌ Cancel":
        context.user_data.pop("awaiting_proof", None)
        context.user_data.pop("awaiting_qr", None)
        context.user_data.pop("topup_amount", None)
        await update.message.reply_text("✅ Cancelled.", reply_markup=main_menu_kb())
        return

    if text == "📦 List Produk":
        await update.message.reply_text("Products part not added in this file version yet.")
        return

    await update.message.reply_text("Please choose a button, or type /start.", reply_markup=main_menu_kb())

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))

    # Admin QR + captions + pending
    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgoytme", setqrgoytme_cmd))
    app.add_handler(CommandHandler("setqrgotyme", setqrgoytme_cmd))  # alias
    app.add_handler(CommandHandler("setqrcaption", setqrcaption_cmd))
    app.add_handler(CommandHandler("pending", pending_cmd))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # ✅ One photo handler fixes your "no reply after sending proof"
    app.add_handler(MessageHandler(filters.PHOTO, on_any_photo))

    # Text menu
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_menu))

    app.run_polling()

if __name__ == "__main__":
    main()
