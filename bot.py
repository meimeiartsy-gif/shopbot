import os
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

from db import (
    init_db, ensure_user, get_balance,
    set_setting, get_setting,
    create_topup, attach_topup_proof, get_topup, approve_topup,
    add_product, list_products, get_product, set_product_file
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

def money(n: int) -> str:
    return f"₱{int(n):,}"

# ================== STARTUP ==================
async def on_startup(app: Application):
    print("Initializing DB...")
    init_db()
    print("DB ready.")

# ================== MENU (INLINE + REPLY KEYBOARD FALLBACK) ==================
def get_reply_keyboard():
    # This shows buttons near the message box (works even if inline buttons hidden)
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Add Balance"), KeyboardButton("📦 List Products")],
            [KeyboardButton("💰 Balance"), KeyboardButton("💬 Chat Admin")]
        ],
        resize_keyboard=True
    )

def get_inline_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 List Products", callback_data="products")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="addbal")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("💬 Chat Admin", url="https://t.me/lovebylunaa")]
    ])

async def send_menu(chat, user_id: int):
    bal = get_balance(user_id)

    welcome_text = get_setting("WELCOME_TEXT")
    if not welcome_text:
        welcome_text = (
            "🌙 Luna’s Prem Shop\n"
            "━━━━━━━━━━━━━━\n"
            "💳 Balance: {balance}\n\n"
            "Choose an option:"
        )

    text = welcome_text.replace("{balance}", money(bal))

    # Send BOTH: reply keyboard + inline buttons
    await chat.reply_text(
        text,
        reply_markup=get_reply_keyboard()
    )
    await chat.reply_text(
        "👇 Quick buttons:",
        reply_markup=get_inline_menu()
    )

# ================== START / MENU ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    await send_menu(update.message, user_id)

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    await send_menu(update.message, user_id)

# ================== ADMIN: SET WELCOME ==================
async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    text = update.message.text.replace("/setwelcome", "", 1).strip()
    if not text:
        await update.message.reply_text(
            "Usage:\n/setwelcome <your text>\n\n"
            "Use {balance} to show balance.\n"
            "Example:\n🌙 Luna Shop\nBalance: {balance}"
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
        await q.message.reply_text(f"💳 Your balance: {money(get_balance(user_id))}")
        return

    if data == "addbal":
        await show_amounts(q.message)
        return

    if data == "products":
        await show_products(q.message)
        return

    if data.startswith("amt:"):
        amt = int(data.split(":")[1])
        context.user_data["topup_amount"] = amt
        await show_methods(q.message, amt)
        return

    if data.startswith("method:"):
        method = data.split(":")[1]
        amt = context.user_data.get("topup_amount")
        if not amt:
            await q.message.reply_text("⚠️ Please type /start again.")
            return

        topup_id = create_topup(user_id, amt, method)

        qr_key = "QR_GCASH" if method == "GCASH" else "QR_GOTYME"
        qr_file_id = get_setting(qr_key)

        caption = (
            f"🧾 TOP UP REQUEST\n"
            f"━━━━━━━━━━━━━━\n"
            f"Amount: {money(amt)}\n"
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

# ================== UI HELPERS ==================
async def show_amounts(chat):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("₱50", callback_data="amt:50"),
         InlineKeyboardButton("₱100", callback_data="amt:100")],
        [InlineKeyboardButton("₱300", callback_data="amt:300"),
         InlineKeyboardButton("₱500", callback_data="amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="amt:1000")],
        [InlineKeyboardButton("⬅ Back", callback_data="home_menu")]
    ])
    await chat.reply_text("Select top-up amount:", reply_markup=kb)

async def show_methods(chat, amt: int):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 GCash", callback_data="method:GCASH"),
         InlineKeyboardButton("🏦 GoTyme", callback_data="method:GOTYME")],
        [InlineKeyboardButton("⬅ Back", callback_data="addbal")]
    ])
    await chat.reply_text(f"✅ Amount selected: {money(amt)}\nChoose payment method:", reply_markup=kb)

async def show_products(chat):
    items = list_products()
    if not items:
        await chat.reply_text("📦 No products yet.\n(Admin can add using /addproduct)")
        return

    buttons = []
    text_lines = ["📦 Products:", "━━━━━━━━━━━━━━"]
    for pid, name, price in items:
        text_lines.append(f"{pid}) {name} — {money(price)}")
        buttons.append([InlineKeyboardButton(f"{pid}. {name}", callback_data=f"prod:{pid}")])

    buttons.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="home_menu")])

    await chat.reply_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(buttons))

# ================== HOME MENU CALLBACK ==================
async def home_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id
    ensure_user(user_id)
    await send_menu(q.message, user_id)

# ================== USER /paid ==================
async def paid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

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
                f"Amount: {money(amount)}\n"
                f"Method: {method}\n\n"
                f"Approve with: /approve {topup_id}"
            )
        )
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=proof_file_id)
        except:
            pass

# ================== ADMIN /approve ==================
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

    await update.message.reply_text(f"✅ Approved topup #{topup_id}. Added {money(amount)}.")
    await context.bot.send_message(
        chat_id=credited_user_id,
        text=f"✅ Topup approved! +{money(amount)} added to your balance."
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

# ================== ADMIN: PRODUCTS ==================
async def addproduct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    # /addproduct Name | price
    text = update.message.text.replace("/addproduct", "", 1).strip()
    if "|" not in text:
        await update.message.reply_text("Usage:\n/addproduct Netflix 1 Month | 150")
        return

    name, price_s = [x.strip() for x in text.split("|", 1)]
    try:
        price = int(price_s)
    except:
        await update.message.reply_text("Price must be a number.")
        return

    pid = add_product(name, price)
    await update.message.reply_text(f"✅ Product added.\nID: {pid}\nName: {name}\nPrice: {money(price)}")

async def setproductfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /setproductfile <product_id> (attach file as Document)")
        return

    if not update.message.document:
        await update.message.reply_text("Attach the file as Document with caption: /setproductfile <product_id>")
        return

    pid = int(context.args[0])
    file_id = update.message.document.file_id
    set_product_file(pid, file_id)
    await update.message.reply_text("✅ Product file delivery set!")

# ================== REPLY KEYBOARD TEXT HANDLER ==================
async def reply_keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    user_id = update.effective_user.id
    ensure_user(user_id)

    if txt == "➕ Add Balance":
        await show_amounts(update.message)
        return
    if txt == "💰 Balance":
        await update.message.reply_text(f"💳 Your balance: {money(get_balance(user_id))}")
        return
    if txt == "📦 List Products":
        await show_products(update.message)
        return
    if txt == "💬 Chat Admin":
        await update.message.reply_text(f"Chat admin here: https://t.me/lovebylunaa")
        return

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # user commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("paid", paid_cmd))

    # admin commands
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("setqrgcash", setqrgcash_cmd))
    app.add_handler(CommandHandler("setqrgotyme", setqrgotyme_cmd))
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("addproduct", addproduct_cmd))
    app.add_handler(CommandHandler("setproductfile", setproductfile_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(home_menu_cb, pattern="^home_menu$"))
    app.add_handler(CallbackQueryHandler(on_callback))

    # reply keyboard clicks
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_keyboard_handler))

    print("Starting bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
