import os
import random
import string
import logging
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

ADMIN_USERNAMES = [u.strip().lstrip("@").lower() for u in os.getenv("ADMIN_USERNAMES", "lovebylunaa").split(",") if u.strip()]

AMOUNTS = [50, 100, 300, 500, 1000]

def is_admin(user) -> bool:
    username = (user.username or "").lower()
    return username in ADMIN_USERNAMES

def topup_id():
    # SHOPNLUNA-6-TU3SM4 style
    part1 = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    part2 = str(random.randint(1, 9))
    return f"SHOPNLUNA-{part2}-{part1}"

def money(n: int) -> str:
    return f"₱{n}"

def main_menu_kb(is_admin_user: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("📦 List Produk"), KeyboardButton("💰 Balance")],
        [KeyboardButton("➕ Add Balance"), KeyboardButton("💬 Chat Admin")],
    ]
    if is_admin_user:
        rows.append([KeyboardButton("🛠️ Admin Panel")])
    rows.append([KeyboardButton("❌ Cancel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username)
    u = db.get_user(user.id)
    welcome_tpl = db.get_setting("welcome_text")
    text = welcome_tpl.format(balance=u["balance"])
    await update.message.reply_text(
        text,
        reply_markup=main_menu_kb(is_admin(user)),
        parse_mode=ParseMode.MARKDOWN
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Cancelled.", reply_markup=main_menu_kb(is_admin(update.effective_user)))

async def list_produk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = db.list_products(active_only=True)
    header = db.get_setting("product_list_header")
    if not products:
        await update.message.reply_text(db.get_setting("no_products_text"))
        return

    lines = [header, ""]
    for p in products:
        lines.append(f"*{p['product_id']}* — {p['name']}")
        if p["description"]:
            lines.append(f"_{p['description']}_")
        lines.append(f"Price: {money(p['price'])} | Stock: {p['stock']}")
        lines.append("")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    await update.message.reply_text(f"💰 Your balance: {money(u['balance'])}")

async def chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_username = ADMIN_USERNAMES[0] if ADMIN_USERNAMES else "lovebylunaa"
    txt = db.get_setting("chat_admin_text").format(admin_username=admin_username)
    await update.message.reply_text(txt)

async def add_balance_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # show amount buttons inline
    admin_username = ADMIN_USERNAMES[0] if ADMIN_USERNAMES else "lovebylunaa"
    intro = db.get_setting("add_balance_intro").format(admin_username=admin_username)

    kb = []
    row = []
    for a in AMOUNTS:
        row.append(InlineKeyboardButton(money(a), callback_data=f"topup:amount:{a}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="topup:cancel")])

    await update.message.reply_text(intro, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        await update.message.reply_text("❌ Admin only.")
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Products", callback_data="admin:products")],
        [InlineKeyboardButton("💳 Topups", callback_data="admin:topups")],
        [InlineKeyboardButton("🧾 Orders", callback_data="admin:orders")],
        [InlineKeyboardButton("👤 Users", callback_data="admin:users")],
        [InlineKeyboardButton("🛠️ Edit Bot Texts", callback_data="admin:settings")],
        [InlineKeyboardButton("📢 Make Announcement", callback_data="admin:announce")],
    ])
    await update.message.reply_text("🛠️ Admin Panel — choose:", reply_markup=kb)

# ---------- Admin flows (state machine using context.user_data) ----------

def set_state(context: ContextTypes.DEFAULT_TYPE, state: str, payload: dict | None = None):
    context.user_data["state"] = state
    context.user_data["payload"] = payload or {}

def get_state(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("state"), context.user_data.get("payload", {})

async def ask_text(update: Update, prompt: str):
    await update.effective_message.reply_text(prompt, parse_mode=ParseMode.MARKDOWN)

# ---------- Callbacks ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    data = q.data

    # TOPUP FLOW
    if data == "topup:cancel":
        context.user_data.pop("pending_topup", None)
        await q.message.reply_text("✅ Cancelled topup.")
        return

    if data.startswith("topup:amount:"):
        amt = int(data.split(":")[-1])
        context.user_data["pending_topup_amount"] = amt
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("GCash", callback_data="topup:method:gcash"),
             InlineKeyboardButton("GoTyme", callback_data="topup:method:gotyme")],
            [InlineKeyboardButton("❌ Cancel", callback_data="topup:cancel")]
        ])
        await q.message.reply_text(f"Amount: {money(amt)}\nChoose payment method:", reply_markup=kb)
        return

    if data.startswith("topup:method:"):
        method = data.split(":")[-1]
        amt = context.user_data.get("pending_topup_amount")
        if not amt:
            await q.message.reply_text("❌ Please choose amount again.")
            return

        tid = topup_id()
        db.create_topup(tid, user.id, amt, method.upper())
        context.user_data["pending_topup_id"] = tid

        admin_username = ADMIN_USERNAMES[0] if ADMIN_USERNAMES else "lovebylunaa"
        instructions = db.get_setting("topup_instructions").format(admin_username=admin_username)

        # show QR
        if method == "gcash":
            file_id = db.get_setting("gcash_qr_file_id")
            caption = db.get_setting("gcash_caption").format(admin_username=admin_username)
        else:
            file_id = db.get_setting("gotyme_qr_file_id")
            caption = db.get_setting("gotyme_caption").format(admin_username=admin_username)

        await q.message.reply_text(f"Topup ID: *{tid}*\nMethod: *{method.upper()}*\nAmount: *{money(amt)}*", parse_mode=ParseMode.MARKDOWN)

        if file_id:
            await q.message.reply_photo(photo=file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
        else:
            await q.message.reply_text("⚠️ QR not set yet. Please contact admin.")

        await q.message.reply_text(instructions, parse_mode=ParseMode.MARKDOWN)
        return

    # ADMIN PANEL
    if data.startswith("admin:"):
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        section = data.split(":", 1)[1]

        if section == "products":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Product", callback_data="adminprod:add")],
                [InlineKeyboardButton("📄 List Products", callback_data="adminprod:list")],
                [InlineKeyboardButton("✏️ Edit Product", callback_data="adminprod:edit")],
                [InlineKeyboardButton("🗑️ Delete Product", callback_data="adminprod:delete")],
            ])
            await q.message.reply_text("📦 Products:", reply_markup=kb)
            return

        if section == "topups":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧾 Pending Topups", callback_data="admintopup:pending")],
                [InlineKeyboardButton("📜 Topup History (soon)", callback_data="noop")],
            ])
            await q.message.reply_text("💳 Topups:", reply_markup=kb)
            return

        if section == "orders":
            await q.message.reply_text("🧾 Orders: (you can add later if you want full checkout/delivery flow)")
            return

        if section == "users":
            await q.message.reply_text("👤 Users: (you can add later if you want user search, manual balance edit)")
            return

        if section == "settings":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Welcome Text", callback_data="adminset:welcome_text")],
                [InlineKeyboardButton("✏️ Product List Header", callback_data="adminset:product_list_header")],
                [InlineKeyboardButton("✏️ Add Balance Intro", callback_data="adminset:add_balance_intro")],
                [InlineKeyboardButton("✏️ Topup Instructions", callback_data="adminset:topup_instructions")],
                [InlineKeyboardButton("✏️ GCash Caption", callback_data="adminset:gcash_caption")],
                [InlineKeyboardButton("✏️ GoTyme Caption", callback_data="adminset:gotyme_caption")],
                [InlineKeyboardButton("🖼️ Set GCash QR", callback_data="adminset:gcash_qr")],
                [InlineKeyboardButton("🖼️ Set GoTyme QR", callback_data="adminset:gotyme_qr")],
            ])
            await q.message.reply_text("🛠️ Edit Bot Texts / QR:", reply_markup=kb)
            return

        if section == "announce":
            set_state(context, "ADMIN_ANNOUNCE")
            await q.message.reply_text("📢 Send your announcement text now (it will be sent to all users).")
            return

    # ADMIN SETTINGS EDIT
    if data.startswith("adminset:"):
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        key = data.split(":", 1)[1]

        if key in {"gcash_qr", "gotyme_qr"}:
            set_state(context, "ADMIN_SET_QR", {"which": key})
            await q.message.reply_text("Send the QR as PHOTO (not file). You can add caption too.")
            return

        # text keys
        set_state(context, "ADMIN_SET_TEXT", {"key": key})
        current = db.get_setting(key)
        await q.message.reply_text(f"Current `{key}`:\n\n{current}\n\nNow send the new text.", parse_mode=ParseMode.MARKDOWN)
        return

    # ADMIN TOPUP
    if data == "admintopup:pending":
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        pending = db.list_pending_topups(limit=50)
        if not pending:
            await q.message.reply_text("✅ No pending topups.")
            return

        for t in pending:
            uname = t.get("username") or "(no username)"
            msg = (
                f"🧾 *PENDING TOPUP*\n"
                f"Topup ID: *{t['topup_id']}*\n"
                f"User: `{t['user_id']}` (@{uname})\n"
                f"Amount: *{money(t['amount'])}*\n"
                f"Method: *{t['method']}*\n"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"topupapprove:{t['topup_db_id']}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"topupreject:{t['topup_db_id']}")
                ]
            ])
            await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    if data.startswith("topupapprove:"):
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        topup_db_id = int(data.split(":")[-1])
        t = db.approve_topup(topup_db_id, user.id)
        if not t:
            await q.message.reply_text("❌ Not found or already handled.")
            return
        await q.message.reply_text(f"✅ Approved {t['topup_id']} (+{money(t['amount'])})")

        # notify customer
        try:
            await context.bot.send_message(
                chat_id=t["user_id"],
                text=f"✅ Topup approved!\nTopup ID: {t['topup_id']}\nAmount: {money(t['amount'])}"
            )
        except Exception as e:
            log.warning("Notify user failed: %s", e)
        return

    if data.startswith("topupreject:"):
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        topup_db_id = int(data.split(":")[-1])
        t = db.reject_topup(topup_db_id, user.id)
        if not t:
            await q.message.reply_text("❌ Not found or already handled.")
            return
        await q.message.reply_text(f"❌ Rejected {t['topup_id']}")

        try:
            await context.bot.send_message(
                chat_id=t["user_id"],
                text=f"❌ Topup rejected.\nTopup ID: {t['topup_id']}\nIf you believe this is a mistake, DM admin."
            )
        except Exception as e:
            log.warning("Notify user failed: %s", e)
        return

    # Products (minimal)
    if data == "adminprod:list":
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        products = db.list_products(active_only=False)
        if not products:
            await q.message.reply_text("No products yet.")
            return
        lines = ["📦 *All Products*", ""]
        for p in products:
            lines.append(f"*{p['product_id']}* — {p['name']} | {money(p['price'])} | stock {p['stock']} | active {p['active']}")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if data == "adminprod:add":
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        set_state(context, "ADMIN_ADD_PRODUCT", {})
        await q.message.reply_text(
            "➕ Add product:\nSend like this:\n\n"
            "`name | price | stock | description`\n\n"
            "Example:\n`Netflix 1 month | 100 | 10 | Premium slot`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "adminprod:edit":
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        set_state(context, "ADMIN_EDIT_PRODUCT_ID", {})
        await q.message.reply_text("✏️ Send product_id to edit (example: `3`)", parse_mode=ParseMode.MARKDOWN)
        return

    if data == "adminprod:delete":
        if not is_admin(user):
            await q.message.reply_text("❌ Admin only.")
            return
        set_state(context, "ADMIN_DELETE_PRODUCT", {})
        await q.message.reply_text("🗑️ Send product_id to delete (example: `3`)", parse_mode=ParseMode.MARKDOWN)
        return

    if data == "noop":
        await q.message.reply_text("Coming soon.")
        return

# ---------- Text menu handler ----------

async def on_text_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    if text == "/start":
        await start(update, context); return
    if text == "/menu":
        await menu(update, context); return

    if text == "📦 List Produk":
        await list_produk(update, context); return
    if text == "💰 Balance":
        await balance(update, context); return
    if text == "➕ Add Balance":
        await add_balance_entry(update, context); return
    if text == "💬 Chat Admin":
        await chat_admin(update, context); return
    if text == "❌ Cancel":
        await cancel(update, context); return
    if text == "🛠️ Admin Panel":
        await admin_panel(update, context); return

    # If admin is in a state flow, handle below
    state, payload = get_state(context)
    if state:
        await handle_state(update, context, state, payload)
        return

    # default
    await update.message.reply_text("Type /start or use the buttons.")

# ---------- Photo handler ----------
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username)

    # Admin QR setting state?
    state, payload = get_state(context)
    if state == "ADMIN_SET_QR" and is_admin(user):
        which = payload.get("which")
        file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""

        if which == "gcash_qr":
            db.set_setting("gcash_qr_file_id", file_id)
            if caption.strip():
                db.set_setting("gcash_caption", caption)
            set_state(context, "")
            await update.message.reply_text("✅ GCash QR saved! (caption saved if provided)")
            return
        if which == "gotyme_qr":
            db.set_setting("gotyme_qr_file_id", file_id)
            if caption.strip():
                db.set_setting("gotyme_caption", caption)
            set_state(context, "")
            await update.message.reply_text("✅ GoTyme QR saved! (caption saved if provided)")
            return

    # Customer payment proof:
    proof_file_id = update.message.photo[-1].file_id

    topup_db_id = db.attach_topup_proof(user.id, proof_file_id)
    if not topup_db_id:
        await update.message.reply_text("❌ No pending topup found. Please click ➕ Add Balance first.")
        return

    t = db.get_topup_by_dbid(topup_db_id)
    admin_username = ADMIN_USERNAMES[0] if ADMIN_USERNAMES else "lovebylunaa"

    # reply to customer
    await update.message.reply_text(
        f"✅ Proof received!\nTopup ID: {t['topup_id']}\nAmount: {money(t['amount'])}\n"
        f"⏳ Waiting for admin approval.\nAdmin: @{admin_username}"
    )

    # notify admin(s) with approve buttons
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"topupapprove:{t['topup_db_id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"topupreject:{t['topup_db_id']}")
        ]
    ])
    admin_msg = (
        f"🧾 *TOPUP PROOF RECEIVED*\n"
        f"Topup ID: *{t['topup_id']}*\n"
        f"User: `{t['user_id']}` (@{user.username or 'no_username'})\n"
        f"Amount: *{money(t['amount'])}*\n"
        f"Method: *{t['method']}*\n"
    )

    for admin_u in ADMIN_USERNAMES:
        # we cannot DM by username directly without chat_id; simplest: send to same chat if admin is using bot.
        # So: send to admin who last interacted via "pending_topups"? Not stored.
        # We'll still show inside /pending list, and approve buttons work there too.
        pass

    # If you want admin instant DM notifications, you must add ADMIN_CHAT_ID in env (recommended)
    admin_chat_id = os.getenv("ADMIN_CHAT_ID", "").strip()
    if admin_chat_id:
        try:
            await context.bot.send_photo(
                chat_id=int(admin_chat_id),
                photo=proof_file_id,
                caption=admin_msg,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )
        except Exception as e:
            log.warning("Failed to send admin DM: %s", e)

# ---------- Handle admin text states ----------
async def handle_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, payload: dict):
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not is_admin(user):
        context.user_data.clear()
        await update.message.reply_text("❌ Admin only.")
        return

    if state == "ADMIN_SET_TEXT":
        key = payload.get("key")
        if not key:
            context.user_data.clear()
            await update.message.reply_text("❌ Missing key.")
            return
        db.set_setting(key, text)
        context.user_data.clear()
        await update.message.reply_text(f"✅ Updated `{key}`.", parse_mode=ParseMode.MARKDOWN)
        return

    if state == "ADMIN_ANNOUNCE":
        # broadcast to all users
        prefix = db.get_setting("announcement_prefix")
        msg = prefix + text
        users = db.list_users()
        sent = 0
        failed = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u["user_id"], text=msg, parse_mode=ParseMode.MARKDOWN)
                sent += 1
            except Exception:
                failed += 1
        context.user_data.clear()
        await update.message.reply_text(f"✅ Announcement sent.\nSent: {sent}\nFailed: {failed}")
        return

    if state == "ADMIN_ADD_PRODUCT":
        # name | price | stock | description
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 3:
            await update.message.reply_text("❌ Format wrong. Use: `name | price | stock | description`", parse_mode=ParseMode.MARKDOWN)
            return
        name = parts[0]
        try:
            price = int(parts[1])
            stock = int(parts[2])
        except:
            await update.message.reply_text("❌ Price/stock must be numbers.")
            return
        description = parts[3] if len(parts) >= 4 else ""
        pid = db.create_product(name, description, price, stock, None)
        context.user_data.clear()
        await update.message.reply_text(f"✅ Product added. ID = {pid}\n(Optional) To attach delivery file, send it later and we can add a button for that.)")
        return

    if state == "ADMIN_EDIT_PRODUCT_ID":
        try:
            pid = int(text)
        except:
            await update.message.reply_text("❌ Send a number product_id.")
            return
        p = db.get_product(pid)
        if not p:
            await update.message.reply_text("❌ Product not found.")
            return
        set_state(context, "ADMIN_EDIT_PRODUCT_FIELDS", {"pid": pid})
        await update.message.reply_text(
            f"Editing product {pid}:\n"
            f"Current: {p['name']} | {money(p['price'])} | stock {p['stock']}\n\n"
            f"Send like:\n`name | price | stock | description`\n(you can keep same values too)",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if state == "ADMIN_EDIT_PRODUCT_FIELDS":
        pid = payload.get("pid")
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 3:
            await update.message.reply_text("❌ Format wrong. Use: `name | price | stock | description`", parse_mode=ParseMode.MARKDOWN)
            return
        name = parts[0]
        try:
            price = int(parts[1])
            stock = int(parts[2])
        except:
            await update.message.reply_text("❌ Price/stock must be numbers.")
            return
        description = parts[3] if len(parts) >= 4 else ""
        db.update_product(pid, name=name, price=price, stock=stock, description=description)
        context.user_data.clear()
        await update.message.reply_text("✅ Product updated.")
        return

    if state == "ADMIN_DELETE_PRODUCT":
        try:
            pid = int(text)
        except:
            await update.message.reply_text("❌ Send a number product_id.")
            return
        p = db.get_product(pid)
        if not p:
            await update.message.reply_text("❌ Product not found.")
            return
        db.delete_product(pid)
        context.user_data.clear()
        await update.message.reply_text("✅ Product deleted.")
        return

    # fallback
    context.user_data.clear()
    await update.message.reply_text("✅ Done.")

# ---------- Commands to help admin ----------
async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        await update.message.reply_text("❌ Admin only.")
        return
    pending = db.list_pending_topups(limit=50)
    if not pending:
        await update.message.reply_text("✅ No pending topups.")
        return
    for t in pending:
        uname = t.get("username") or "(no username)"
        msg = (
            f"🧾 *PENDING TOPUP*\n"
            f"Topup ID: *{t['topup_id']}*\n"
            f"User: `{t['user_id']}` (@{uname})\n"
            f"Amount: *{money(t['amount'])}*\n"
            f"Method: *{t['method']}*\n"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"topupapprove:{t['topup_db_id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"topupreject:{t['topup_db_id']}")
            ]
        ])
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# ---------- Main ----------
def main():
    db.init_db()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("pending", pending_cmd))

    app.add_handler(CallbackQueryHandler(on_callback))

    # Photos: QR set + payment proof
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))

    # Text menu
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_menu))

    log.info("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
