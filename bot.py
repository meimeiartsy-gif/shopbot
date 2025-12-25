import os
import logging
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    MenuButtonDefault,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import (
    ensure_schema,
    ensure_user,
    fetch_all,
    fetch_one,
    execute,
    get_setting,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

logging.basicConfig(level=logging.INFO)


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


async def hard_remove_keyboard(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """
    HARD remove any old reply keyboard + reset menu button.
    (Telegram sometimes requires multiple removes.)
    """
    # 1) Remove reply keyboard (must have visible text)
    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Buttons cleared.",
        reply_markup=ReplyKeyboardRemove(selective=False),
    )

    # 2) Send another remove (Telegram sometimes needs a 2nd one)
    await context.bot.send_message(
        chat_id=chat_id,
        text="(clearing...)",
        reply_markup=ReplyKeyboardRemove(selective=False),
    )

    # 3) Reset menu button to default
    await context.bot.set_chat_menu_button(
        chat_id=chat_id,
        menu_button=MenuButtonDefault(),
    )


async def send_customer_home(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    text = get_setting("TEXT_HOME") or "Welcome to Luna’s Prem Shop 💖"
    thumb = get_setting("THUMB_HOME")

    if thumb:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=thumb,
            caption=text,
        )
    else:
        await context.bot.send_message(chat_id=chat_id, text=text)


# ─────────────────────────────
# COMMANDS
# ─────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    # HARD remove old keyboards/menu every time
    await hard_remove_keyboard(context, user.id)

    if is_admin(user.id):
        await context.bot.send_message(
            chat_id=user.id,
            text="🔐 Admin Panel",
            reply_markup=admin_menu(),
        )
    else:
        await send_customer_home(context, user.id)


async def clearkb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await hard_remove_keyboard(context, update.effective_chat.id)
    await update.message.reply_text("✅ Cleared.", reply_markup=ReplyKeyboardRemove())


# ─────────────────────────────
# THIS IS THE IMPORTANT PART:
# If the old buttons are still showing,
# when user presses them Telegram sends TEXT.
# We catch ANY text and clear keyboard again.
# ─────────────────────────────

async def any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    # Always try to clear old reply keyboard on ANY text
    await hard_remove_keyboard(context, user.id)

    # Admin: let them type "Admin" to open panel quickly if they want
    if is_admin(user.id):
        if (update.message.text or "").strip().lower() == "admin":
            await update.message.reply_text(
                "🔐 Admin Panel",
                reply_markup=admin_menu(),
            )
        else:
            # do nothing / or show admin panel always:
            await update.message.reply_text(
                "🔐 Admin Panel",
                reply_markup=admin_menu(),
            )
        return

    # Customer: always show home after clearing keyboard
    await send_customer_home(context, user.id)


# ─────────────────────────────
# CALLBACKS
# ─────────────────────────────

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    if not is_admin(uid):
        return

    # ─── Pending topups with inline approve/reject
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
                f"Top-up ID: {r['id']}\nUser: {r['user_id']}\nAmount: ₱{r['amount']}",
                reply_markup=kb
            )

    elif q.data.startswith("approve_"):
        tid = int(q.data.split("_")[1])
        t = fetch_one("SELECT * FROM topups WHERE id=%s", (tid,))
        if not t:
            await q.message.reply_text("Top-up not found.")
            return

        execute("UPDATE topups SET status='approved' WHERE id=%s", (tid,))
        execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (t["amount"], t["user_id"]))

        await context.bot.send_message(
            chat_id=t["user_id"],
            text=f"✅ Your top-up of ₱{t['amount']} has been approved!"
        )
        await q.message.reply_text("✅ Approved.")

    elif q.data.startswith("reject_"):
        tid = int(q.data.split("_")[1])
        t = fetch_one("SELECT * FROM topups WHERE id=%s", (tid,))
        execute("UPDATE topups SET status='rejected' WHERE id=%s", (tid,))
        if t:
            await context.bot.send_message(
                chat_id=t["user_id"],
                text=f"❌ Your top-up of ₱{t['amount']} was rejected."
            )
        await q.message.reply_text("❌ Rejected.")

    elif q.data == "admin_purchases":
        rows = fetch_all("""
            SELECT user_id, total_price, created_at
            FROM purchases
            ORDER BY id DESC
            LIMIT 20
        """)
        if not rows:
            await q.message.reply_text("No purchases yet.")
            return

        msg = "🧾 Purchases (last 20)\n\n"
        for r in rows:
            msg += f"{r['user_id']} — ₱{r['total_price']} — {r['created_at']}\n"
        await q.message.reply_text(msg)

    elif q.data == "admin_users":
        rows = fetch_all("SELECT user_id, username, balance FROM users ORDER BY id DESC LIMIT 50")
        if not rows:
            await q.message.reply_text("No users yet.")
            return

        msg = "👥 Users (last 50)\n\n"
        for r in rows:
            uname = f"@{r['username']}" if r["username"] else "(no username)"
            msg += f"{r['user_id']} {uname} — ₱{r['balance']}\n"
        await q.message.reply_text(msg)

    else:
        await q.message.reply_text("⚠️ This admin button is not implemented yet.")


# ─────────────────────────────
# MAIN
# ─────────────────────────────

def main():
    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clearkb", clearkb))

    # IMPORTANT: catch ANY text (old keyboard presses are text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, any_text))

    app.add_handler(CallbackQueryHandler(callbacks))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
