import os
import uuid
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
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

from db import ensure_schema, db, fetch_all, fetch_one, exec_sql
from utils import (
    parse_int_money,
    send_clean_menu,
    delete_last_menu,
    push_screen,
    pop_screen,
    peek_screen,
)

from payments import payment_method_menu, topup_amount_menu, send_payment_qr

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = {7719956917}  # <-- your admin id

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Shop", "💳 Add Balance"],
        ["💰 Balance", "📜 History"],
        ["🆘 Help", "🔐 Admin"],
    ],
    resize_keyboard=True
)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def fmt_money(n: int) -> str:
    return f"₱{n:,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    exec_sql("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user.id,))
    context.user_data["nav_stack"] = []
    await send_clean_menu(
        update, context,
        "Welcome to **Luna’s Prem Shop** 💗\n\nChoose an option below:",
        reply_markup=MAIN_MENU,
        parse_mode=ParseMode.MARKDOWN
    )

# =========================
# BASIC MENU
# =========================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_clean_menu(
        update, context,
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse and buy\n"
        "• Tap **Add Balance** to top up\n"
        "• After you send proof, wait for approval\n",
        parse_mode=ParseMode.MARKDOWN
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = fetch_one("SELECT COALESCE(balance,0) AS balance FROM users WHERE user_id=%s", (user_id,))
    bal = int(row["balance"]) if row else 0
    await send_clean_menu(update, context, f"💰 Your balance: **{fmt_money(bal)}**", parse_mode=ParseMode.MARKDOWN)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = fetch_all("""
        SELECT p.created_at, v.name as variant_name, p.qty, p.total_price, p.delivered
        FROM purchases p
        JOIN variants v ON v.id = p.variant_id
        WHERE p.user_id=%s
        ORDER BY p.created_at DESC
        LIMIT 15
    """, (user_id,))
    if not rows:
        await send_clean_menu(update, context, "📜 No purchases yet.")
        return

    lines = ["📜 **Recent Purchases**\n"]
    for r in rows:
        status = "✅ delivered" if r["delivered"] else "⏳ pending"
        lines.append(f"• {r['variant_name']} x{r['qty']} — {fmt_money(int(r['total_price']))} — {status}")
    await send_clean_menu(update, context, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# =========================
# TOP-UP
# =========================
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    push_screen(context, "main")
    await send_clean_menu(
        update, context,
        "💳 **Add Balance**\n\nChoose payment method:",
        reply_markup=payment_method_menu(),
        parse_mode=ParseMode.MARKDOWN
    )

async def cb_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if q.data == "pay:back":
        await send_clean_menu(update, context, "Back ✅", reply_markup=MAIN_MENU)
        return

    method = q.data.split(":", 1)[1]
    context.user_data["topup_method"] = method

    # QR
    await send_payment_qr(update, context, method)

    # Amounts
    await send_clean_menu(
        update, context,
        "✨ Choose top-up amount:",
        reply_markup=topup_amount_menu(),
    )

async def cb_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if "topup_method" not in context.user_data:
        await send_clean_menu(update, context, "Tap **Add Balance** again and choose method first.", parse_mode=ParseMode.MARKDOWN)
        return

    amount = int(q.data.split(":", 1)[1])
    method = context.user_data["topup_method"]

    topup_id = f"shopnluna:{uuid.uuid4().hex[:10]}"
    context.user_data["topup_id"] = topup_id

    exec_sql(
        """INSERT INTO topups (topup_id, user_id, amount, method, status)
           VALUES (%s,%s,%s,%s,'PENDING');""",
        (topup_id, q.from_user.id, amount, method)
    )

    await send_clean_menu(
        update, context,
        f"🆔 **Top-up ID:** `{topup_id}`\n"
        f"💵 Amount: **{fmt_money(amount)}**\n"
        f"💳 Method: **{method.upper()}**\n\n"
        "📸 Now send your **payment screenshot** here.\n"
        "⏳ Status: **Waiting for approval**",
        parse_mode=ParseMode.MARKDOWN
    )

async def proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "topup_id" not in context.user_data:
        return

    topup_id = context.user_data["topup_id"]
    user_id = update.effective_user.id
    file_id = update.message.photo[-1].file_id

    exec_sql("UPDATE topups SET proof_file_id=%s WHERE topup_id=%s", (file_id, topup_id))

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"admin:topup:approve:{topup_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"admin:topup:reject:{topup_id}"),
    ]])

    for admin_id in ADMIN_IDS:
        await context.bot.send_photo(
            chat_id=admin_id,
            photo=file_id,
            caption=(
                "💳 **New Top-up Proof**\n\n"
                f"Topup ID: `{topup_id}`\n"
                f"User ID: `{user_id}`\n\n"
                "Choose an action:"
            ),
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )

    await send_clean_menu(update, context, "✅ Proof received.\n⏳ Waiting for admin approval.\n\nThank you 💗")

# =========================
# SHOP (Customer)
# =========================
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = fetch_all("SELECT id, name FROM categories ORDER BY id ASC")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="shop:back")])

    push_screen(context, "main")
    await send_clean_menu(update, context, "🛍 **Shop Categories**", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if q.data == "shop:back":
        await send_clean_menu(update, context, "Back ✅", reply_markup=MAIN_MENU)
        return

    parts = q.data.split(":")
    if parts[1] == "cat":
        cat_id = int(parts[2])
        products = fetch_all("""
            SELECT id, name FROM products
            WHERE is_active=TRUE AND category_id=%s
            ORDER BY id ASC
        """, (cat_id,))
        if not products:
            await send_clean_menu(update, context, "No products in this category yet 💗")
            return

        rows = [[InlineKeyboardButton(f"#{i+1} {p['name']}", callback_data=f"shop:prod:{p['id']}")]
                for i, p in enumerate(products)]
        rows.append([InlineKeyboardButton("⬅️ Categories", callback_data="shop:cats")])
        await send_clean_menu(update, context, "Choose product:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if parts[1] == "cats":
        await shop_from_callback(update, context)
        return

    if parts[1] == "prod":
        prod_id = int(parts[2])
        prod = fetch_one("SELECT id,name,description FROM products WHERE id=%s AND is_active=TRUE", (prod_id,))
        if not prod:
            await send_clean_menu(update, context, "Product not found.")
            return

        variants = fetch_all("""
            SELECT v.id, v.name, v.price,
                   (SELECT COUNT(*) FROM stock_items s WHERE s.variant_id=v.id AND s.is_sold=FALSE) AS stock
            FROM variants v
            WHERE v.product_id=%s AND v.is_active=TRUE
            ORDER BY v.id ASC
        """, (prod_id,))

        if not variants:
            await send_clean_menu(update, context, "No variants yet for this product.")
            return

        rows = [[InlineKeyboardButton(
            f"#{i+1} {v['name']} — {fmt_money(int(v['price']))} (stock {v['stock']})",
            callback_data=f"buy:variant:{v['id']}"
        )] for i, v in enumerate(variants)]

        rows.append([InlineKeyboardButton("⬅️ Products", callback_data=f"shop:cat:{prod['id']}")])
        rows.append([InlineKeyboardButton("⬅️ Categories", callback_data="shop:cats")])

        await send_clean_menu(
            update, context,
            f"🧾 **{prod['name']}**\n\n{prod['description']}\n\nChoose variant:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.MARKDOWN
        )
        return

async def shop_from_callback(update, context):
    cats = fetch_all("SELECT id, name FROM categories ORDER BY id ASC")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="shop:back")])
    await send_clean_menu(update, context, "🛍 **Shop Categories**", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

# =========================
# BUY FLOW (variant -> qty -> confirm)
# =========================
async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    parts = q.data.split(":")
    if parts[0] != "buy":
        return

    if parts[1] == "variant":
        variant_id = int(parts[2])
        v = fetch_one("""
            SELECT v.id, v.name, v.price, v.thumbnail_file_id, p.name AS product_name
            FROM variants v JOIN products p ON p.id=v.product_id
            WHERE v.id=%s AND v.is_active=TRUE
        """, (variant_id,))
        if not v:
            await send_clean_menu(update, context, "Variant not found.")
            return

        stock = fetch_one("SELECT COUNT(*) AS c FROM stock_items WHERE variant_id=%s AND is_sold=FALSE", (variant_id,))
        stock_count = int(stock["c"]) if stock else 0

        context.user_data["buy_variant_id"] = variant_id
        context.user_data["buy_unit_price"] = int(v["price"])

        # qty buttons
        qty_rows = [
            [InlineKeyboardButton("1", callback_data="buy:qty:1"),
             InlineKeyboardButton("2", callback_data="buy:qty:2"),
             InlineKeyboardButton("3", callback_data="buy:qty:3")],
            [InlineKeyboardButton("5", callback_data="buy:qty:5"),
             InlineKeyboardButton("10", callback_data="buy:qty:10")],
            [InlineKeyboardButton("⬅️ Back", callback_data="buy:back_to_variants")],
        ]

        text = (
            f"✅ **{v['product_name']}**\n"
            f"• Variant: **{v['name']}**\n"
            f"• Price: **{fmt_money(int(v['price']))}**\n"
            f"• Stock: **{stock_count}**\n\n"
            "Choose quantity:"
        )

        # show thumbnail if available
        if v.get("thumbnail_file_id"):
            try:
                await delete_last_menu(context, q.message.chat_id)
                msg = await q.message.reply_photo(
                    photo=v["thumbnail_file_id"],
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(qty_rows),
                    parse_mode=ParseMode.MARKDOWN
                )
                context.user_data["last_menu_msg_id"] = msg.message_id
                return
            except Exception:
                pass

        await send_clean_menu(update, context, text, reply_markup=InlineKeyboardMarkup(qty_rows), parse_mode=ParseMode.MARKDOWN)
        return

    if parts[1] == "qty":
        qty = int(parts[2])
        variant_id = context.user_data.get("buy_variant_id")
        unit_price = context.user_data.get("buy_unit_price")
        if not variant_id or not unit_price:
            await send_clean_menu(update, context, "Please choose a variant again.")
            return

        # check stock
        stock = fetch_one("SELECT COUNT(*) AS c FROM stock_items WHERE variant_id=%s AND is_sold=FALSE", (variant_id,))
        stock_count = int(stock["c"]) if stock else 0
        if qty > stock_count:
            await send_clean_menu(update, context, f"❌ Not enough stock.\nAvailable: {stock_count}")
            return

        total = qty * unit_price
        context.user_data["buy_qty"] = qty
        context.user_data["buy_total"] = total

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Order", callback_data="buy:confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="buy:cancel")],
            [InlineKeyboardButton("⬅️ Back", callback_data="buy:back_to_qty")],
        ])

        await send_clean_menu(
            update, context,
            f"🧾 **Confirm Order**\n\nQty: **{qty}**\nTotal: **{fmt_money(total)}**\n\nProceed?",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if parts[1] == "cancel":
        context.user_data.pop("buy_variant_id", None)
        context.user_data.pop("buy_unit_price", None)
        context.user_data.pop("buy_qty", None)
        context.user_data.pop("buy_total", None)
        await send_clean_menu(update, context, "Cancelled ✅", reply_markup=MAIN_MENU)
        return

    if parts[1] == "confirm":
        user_id = q.from_user.id
        variant_id = context.user_data.get("buy_variant_id")
        qty = int(context.user_data.get("buy_qty", 0))
        unit_price = int(context.user_data.get("buy_unit_price", 0))
        total = int(context.user_data.get("buy_total", 0))

        if not variant_id or qty <= 0 or total <= 0:
            await send_clean_menu(update, context, "Please try again.")
            return

        purchase_token = f"pur:{uuid.uuid4().hex[:12]}"

        # atomic: check balance + reserve stock + deduct balance
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
                cur.execute("SELECT balance FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
                bal = int(cur.fetchone()[0])

                if bal < total:
                    conn.rollback()
                    await send_clean_menu(update, context, f"❌ Not enough balance.\nBalance: {fmt_money(bal)}\nNeed: {fmt_money(total)}")
                    return

                # reserve stock rows (first available)
                cur.execute("""
                    SELECT id, payload
                    FROM stock_items
                    WHERE variant_id=%s AND is_sold=FALSE
                    ORDER BY id ASC
                    LIMIT %s
                    FOR UPDATE
                """, (variant_id, qty))
                rows = cur.fetchall()

                if len(rows) < qty:
                    conn.rollback()
                    await send_clean_menu(update, context, "❌ Stock changed. Not enough stock now. Please try again.")
                    return

                # deduct balance
                cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (total, user_id))

                # create purchase
                cur.execute("""
                    INSERT INTO purchases(purchase_token,user_id,variant_id,qty,unit_price,total_price,delivered)
                    VALUES(%s,%s,%s,%s,%s,%s,FALSE)
                """, (purchase_token, user_id, variant_id, qty, unit_price, total))

                # mark stock sold
                stock_ids = [r[0] for r in rows]
                cur.execute("""
                    UPDATE stock_items
                    SET is_sold=TRUE, sold_at=NOW(), purchase_token=%s
                    WHERE id = ANY(%s)
                """, (purchase_token, stock_ids))

                conn.commit()

        # deliver
        lines = [r[1] for r in rows]
        deliver_text = "\n".join(lines)

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ **Order Delivered**\nToken: `{purchase_token}`\n\n{deliver_text}",
            parse_mode=ParseMode.MARKDOWN
        )

        exec_sql("UPDATE purchases SET delivered=TRUE, delivered_at=NOW() WHERE purchase_token=%s", (purchase_token,))

        # cleanup
        context.user_data.pop("buy_variant_id", None)
        context.user_data.pop("buy_unit_price", None)
        context.user_data.pop("buy_qty", None)
        context.user_data.pop("buy_total", None)

        await send_clean_menu(update, context, "✅ Purchase complete! 💗", reply_markup=MAIN_MENU)
        return

# =========================
# ADMIN
# =========================
def admin_panel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧩 Manage Products", callback_data="admin:products")],
        [InlineKeyboardButton("🧷 Manage Variants", callback_data="admin:variants")],
        [InlineKeyboardButton("📦 Add Stock", callback_data="admin:stock")],
        [InlineKeyboardButton("🖼 Set Thumbnail", callback_data="admin:thumb")],
        [InlineKeyboardButton("⏳ Pending Topups", callback_data="admin:topups")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin:back")],
    ])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await send_clean_menu(update, context, "❌ Access denied.")
        return
    await send_clean_menu(update, context, "🔐 **Admin Panel**", reply_markup=admin_panel_kb(), parse_mode=ParseMode.MARKDOWN)

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await send_clean_menu(update, context, "❌ Access denied.")
        return

    data = q.data

    if data == "admin:back":
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("admin_step", None)
        await send_clean_menu(update, context, "Back ✅", reply_markup=MAIN_MENU)
        return

    # TOPUP approve/reject
    if data.startswith("admin:topup:"):
        _, _, action, topup_id = data.split(":", 3)
        await admin_decide_topup(update, context, action, topup_id)
        return

    if data == "admin:topups":
        rows = fetch_all("""
            SELECT topup_id,user_id,amount,method,created_at
            FROM topups WHERE status='PENDING'
            ORDER BY created_at DESC LIMIT 10
        """)
        if not rows:
            await send_clean_menu(update, context, "✅ No pending top-ups.")
            return
        lines = ["⏳ **Pending Top-ups**\n"]
        for r in rows:
            lines.append(f"• `{r['topup_id']}` — user `{r['user_id']}` — {fmt_money(int(r['amount']))} — {r['method']}")
        await send_clean_menu(update, context, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # ===== Manage Products =====
    if data == "admin:products":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        kb = [[InlineKeyboardButton(c["name"], callback_data=f"admin:products:cat:{c['id']}")] for c in cats]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")])
        await send_clean_menu(update, context, "Choose category:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "admin:panel":
        await send_clean_menu(update, context, "🔐 **Admin Panel**", reply_markup=admin_panel_kb(), parse_mode=ParseMode.MARKDOWN)
        return

    if data.startswith("admin:products:cat:"):
        cat_id = int(data.split(":")[-1])
        context.user_data["admin_cat_id"] = cat_id
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Product", callback_data="admin:products:add")],
            [InlineKeyboardButton("✏️ Edit/Delete Product", callback_data="admin:products:list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:products")],
        ])
        await send_clean_menu(update, context, "Product actions:", reply_markup=kb)
        return

    if data == "admin:products:add":
        context.user_data["admin_mode"] = "add_product"
        context.user_data["admin_step"] = "name"
        await send_clean_menu(update, context, "Send product name now:")
        return

    if data == "admin:products:list":
        cat_id = int(context.user_data.get("admin_cat_id", 0))
        prods = fetch_all("SELECT id,name FROM products WHERE category_id=%s ORDER BY id", (cat_id,))
        if not prods:
            await send_clean_menu(update, context, "No products yet.")
            return
        kb = [[InlineKeyboardButton(f"#{i+1} {p['name']}", callback_data=f"admin:products:edit:{p['id']}")] for i,p in enumerate(prods)]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin:products:cat:{cat_id}")])
        await send_clean_menu(update, context, "Choose product:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:products:edit:"):
        prod_id = int(data.split(":")[-1])
        context.user_data["admin_prod_id"] = prod_id
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Name", callback_data="admin:products:editname")],
            [InlineKeyboardButton("✏️ Edit Description", callback_data="admin:products:editdesc")],
            [InlineKeyboardButton("🗑 Delete Product", callback_data="admin:products:delete")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:products:list")],
        ])
        await send_clean_menu(update, context, "Edit product:", reply_markup=kb)
        return

    if data == "admin:products:editname":
        context.user_data["admin_mode"] = "edit_product_name"
        context.user_data["admin_step"] = "text"
        await send_clean_menu(update, context, "Send new product name:")
        return

    if data == "admin:products:editdesc":
        context.user_data["admin_mode"] = "edit_product_desc"
        context.user_data["admin_step"] = "text"
        await send_clean_menu(update, context, "Send new product description:")
        return

    if data == "admin:products:delete":
        prod_id = int(context.user_data.get("admin_prod_id", 0))
        exec_sql("DELETE FROM products WHERE id=%s", (prod_id,))
        await send_clean_menu(update, context, "✅ Product deleted.", reply_markup=admin_panel_kb())
        return

    # ===== Manage Variants =====
    if data == "admin:variants":
        # choose product first
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id")
        kb = [[InlineKeyboardButton(c["name"], callback_data=f"admin:variants:cat:{c['id']}")] for c in cats]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")])
        await send_clean_menu(update, context, "Choose category:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:variants:cat:"):
        cat_id = int(data.split(":")[-1])
        prods = fetch_all("SELECT id,name FROM products WHERE category_id=%s ORDER BY id", (cat_id,))
        if not prods:
            await send_clean_menu(update, context, "No products yet in this category.")
            return
        kb = [[InlineKeyboardButton(f"#{i+1} {p['name']}", callback_data=f"admin:variants:prod:{p['id']}")] for i,p in enumerate(prods)]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:variants")])
        await send_clean_menu(update, context, "Choose product for variant:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:variants:prod:"):
        prod_id = int(data.split(":")[-1])
        context.user_data["admin_prod_id"] = prod_id
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Variant", callback_data="admin:variant:add")],
            [InlineKeyboardButton("✏️ Edit/Delete Variant", callback_data="admin:variant:list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:variants")],
        ])
        await send_clean_menu(update, context, "Variant actions:", reply_markup=kb)
        return

    if data == "admin:variant:add":
        context.user_data["admin_mode"] = "add_variant"
        context.user_data["admin_step"] = "name"
        await send_clean_menu(update, context, "Send variant name now (example: 1 Month / 3 Months):")
        return

    if data == "admin:variant:list":
        prod_id = int(context.user_data.get("admin_prod_id", 0))
        vars_ = fetch_all("SELECT id,name,price FROM variants WHERE product_id=%s ORDER BY id", (prod_id,))
        if not vars_:
            await send_clean_menu(update, context, "No variants yet.")
            return
        kb = [[InlineKeyboardButton(f"#{i+1} {v['name']} — {fmt_money(int(v['price']))}", callback_data=f"admin:variant:edit:{v['id']}")]
              for i,v in enumerate(vars_)]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data=f"admin:variants:prod:{prod_id}")])
        await send_clean_menu(update, context, "Choose variant:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:variant:edit:"):
        vid = int(data.split(":")[-1])
        context.user_data["admin_variant_id"] = vid
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Name", callback_data="admin:variant:editname")],
            [InlineKeyboardButton("💸 Edit Price", callback_data="admin:variant:editprice")],
            [InlineKeyboardButton("🗑 Delete Variant", callback_data="admin:variant:delete")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:variant:list")],
        ])
        await send_clean_menu(update, context, "Edit variant:", reply_markup=kb)
        return

    if data == "admin:variant:editname":
        context.user_data["admin_mode"] = "edit_variant_name"
        context.user_data["admin_step"] = "text"
        await send_clean_menu(update, context, "Send new variant name:")
        return

    if data == "admin:variant:editprice":
        context.user_data["admin_mode"] = "edit_variant_price"
        context.user_data["admin_step"] = "price"
        await send_clean_menu(update, context, "Send variant price (number only):")
        return

    if data == "admin:variant:delete":
        vid = int(context.user_data.get("admin_variant_id", 0))
        exec_sql("DELETE FROM variants WHERE id=%s", (vid,))
        await send_clean_menu(update, context, "✅ Variant deleted.", reply_markup=admin_panel_kb())
        return

    # ===== Add Stock =====
    if data == "admin:stock":
        # pick variant first
        vars_ = fetch_all("""
            SELECT v.id, p.name as product_name, v.name, v.price
            FROM variants v JOIN products p ON p.id=v.product_id
            WHERE v.is_active=TRUE
            ORDER BY p.id, v.id
            LIMIT 50
        """)
        if not vars_:
            await send_clean_menu(update, context, "No variants found.")
            return

        kb = [[InlineKeyboardButton(f"#{i+1} {r['product_name']} / {r['name']}", callback_data=f"admin:stock:pick:{r['id']}")]
              for i,r in enumerate(vars_)]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")])
        await send_clean_menu(update, context, "Choose variant to add stock:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:stock:pick:"):
        vid = int(data.split(":")[-1])
        context.user_data["admin_variant_id"] = vid
        context.user_data["admin_mode"] = "add_stock"
        context.user_data["admin_step"] = "stock_text"
        await send_clean_menu(
            update, context,
            "Send stock lines now.\n\nExample:\nemail1:pass1\nemail2:pass2\n\n(Each line = 1 stock item)"
        )
        return

    # ===== Set Thumbnail =====
    if data == "admin:thumb":
        vars_ = fetch_all("""
            SELECT v.id, p.name as product_name, v.name
            FROM variants v JOIN products p ON p.id=v.product_id
            WHERE v.is_active=TRUE
            ORDER BY p.id, v.id
            LIMIT 50
        """)
        if not vars_:
            await send_clean_menu(update, context, "No variants found.")
            return

        kb = [[InlineKeyboardButton(f"#{i+1} {r['product_name']} / {r['name']}", callback_data=f"admin:thumb:pick:{r['id']}")]
              for i,r in enumerate(vars_)]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:panel")])
        await send_clean_menu(update, context, "Choose variant to set thumbnail:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:thumb:pick:"):
        vid = int(data.split(":")[-1])
        context.user_data["admin_variant_id"] = vid
        context.user_data["admin_mode"] = "set_thumb"
        context.user_data["admin_step"] = "photo"
        await send_clean_menu(update, context, "Now send the thumbnail image (photo):")
        return

async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles admin text steps (add/edit product/variant, add stock)
    """
    if not is_admin(update.effective_user.id):
        return

    mode = context.user_data.get("admin_mode")
    step = context.user_data.get("admin_step")
    if not mode or not step:
        return

    text = (update.message.text or "").strip()

    # ADD PRODUCT
    if mode == "add_product":
        if step == "name":
            context.user_data["tmp_product_name"] = text
            context.user_data["admin_step"] = "desc"
            await send_clean_menu(update, context, "Send product description now:")
            return
        if step == "desc":
            cat_id = int(context.user_data.get("admin_cat_id", 0))
            name = context.user_data.get("tmp_product_name", "Product")
            desc = text
            exec_sql("INSERT INTO products(category_id,name,description,is_active) VALUES(%s,%s,%s,TRUE)", (cat_id, name, desc))
            context.user_data.pop("admin_mode", None)
            context.user_data.pop("admin_step", None)
            context.user_data.pop("tmp_product_name", None)
            await send_clean_menu(update, context, "✅ Product added!", reply_markup=admin_panel_kb())
            return

    # EDIT PRODUCT NAME/DESC
    if mode == "edit_product_name":
        prod_id = int(context.user_data.get("admin_prod_id", 0))
        exec_sql("UPDATE products SET name=%s WHERE id=%s", (text, prod_id))
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("admin_step", None)
        await send_clean_menu(update, context, "✅ Updated product name.", reply_markup=admin_panel_kb())
        return

    if mode == "edit_product_desc":
        prod_id = int(context.user_data.get("admin_prod_id", 0))
        exec_sql("UPDATE products SET description=%s WHERE id=%s", (text, prod_id))
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("admin_step", None)
        await send_clean_menu(update, context, "✅ Updated product description.", reply_markup=admin_panel_kb())
        return

    # ADD VARIANT
    if mode == "add_variant":
        if step == "name":
            context.user_data["tmp_variant_name"] = text
            context.user_data["admin_step"] = "price"
            await send_clean_menu(update, context, "Send variant price (number only):")
            return

        if step == "price":
            price = parse_int_money(text)
            if price < 0:
                await send_clean_menu(update, context, "Send price as number only. Example: 8")
                return

            prod_id = int(context.user_data.get("admin_prod_id", 0))
            vname = context.user_data.get("tmp_variant_name", "Variant")
            exec_sql("INSERT INTO variants(product_id,name,price,is_active) VALUES(%s,%s,%s,TRUE)", (prod_id, vname, price))

            context.user_data.pop("admin_mode", None)
            context.user_data.pop("admin_step", None)
            context.user_data.pop("tmp_variant_name", None)
            await send_clean_menu(update, context, f"✅ Variant added! Price: {fmt_money(price)}", reply_markup=admin_panel_kb())
            return

    # EDIT VARIANT NAME
    if mode == "edit_variant_name":
        vid = int(context.user_data.get("admin_variant_id", 0))
        exec_sql("UPDATE variants SET name=%s WHERE id=%s", (text, vid))
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("admin_step", None)
        await send_clean_menu(update, context, "✅ Updated variant name.", reply_markup=admin_panel_kb())
        return

    # EDIT VARIANT PRICE
    if mode == "edit_variant_price":
        vid = int(context.user_data.get("admin_variant_id", 0))
        price = parse_int_money(text)
        if price < 0:
            await send_clean_menu(update, context, "Send price as number only. Example: 8")
            return
        exec_sql("UPDATE variants SET price=%s WHERE id=%s", (price, vid))
        context.user_data.pop("admin_mode", None)
        context.user_data.pop("admin_step", None)
        await send_clean_menu(update, context, f"✅ Updated price to {fmt_money(price)}", reply_markup=admin_panel_kb())
        return

    # ADD STOCK (bulk lines)
    if mode == "add_stock":
        vid = int(context.user_data.get("admin_variant_id", 0))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            await send_clean_menu(update, context, "Send stock lines. Each line = 1 item.")
            return

        with db() as conn:
            with conn.cursor() as cur:
                for ln in lines:
                    cur.execute("INSERT INTO stock_items(variant_id,payload,is_sold) VALUES(%s,%s,FALSE)", (vid, ln))
                conn.commit()

        context.user_data.pop("admin_mode", None)
        context.user_data.pop("admin_step", None)
        await send_clean_menu(update, context, f"✅ Added {len(lines)} stock item(s).", reply_markup=admin_panel_kb())
        return

async def admin_photo_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles admin photo step for thumbnails
    """
    if not is_admin(update.effective_user.id):
        return

    mode = context.user_data.get("admin_mode")
    step = context.user_data.get("admin_step")
    if mode != "set_thumb" or step != "photo":
        return

    vid = int(context.user_data.get("admin_variant_id", 0))
    file_id = update.message.photo[-1].file_id
    exec_sql("UPDATE variants SET thumbnail_file_id=%s WHERE id=%s", (file_id, vid))

    context.user_data.pop("admin_mode", None)
    context.user_data.pop("admin_step", None)
    await send_clean_menu(update, context, "✅ Thumbnail set!", reply_markup=admin_panel_kb())

async def admin_decide_topup(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, topup_id: str):
    q = update.callback_query
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, amount, status FROM topups WHERE topup_id=%s FOR UPDATE", (topup_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                await send_clean_menu(update, context, "Top-up not found.")
                return

            user_id, amount, status = int(row[0]), int(row[1]), row[2]
            if status != "PENDING":
                conn.rollback()
                await send_clean_menu(update, context, f"Already decided: {status}")
                return

            if action == "approve":
                cur.execute("UPDATE topups SET status='APPROVED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s", (q.from_user.id, topup_id))
                cur.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
                cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amount, user_id))
                conn.commit()
                await context.bot.send_message(chat_id=user_id, text=f"✅ Top-up approved! Amount: {fmt_money(amount)}")
                await send_clean_menu(update, context, "✅ Approved.", reply_markup=admin_panel_kb())
                return

            if action == "reject":
                cur.execute("UPDATE topups SET status='REJECTED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s", (q.from_user.id, topup_id))
                conn.commit()
                await context.bot.send_message(chat_id=user_id, text="❌ Top-up rejected. Contact admin if mistake.")
                await send_clean_menu(update, context, "❌ Rejected.", reply_markup=admin_panel_kb())
                return

# =========================
# BOOT
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in Railway Variables.")
    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))

    # main menu
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop))
    app.add_handler(MessageHandler(filters.Regex(r"^💳 Add Balance$"), add_balance))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 Balance$"), balance))
    app.add_handler(MessageHandler(filters.Regex(r"^📜 History$"), history))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))

    # callbacks (IMPORTANT: patterns must match callback_data prefixes)
    app.add_handler(CallbackQueryHandler(cb_payment, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(cb_amount, pattern=r"^amt:"))
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))

    # photos
    app.add_handler(MessageHandler(filters.PHOTO, proof_photo))          # user topup proof
    app.add_handler(MessageHandler(filters.PHOTO, admin_photo_router))  # admin thumbnail

    # admin text steps (add/edit product/variant/stock)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_router))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
