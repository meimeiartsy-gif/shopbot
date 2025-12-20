import os
import traceback
import uuid
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

from db import (
    init_db,
    ensure_user, get_balance, add_balance, deduct_balance_if_enough,
    set_setting, get_setting,
    add_product, list_products, get_product,
    add_variant, list_variants, set_variant_file,
    add_stock_items, count_stock, take_stock_items,
    create_topup, attach_topup_proof, get_topup, list_pending_topups,
    approve_topup, reject_topup
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@lovebylunaa")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")
if not ADMIN_IDS:
    print("⚠️ ADMIN_IDS is empty. Set it in Railway Variables: e.g. 12345,67890")

SHOP_PREFIX = os.getenv("SHOP_PREFIX", "shopnluna").strip()

AMOUNTS = [50, 100, 300, 500, 1000]

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def parse_product(fullname: str):
    # "Category | Product"
    if "|" in fullname:
        a, b = fullname.split("|", 1)
        return a.strip(), b.strip()
    return "All", fullname.strip()

def money(n: int) -> str:
    return f"₱{n:,}"

def make_topup_id() -> str:
    return f"{SHOP_PREFIX}:TU{uuid.uuid4().hex[:10].upper()}"

# ---------------- STARTUP ----------------
async def on_startup(app: Application):
    init_db()
    print("✅ DB init + migrations done")

# ---------------- MAIN UI ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    bal = get_balance(user_id)
    prods = list_products()

    cats = sorted({parse_product(p["name"])[0] for p in prods}) if prods else ["All"]

    text = (
        "🌙 *Luna’s Prem Shop*\n"
        "━━━━━━━━━━━━━━\n"
        f"💳 *Balance:* {money(bal)}\n"
        "Choose a category:"
    )

    kb = []
    row = []
    for c in cats:
        row.append(InlineKeyboardButton(f"✨ {c}", callback_data=f"cat:{c}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([
        InlineKeyboardButton("💳 Balance", callback_data="balance"),
        InlineKeyboardButton("➕ Add Balance", callback_data="addbal")
    ])

    if is_admin(user_id):
        kb.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin")])

    kb.append([InlineKeyboardButton("💬 Chat Admin", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def balance_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, via_edit=False):
    user_id = update.effective_user.id
    ensure_user(user_id)
    bal = get_balance(user_id)

    text = (
        "💳 *Your Balance*\n"
        "━━━━━━━━━━━━━━\n"
        f"{money(bal)}"
    )

    kb = [
        [InlineKeyboardButton("➕ Add Balance", callback_data="addbal")],
        [InlineKeyboardButton("🏠 Home", callback_data="home")]
    ]
    if via_edit:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# ---------------- ADD BALANCE FLOW ----------------
async def add_balance_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "➕ *Add Balance*\n━━━━━━━━━━━━━━\nChoose payment method:"
    kb = [
        [InlineKeyboardButton("💳 GCash", callback_data="paym:gcash"),
         InlineKeyboardButton("🏦 GoTyme", callback_data="paym:gotyme")],
        [InlineKeyboardButton("🏠 Home", callback_data="home")]
    ]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def choose_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, method: str):
    context.user_data["topup_method"] = method

    title = "GCash" if method == "gcash" else "GoTyme"
    text = f"{'💳' if method=='gcash' else '🏦'} *{title} Top-up*\n━━━━━━━━━━━━━━\nChoose amount:"

    kb = []
    row = []
    for a in AMOUNTS:
        row.append(InlineKeyboardButton(money(a), callback_data=f"amt:{a}"))
        if len(row) == 3:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton("⬅ Back", callback_data="addbal")])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def show_qr_and_wait_proof(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int):
    user_id = update.effective_user.id
    ensure_user(user_id)

    method = context.user_data.get("topup_method")
    if method not in ("gcash", "gotyme"):
        await update.callback_query.answer("Choose payment method first.", show_alert=True)
        return

    # Create topup request NOW
    topup_id = make_topup_id()
    create_topup(topup_id, user_id, amount, method)

    # Save pending proof state
    context.user_data["awaiting_proof"] = True
    context.user_data["awaiting_topup_id"] = topup_id

    # Get QR + caption text
    qr_key = "QR_GCASH" if method == "gcash" else "QR_GOTYME"
    txt_key = "TXT_GCASH" if method == "gcash" else "TXT_GOTYME"

    qr_file_id = get_setting(qr_key)
    pay_text = get_setting(txt_key) or (
        "📌 *Instructions*\n"
        "1) Scan the QR and pay the exact amount\n"
        "2) Then *send screenshot proof here*\n"
        "3) Wait for admin approval\n\n"
        f"For fast approval DM admin: {ADMIN_USERNAME}"
    )

    caption = (
        f"{pay_text}\n\n"
        f"🧾 *TopUp ID:* `{topup_id}`\n"
        f"💰 *Amount:* {money(amount)}\n\n"
        "✅ Now send your payment screenshot *as photo* here."
    )

    chat = update.callback_query.message
    if qr_file_id:
        await chat.reply_photo(photo=qr_file_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
    else:
        await chat.reply_text(
            caption + "\n\n⚠️ Admin has not set QR yet.",
            parse_mode=ParseMode.MARKDOWN
        )

    await update.callback_query.answer("Created top-up. Send proof now ✅")

# Customer sends proof photo
async def capture_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_proof"):
        return

    user_id = update.effective_user.id
    topup_id = context.user_data.get("awaiting_topup_id")
    if not topup_id:
        return

    # Accept PHOTO (best) or Document image
    proof_file_id = None
    if update.message.photo:
        proof_file_id = update.message.photo[-1].file_id
    elif update.message.document:
        proof_file_id = update.message.document.file_id

    if not proof_file_id:
        await update.message.reply_text("Please send the proof as a PHOTO (or image document).")
        return

    row = get_topup(topup_id)
    if not row or int(row["user_id"]) != int(user_id):
        await update.message.reply_text("Top-up request not found. Please start again: /start")
        context.user_data["awaiting_proof"] = False
        context.user_data["awaiting_topup_id"] = None
        return

    attach_topup_proof(topup_id, proof_file_id)

    # Stop awaiting mode
    context.user_data["awaiting_proof"] = False
    context.user_data["awaiting_topup_id"] = None

    await update.message.reply_text(
        f"✅ Thank you! Proof received.\n🧾 TopUp ID: {topup_id}\n⏳ Waiting for admin approval.\n\n"
        f"For fast approval DM admin: {ADMIN_USERNAME}"
    )

    # Notify admins with approve/reject buttons + proof
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"adm_appr:{topup_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"adm_rej:{topup_id}")
    ]])

    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=aid,
                text=(
                    "🧾 *New Top-up Proof*\n"
                    f"TopUp ID: `{topup_id}`\n"
                    f"User: `{user_id}`\n"
                    f"Method: `{row['method']}`\n"
                    f"Amount: `{money(int(row['amount']))}`"
                ),
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN
            )
            await context.bot.send_photo(chat_id=aid, photo=proof_file_id)
        except Exception as e:
            print("Admin notify failed:", e)

# ---------------- SHOP: CATEGORIES / PRODUCTS / VARIANTS ----------------
async def show_category(update: Update, context: ContextTypes.DEFAULT_TYPE, cat: str):
    prods = list_products()
    items = []
    for p in prods:
        c, title = parse_product(p["name"])
        if c == cat:
            items.append((p["id"], title))

    text = f"✨ *{cat}*\n━━━━━━━━━━━━━━\nSelect a product:"
    kb = [[InlineKeyboardButton(f"• {title}", callback_data=f"prod:{pid}")]
          for pid, title in items] or [[InlineKeyboardButton("No products yet", callback_data="noop")]]

    kb.append([InlineKeyboardButton("🏠 Home", callback_data="home")])

    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    prod = get_product(product_id)
    if not prod:
        await update.callback_query.answer("Product not found", show_alert=True)
        return

    cat, title = parse_product(prod["name"])
    desc = (prod.get("description") or "").strip()
    variants = list_variants(product_id)

    lines = [f"🛍 *{title}*", "━━━━━━━━━━━━━━"]
    if desc:
        lines.append(desc)

    kb = []
    for v in variants:
        vid = int(v["id"])
        vname = v["name"]
        price = int(v["price"])
        file_id = v.get("telegram_file_id")

        stock_label = "∞" if file_id else str(count_stock(vid))
        kb.append([InlineKeyboardButton(
            f"{vname} — {money(price)} (Stock: {stock_label})",
            callback_data=f"buy:{vid}"
        )])

    kb.append([InlineKeyboardButton("⬅ Back", callback_data=f"cat:{cat}")])
    await update.callback_query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# ---------------- BUY FLOW ----------------
async def ask_qty(update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int):
    context.user_data["buy_variant_id"] = variant_id
    await update.callback_query.message.reply_text("🔢 Enter quantity to buy (1–50), or type `cancel`.", parse_mode=ParseMode.MARKDOWN)

async def handle_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "buy_variant_id" not in context.user_data:
        return

    text = (update.message.text or "").strip().lower()
    if text == "cancel":
        context.user_data.pop("buy_variant_id", None)
        await update.message.reply_text("❎ Cancelled.")
        return

    try:
        qty = int(text)
        if qty < 1 or qty > 50:
            raise ValueError()
    except:
        await update.message.reply_text("Enter a number 1–50, or type `cancel`.", parse_mode=ParseMode.MARKDOWN)
        return

    user_id = update.effective_user.id
    ensure_user(user_id)
    variant_id = int(context.user_data.pop("buy_variant_id"))

    # fetch variant + product
    from db import fetchone
    row = fetchone("""
        SELECT v.id AS vid, v.name AS vname, v.price AS price, v.telegram_file_id AS file_id,
               p.name AS pname
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s
    """, (variant_id,))

    if not row:
        await update.message.reply_text("Package not found.")
        return

    price = int(row["price"])
    total = price * qty
    bal = get_balance(user_id)

    # FILE DELIVERY
    if row.get("file_id"):
        if qty != 1:
            await update.message.reply_text("❌ File products quantity must be 1.")
            return
        if not deduct_balance_if_enough(user_id, total):
            await update.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.")
            return

        await update.message.reply_document(
            document=row["file_id"],
            caption=f"✅ Purchase Successful\n💰 Paid: {money(total)}\n🧾 {row['vname']}"
        )
        return

    # STOCK DELIVERY
    if count_stock(variant_id) < qty:
        await update.message.reply_text("❌ Not enough stock.")
        return
    if not deduct_balance_if_enough(user_id, total):
        await update.message.reply_text(f"❌ Need {money(total)}, you have {money(bal)}.")
        return

    items = take_stock_items(variant_id, qty, user_id)
    if not items:
        # refund if stock race happened
        add_balance(user_id, total)
        await update.message.reply_text("❌ Stock changed. Try again.")
        return

    await update.message.reply_text(
        "✅ Purchase Successful\n"
        f"💰 Paid: {money(total)}\n\n"
        "📦 Delivery:\n" + "\n".join(items)
    )

# ---------------- ADMIN PANEL ----------------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("Admins only", show_alert=True)
        return

    text = "🛠 *Admin Panel*\n━━━━━━━━━━━━━━"
    kb = [
        [InlineKeyboardButton("🧾 Pending Top-ups", callback_data="adm_pending")],
        [InlineKeyboardButton("🏷 Add Product (command)", callback_data="adm_help_prod")],
        [InlineKeyboardButton("🏠 Home", callback_data="home")]
    ]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("Admins only", show_alert=True)
        return

    rows = list_pending_topups(30)
    if not rows:
        await update.callback_query.edit_message_text("✅ No pending top-ups right now.", parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["🧾 *Pending Top-ups*", "━━━━━━━━━━━━━━"]
    kb = []
    for r in rows[:10]:
        tid = r["topup_id"]
        lines.append(f"• `{tid}` — user `{r['user_id']}` — {money(int(r['amount']))} — {r['method']}")
        kb.append([InlineKeyboardButton(f"Open {tid}", callback_data=f"adm_open:{tid}")])

    kb.append([InlineKeyboardButton("⬅ Back", callback_data="admin")])
    await update.callback_query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def admin_open_topup(update: Update, context: ContextTypes.DEFAULT_TYPE, topup_id: str):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("Admins only", show_alert=True)
        return

    row = get_topup(topup_id)
    if not row:
        await update.callback_query.answer("Not found", show_alert=True)
        return

    text = (
        "🧾 *Top-up Details*\n"
        "━━━━━━━━━━━━━━\n"
        f"ID: `{row['topup_id']}`\n"
        f"User: `{row['user_id']}`\n"
        f"Amount: `{money(int(row['amount']))}`\n"
        f"Method: `{row['method']}`\n"
        f"Status: `{row['status']}`\n"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"adm_appr:{topup_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"adm_rej:{topup_id}")
    ], [InlineKeyboardButton("⬅ Back", callback_data="adm_pending")]])

    await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    if row.get("proof_file_id"):
        try:
            await update.callback_query.message.reply_photo(photo=row["proof_file_id"])
        except:
            pass

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE, topup_id: str):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("Admins only", show_alert=True)
        return

    credited_user = approve_topup(topup_id, update.effective_user.id)
    if not credited_user:
        await update.callback_query.answer("Cannot approve (maybe already decided).", show_alert=True)
        return

    # notify user
    try:
        bal = get_balance(credited_user)
        await context.bot.send_message(
            chat_id=credited_user,
            text=f"✅ Top-up approved!\n🧾 {topup_id}\n💳 New balance: {money(bal)}"
        )
    except Exception as e:
        print("Notify user failed:", e)

    await update.callback_query.answer("Approved ✅", show_alert=False)
    await admin_pending(update, context)

async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE, topup_id: str):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("Admins only", show_alert=True)
        return

    uid = reject_topup(topup_id, update.effective_user.id)
    if not uid:
        await update.callback_query.answer("Cannot reject (maybe already decided).", show_alert=True)
        return

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"❌ Top-up rejected.\n🧾 {topup_id}\nIf you think this is a mistake, DM admin: {ADMIN_USERNAME}"
        )
    except:
        pass

    await update.callback_query.answer("Rejected ❌", show_alert=False)
    await admin_pending(update, context)

# ---------------- ADMIN COMMANDS: SET QR + TEXT + PRODUCTS ----------------
async def setqr_gcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not update.message.photo:
        await update.message.reply_text("Send GCash QR as PHOTO with caption: /setqr_gcash")
        return
    file_id = update.message.photo[-1].file_id
    set_setting("QR_GCASH", file_id)
    await update.message.reply_text("✅ GCash QR saved.")

async def setqr_gotyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not update.message.photo:
        await update.message.reply_text("Send GoTyme QR as PHOTO with caption: /setqr_gotyme")
        return
    file_id = update.message.photo[-1].file_id
    set_setting("QR_GOTYME", file_id)
    await update.message.reply_text("✅ GoTyme QR saved.")

async def settext_gcash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.replace("/settext_gcash", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage: /settext_gcash <instructions text>")
        return
    set_setting("TXT_GCASH", text)
    await update.message.reply_text("✅ GCash instructions saved.")

async def settext_gotyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.replace("/settext_gotyme", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage: /settext_gotyme <instructions text>")
        return
    set_setting("TXT_GOTYME", text)
    await update.message.reply_text("✅ GoTyme instructions saved.")

async def cmd_addproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Usage: /addproduct Category | Product Name")
        return
    pid = add_product(name, "")
    await update.message.reply_text(f"✅ Product added. ID: {pid}")

async def cmd_addvariant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /addvariant <product_id> <price> <variant name...>")
        return
    product_id = int(context.args[0])
    price = int(context.args[1])
    name = " ".join(context.args[2:]).strip()
    vid = add_variant(product_id, name, price)
    await update.message.reply_text(f"✅ Variant added. ID: {vid}")

async def cmd_addstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage:\n/addstock <variant_id>\n<one item per line>")
        return
    variant_id = int(context.args[0])
    lines = update.message.text.splitlines()
    if len(lines) < 2:
        await update.message.reply_text("Put items on next lines (one per line).")
        return
    items = [ln.strip() for ln in lines[1:] if ln.strip()]
    n = add_stock_items(variant_id, items)
    await update.message.reply_text(f"✅ Added {n} stock items.")

async def cmd_setfile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /setfile <variant_id> (attach file as Document)")
        return
    if not update.message.document:
        await update.message.reply_text("Attach file as Document with caption: /setfile <variant_id>")
        return
    variant_id = int(context.args[0])
    file_id = update.message.document.file_id
    set_variant_file(variant_id, file_id)
    await update.message.reply_text("✅ Auto-delivery file set for this variant.")

# ---------------- CALLBACK ROUTER ----------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    try:
        if data == "home":
            await q.answer()
            # easiest home: send a fresh home screen
            await q.message.reply_text("🏠 Returning home…")
            fake_update = Update(update.update_id, message=q.message)
            await start(fake_update, context)
            return

        if data == "balance":
            await q.answer()
            await balance_screen(update, context, via_edit=False)
            return

        if data == "addbal":
            await q.answer()
            await add_balance_method(update, context)
            return

        if data.startswith("paym:"):
            await q.answer()
            method = data.split(":", 1)[1]
            await choose_amount(update, context, method)
            return

        if data.startswith("amt:"):
            await q.answer()
            amount = int(data.split(":", 1)[1])
            await show_qr_and_wait_proof(update, context, amount)
            return

        if data.startswith("cat:"):
            await q.answer()
            cat = data.split(":", 1)[1]
            await show_category(update, context, cat)
            return

        if data.startswith("prod:"):
            await q.answer()
            pid = int(data.split(":", 1)[1])
            await show_product(update, context, pid)
            return

        if data.startswith("buy:"):
            await q.answer()
            vid = int(data.split(":", 1)[1])
            await ask_qty(update, context, vid)
            return

        # ADMIN PANEL
        if data == "admin":
            await q.answer()
            await admin_panel(update, context)
            return

        if data == "adm_pending":
            await q.answer()
            await admin_pending(update, context)
            return

        if data.startswith("adm_open:"):
            await q.answer()
            tid = data.split(":", 1)[1]
            await admin_open_topup(update, context, tid)
            return

        if data.startswith("adm_appr:"):
            await q.answer()
            tid = data.split(":", 1)[1]
            await admin_approve(update, context, tid)
            return

        if data.startswith("adm_rej:"):
            await q.answer()
            tid = data.split(":", 1)[1]
            await admin_reject(update, context, tid)
            return

        await q.answer("No action", show_alert=False)

    except Exception as e:
        # Show user friendly msg + print real error to logs
        await q.answer("Bot error happened, try again in a moment.", show_alert=True)
        print("🔥 CALLBACK ERROR:", e)
        traceback.print_exc()

# ---------------- ERROR HANDLER ----------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("🔥 UNHANDLED ERROR:", context.error)
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # commands
    app.add_handler(CommandHandler("start", start))

    # admin setup
    app.add_handler(CommandHandler("setqr_gcash", setqr_gcash))
    app.add_handler(CommandHandler("setqr_gotyme", setqr_gotyme))
    app.add_handler(CommandHandler("settext_gcash", settext_gcash))
    app.add_handler(CommandHandler("settext_gotyme", settext_gotyme))

    # admin products
    app.add_handler(CommandHandler("addproduct", cmd_addproduct))
    app.add_handler(CommandHandler("addvariant", cmd_addvariant))
    app.add_handler(CommandHandler("addstock", cmd_addstock))
    app.add_handler(CommandHandler("setfile", cmd_setfile))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    # payment proof photo (ONLY triggers if user is awaiting proof)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, capture_payment_proof))

    # qty input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_qty))

    # global error handler
    app.add_error_handler(error_handler)

    print("✅ Starting bot polling…")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
