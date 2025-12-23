import os
import logging
import uuid

GCASH_QR_FILE_ID = os.getenv("GCASH_QR_FILE_ID", "")
GOTYME_QR_FILE_ID = os.getenv("GOTYME_QR_FILE_ID", "")

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from db import (
    ensure_schema, ensure_user, fetch_one, fetch_all, exec_sql,
    set_setting, get_setting, purchase_variant
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# 🔴 HARD FIX: ensure admin IDs load correctly
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

if not ADMIN_IDS:
    log.error("❌ ADMIN_IDS is EMPTY. Admin button will NEVER work.")

# ---------------- HELPERS ----------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

def main_menu(uid: int):
    rows = [
        ["Home", "Shop"],
        ["My Account", "Add Balance"],
        ["Reseller Register", "Chat Admin"],
        ["Help"],
    ]
    if is_admin(uid):
        rows.append(["Admin"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# ---------------- START / HOME ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    text = get_setting("TEXT_HOME") or (
        "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:"
    )

    thumb = get_setting("THUMB_HOME")

    if thumb:
        await update.message.reply_photo(
            photo=thumb,
            caption=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(user.id)
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(user.id)
        )

# ---------------- ADMIN PANEL ----------------
def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Pending Topups", callback_data="admin:topups")],
        [InlineKeyboardButton("👥 Users", callback_data="admin:users")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin:purchases")],
    ])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_admin(user.id):
        await update.message.reply_text("❌ Access denied.")
        return

    text = (
        "🔐 **Admin Panel**\n\n"
        "Commands:\n"
        "• `/settext <panel> <text>`\n"
        "• `/setthumb <panel>`\n"
        "• `/setpay <gcash|gotyme>`\n"
        "• `/fileid` (reply to media)\n\n"
        "Panels:\n"
        "`home, shop, help, account, addbal, admin`"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_keyboard()
    )

# ---------------- ADMIN CALLBACKS ----------------
async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    action = q.data.split(":")[1]

    if action == "topups":
        rows = fetch_all("""
            SELECT id,user_id,amount,status
            FROM topup_requests
            WHERE status='PENDING'
            ORDER BY id DESC
        """)
        if not rows:
            await q.message.reply_text("✅ No pending topups.")
            return

        msg = ["💰 **Pending Topups**\n"]
        for r in rows:
            msg.append(
                f"• #{r['id']} | `{r['user_id']}` | {money(r['amount'])}"
            )
        await q.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN)

    if action == "users":
        rows = fetch_all("""
            SELECT user_id,username,balance,points,is_reseller
            FROM users
            ORDER BY joined_at DESC
            LIMIT 30
        """)
        msg = ["👥 **Users**\n"]
        for r in rows:
            msg.append(
                f"`{r['user_id']}` @{r['username']} | "
                f"Bal {money(r['balance'])} | "
                f"Pts {r['points']} | "
                f"{'Reseller' if r['is_reseller'] else 'User'}"
            )
        await q.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN)

    if action == "purchases":
        rows = fetch_all("""
            SELECT user_id,total_price,created_at
            FROM purchases
            ORDER BY id DESC
            LIMIT 10
        """)
        msg = ["🧾 **Last Purchases**\n"]
        for r in rows:
            msg.append(
                f"`{r['user_id']}` paid {money(r['total_price'])} @ {r['created_at']}"
            )
        await q.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN)

# ---------------- TEXT ROUTER (FIXED) ----------------
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip().lower()

    ensure_user(user.id, user.username)

    # 🔴 ADMIN MUST BE FIRST
    if text == "admin":
        await admin_cmd(update, context)
        return

    if text == "home":
        await start_cmd(update, context)
        return

    if text == "shop":
        await update.message.reply_text("🛍 Shop loading…")
        return

    if text == "my account":
        await update.message.reply_text("👤 Account loading…")
        return

    if text == "add balance":
        await update.message.reply_text("💰 Add balance loading…")
        return

    if text == "help":
        await update.message.reply_text("🆘 Help loading…")
        return

    if text == "chat admin":
        await update.message.reply_text("@lovebylunaa")
        return

# ---------------- BOOT ----------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))

    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
