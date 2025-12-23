import os
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from db import (
    ensure_schema, ensure_user, fetch_one, fetch_all, exec_sql,
    set_setting, get_setting
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

# -------------------- MAIN MENU (PERMANENT KEYBOARD) --------------------
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🏠 Home", "🛍 Shop"],
        ["👤 My Account", "📩 Reseller Register"],
        ["💬 Chat Admin", "🆘 Help"],
        ["🔐 Admin"],
    ],
    resize_keyboard=True
)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def money(n: int) -> str:
    return f"₱{n:,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass


# ============================================================
# SETTINGS (TEXT + THUMBNAILS)
# ============================================================
TEXT_KEYS = {
    "WELCOME_TEXT": "Welcome / Home text",
    "SHOP_CATS_TEXT": "Shop: Categories header",
    "SHOP_PRODS_TEXT": "Shop: Products header",
    "SHOP_VARIANTS_TEXT": "Shop: Packages header",
    "NO_PRODUCTS_TEXT": "Shop: No products text",
    "NO_VARIANTS_TEXT": "Shop: No packages text",
    "HELP_TEXT": "Help text",
    "CHAT_ADMIN_TEXT": "Chat Admin text",
    "ADMIN_PANEL_TEXT": "Admin panel intro text",
}

THUMB_KEYS = {
    "START_THUMB_FILE_ID": "Start/Home thumbnail",
    "SHOP_THUMB_FILE_ID": "Shop thumbnail",
    "HELP_THUMB_FILE_ID": "Help thumbnail",
    "ADMIN_THUMB_FILE_ID": "Admin thumbnail",
}

DEFAULT_TEXT = {
    "WELCOME_TEXT": "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
    "SHOP_CATS_TEXT": "🛍 **Shop Categories**",
    "SHOP_PRODS_TEXT": "📦 **Select a product:**",
    "SHOP_VARIANTS_TEXT": "🧾 **Choose a package:**",
    "NO_PRODUCTS_TEXT": "❌ No products yet in this category.",
    "NO_VARIANTS_TEXT": "❌ No packages yet.",
    "HELP_TEXT": (
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse\n"
        "• Buy = auto delivery\n"
        "• **Reseller Register** = apply for reseller\n"
    ),
    "CHAT_ADMIN_TEXT": "💬 Chat admin here:\n@lovebylunaa",
    "ADMIN_PANEL_TEXT": "🔐 **Admin Panel**\nChoose what you want to edit:",
}


# ============================================================
# HOME / START
# ============================================================
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    welcome = get_setting("WELCOME_TEXT") or DEFAULT_TEXT["WELCOME_TEXT"]
    thumb = get_setting("START_THUMB_FILE_ID")

    msg = update.message if update.message else update.callback_query.message

    if thumb:
        await msg.reply_photo(
            photo=thumb,
            caption=welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=MAIN_MENU
        )
    else:
        await msg.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_MENU)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)

async def home_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_home(update, context)


# ============================================================
# ACCOUNT / POINTS
# ============================================================
async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    u = fetch_one("SELECT user_id, username, joined_at, points, is_reseller FROM users WHERE user_id=%s", (user.id,))
    purchases = fetch_one("SELECT COUNT(*) AS c FROM purchases WHERE user_id=%s", (user.id,))
    count = int(purchases["c"]) if purchases else 0

    username = f"@{u['username']}" if u and u.get("username") else "(no username)"
    joined = u["joined_at"].strftime("%Y-%m-%d %H:%M") if u else "—"
    points = int(u["points"]) if u else 0
    reseller = "✅ YES" if u and u["is_reseller"] else "❌ NO"

    text = (
        "👤 **My Account**\n\n"
        f"Username: **{username}**\n"
        f"User ID: `{user.id}`\n"
        f"Joined: **{joined}**\n"
        f"Purchases: **{count}**\n"
        f"Points: **{points}**\n"
        f"Reseller: **{reseller}**"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ============================================================
# HELP / CHAT ADMIN
# ============================================================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_setting("HELP_TEXT") or DEFAULT_TEXT["HELP_TEXT"]
    thumb = get_setting("HELP_THUMB_FILE_ID")

    if thumb:
        await update.message.reply_photo(photo=thumb, caption=text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_setting("CHAT_ADMIN_TEXT") or DEFAULT_TEXT["CHAT_ADMIN_TEXT"]
    await update.message.reply_text(text)


# ============================================================
# SHOP UI (CLEAN BACK = EDIT SAME MESSAGE)
# ============================================================
async def shop_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]

    header = get_setting("SHOP_CATS_TEXT") or DEFAULT_TEXT["SHOP_CATS_TEXT"]
    thumb = get_setting("SHOP_THUMB_FILE_ID")

    if thumb:
        await update.message.reply_photo(
            photo=thumb,
            caption=header,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows)
        )
    else:
        await update.message.reply_text(
            header,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows)
        )

async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    if action == "cat":
        cat_id = int(parts[2])
        context.user_data["shop_cat_id"] = cat_id

        prods = fetch_all("""
            SELECT id,name,description FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id
        """, (cat_id,))

        if not prods:
            await q.message.edit_text(
                get_setting("NO_PRODUCTS_TEXT") or DEFAULT_TEXT["NO_PRODUCTS_TEXT"],
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅ Back", callback_data="shop:back")]
                ])
            )
            return

        header = get_setting("SHOP_PRODS_TEXT") or DEFAULT_TEXT["SHOP_PRODS_TEXT"]
        rows = [[InlineKeyboardButton(p["name"], callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:back")])

        await q.message.edit_text(header, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "prod":
        pid = int(parts[2])
        prod = fetch_one("SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE", (pid,))
        if not prod:
            await q.message.edit_text("Product not found.")
            return

        vars_ = fetch_all("""
            SELECT id,name,price FROM variants
            WHERE product_id=%s AND is_active=TRUE
            ORDER BY id
        """, (pid,))

        if not vars_:
            # back to products list
            cat_id = context.user_data.get("shop_cat_id")
            back_cb = f"shop:cat:{cat_id}" if cat_id else "shop:back"
            await q.message.edit_text(
                get_setting("NO_VARIANTS_TEXT") or DEFAULT_TEXT["NO_VARIANTS_TEXT"],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data=back_cb)]])
            )
            return

        header = get_setting("SHOP_VARIANTS_TEXT") or DEFAULT_TEXT["SHOP_VARIANTS_TEXT"]

        rows = []
        for v in vars_:
            stock = fetch_one("SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE", (v["id"],))
            s = int(stock["c"]) if stock else 0
            rows.append([InlineKeyboardButton(
                f"{v['name']} — {money(int(v['price']))} (Stock: {s})",
                callback_data=f"buy:{v['id']}"
            )])

        # back to product list
        cat_id = context.user_data.get("shop_cat_id")
        back_cb = f"shop:cat:{cat_id}" if cat_id else "shop:back"
        rows.append([InlineKeyboardButton("⬅ Back", callback_data=back_cb)])

        text = f"🧾 **{prod['name']}**\n\n{prod['description']}\n\n{header}"
        await q.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "back":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        header = get_setting("SHOP_CATS_TEXT") or DEFAULT_TEXT["SHOP_CATS_TEXT"]
        await q.message.edit_text(header, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return


# ============================================================
# BUY + AUTO DELIVERY
# ============================================================
async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    variant_id = int(q.data.split(":")[1])
    v = fetch_one("""
        SELECT v.id, v.name, v.price,
               p.name AS product_name
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.message.reply_text("Package not found.")
        return

    with_row = fetch_one("""
        SELECT id, file_id FROM file_stocks
        WHERE variant_id=%s AND is_sold=FALSE
        ORDER BY id
        LIMIT 1
    """, (variant_id,))
    if not with_row:
        await q.message.reply_text("❌ Out of stock.")
        return

    price = int(v["price"])
    stock_id = int(with_row["id"])
    file_id = with_row["file_id"]

    exec_sql("""
        UPDATE file_stocks SET is_sold=TRUE, sold_to=%s, sold_at=NOW()
        WHERE id=%s AND is_sold=FALSE
    """, (user.id, stock_id))

    exec_sql("INSERT INTO purchases(user_id, variant_id, price) VALUES(%s,%s,%s)", (user.id, variant_id, price))
    exec_sql("UPDATE users SET points=points+1 WHERE user_id=%s", (user.id,))

    await context.bot.send_document(
        chat_id=user.id,
        document=file_id,
        caption=f"✅ Delivery: {v['product_name']} — {v['name']}\nPrice: {money(price)}"
    )
    await q.message.reply_text("✅ Purchase delivered!")


# ============================================================
# RESELLER REGISTRATION FORM + ADMIN EDIT TEXT MODE
# ============================================================
async def reseller_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reseller_step"] = 1
    await update.message.reply_text(
        "📩 **Reseller Registration**\n\nSend your **Full Name**:",
        parse_mode=ParseMode.MARKDOWN
    )

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ADMIN TEXT EDIT MODE
    if context.user_data.get("edit_text_key"):
        key = context.user_data.pop("edit_text_key")
        set_setting(key, update.message.text.strip())
        await update.message.reply_text(f"✅ Updated: {TEXT_KEYS.get(key, key)}")
        return

    # RESELLER FORM MODE
    if "reseller_step" not in context.user_data:
        return

    step = context.user_data["reseller_step"]
    text = update.message.text.strip()

    if step == 1:
        context.user_data["res_full_name"] = text
        context.user_data["reseller_step"] = 2
        await update.message.reply_text("Send your **Contact** (Telegram/FB/etc):", parse_mode=ParseMode.MARKDOWN)
        return

    if step == 2:
        context.user_data["res_contact"] = text
        context.user_data["reseller_step"] = 3
        await update.message.reply_text("Send your **Shop Link** (FB page / Telegram channel / etc):", parse_mode=ParseMode.MARKDOWN)
        return

    if step == 3:
        user = update.effective_user
        full_name = context.user_data.pop("res_full_name")
        contact = context.user_data.pop("res_contact")
        shop_link = text
        context.user_data.pop("reseller_step", None)

        exec_sql("""
            INSERT INTO reseller_applications(user_id, username, full_name, contact, shop_link)
            VALUES(%s,%s,%s,%s,%s)
        """, (user.id, user.username, full_name, contact, shop_link))

        await update.message.reply_text("✅ Submitted! Waiting for admin approval 💗")

        for admin_id in ADMIN_IDS:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"admin:res:approve:{user.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"admin:res:reject:{user.id}")
            ]])
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📩 New Reseller Application\n\n"
                    f"User: @{user.username} (`{user.id}`)\n"
                    f"Name: {full_name}\n"
                    f"Contact: {contact}\n"
                    f"Shop: {shop_link}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )


# ============================================================
# ADMIN PANEL (EDIT TEXTS + SET THUMBNAILS)
# ============================================================
def admin_panel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Texts", callback_data="adminpanel:texts")],
        [InlineKeyboardButton("🖼 Set Thumbnails", callback_data="adminpanel:thumbs")],
        [InlineKeyboardButton("⬅ Close", callback_data="adminpanel:close")],
    ])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return

    text = get_setting("ADMIN_PANEL_TEXT") or DEFAULT_TEXT["ADMIN_PANEL_TEXT"]
    thumb = get_setting("ADMIN_THUMB_FILE_ID")

    if thumb:
        await update.message.reply_photo(photo=thumb, caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_panel_kb())
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_panel_kb())

async def cb_adminpanel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    parts = q.data.split(":")
    action = parts[1]

    if action == "texts":
        rows = []
        for key, label in TEXT_KEYS.items():
            rows.append([InlineKeyboardButton(label, callback_data=f"adminpanel:settext:{key}")])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="adminpanel:back")])
        await q.message.edit_text("✏️ Choose text to edit:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "settext":
        key = parts[2]
        context.user_data["edit_text_key"] = key
        current = get_setting(key) or DEFAULT_TEXT.get(key, "")
        await q.message.edit_text(
            f"✏️ Editing: **{TEXT_KEYS.get(key, key)}**\n\nCurrent:\n{current}\n\nSend the new text now:",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if action == "thumbs":
        rows = []
        for key, label in THUMB_KEYS.items():
            rows.append([InlineKeyboardButton(label, callback_data=f"adminpanel:setthumb:{key}")])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="adminpanel:back")])
        await q.message.edit_text("🖼 Choose thumbnail to set:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "setthumb":
        key = parts[2]
        context.user_data["await_thumb_key"] = key
        await q.message.edit_text(
            f"🖼 Send the PHOTO now for:\n**{THUMB_KEYS.get(key, key)}**",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if action == "back":
        text = get_setting("ADMIN_PANEL_TEXT") or DEFAULT_TEXT["ADMIN_PANEL_TEXT"]
        await q.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_panel_kb())
        return

    if action == "close":
        await q.message.edit_text("✅ Closed.")
        return


async def capture_thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    key = context.user_data.get("await_thumb_key")
    if not key:
        return

    if not update.message.photo:
        await update.message.reply_text("Send as PHOTO please.")
        return

    file_id = update.message.photo[-1].file_id
    set_setting(key, file_id)
    context.user_data.pop("await_thumb_key", None)
    await update.message.reply_text(f"✅ Thumbnail saved: {THUMB_KEYS.get(key, key)}")


# ============================================================
# ADMIN: approve/reject reseller
# ============================================================
async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    data = q.data.split(":")
    if len(data) < 4:
        return

    if data[1] == "res":
        action = data[2]
        uid = int(data[3])

        if action == "approve":
            exec_sql("UPDATE users SET is_reseller=TRUE WHERE user_id=%s", (uid,))
            exec_sql("""
                UPDATE reseller_applications
                SET status='APPROVED', decided_at=NOW(), admin_id=%s
                WHERE user_id=%s AND status='PENDING'
            """, (q.from_user.id, uid))
            await context.bot.send_message(uid, "✅ Your reseller application is APPROVED! 💗")
            await q.message.reply_text("✅ Approved.")
            return

        if action == "reject":
            exec_sql("""
                UPDATE reseller_applications
                SET status='REJECTED', decided_at=NOW(), admin_id=%s
                WHERE user_id=%s AND status='PENDING'
            """, (q.from_user.id, uid))
            await context.bot.send_message(uid, "❌ Your reseller application was rejected.")
            await q.message.reply_text("❌ Rejected.")
            return

# -------------------- ADMIN: GET FILE ID --------------------
async def fileid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only.")
        return

    await update.message.reply_text(
        "📎 Send the file now as a *DOCUMENT*.\n"
        "I will reply with its file_id.",
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["await_fileid"] = True


async def capture_fileid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.user_data.get("await_fileid"):
        return
    if not update.message.document:
        await update.message.reply_text("❌ Please send as DOCUMENT.")
        return

    file_id = update.message.document.file_id
    context.user_data["await_fileid"] = False

    await update.message.reply_text(
        f"✅ **File ID captured:**\n\n`{file_id}`\n\n"
        "Copy this and paste it into PostgreSQL.",
        parse_mode=ParseMode.MARKDOWN
    )


# ============================================================
# BOOT
# ============================================================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))

    # Menu buttons
    app.add_handler(MessageHandler(filters.Regex(r"^🏠 Home$"), home_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop_btn))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 My Account$"), my_account))
    app.add_handler(MessageHandler(filters.Regex(r"^📩 Reseller Register$"), reseller_register))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Chat Admin$"), chat_admin))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))
    app.add_handler(CallbackQueryHandler(cb_adminpanel, pattern=r"^adminpanel:"))

    # Admin photo capture for thumbnails
    app.add_handler(MessageHandler(filters.PHOTO, capture_thumb))

    # Text handler (reseller form + admin edit texts)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # Admin: get Telegram file_id
app.add_handler(CommandHandler("fileid", fileid_cmd))
app.add_handler(MessageHandler(filters.Document.ALL, capture_fileid))


    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
