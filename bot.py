import os
import logging

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
    ensure_schema,
    ensure_user,
    fetch_one,
    fetch_all,
    exec_sql,
    set_setting,
    get_setting,
    purchase_variant,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}


# -------------------- HELPERS --------------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def money(n: int) -> str:
    return f"₱{int(n):,}"


async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass


# Inline (message) menu — NO bottom panel keyboard
def main_menu(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
            InlineKeyboardButton("🛍 Shop", callback_data="nav:shop"),
        ],
        [
            InlineKeyboardButton("👤 My Account", callback_data="nav:account"),
            InlineKeyboardButton("💰 Add Balance", callback_data="nav:addbal"),
        ],
        [
            InlineKeyboardButton("📩 Reseller Register", callback_data="nav:reseller"),
            InlineKeyboardButton("💬 Chat Admin", callback_data="nav:chatadmin"),
        ],
        [
            InlineKeyboardButton("📢 Announcement", callback_data="nav:announce"),
            InlineKeyboardButton("🆘 Help", callback_data="nav:help"),
        ],
    ]
    if is_admin(uid):
        rows.append([InlineKeyboardButton("🔐 Admin", callback_data="nav:admin")])
    return InlineKeyboardMarkup(rows)


PANEL_TEXT_KEYS = {
    "home": "TEXT_HOME",
    "shop": "TEXT_SHOP",
    "help": "TEXT_HELP",
    "account": "TEXT_ACCOUNT",
    "addbal": "TEXT_ADDBAL",
    "admin": "TEXT_ADMIN",
    "announce": "TEXT_ANNOUNCE",
}

PANEL_THUMB_KEYS = {
    "home": "THUMB_HOME",
    "shop": "THUMB_SHOP",
    "help": "THUMB_HELP",
    "account": "THUMB_ACCOUNT",
    "addbal": "THUMB_ADDBAL",
    "admin": "THUMB_ADMIN",
    "announce": "THUMB_ANNOUNCE",
}


async def send_panel(msg, panel: str, default_text: str, menu=None, remove_keyboard=False):
    text_key = PANEL_TEXT_KEYS.get(panel, "")
    thumb_key = PANEL_THUMB_KEYS.get(panel, "")

    text = get_setting(text_key) or default_text
    thumb = get_setting(thumb_key)

    rk = ReplyKeyboardRemove() if remove_keyboard else None

    if thumb:
        await msg.reply_photo(
            photo=thumb,
            caption=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=menu if menu else rk,
        )
    else:
        await msg.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=menu if menu else rk,
        )


# -------------------- START / HOME --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)

    # IMPORTANT: remove old bottom keyboard panel
    await update.message.reply_text("✅ Loading menu...", reply_markup=ReplyKeyboardRemove())

    await send_panel(
        update.message,
        "home",
        "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
        menu=main_menu(user.id),
        remove_keyboard=True,
    )


# -------------------- NAV (INLINE MENU) --------------------
async def cb_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    where = q.data.split(":")[1]

    if where == "home":
        await q.message.reply_text("🏠", reply_markup=ReplyKeyboardRemove())
        await send_panel(
            q.message,
            "home",
            "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
            menu=main_menu(user.id),
        )
        return

    if where == "shop":
        await shop_btn_inline(q, context)
        return

    if where == "account":
        await my_account_inline(q, context)
        return

    if where == "addbal":
        await add_balance_inline(q, context)
        return

    if where == "reseller":
        await reseller_register_inline(q, context)
        return

    if where == "chatadmin":
        await chat_admin_inline(q, context)
        return

    if where == "help":
        await help_inline(q, context)
        return

    if where == "announce":
        await announcement_inline(q, context)
        return

    if where == "admin":
        await admin_inline(q, context)
        return


# -------------------- HELP / CHAT ADMIN / ANNOUNCEMENT --------------------
async def help_inline(q, context):
    await q.message.reply_text("🧾", reply_markup=ReplyKeyboardRemove())
    await send_panel(
        q.message,
        "help",
        "🆘 **Help**\n\n• Tap **Shop** to browse\n• Confirm = auto delivery + auto deduct\n• Add Balance = admin approval\n",
        menu=main_menu(q.from_user.id),
    )


async def chat_admin_inline(q, context):
    admin_user = get_setting("ADMIN_USERNAME") or "@lovebylunaa"
    await q.message.reply_text(f"💬 Chat admin here:\n{admin_user}")


async def announcement_inline(q, context):
    await send_panel(
        q.message,
        "announce",
        "📢 **Announcement**\n\nNo announcement set yet.",
        menu=main_menu(q.from_user.id),
    )


# -------------------- MY ACCOUNT --------------------
async def my_account_inline(q, context):
    user = q.from_user
    ensure_user(user.id, user.username)

    u = fetch_one("SELECT * FROM users WHERE user_id=%s", (user.id,))
    purchases = fetch_one("SELECT COUNT(*) AS c FROM purchases WHERE user_id=%s", (user.id,))
    count = int(purchases["c"]) if purchases else 0

    username = f"@{u['username']}" if u and u.get("username") else "(no username)"
    joined = u["joined_at"].strftime("%Y-%m-%d %H:%M") if u else "—"
    points = int(u["points"]) if u else 0
    reseller = "✅ YES" if u and u["is_reseller"] else "❌ NO"
    balance = int(u["balance"]) if u else 0

    text = (
        "👤 **My Account**\n\n"
        f"Username: **{username}**\n"
        f"User ID: `{user.id}`\n"
        f"Joined: **{joined}**\n"
        f"Purchases: **{count}**\n"
        f"Points: **{points}** (1 order = 1 point)\n"
        f"Balance: **{money(balance)}**\n"
        f"Reseller: **{reseller}**"
    )
    await send_panel(q.message, "account", text, menu=main_menu(user.id))


# -------------------- SHOP UI --------------------
async def shop_btn_inline(q, context):
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
    if not cats:
        await q.message.reply_text("No categories yet.")
        return

    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    rows.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="nav:home")])

    await send_panel(
        q.message,
        "shop",
        "🛍 **Shop Categories**",
        menu=InlineKeyboardMarkup(rows),
    )


async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    if action == "backcats":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
        rows.append([InlineKeyboardButton("⬅ Back to Menu", callback_data="nav:home")])
        await q.edit_message_text(
            "🛍 **Shop Categories**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if action == "cat":
        cat_id = int(parts[2])
        prods = fetch_all(
            """
            SELECT id,name FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id
            """,
            (cat_id,),
        )

        if not prods:
            await q.edit_message_text(
                "No products yet in this category.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")]]
                ),
            )
            return

        rows = [[InlineKeyboardButton(p["name"], callback_data=f"shop:prod:{p['id']}")] for p in prods]
        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")])
        await q.edit_message_text("Select a product:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if action == "prod":
        pid = int(parts[2])
        prod = fetch_one(
            "SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE",
            (pid,),
        )
        if not prod:
            await q.edit_message_text("Product not found.")
            return

        vars_ = fetch_all(
            """
            SELECT id,name,price,bundle_qty,delivery_type FROM variants
            WHERE product_id=%s AND is_active=TRUE
            ORDER BY id
            """,
            (pid,),
        )
        if not vars_:
            await q.edit_message_text(
                "No packages yet.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")]]
                ),
            )
            return

        rows = []
        for v in vars_:
            available_items = fetch_one(
                "SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE",
                (v["id"],),
            )
            stock_items = int(available_items["c"]) if available_items else 0
            bundle_qty = int(v["bundle_qty"])
            stock_units = stock_items // bundle_qty if bundle_qty > 0 else 0

            rows.append(
                [
                    InlineKeyboardButton(
                        f"{v['name']} — {money(int(v['price']))} (Stock: {stock_units})",
                        callback_data=f"buy:open:{v['id']}",
                    )
                ]
            )

        rows.append([InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")])

        text = f"🧾 **{prod['name']}**\n\n{prod.get('description') or ''}\n\nChoose a package below:"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))
        return


# -------------------- CHECKOUT UI --------------------
def checkout_keyboard(qty: int, variant_id: int):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➖", callback_data=f"buy:qty:{variant_id}:-1"),
                InlineKeyboardButton(f"Qty: {qty}", callback_data="noop"),
                InlineKeyboardButton("➕", callback_data=f"buy:qty:{variant_id}:+1"),
            ],
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"buy:confirm:{variant_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"buy:cancel:{variant_id}"),
            ],
            [InlineKeyboardButton("⬅ Back", callback_data="shop:backcats")],
        ]
    )


async def show_checkout(q, user_id: int, variant_id: int, qty: int):
    v = fetch_one(
        """
        SELECT v.id, v.name, v.price, v.bundle_qty, v.delivery_type,
               p.name AS product_name
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
        """,
        (variant_id,),
    )
    if not v:
        await q.edit_message_text("Package not found.")
        return

    u = fetch_one("SELECT balance FROM users WHERE user_id=%s", (user_id,))
    balance = int(u["balance"]) if u else 0

    available_items = fetch_one(
        "SELECT COUNT(*) AS c FROM file_stocks WHERE variant_id=%s AND is_sold=FALSE",
        (variant_id,),
    )
    stock_items = int(available_items["c"]) if available_items else 0
    bundle_qty = int(v["bundle_qty"])
    stock_units = stock_items // bundle_qty if bundle_qty > 0 else 0

    total = int(v["price"]) * qty

    text = (
        "🧾 **Checkout**\n\n"
        f"Product: **{v['product_name']}**\n"
        f"Package: **{v['name']}**\n"
        f"Price each: **{money(int(v['price']))}**\n"
        f"Available stock: **{stock_units}**\n"
        f"Your balance: **{money(balance)}**\n\n"
        f"Total: **{money(total)}**\n\n"
        "Adjust quantity then confirm:"
    )

    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=checkout_keyboard(qty, variant_id))


async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    user = q.from_user
    ensure_user(user.id, user.username)

    parts = q.data.split(":")
    action = parts[1]

    if action == "open":
        variant_id = int(parts[2])
        context.user_data["checkout_variant"] = variant_id
        context.user_data["checkout_qty"] = 1
        await show_checkout(q, user.id, variant_id, 1)
        return

    if action == "qty":
        variant_id = int(parts[2])
        delta = int(parts[3].replace("+", ""))

        if context.user_data.get("checkout_variant") != variant_id:
            context.user_data["checkout_variant"] = variant_id
            context.user_data["checkout_qty"] = 1

        qty = int(context.user_data.get("checkout_qty", 1))
        qty = max(1, qty + delta)
        context.user_data["checkout_qty"] = qty

        await show_checkout(q, user.id, variant_id, qty)
        return

    if action == "cancel":
        context.user_data.pop("checkout_variant", None)
        context.user_data.pop("checkout_qty", None)
        await q.edit_message_text("❌ Cancelled. Tap **Shop** to browse again.", parse_mode=ParseMode.MARKDOWN)
        return

    if action == "confirm":
        variant_id = int(parts[2])
        qty = int(context.user_data.get("checkout_qty", 1))

        result = purchase_variant(user.id, variant_id, qty)

        if not result["ok"]:
            if result["error"] == "NOT_ENOUGH_BALANCE":
                await q.edit_message_text(
                    f"❌ Not enough balance.\nNeed {money(result['need'])}, you have {money(result['have'])}.",
                    reply_markup=checkout_keyboard(qty, variant_id),
                )
                return
            if result["error"] == "NOT_ENOUGH_STOCK":
                await q.edit_message_text(
                    f"❌ Not enough stock.\nNeed {result['need']} item(s), only {result['have']} available.",
                    reply_markup=checkout_keyboard(qty, variant_id),
                )
                return

            await q.edit_message_text("❌ Purchase failed.")
            return

        v = result["variant"]
        stocks = result["stocks"]
        total = result["total"]

        try:
            if v["delivery_type"] == "file":
                sent = 0
                for s in stocks:
                    if s.get("file_id"):
                        await context.bot.send_document(
                            chat_id=user.id,
                            document=s["file_id"],
                            caption=f"✅ Delivery: {v['product_name']} — {v['name']}",
                        )
                        sent += 1
                await context.bot.send_message(user.id, f"✅ Delivered {sent} file(s). Total paid: {money(total)}")
            else:
                lines = [s["delivery_text"] for s in stocks if s.get("delivery_text")]
                if not lines:
                    lines = ["(No delivery text found in stock rows)"]
                chunk = "\n".join(lines)
                await context.bot.send_message(
                    user.id,
                    f"✅ **Delivery**: {v['product_name']} — {v['name']}\n"
                    f"Total paid: **{money(total)}**\n\n"
                    f"{chunk}",
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception as e:
            log.exception("Delivery failed: %s", e)
            await context.bot.send_message(
                user.id,
                "⚠️ Payment & stock were processed, but delivery failed to send.\nPlease contact admin.",
            )

        context.user_data.pop("checkout_variant", None)
        context.user_data.pop("checkout_qty", None)
        await q.edit_message_text("✅ Purchase completed! Tap **Shop** to buy again.", parse_mode=ParseMode.MARKDOWN)
        return


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)


# -------------------- ADD BALANCE (APPROVAL FLOW) --------------------
TOPUP_AMOUNTS = [50, 100, 300, 1000]


def topup_amount_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"➕ {money(a)}", callback_data=f"topup:amt:{a}") for a in TOPUP_AMOUNTS[:2]],
            [InlineKeyboardButton(f"➕ {money(a)}", callback_data=f"topup:amt:{a}") for a in TOPUP_AMOUNTS[2:]],
            [InlineKeyboardButton("❌ Cancel", callback_data="topup:cancel")],
        ]
    )


async def add_balance_inline(q, context):
    user = q.from_user
    ensure_user(user.id, user.username)

    text = "💰 **Add Balance**\n\nChoose payment method:"
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📱 GCash", callback_data="topup:pay:gcash"),
                InlineKeyboardButton("🏦 GoTyme", callback_data="topup:pay:gotyme"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="topup:cancel")],
        ]
    )
    context.user_data["topup_step"] = "choose_method"
    await send_panel(q.message, "addbal", text, menu=kb)


async def cb_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    action = parts[1]

    if action == "cancel":
        for k in ("topup_step", "topup_method", "topup_amount"):
            context.user_data.pop(k, None)
        await q.edit_message_text("❌ Cancelled.")
        return

    if action == "pay":
        method = parts[2]
        context.user_data["topup_method"] = method
        context.user_data["topup_step"] = "choose_amount"

        qr_key = "PAY_GCASH_QR" if method == "gcash" else "PAY_GOTYME_QR"
        qr = get_setting(qr_key)

        caption = (
            f"✅ **{method.upper()} selected**\n\n"
            "1) Pay now\n"
            "2) Choose amount below\n"
            "3) Then send proof screenshot (PHOTO)"
        )

        if qr:
            await q.message.reply_photo(photo=qr, caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=topup_amount_keyboard())
        else:
            await q.edit_message_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=topup_amount_keyboard())
        return

    if action == "amt":
        amt = int(parts[2])
        context.user_data["topup_amount"] = amt
        context.user_data["topup_step"] = "await_proof"
        await q.edit_message_text(
            f"✅ Amount selected: **{money(amt)}**\n\nNow send your **proof screenshot** as PHOTO.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # approve / reject
    if action in ("ok", "no"):
        if not is_admin(q.from_user.id):
            await q.message.reply_text("❌ Access denied.")
            return

        req_id = int(parts[2])
        req = fetch_one("SELECT * FROM topup_requests WHERE id=%s", (req_id,))
        if not req or req["status"] != "PENDING":
            try:
                await q.edit_message_caption("Already decided.")
            except Exception:
                await q.edit_message_text("Already decided.")
            return

        if action == "ok":
            exec_sql("UPDATE users SET balance=balance+%s WHERE user_id=%s", (int(req["amount"]), int(req["user_id"])))
            exec_sql(
                "UPDATE topup_requests SET status='APPROVED', decided_at=NOW(), admin_id=%s WHERE id=%s",
                (q.from_user.id, req_id),
            )
            await context.bot.send_message(int(req["user_id"]), f"✅ Topup approved! Added {money(int(req['amount']))}")
            try:
                await q.edit_message_caption("✅ Approved")
            except Exception:
                await q.edit_message_text("✅ Approved")
        else:
            exec_sql(
                "UPDATE topup_requests SET status='REJECTED', decided_at=NOW(), admin_id=%s WHERE id=%s",
                (q.from_user.id, req_id),
            )
            await context.bot.send_message(int(req["user_id"]), "❌ Topup rejected.")
            try:
                await q.edit_message_caption("❌ Rejected")
            except Exception:
                await q.edit_message_text("❌ Rejected")
        return


async def photo_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    SINGLE photo handler:
    - topup proof
    - admin setthumb/setpay/setannouncementthumb
    """
    user = update.effective_user

    # 1) topup proof
    if context.user_data.get("topup_step") == "await_proof":
        ensure_user(user.id, user.username)

        proof_id = update.message.photo[-1].file_id
        amt = int(context.user_data.get("topup_amount", 0))
        if amt <= 0:
            await update.message.reply_text("❌ Invalid amount. Please restart Add Balance.")
            return

        exec_sql(
            "INSERT INTO topup_requests(user_id, amount, proof_file_id) VALUES(%s,%s,%s)",
            (user.id, amt, proof_id),
        )
        req = fetch_one("SELECT id FROM topup_requests WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user.id,))
        req_id = int(req["id"])

        for admin_id in ADMIN_IDS:
            kb = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("✅ Approve", callback_data=f"topup:ok:{req_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"topup:no:{req_id}"),
                ]]
            )
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=proof_id,
                caption=(
                    "💰 **Topup Request**\n\n"
                    f"User: @{user.username}\n"
                    f"ID: `{user.id}`\n"
                    f"Amount: **{money(amt)}**\n\n"
                    "Approve?"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb,
            )

        for k in ("topup_step", "topup_method", "topup_amount"):
            context.user_data.pop(k, None)

        await update.message.reply_text("✅ Submitted! Waiting for admin approval.")
        return

    # 2) admin thumbnails
    if is_admin(user.id) and context.user_data.get("await_thumb_panel"):
        panel = context.user_data.pop("await_thumb_panel")
        file_id = update.message.photo[-1].file_id
        set_setting(PANEL_THUMB_KEYS[panel], file_id)
        await update.message.reply_text(f"✅ Thumbnail saved for {panel}.")
        return

    # 3) admin payment QR
    if is_admin(user.id) and context.user_data.get("await_pay_qr"):
        method = context.user_data.pop("await_pay_qr")
        file_id = update.message.photo[-1].file_id
        key = "PAY_GCASH_QR" if method == "gcash" else "PAY_GOTYME_QR"
        set_setting(key, file_id)
        await update.message.reply_text(f"✅ {method.upper()} QR saved!")
        return


# -------------------- RESELLER REGISTER --------------------
async def reseller_register_inline(q, context):
    context.user_data["reseller_step"] = 1
    await q.message.reply_text("📩 **Reseller Registration**\n\nSend your **Full Name**:", parse_mode=ParseMode.MARKDOWN)


async def reseller_form_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "reseller_step" not in context.user_data:
        return

    step = context.user_data["reseller_step"]
    text = (update.message.text or "").strip()

    if step == 1:
        context.user_data["res_full_name"] = text
        context.user_data["reseller_step"] = 2
        await update.message.reply_text("Send your **Contact** (Telegram/FB/etc):", parse_mode=ParseMode.MARKDOWN)
        return

    if step == 2:
        context.user_data["res_contact"] = text
        context.user_data["reseller_step"] = 3
        await update.message.reply_text("Send your **Shop Link**:", parse_mode=ParseMode.MARKDOWN)
        return

    if step == 3:
        user = update.effective_user
        full_name = context.user_data.pop("res_full_name")
        contact = context.user_data.pop("res_contact")
        shop_link = text
        context.user_data.pop("reseller_step", None)

        exec_sql(
            """
            INSERT INTO reseller_applications(user_id, username, full_name, contact, shop_link)
            VALUES(%s,%s,%s,%s,%s)
            """,
            (user.id, user.username, full_name, contact, shop_link),
        )

        await update.message.reply_text("✅ Submitted! Waiting for admin approval 💗")


# -------------------- ADMIN --------------------
def admin_panel_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 Topup Pending", callback_data="admin:topups:pending")],
            [InlineKeyboardButton("👥 Users & Balances", callback_data="admin:users:list")],
            [InlineKeyboardButton("🧾 Last 10 Purchases", callback_data="admin:purchases:last10")],
        ]
    )


async def admin_inline(q, context):
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    text = (
        "🔐 **Admin Panel** ✅\n\n"
        "Commands:\n"
        "• `/settext <home|shop|help|account|addbal|admin|announce> <text>`\n"
        "• `/setthumb <home|shop|help|account|addbal|admin|announce>` then send PHOTO\n"
        "• `/setpay <gcash|gotyme>` then send PHOTO (QR)\n"
        "• `/announce <text>` (saves Announcement panel)\n"
        "• `/broadcast <text>` (send to ALL users)\n"
        "• `/notify <user_id> <text>`\n"
        "• `/addbal <user_id> <amount>`\n"
        "• `/fileid` (reply to photo/doc)\n"
    )
    await send_panel(q.message, "admin", text, menu=admin_panel_keyboard())


async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    parts = q.data.split(":")

    if parts[1] == "topups" and parts[2] == "pending":
        rows = fetch_all(
            """
            SELECT id, user_id, amount, created_at
            FROM topup_requests
            WHERE status='PENDING'
            ORDER BY id DESC
            LIMIT 20
            """
        )
        if not rows:
            await q.edit_message_text("✅ No pending topups.")
            return

        lines = ["💰 **Pending Topups** (latest 20)\n"]
        kb_rows = []
        for r in rows:
            lines.append(f"• #{r['id']} — `{r['user_id']}` — **{money(r['amount'])}** — {r['created_at']}")
            kb_rows.append([InlineKeyboardButton(f"View #{r['id']}", callback_data=f"admin:topup:view:{r['id']}")])

        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if parts[1] == "topup" and parts[2] == "view":
        req_id = int(parts[3])
        req = fetch_one("SELECT * FROM topup_requests WHERE id=%s", (req_id,))
        if not req:
            await q.edit_message_text("Not found.")
            return

        kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Approve", callback_data=f"topup:ok:{req_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"topup:no:{req_id}"),
            ]]
        )

        await q.message.reply_photo(
            photo=req["proof_file_id"],
            caption=(
                "💰 **Topup Request**\n\n"
                f"Request: **#{req_id}**\n"
                f"User ID: `{req['user_id']}`\n"
                f"Amount: **{money(req['amount'])}**\n"
                f"Status: **{req['status']}**"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )
        await q.edit_message_text("Opened topup proof above ✅")
        return

    if parts[1] == "users" and parts[2] == "list":
        rows = fetch_all(
            """
            SELECT u.user_id, u.username, u.balance, u.points, u.is_reseller,
                   (SELECT COUNT(*) FROM purchases p WHERE p.user_id=u.user_id) AS purchases
            FROM users u
            ORDER BY u.joined_at DESC
            LIMIT 30
            """
        )
        if not rows:
            await q.edit_message_text("No users yet.")
            return

        lines = ["👥 **Users (latest 30)**\n"]
        for r in rows:
            uname = f"@{r['username']}" if r.get("username") else "(no username)"
            lines.append(
                f"• `{r['user_id']}` {uname} | Bal: **{money(r['balance'])}** | Buy: **{r['purchases']}** | "
                f"Pts: **{r['points']}** | Reseller: {'✅' if r['is_reseller'] else '❌'}"
            )

        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    if parts[1] == "purchases" and parts[2] == "last10":
        rows = fetch_all(
            """
            SELECT p.id, p.user_id, p.variant_id, p.quantity, p.total_price, p.created_at
            FROM purchases p
            ORDER BY p.id DESC
            LIMIT 10
            """
        )
        if not rows:
            await q.edit_message_text("No purchases yet.")
            return

        lines = ["🧾 **Last 10 Purchases**\n"]
        for r in rows:
            lines.append(
                f"• #{r['id']} — `{r['user_id']}` — variant `{r['variant_id']}` — qty **{r['quantity']}** — "
                f"paid **{money(r['total_price'])}** — {r['created_at']}"
            )
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return


# -------------------- ADMIN COMMANDS --------------------
async def settext_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: /settext <home|shop|help|account|addbal|admin|announce> <text>")
        return

    panel = parts[1].strip().lower()
    text = parts[2].strip()

    if panel not in PANEL_TEXT_KEYS:
        await update.message.reply_text("Panel must be: home/shop/help/account/addbal/admin/announce")
        return

    set_setting(PANEL_TEXT_KEYS[panel], text)
    await update.message.reply_text(f"✅ Text updated for {panel}.")


async def setthumb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /setthumb <home|shop|help|account|addbal|admin|announce>")
        return

    panel = parts[1].strip().lower()
    if panel not in PANEL_THUMB_KEYS:
        await update.message.reply_text("Panel must be: home/shop/help/account/addbal/admin/announce")
        return

    context.user_data["await_thumb_panel"] = panel
    await update.message.reply_text("Send the PHOTO now.")


async def setpay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /setpay <gcash|gotyme>")
        return

    method = parts[1].strip().lower()
    if method not in ("gcash", "gotyme"):
        await update.message.reply_text("Method must be gcash or gotyme")
        return

    context.user_data["await_pay_qr"] = method
    await update.message.reply_text("Send the QR as PHOTO now.")


async def announce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /announce <text>")
        return

    set_setting("TEXT_ANNOUNCE", parts[1].strip())
    await update.message.reply_text("✅ Announcement saved (Announcement button will show it).")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /broadcast <text>")
        return

    text = parts[1].strip()
    users = fetch_all("SELECT user_id FROM users ORDER BY joined_at DESC LIMIT 5000")
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(int(u["user_id"]), f"📢 {text}")
            sent += 1
        except Exception:
            pass

    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")


async def notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    parts = update.message.text.split(" ", 2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: /notify <user_id> <text>")
        return

    uid = int(parts[1])
    text = parts[2].strip()
    await context.bot.send_message(uid, f"🔔 {text}")
    await update.message.reply_text("✅ Sent.")


async def addbal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    parts = update.message.text.split()
    if len(parts) != 3:
        await update.message.reply_text("Usage: /addbal <user_id> <amount>")
        return
    uid = int(parts[1])
    amt = int(parts[2])
    exec_sql("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amt, uid))
    await update.message.reply_text(f"✅ Added {money(amt)} to {uid}")


async def fileid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to a photo/document message then send /fileid")
        return

    r = msg.reply_to_message
    if r.photo:
        await msg.reply_text(f"PHOTO file_id:\n`{r.photo[-1].file_id}`", parse_mode=ParseMode.MARKDOWN)
        return
    if r.document:
        await msg.reply_text(f"DOCUMENT file_id:\n`{r.document.file_id}`", parse_mode=ParseMode.MARKDOWN)
        return

    await msg.reply_text("Replied message has no photo/document.")


# -------------------- BOOT --------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("settext", settext_cmd))
    app.add_handler(CommandHandler("setthumb", setthumb_cmd))
    app.add_handler(CommandHandler("setpay", setpay_cmd))
    app.add_handler(CommandHandler("announce", announce_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("notify", notify_cmd))
    app.add_handler(CommandHandler("addbal", addbal_cmd))
    app.add_handler(CommandHandler("fileid", fileid_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_nav, pattern=r"^nav:"))
    app.add_handler(CallbackQueryHandler(cb_noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_topup, pattern=r"^topup:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))

    # One photo handler only (proof + admin uploads)
    app.add_handler(MessageHandler(filters.PHOTO, photo_router))

    # Reseller form steps
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reseller_form_text))

    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
