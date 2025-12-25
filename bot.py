import os
import logging
import uuid

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from db import (
    ensure_schema, ensure_user, fetch_one, fetch_all,
    exec_sql, set_setting, get_setting, purchase_variant
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

# --------------------------------------------------------------------
# UTILITIES
# --------------------------------------------------------------------

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{int(n):,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

# Removes old bottom keyboard once
async def kill_reply_keyboard_once(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    key = f"kb_removed:{chat_id}"
    if context.bot_data.get(key):
        return
    context.bot_data[key] = True
    await update.effective_chat.send_message("✅", reply_markup=ReplyKeyboardRemove())

# --------------------------------------------------------------------
# MENUS
# --------------------------------------------------------------------
def inline_main_menu(uid: int):
    rows = [
        [InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
         InlineKeyboardButton("🛍 Shop", callback_data="nav:shop")],
        [InlineKeyboardButton("👤 My Account", callback_data="nav:account"),
         InlineKeyboardButton("💰 Add Balance", callback_data="nav:addbal")],
        [InlineKeyboardButton("📩 Reseller Register", callback_data="nav:reseller"),
         InlineKeyboardButton("💬 Chat Admin", callback_data="nav:chatadmin")],
        [InlineKeyboardButton("🆘 Help", callback_data="nav:help")]
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🔐 Admin", callback_data="nav:admin")])
    return InlineKeyboardMarkup(rows)

# --------------------------------------------------------------------
# START
# --------------------------------------------------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await kill_reply_keyboard_once(update, context)
    user = update.effective_user
    ensure_user(user.id, user.username)
    text = get_setting("TEXT_HOME") or "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:"
    thumb = get_setting("THUMB_HOME")

    if thumb:
        await update.message.reply_photo(photo=thumb, caption=text, parse_mode=ParseMode.MARKDOWN,
                                         reply_markup=inline_main_menu(user.id))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=inline_main_menu(user.id))

# --------------------------------------------------------------------
# INLINE CALLBACKS
# --------------------------------------------------------------------
async def cb_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)
    where = q.data.split(":")[1]

    if where == "home":
        await q.message.reply_text("🏠 Returning to menu...", reply_markup=ReplyKeyboardRemove())
        await start_cmd(update, context)

    elif where == "shop":
        await q.message.reply_text("🛍 Shop loading...")

    elif where == "account":
        await q.message.reply_text("👤 My Account loading...")

    elif where == "addbal":
        await q.message.reply_text("💰 Add Balance loading...")

    elif where == "help":
        await q.message.reply_text("🆘 Help loading...")

    elif where == "reseller":
        await q.message.reply_text("📩 Reseller Register loading...")

    elif where == "chatadmin":
        await q.message.reply_text("@lovebylunaa")

    elif where == "admin":
        if not is_admin(q.from_user.id):
            await q.message.reply_text("❌ Access denied.")
            return
        await admin_cmd(update, context)

# --------------------------------------------------------------------
# ADMIN PANEL
# --------------------------------------------------------------------
def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Pending Topups", callback_data="admin:topups")],
        [InlineKeyboardButton("👥 Users", callback_data="admin:users")],
        [InlineKeyboardButton("🧾 Purchases", callback_data="admin:purchases")]
    ])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Access denied.")
        return

    text = (
        "🔐 <b>Admin Panel</b>\n\n"
        "Commands:\n"
        "• /settext &lt;panel&gt; &lt;text&gt;\n"
        "• /setthumb &lt;panel&gt; (send photo)\n"
        "• /setpay &lt;gcash|gotyme&gt;\n"
        "• /announce &lt;text&gt;\n"
        "• /fileid (reply to media)\n\n"
        "Panels:\n"
        "<code>home, shop, help, account, addbal, admin</code>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())

# --------------------------------------------------------------------
# ADMIN CALLBACKS
# --------------------------------------------------------------------
async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return
    action = q.data.split(":")[1]

    if action == "topups":
        rows = fetch_all("SELECT id,user_id,amount,status FROM topup_requests WHERE status='PENDING' ORDER BY id DESC")
        if not rows:
            await q.message.reply_text("✅ No pending topups.")
            return
        msg = ["💰 <b>Pending Topups</b>"]
        for r in rows:
            msg.append(f"#{r['id']} — {r['user_id']} — ₱{r['amount']} — {r['status']}")
        await q.message.reply_text("\n".join(msg), parse_mode=ParseMode.HTML)
    elif action == "users":
        users = fetch_all("SELECT user_id,username,balance FROM users ORDER BY id DESC LIMIT 10")
        lines = ["👥 <b>Users</b>"]
        for u in users:
            lines.append(f"• {u['user_id']} — @{u['username']} — ₱{u['balance']}")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    elif action == "purchases":
        rows = fetch_all("SELECT user_id,total_price,created_at FROM purchases ORDER BY id DESC LIMIT 10")
        msg = ["🧾 <b>Last Purchases</b>"]
        for r in rows:
            msg.append(f"{r['user_id']} — ₱{r['total_price']} — {r['created_at']}")
        await q.message.reply_text("\n".join(msg), parse_mode=ParseMode.HTML)

# --------------------------------------------------------------------
# TEXT ROUTER (for old buttons)
# --------------------------------------------------------------------
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await kill_reply_keyboard_once(update, context)
    t = (update.message.text or "").strip().lower()
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username)

    if t == "admin" and is_admin(uid):
        await admin_cmd(update, context)
        return

    if t in ("home", "shop", "my account", "add balance", "help",
             "chat admin", "reseller register"):
        await update.message.reply_text("✅ Menu refreshed.", reply_markup=inline_main_menu(uid))
        return

# --------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    ensure_schema()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(cb_nav, pattern=r"^nav:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
