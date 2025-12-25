import os
import logging
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    MenuButtonCommands
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from db import (
    ensure_schema,
    ensure_user,
    fetch_all,
    fetch_one,
    execute,
    get_setting,
    set_setting
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

logging.basicConfig(level=logging.INFO)

# ─────────────────────────────
# HELPERS
# ─────────────────────────────

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Announcement", callback_data="admin_announce")],
        [InlineKeyboardButton("✏️ Edit Text", callback_data="admin_text")],
        [InlineKeyboardButton("🖼 Set Thumbnail", callback_data="admin_thumb")],
        [InlineKeyboardButton("💰 Pending Top-ups", callback_data="admin_topups")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin_purchases")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
    ])

# ─────────────────────────────
# START
# ─────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    # 🔥 FORCE REMOVE ANY KEYBOARD
    await update.message.reply_text(
        " ",
        reply_markup=ReplyKeyboardRemove()
    )

    # 🔥 FORCE REMOVE MENU BUTTON
    await context.bot.set_chat_menu_button(
        chat_id=user.id,
        menu_button=MenuButtonCommands()
    )

    if is_admin(user.id):
        await update.message.reply_text(
            "🔐 **Admin Panel**",
            reply_markup=admin_menu(),
            parse_mode="Markdown"
        )
    else:
        text = get_setting("TEXT_HOME") or "Welcome to Luna’s Prem Shop 💖"
        thumb = get_setting("THUMB_HOME")

        if thumb:
            await update.message.reply_photo(
                photo=thumb,
                caption=text,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(text)

# ─────────────────────────────
# CALLBACKS
# ─────────────────────────────

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if not is_admin(uid):
        return

    # ─── ADMIN ───
    if q.data == "admin_topups":
        rows = fetch_all("""
            SELECT id, user_id, amount
            FROM topups
            WHERE status='pending'
            ORDER BY id DESC
        """)

        if not rows:
            await q.message.reply_text("No pending top-ups.")
            return

        for r in rows:
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{r['id']}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{r['id']}")
                ]
            ])
            await q.message.reply_text(
                f"User: {r['user_id']}\nAmount: ₱{r['amount']}",
                reply_markup=kb
            )

    elif q.data.startswith("approve_"):
        tid = int(q.data.split("_")[1])
        t = fetch_one("SELECT * FROM topups WHERE id=%s", (tid,))
        if not t:
            return

        execute("UPDATE topups SET status='approved' WHERE id=%s", (tid,))
        execute(
            "UPDATE users SET balance = balance + %s WHERE user_id=%s",
            (t["amount"], t["user_id"])
        )

        await context.bot.send_message(
            t["user_id"],
            f"✅ Your top-up of ₱{t['amount']} has been approved!"
        )
        await q.message.reply_text("Top-up approved.")

    elif q.data.startswith("reject_"):
        tid = int(q.data.split("_")[1])
        execute("UPDATE topups SET status='rejected' WHERE id=%s", (tid,))
        await q.message.reply_text("Top-up rejected.")

    elif q.data == "admin_purchases":
        rows = fetch_all("SELECT user_id, total_price, created_at FROM purchases ORDER BY id DESC LIMIT 10")
        msg = "🧾 Purchases\n\n"
        for r in rows:
            msg += f"{r['user_id']} — ₱{r['total_price']} — {r['created_at']}\n"
        await q.message.reply_text(msg)

    elif q.data == "admin_users":
        rows = fetch_all("SELECT user_id, username, balance FROM users ORDER BY id DESC")
        msg = "👥 Users\n\n"
        for r in rows:
            msg += f"{r['user_id']} @{r['username']} — ₱{r['balance']}\n"
        await q.message.reply_text(msg)

# ─────────────────────────────
# MAIN
# ─────────────────────────────

def main():
    ensure_schema()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
