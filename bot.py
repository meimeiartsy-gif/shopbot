import os
import uuid
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from db import ensure_schema, fetch_all, fetch_one, exec_sql, db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("premshop")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = set(int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit())

GCASH_QR_FILE_ID = os.getenv("GCASH_QR_FILE_ID", "").strip()
GOTYME_QR_FILE_ID = os.getenv("GOTYME_QR_FILE_ID", "").strip()

PAYMENT_TEXT_GCASH = os.getenv(
    "PAYMENT_TEXT_GCASH",
    "📌 *GCash Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here"
).strip()

PAYMENT_TEXT_GOTYME = os.getenv(
    "PAYMENT_TEXT_GOTYME",
    "📌 *GoTyme Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here"
).strip()

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Shop", "💳 Add Balance"],
        ["💰 Balance", "📜 History"],
        ["🆘 Help", "🔐 Admin"]
    ],
    resize_keyboard=True
)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def fmt_money(n: int) -> str:
    return f"₱{n:,}"

# ---------- BACK STACK ----------
def push_state(context, state: str):
    stack = context.user_data.get("nav_stack", [])
    stack.append(state)
    context.user_data["nav_stack"] = stack

def pop_state(context):
    stack = context.user_data.get("nav_stack", [])
    if stack:
        stack.pop()
    context.user_data["nav_stack"] = stack
    return stack[-1] if stack else ""

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    prev = pop_state(context)
    if not prev:
        await q.message.reply_text("Back to menu ✅", reply_markup=MAIN_MENU)
        return
    await render_state(q, context, prev)

async def render_state(q, context, state: str):
    if state == "cats":
        await show_categories(q, context, push=False); return
    if state.startswith("cat:"):
        await show_products(q, context, int(state.split(":")[1]), push=False); return
    if state.startswith("prod:"):
        await show_product(q, context, int(state.split(":")[1]), push=False); return
    if state.startswith("checkout:"):
        _, vid, qty = state.split(":")
        await show_checkout(q, context, int(vid), int(qty), push=False); return

# =========================
# START / BASIC
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    exec_sql("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
    context.user_data["nav_stack"] = []
    await update.message.reply_text(
        "Welcome to **Luna’s Prem Shop** 💗",
        reply_markup=MAIN_MENU,
        parse_mode=ParseMode.MARKDOWN
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Help**\n\n"
        "• Tap **Shop** to browse/buy\n"
        "• Tap **Add Balance** to top-up\n"
        "• Send proof screenshot after payment\n\n"
        "If you need help, contact admin 💗",
        parse_mode=ParseMode.MARKDOWN
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = fetch_one("SELECT balance FROM users WHERE user_id=%s", (uid,))
    bal = int(row["balance"]) if row else 0
    await update.message.reply_text(f"💰 Your balance: **{fmt_money(bal)}**", parse_mode=ParseMode.MARKDOWN)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = fetch_all("""
        SELECT oi.id, p.name AS product_name, v.name AS variant_name, oi.qty, oi.unit_price, oi.delivered
        FROM order_items oi
        JOIN variants v ON v.id=oi.variant_id
        JOIN products p ON p.id=oi.product_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.user_id=%s
        ORDER BY oi.id DESC
        LIMIT 15
    """, (uid,))
    if not rows:
        await update.message.reply_text("📜 No orders yet.")
        return
    lines = ["📜 **Recent Orders**\n"]
    for r in rows:
        st = "✅ delivered" if r["delivered"] else "⏳ pending"
        lines.append(f"• {r['product_name']} ({r['variant_name']}) x{r['qty']} — {fmt_money(int(r['unit_price']))} — {st}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# =========================
# TOP-UP FLOW
# =========================
async def add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("💙 GCash", callback_data="pay:gcash")],
        [InlineKeyboardButton("💜 GoTyme", callback_data="pay:gotyme")],
    ]
    await update.message.reply_text("💳 Choose payment method:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    method = q.data.split(":", 1)[1]
    context.user_data["topup_method"] = method

    if method == "gcash":
        file_id = GCASH_QR_FILE_ID
        text = PAYMENT_TEXT_GCASH
    else:
        file_id = GOTYME_QR_FILE_ID
        text = PAYMENT_TEXT_GOTYME

    if file_id:
        await q.message.reply_photo(photo=file_id, caption=text, parse_mode=ParseMode.MARKDOWN)
    else:
        await q.message.reply_text(text + "\n\n⚠️ QR not set in Railway variables.", parse_mode=ParseMode.MARKDOWN)

    rows = [
        [InlineKeyboardButton("₱50", callback_data="amt:50"), InlineKeyboardButton("₱100", callback_data="amt:100")],
        [InlineKeyboardButton("₱300", callback_data="amt:300"), InlineKeyboardButton("₱500", callback_data="amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="amt:1000")],
    ]
    await q.message.reply_text("✨ Choose top-up amount:", reply_markup=InlineKeyboardMarkup(rows))

async def cb_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if "topup_method" not in context.user_data:
        await q.message.reply_text("Please choose method again.")
        return

    amount = int(q.data.split(":", 1)[1])
    method = context.user_data["topup_method"]
    topup_id = f"shopnluna:{uuid.uuid4().hex[:10]}"
    context.user_data["topup_id"] = topup_id

    exec_sql("""
        INSERT INTO topups(topup_id,user_id,amount,method,status)
        VALUES(%s,%s,%s,%s,'PENDING')
    """, (topup_id, q.from_user.id, amount, method))

    await q.message.reply_text(
        f"🆔 **Top-up ID:** `{topup_id}`\n"
        f"Amount: **{fmt_money(amount)}**\n"
        f"Method: **{method.upper()}**\n\n"
        "📸 Now send your **payment screenshot** here.\n"
        "⏳ Waiting for admin approval.",
        parse_mode=ParseMode.MARKDOWN
    )

async def proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "topup_id" not in context.user_data:
        return
    topup_id = context.user_data["topup_id"]
    file_id = update.message.photo[-1].file_id
    exec_sql("UPDATE topups SET proof_file_id=%s WHERE topup_id=%s", (file_id, topup_id))

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"admin:topup:approve:{topup_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin:topup:reject:{topup_id}")
        ]
    ])

    for aid in ADMIN_IDS:
        await context.bot.send_photo(
            chat_id=aid,
            photo=file_id,
            caption=f"💳 **New Top-up Proof**\nTopup: `{topup_id}`\nUser: `{update.effective_user.id}`",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )

    await update.message.reply_text("✅ Proof received. Waiting for admin approval 💗")

# =========================
# SHOP
# =========================
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qmsg = update.message
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id ASC")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    await qmsg.reply_text("🛍 **Categories**", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

async def show_categories(q, context, push=True):
    if push: push_state(context, "cats")
    cats = fetch_all("SELECT id,name FROM categories ORDER BY id ASC")
    rows = [[InlineKeyboardButton(c["name"], callback_data=f"shop:cat:{c['id']}")] for c in cats]
    await q.message.reply_text("🛍 **Categories**", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN)

async def show_products(q, context, cat_id: int, push=True):
    if push: push_state(context, f"cat:{cat_id}")
    prods = fetch_all("""
        SELECT p.id,p.name,
               (SELECT COUNT(*) FROM variants v WHERE v.product_id=p.id AND v.is_active=TRUE) AS vcount
        FROM products p
        WHERE p.is_active=TRUE AND p.category_id=%s
        ORDER BY p.id ASC
    """, (cat_id,))
    if not prods:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav:back")]])
        await q.message.reply_text("No products here yet 💗", reply_markup=kb)
        return
    rows = [[InlineKeyboardButton(f"{p['name']} ({p['vcount']} variants)", callback_data=f"shop:prod:{p['id']}")] for p in prods]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="nav:back")])
    await q.message.reply_text("Select a product:", reply_markup=InlineKeyboardMarkup(rows))

async def show_product(q, context, prod_id: int, push=True):
    if push: push_state(context, f"prod:{prod_id}")
    prod = fetch_one("SELECT * FROM products WHERE id=%s AND is_active=TRUE", (prod_id,))
    if not prod:
        await q.message.reply_text("Product not found.")
        return

    variants = fetch_all("""
        SELECT v.id,v.name,v.price,
               (SELECT COUNT(*) FROM stocks s WHERE s.variant_id=v.id AND s.is_sold=FALSE) AS stock_left
        FROM variants v
        WHERE v.product_id=%s AND v.is_active=TRUE
        ORDER BY v.id ASC
    """, (prod_id,))

    lines = [f"🧾 **{prod['name']}**", "", prod["description"] or "", "", "Choose a variant:"]
    kb_rows = []
    for v in variants:
        kb_rows.append([
            InlineKeyboardButton(
                f"{v['name']} — {fmt_money(int(v['price']))} | stock:{int(v['stock_left'])}",
                callback_data=f"shop:var:{v['id']}"
            )
        ])
    kb_rows.append([InlineKeyboardButton("⬅️ Back", callback_data="nav:back")])
    kb = InlineKeyboardMarkup(kb_rows)

    if prod.get("thumbnail_file_id"):
        try:
            await q.message.reply_photo(photo=prod["thumbnail_file_id"], caption="\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            return
        except Exception:
            pass
    await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def show_checkout(q, context, variant_id: int, qty: int, push=True):
    if push: push_state(context, f"checkout:{variant_id}:{qty}")
    v = fetch_one("""
        SELECT v.id,v.name,v.price,
               p.name AS product_name,
               (SELECT COUNT(*) FROM stocks s WHERE s.variant_id=v.id AND s.is_sold=FALSE) AS stock_left
        FROM variants v
        JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.message.reply_text("Variant not found.")
        return

    total = int(v["price"]) * qty
    stock_left = int(v["stock_left"])
    if qty > stock_left:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav:back")]])
        await q.message.reply_text(f"❌ Not enough stock.\nAvailable: {stock_left}", reply_markup=kb)
        return

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"qty:dec:{variant_id}:{qty}"),
            InlineKeyboardButton(f"Qty: {qty}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"qty:inc:{variant_id}:{qty}")
        ],
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"buy:confirm:{variant_id}:{qty}"),
            InlineKeyboardButton("❌ Cancel", callback_data="buy:cancel")
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="nav:back")]
    ])

    await q.message.reply_text(
        f"🛒 **Checkout**\n\n"
        f"Product: **{v['product_name']}**\n"
        f"Variant: **{v['name']}**\n"
        f"Unit: **{fmt_money(int(v['price']))}**\n"
        f"Qty: **{qty}**\n"
        f"Total: **{fmt_money(total)}**\n\n"
        f"Stock left: **{stock_left}**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb
    )

async def cb_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "noop":
        return

    if data.startswith("shop:cat:"):
        cat_id = int(data.split(":")[2])
        push_state(context, "cats")
        await show_products(q, context, cat_id, push=True)
        return

    if data.startswith("shop:prod:"):
        prod_id = int(data.split(":")[2])
        await show_product(q, context, prod_id, push=True)
        return

    if data.startswith("shop:var:"):
        variant_id = int(data.split(":")[2])
        await show_checkout(q, context, variant_id, 1, push=True)
        return

async def cb_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, action, variant_id, qty = q.data.split(":")
    variant_id = int(variant_id)
    qty = int(qty)
    qty = qty + 1 if action == "inc" else max(1, qty - 1)
    await show_checkout(q, context, variant_id, qty, push=True)

async def cb_buy_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pop_state(context)      # checkout
    prev = pop_state(context)  # back
    if not prev:
        await q.message.reply_text("Cancelled ✅", reply_markup=MAIN_MENU)
        return
    await render_state(q, context, prev)

async def cb_buy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, _, variant_id, qty = q.data.split(":")
    variant_id = int(variant_id)
    qty = int(qty)
    user_id = q.from_user.id

    v = fetch_one("""
        SELECT v.id,v.name,v.price,p.id AS product_id,p.name AS product_name
        FROM variants v JOIN products p ON p.id=v.product_id
        WHERE v.id=%s AND v.is_active=TRUE
    """, (variant_id,))
    if not v:
        await q.message.reply_text("Variant not found.")
        return

    unit_price = int(v["price"])
    total = unit_price * qty

    stock_payloads = []
    order_item_id = None

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
            cur.execute("SELECT balance FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
            bal = int(cur.fetchone()[0])
            if bal < total:
                conn.rollback()
                await q.message.reply_text(f"❌ Not enough balance.\nBalance: {fmt_money(bal)}\nTotal: {fmt_money(total)}")
                return

            cur.execute("""
                SELECT id,payload FROM stocks
                WHERE variant_id=%s AND is_sold=FALSE
                ORDER BY id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            """, (variant_id, qty))
            rows = cur.fetchall()
            if len(rows) < qty:
                conn.rollback()
                await q.message.reply_text("❌ Not enough stock. Try again later.")
                return

            stock_ids = []
            for sid, payload in rows:
                stock_ids.append(sid)
                stock_payloads.append(payload)

            cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (total, user_id))

            order_token = f"ord:{uuid.uuid4().hex}"
            cur.execute("INSERT INTO orders(order_token,user_id,total,status) VALUES(%s,%s,%s,'PAID') RETURNING id",
                        (order_token, user_id, total))
            order_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO order_items(order_id,product_id,variant_id,qty,unit_price,delivered)
                VALUES(%s,%s,%s,%s,%s,FALSE) RETURNING id
            """, (order_id, v["product_id"], variant_id, qty, unit_price))
            order_item_id = cur.fetchone()[0]

            cur.execute("""
                UPDATE stocks
                SET is_sold=TRUE, sold_at=NOW(), sold_to=%s, order_item_id=%s
                WHERE id = ANY(%s)
            """, (user_id, order_item_id, stock_ids))

            conn.commit()

    delivered_text = "\n".join(stock_payloads)
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"📦 **Auto Delivery**\n\n"
            f"Product: **{v['product_name']}**\n"
            f"Variant: **{v['name']}**\n"
            f"Qty: **{qty}**\n\n"
            f"✅ Here are your items:\n\n"
            f"```{delivered_text}```\n\n"
            "Keep it safe 💗"
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    exec_sql("UPDATE order_items SET delivered=TRUE, delivered_at=NOW() WHERE id=%s", (order_item_id,))
    await q.message.reply_text("✅ Order confirmed & delivered 💗")

# =========================
# ADMIN PANEL (FULL)
# =========================
ADMIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ Add Product", callback_data="admin:addprod")],
    [InlineKeyboardButton("✏️ Edit/Delete Product", callback_data="admin:manageprod")],
    [InlineKeyboardButton("➕ Add Variant", callback_data="admin:addvar")],
    [InlineKeyboardButton("✏️ Edit/Delete Variant", callback_data="admin:managevar")],
    [InlineKeyboardButton("➕ Add Stock", callback_data="admin:addstock")],
    [InlineKeyboardButton("🖼 Set Thumbnail", callback_data="admin:setthumb")],
    [InlineKeyboardButton("⏳ Pending Top-ups", callback_data="admin:pendingtopups")],
])

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return
    await update.message.reply_text("🔐 **Admin Panel**", reply_markup=ADMIN_MENU, parse_mode=ParseMode.MARKDOWN)

def admin_only(uid: int) -> bool:
    return is_admin(uid)

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not admin_only(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    data = q.data

    # --- Topup approve/reject ---
    if data.startswith("admin:topup:"):
        _, _, action, topup_id = data.split(":", 3)
        await admin_decide_topup(q, context, action, topup_id)
        return

    # --- Pending topups list ---
    if data == "admin:pendingtopups":
        rows = fetch_all("""
            SELECT topup_id,user_id,amount,method,created_at,proof_file_id
            FROM topups WHERE status='PENDING'
            ORDER BY created_at DESC LIMIT 10
        """)
        if not rows:
            await q.message.reply_text("✅ No pending top-ups.")
            return

        for r in rows:
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"admin:topup:approve:{r['topup_id']}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"admin:topup:reject:{r['topup_id']}")
                ]
            ])
            txt = (
                f"💳 **Pending Top-up**\n"
                f"ID: `{r['topup_id']}`\n"
                f"User: `{r['user_id']}`\n"
                f"Amount: **{fmt_money(int(r['amount']))}**\n"
                f"Method: **{r['method']}**"
            )
            if r.get("proof_file_id"):
                await q.message.reply_photo(photo=r["proof_file_id"], caption=txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            else:
                await q.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    # --- Add Product ---
    if data == "admin:addprod":
        cats = fetch_all("SELECT id,name FROM categories ORDER BY id ASC")
        kb = [[InlineKeyboardButton(c["name"], callback_data=f"admin:addprod:cat:{c['id']}")] for c in cats]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:back")])
        await q.message.reply_text("Choose category:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:addprod:cat:"):
        cat_id = int(data.split(":")[3])
        context.user_data["admin_addprod_cat"] = cat_id
        context.user_data["admin_mode"] = "addprod_name"
        await q.message.reply_text("Send product name now:")
        return

    # --- Manage products (list) ---
    if data == "admin:manageprod":
        prods = fetch_all("SELECT id,name FROM products ORDER BY id DESC LIMIT 30")
        if not prods:
            await q.message.reply_text("No products yet.")
            return
        kb = [[InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"admin:prod:{p['id']}")] for p in prods]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:back")])
        await q.message.reply_text("Select product to manage:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:prod:"):
        prod_id = int(data.split(":")[2])
        prod = fetch_one("SELECT * FROM products WHERE id=%s", (prod_id,))
        if not prod:
            await q.message.reply_text("Not found.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Name", callback_data=f"admin:prodedit:name:{prod_id}")],
            [InlineKeyboardButton("✏️ Edit Description", callback_data=f"admin:prodedit:desc:{prod_id}")],
            [InlineKeyboardButton("✅ Toggle Active", callback_data=f"admin:prodedit:toggle:{prod_id}")],
            [InlineKeyboardButton("🗑 Delete Product", callback_data=f"admin:proddelete:{prod_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:manageprod")],
        ])
        await q.message.reply_text(
            f"🧾 **Product #{prod_id}**\nName: {prod['name']}\nActive: {prod['is_active']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return

    if data.startswith("admin:prodedit:toggle:"):
        prod_id = int(data.split(":")[3])
        exec_sql("UPDATE products SET is_active = NOT is_active WHERE id=%s", (prod_id,))
        await q.message.reply_text("✅ Updated active status.")
        return

    if data.startswith("admin:proddelete:"):
        prod_id = int(data.split(":")[2])
        exec_sql("DELETE FROM products WHERE id=%s", (prod_id,))
        await q.message.reply_text("🗑 Deleted product.")
        return

    if data.startswith("admin:prodedit:name:"):
        prod_id = int(data.split(":")[3])
        context.user_data["admin_mode"] = "edit_prod_name"
        context.user_data["admin_target_id"] = prod_id
        await q.message.reply_text("Send the NEW product name:")
        return

    if data.startswith("admin:prodedit:desc:"):
        prod_id = int(data.split(":")[3])
        context.user_data["admin_mode"] = "edit_prod_desc"
        context.user_data["admin_target_id"] = prod_id
        await q.message.reply_text("Send the NEW product description:")
        return

    # --- Add Variant ---
    if data == "admin:addvar":
        prods = fetch_all("SELECT id,name FROM products ORDER BY id DESC LIMIT 30")
        kb = [[InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"admin:addvar:prod:{p['id']}")] for p in prods]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:back")])
        await q.message.reply_text("Choose product for variant:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:addvar:prod:"):
        prod_id = int(data.split(":")[3])
        context.user_data["admin_addvar_prod"] = prod_id
        context.user_data["admin_mode"] = "addvar_name"
        await q.message.reply_text("Send variant name now (example: 1 Month / 3 Months):")
        return

    # --- Manage Variants ---
    if data == "admin:managevar":
        vars_ = fetch_all("""
            SELECT v.id, p.name AS product_name, v.name, v.price, v.is_active
            FROM variants v JOIN products p ON p.id=v.product_id
            ORDER BY v.id DESC LIMIT 30
        """)
        if not vars_:
            await q.message.reply_text("No variants yet.")
            return
        kb = [[InlineKeyboardButton(f"#{v['id']} {v['product_name']} - {v['name']}", callback_data=f"admin:var:{v['id']}")] for v in vars_]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:back")])
        await q.message.reply_text("Select variant to manage:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:var:"):
        vid = int(data.split(":")[2])
        v = fetch_one("""
            SELECT v.*, p.name AS product_name
            FROM variants v JOIN products p ON p.id=v.product_id
            WHERE v.id=%s
        """, (vid,))
        if not v:
            await q.message.reply_text("Not found.")
            return
        stock_left = fetch_one("SELECT COUNT(*) AS c FROM stocks WHERE variant_id=%s AND is_sold=FALSE", (vid,))["c"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Name", callback_data=f"admin:varedit:name:{vid}")],
            [InlineKeyboardButton("✏️ Edit Price", callback_data=f"admin:varedit:price:{vid}")],
            [InlineKeyboardButton("✅ Toggle Active", callback_data=f"admin:varedit:toggle:{vid}")],
            [InlineKeyboardButton("🗑 Delete Variant", callback_data=f"admin:vardelete:{vid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin:managevar")],
        ])
        await q.message.reply_text(
            f"📦 **Variant #{vid}**\n"
            f"Product: {v['product_name']}\n"
            f"Name: {v['name']}\n"
            f"Price: {fmt_money(int(v['price']))}\n"
            f"Stock left: {stock_left}\n"
            f"Active: {v['is_active']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return

    if data.startswith("admin:varedit:toggle:"):
        vid = int(data.split(":")[3])
        exec_sql("UPDATE variants SET is_active = NOT is_active WHERE id=%s", (vid,))
        await q.message.reply_text("✅ Updated variant active.")
        return

    if data.startswith("admin:vardelete:"):
        vid = int(data.split(":")[2])
        exec_sql("DELETE FROM variants WHERE id=%s", (vid,))
        await q.message.reply_text("🗑 Deleted variant.")
        return

    if data.startswith("admin:varedit:name:"):
        vid = int(data.split(":")[3])
        context.user_data["admin_mode"] = "edit_var_name"
        context.user_data["admin_target_id"] = vid
        await q.message.reply_text("Send NEW variant name:")
        return

    if data.startswith("admin:varedit:price:"):
        vid = int(data.split(":")[3])
        context.user_data["admin_mode"] = "edit_var_price"
        context.user_data["admin_target_id"] = vid
        await q.message.reply_text("Send NEW price (number only):")
        return

    # --- Add Stock ---
    if data == "admin:addstock":
        vars_ = fetch_all("""
            SELECT v.id, p.name AS product_name, v.name
            FROM variants v JOIN products p ON p.id=v.product_id
            ORDER BY v.id DESC LIMIT 30
        """)
        if not vars_:
            await q.message.reply_text("No variants yet.")
            return
        kb = [[InlineKeyboardButton(f"#{v['id']} {v['product_name']} - {v['name']}", callback_data=f"admin:addstock:var:{v['id']}")] for v in vars_]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:back")])
        await q.message.reply_text("Choose variant to add stock:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:addstock:var:"):
        vid = int(data.split(":")[3])
        context.user_data["admin_mode"] = "add_stock_lines"
        context.user_data["admin_target_id"] = vid
        await q.message.reply_text(
            "Paste your stock now (ONE PER LINE).\n\nExample:\nemail1:pass1\nemail2:pass2\nemail3:pass3"
        )
        return

    # --- Set thumbnail ---
    if data == "admin:setthumb":
        prods = fetch_all("SELECT id,name FROM products ORDER BY id DESC LIMIT 30")
        if not prods:
            await q.message.reply_text("No products yet.")
            return
        kb = [[InlineKeyboardButton(f"#{p['id']} {p['name']}", callback_data=f"admin:setthumb:prod:{p['id']}")] for p in prods]
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="admin:back")])
        await q.message.reply_text("Choose product to set thumbnail:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("admin:setthumb:prod:"):
        prod_id = int(data.split(":")[3])
        context.user_data["admin_mode"] = "set_thumb_wait_photo"
        context.user_data["admin_target_id"] = prod_id
        await q.message.reply_text("Send the thumbnail PHOTO now.")
        return

    if data == "admin:back":
        await q.message.reply_text("Back ✅", reply_markup=ADMIN_MENU)
        return

async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    mode = context.user_data.get("admin_mode")
    if not mode:
        return

    text = (update.message.text or "").strip()

    if mode == "addprod_name":
        context.user_data["admin_addprod_name"] = text
        context.user_data["admin_mode"] = "addprod_desc"
        await update.message.reply_text("Send product description now:")
        return

    if mode == "addprod_desc":
        cat_id = context.user_data.get("admin_addprod_cat")
        name = context.user_data.get("admin_addprod_name", "")
        desc = text
        exec_sql("INSERT INTO products(category_id,name,description,is_active) VALUES(%s,%s,%s,TRUE)", (cat_id, name, desc))
        context.user_data["admin_mode"] = None
        await update.message.reply_text("✅ Product added!")
        return

    if mode == "edit_prod_name":
        prod_id = context.user_data["admin_target_id"]
        exec_sql("UPDATE products SET name=%s WHERE id=%s", (text, prod_id))
        context.user_data["admin_mode"] = None
        await update.message.reply_text("✅ Product name updated.")
        return

    if mode == "edit_prod_desc":
        prod_id = context.user_data["admin_target_id"]
        exec_sql("UPDATE products SET description=%s WHERE id=%s", (text, prod_id))
        context.user_data["admin_mode"] = None
        await update.message.reply_text("✅ Product description updated.")
        return

    if mode == "addvar_name":
        context.user_data["admin_addvar_name"] = text
        context.user_data["admin_mode"] = "addvar_price"
        await update.message.reply_text("Send variant price (number only):")
        return

    if mode == "addvar_price":
        prod_id = context.user_data["admin_addvar_prod"]
        vname = context.user_data.get("admin_addvar_name", "")
        try:
            price = int(text)
        except:
            await update.message.reply_text("Send price as number only.")
            return
        exec_sql("INSERT INTO variants(product_id,name,price,is_active) VALUES(%s,%s,%s,TRUE)", (prod_id, vname, price))
        context.user_data["admin_mode"] = None
        await update.message.reply_text("✅ Variant added!")
        return

    if mode == "edit_var_name":
        vid = context.user_data["admin_target_id"]
        exec_sql("UPDATE variants SET name=%s WHERE id=%s", (text, vid))
        context.user_data["admin_mode"] = None
        await update.message.reply_text("✅ Variant name updated.")
        return

    if mode == "edit_var_price":
        vid = context.user_data["admin_target_id"]
        try:
            price = int(text)
        except:
            await update.message.reply_text("Send price as number only.")
            return
        exec_sql("UPDATE variants SET price=%s WHERE id=%s", (price, vid))
        context.user_data["admin_mode"] = None
        await update.message.reply_text("✅ Variant price updated.")
        return

    if mode == "add_stock_lines":
        vid = context.user_data["admin_target_id"]
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            await update.message.reply_text("No lines found. Paste again.")
            return
        with db() as conn:
            with conn.cursor() as cur:
                for ln in lines:
                    cur.execute("INSERT INTO stocks(variant_id,payload,is_sold) VALUES(%s,%s,FALSE)", (vid, ln))
            conn.commit()
        context.user_data["admin_mode"] = None
        await update.message.reply_text(f"✅ Added {len(lines)} stock lines to variant #{vid}.")
        return

async def admin_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    mode = context.user_data.get("admin_mode")
    if mode != "set_thumb_wait_photo":
        return
    prod_id = context.user_data["admin_target_id"]
    file_id = update.message.photo[-1].file_id
    exec_sql("UPDATE products SET thumbnail_file_id=%s WHERE id=%s", (file_id, prod_id))
    context.user_data["admin_mode"] = None
    await update.message.reply_text("✅ Thumbnail saved!")

async def admin_decide_topup(q, context, action: str, topup_id: str):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id,amount,status FROM topups WHERE topup_id=%s FOR UPDATE", (topup_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                await q.message.reply_text("Topup not found.")
                return
            user_id, amount, status = row
            if status != "PENDING":
                conn.rollback()
                await q.message.reply_text("Already decided.")
                return
            if action == "approve":
                cur.execute("UPDATE topups SET status='APPROVED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s",
                            (q.from_user.id, topup_id))
                cur.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
                cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amount, user_id))
                conn.commit()
                await context.bot.send_message(chat_id=user_id, text=f"✅ Topup approved: {fmt_money(amount)}\nYour balance updated 💗")
                await q.message.reply_text("✅ Approved.")
                return
            if action == "reject":
                cur.execute("UPDATE topups SET status='REJECTED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s",
                            (q.from_user.id, topup_id))
                conn.commit()
                await context.bot.send_message(chat_id=user_id, text="❌ Topup rejected. Contact admin if mistake.")
                await q.message.reply_text("❌ Rejected.")
                return

# =========================
# BOOT
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing.")
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_IDS missing in Railway Variables (example: 7719956917).")

    ensure_schema()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Shop$"), shop))
    app.add_handler(MessageHandler(filters.Regex(r"^💳 Add Balance$"), add_balance))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 Balance$"), balance))
    app.add_handler(MessageHandler(filters.Regex(r"^📜 History$"), history))
    app.add_handler(MessageHandler(filters.Regex(r"^🆘 Help$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐 Admin$"), admin_cmd))

    app.add_handler(CallbackQueryHandler(go_back, pattern=r"^nav:back$"))
    app.add_handler(CallbackQueryHandler(cb_pay, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(cb_amt, pattern=r"^amt:"))
    app.add_handler(CallbackQueryHandler(cb_shop, pattern=r"^shop:"))
    app.add_handler(CallbackQueryHandler(cb_qty, pattern=r"^qty:"))
    app.add_handler(CallbackQueryHandler(cb_buy_confirm, pattern=r"^buy:confirm:"))
    app.add_handler(CallbackQueryHandler(cb_buy_cancel, pattern=r"^buy:cancel$"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^admin:"))

    app.add_handler(MessageHandler(filters.PHOTO, proof_photo))

    # Admin text/photo flows
    app.add_handler(MessageHandler(filters.PHOTO, admin_photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_handler))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
