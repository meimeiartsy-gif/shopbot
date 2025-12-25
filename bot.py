import os
import logging
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db import (
    ensure_schema, ensure_user,
    fetch_all, get_setting, set_setting
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

logging.basicConfig(level=logging.INFO)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def customer_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 Open Shop", callback_data="shop")],
        [InlineKeyboardButton("🆘 Help", callback_data="help")]
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Send Announcement", callback_data="admin_announce")],
        [InlineKeyboardButton("✏️ Edit Text", callback_data="admin_text")],
        [InlineKeyboardButton("🖼 Set Thumbnail", callback_data="admin_thumb")],
        [InlineKeyboardButton("💰 Top-up History", callback_data="admin_topups")],
        [InlineKeyboardButton("🧾 Purchase Logs", callback_data="admin_purchases")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
    ])

# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    # FORCE REMOVE ALL REPLY KEYBOARDS (THIS IS THE FIX)
    await update.message.reply_text(
        " ",
        reply_markup=ReplyKeyboardRemove()
    )

    text = get_setting("TEXT_HOME") or "Welcome to **Luna’s Prem Shop** 💖"
    thumb = get_setting("THUMB_HOME")

    if is_admin(user.id):
        await update.message.reply_text(
            "🔐 Admin Control Panel",
            reply_markup=admin_menu(),
            parse_mode="Markdown"
        )
    else:
        if thumb:
            await update.message.reply_photo(
                photo=thumb,
                caption=text,
                reply_markup=customer_menu(),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=customer_menu(),
                parse_mode="Markdown"
            )

# ─────────────────────────────────────────────
# CALLBACK HANDLER
# ─────────────────────────────────────────────

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id

    if q.data == "shop":
        await q.message.reply_text("🛍 Shop loading…")
    elif q.data == "help":
        await q.message.reply_text("🆘 Contact @lovebylunaa")

    # ─── ADMIN ACTIONS ───
    elif not is_admin(uid):
        return

    elif q.data == "admin_announce":
        await q.message.reply_text("Send announcement using:\n/announce YOUR_TEXT")

    elif q.data == "admin_text":
        await q.message.reply_text("Edit text:\n/settext home YOUR_TEXT")

    elif q.data == "admin_thumb":
        await q.message.reply_text("Reply to an image with:\n/setthumb home")

    elif q.data == "admin_topups":
        rows = fetch_all("SELECT user_id, amount, status, created_at FROM topups ORDER BY id DESC LIMIT 10")
        if not rows:
            await q.message.reply_text("No top-ups yet.")
            return
        msg = "💰 Top-up History\n\n"
        for r in rows:
            msg += f"{r['user_id']} — ₱{r['amount']} — {r['status']}\n"
        await q.message.reply_text(msg)

    elif q.data == "admin_purchases":
        rows = fetch_all("SELECT user_id, total_price, created_at FROM purchases ORDER BY id DESC LIMIT 10")
        if not rows:
            await q.message.reply_text("No purchases yet.")
            return
        msg = "🧾 Purchase Logs\n\n"
        for r in rows:
            msg += f"{r['user_id']} — ₱{r['total_price']} — {r['created_at']}\n"
        await q.message.reply_text(msg)

    elif q.data == "admin_users":
        rows = fetch_all("SELECT user_id, username, balance FROM users ORDER BY id DESC LIMIT 10")
        msg = "👥 Users\n\n"
        for r in rows:
            msg += f"{r['user_id']} @{r['username']} — ₱{r['balance']}\n"
        await q.message.reply_text(msg)

# ─────────────────────────────────────────────
# ADMIN COMMANDS
# ─────────────────────────────────────────────

async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = " ".join(context.args)
    if not text:
        return
    users = fetch_all("SELECT user_id FROM users")
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], f"📢 {text}")
        except:
            pass
    await update.message.reply_text("✅ Announcement sent.")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    ensure_schema()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("announce", announce))
    app.add_handler(CallbackQueryHandler(callbacks))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
