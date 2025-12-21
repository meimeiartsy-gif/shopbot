# payments.py
import os
import uuid
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set, List

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import (
    exec_sql,
    fetch_all,
    fetch_one,
    db,
)

log = logging.getLogger("shopbot.payments")

# =========================
# ENV / CONFIG
# =========================
def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()

# Admin IDs comma-separated (example: "7719956917,123456789")
def parse_admin_ids() -> Set[int]:
    raw = _env("ADMIN_IDS")
    if not raw:
        return set()
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            out.add(int(x))
    return out

ADMIN_IDS: Set[int] = parse_admin_ids()

# IMPORTANT:
# These should be Telegram file_id values (recommended).
# If empty, bot will show warning text instead of QR image.
GCASH_QR_FILE_ID = _env("GCASH_QR_FILE_ID")
GOTYME_QR_FILE_ID = _env("GOTYME_QR_FILE_ID")

TOPUP_AMOUNTS = [50, 100, 300, 500, 1000]

DEFAULT_PAYMENT_TEXTS: Dict[str, str] = {
    "gcash": (
        "📌 *GCash Instructions*\n\n"
        "1) Scan the QR\n"
        "2) Pay\n"
        "3) Send screenshot here\n\n"
        "(You can edit this later in Admin → Edit Payment Texts.)"
    ),
    "gotyme": (
        "📌 *GoTyme Instructions*\n\n"
        "1) Scan the QR\n"
        "2) Pay\n"
        "3) Send screenshot here\n\n"
        "(You can edit this later in Admin → Edit Payment Texts.)"
    ),
}

# =========================
# Helpers
# =========================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def fmt_money(n: int) -> str:
    return f"₱{n:,}"

async def safe_answer(q):
    try:
        await q.answer()
    except Exception:
        pass

def payment_text(method: str) -> str:
    # stored in DB bot_texts (optional); fallback to DEFAULT_PAYMENT_TEXTS
    row = fetch_one("SELECT value FROM bot_texts WHERE key=%s", (f"{method}_text",))
    if row and row.get("value"):
        return str(row["value"])
    return DEFAULT_PAYMENT_TEXTS.get(method, "")

def qr_file_id(method: str) -> str:
    if method == "gcash":
        return GCASH_QR_FILE_ID
    return GOTYME_QR_FILE_ID

# =========================
# UI
# =========================
def kb_choose_method() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💙 GCash", callback_data="pay:method:gcash")],
        [InlineKeyboardButton("💜 GoTyme", callback_data="pay:method:gotyme")],
        [InlineKeyboardButton("⬅️ Back", callback_data="pay:back:menu")],
    ])

def kb_choose_amount() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("₱50", callback_data="pay:amt:50"),
            InlineKeyboardButton("₱100", callback_data="pay:amt:100"),
        ],
        [
            InlineKeyboardButton("₱300", callback_data="pay:amt:300"),
            InlineKeyboardButton("₱500", callback_data="pay:amt:500"),
        ],
        [InlineKeyboardButton("₱1000", callback_data="pay:amt:1000")],
        [InlineKeyboardButton("⬅️ Change method", callback_data="pay:back:method")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pay:back:menu")],
    ]
    return InlineKeyboardMarkup(rows)

def kb_admin_proof(topup_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"admin:topup:approve:{topup_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"admin:topup:reject:{topup_id}"),
    ]])

# =========================
# Public entry: "Add Balance"
# (You will call this from bot.py menu handler)
# =========================
async def add_balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("topup_method", None)
    context.user_data.pop("topup_id", None)

    await update.message.reply_text(
        "💳 *Add Balance*\n\nChoose payment method:",
        reply_markup=kb_choose_method(),
        parse_mode=ParseMode.MARKDOWN,
    )

# =========================
# Callback router
# =========================
async def cb_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    data = q.data  # pay:...
    parts = data.split(":")
    # pay:back:menu OR pay:back:method OR pay:method:gcash OR pay:amt:100

    if parts[1] == "back":
        where = parts[2]
        if where == "menu":
            # go back to main menu message
            await q.message.reply_text("✅ Back to menu.")
            return
        if where == "method":
            context.user_data.pop("topup_method", None)
            context.user_data.pop("topup_id", None)
            await q.message.reply_text(
                "Choose payment method:",
                reply_markup=kb_choose_method(),
            )
            return

    if parts[1] == "method":
        method = parts[2]
        context.user_data["topup_method"] = method
        context.user_data.pop("topup_id", None)

        text = payment_text(method)
        file_id = qr_file_id(method)

        # show QR (if available)
        if file_id:
            try:
                await q.message.reply_photo(
                    photo=file_id,
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                # some file_ids are documents, so fallback
                await q.message.reply_document(
                    document=file_id,
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await q.message.reply_text(
                text + "\n\n⚠️ *QR not set.*\nSet `GCASH_QR_FILE_ID` / `GOTYME_QR_FILE_ID` in Railway Variables.",
                parse_mode=ParseMode.MARKDOWN,
            )

        await q.message.reply_text(
            "✨ Choose top-up amount:",
            reply_markup=kb_choose_amount(),
        )
        return

    if parts[1] == "amt":
        if "topup_method" not in context.user_data:
            await q.message.reply_text("Tap **Add Balance** and choose payment method first.", parse_mode=ParseMode.MARKDOWN)
            return

        amount = int(parts[2])
        method = context.user_data["topup_method"]
        topup_id = f"shopnluna:{uuid.uuid4().hex[:10]}"
        context.user_data["topup_id"] = topup_id

        exec_sql(
            """
            INSERT INTO topups (topup_id, user_id, amount, method, status)
            VALUES (%s,%s,%s,%s,'PENDING')
            ON CONFLICT (topup_id) DO NOTHING
            """,
            (topup_id, q.from_user.id, amount, method),
        )

        await q.message.reply_text(
            f"🆔 **Top-up ID:** `{topup_id}`\n"
            f"💵 Amount: **{fmt_money(amount)}**\n"
            f"💳 Method: **{method.upper()}**\n\n"
            "📸 Now send your **payment screenshot** here.\n"
            "⏳ Status: **Waiting for approval**",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

# =========================
# Proof upload (photo)
# =========================
async def on_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # only accept proof if user has active topup_id in session
    topup_id = context.user_data.get("topup_id")
    if not topup_id:
        return

    file_id = update.message.photo[-1].file_id
    user_id = update.effective_user.id

    exec_sql("UPDATE topups SET proof_file_id=%s WHERE topup_id=%s", (file_id, topup_id))

    # notify admins
    if not ADMIN_IDS:
        await update.message.reply_text("✅ Proof received, but ⚠️ ADMIN_IDS is not set in Railway Variables.")
        return

    kb = kb_admin_proof(topup_id)
    for admin_id in ADMIN_IDS:
        try:
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
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"💳 New Top-up Proof\nTopup ID: {topup_id}\nUser ID: {user_id}",
                reply_markup=kb,
            )

    await update.message.reply_text("✅ Proof received.\n⏳ Waiting for admin approval.\n\nThank you 💗")

# =========================
# Admin: approve / reject topup
# (callback starts with admin:topup:approve:TOPUP_ID)
# =========================
async def cb_admin_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer(q)

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Access denied.")
        return

    data = q.data  # admin:topup:approve:xxxx
    parts = data.split(":", 3)
    action = parts[2]
    topup_id = parts[3]

    with db() as conn:
        with conn.cursor(cursor_factory=None) as cur:
            cur.execute("SELECT user_id, amount, status FROM topups WHERE topup_id=%s FOR UPDATE", (topup_id,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                await q.message.reply_text("Top-up not found.")
                return

            user_id, amount, status = row[0], int(row[1]), row[2]
            if status != "PENDING":
                conn.rollback()
                await q.message.reply_text(f"Already decided: {status}")
                return

            if action == "approve":
                cur.execute(
                    "UPDATE topups SET status='APPROVED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s",
                    (q.from_user.id, topup_id),
                )
                cur.execute("INSERT INTO users(user_id) VALUES(%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
                cur.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amount, user_id))
                conn.commit()

                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ **Top-up Approved!**\n\n"
                        f"Topup ID: `{topup_id}`\n"
                        f"Amount: **{fmt_money(amount)}**\n\n"
                        "Your balance has been updated 💗"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
                await q.message.reply_text("✅ Approved.")
                return

            if action == "reject":
                cur.execute(
                    "UPDATE topups SET status='REJECTED', decided_at=NOW(), admin_id=%s WHERE topup_id=%s",
                    (q.from_user.id, topup_id),
                )
                conn.commit()

                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "❌ **Top-up Rejected**\n\n"
                        f"Topup ID: `{topup_id}`\n"
                        "If you think this is a mistake, contact admin."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
                await q.message.reply_text("❌ Rejected.")
                return

    await q.message.reply_text("Unknown action.")

# =========================
# Register handlers
# =========================
def register_payments_handlers(app):
    # user callbacks
    app.add_handler(CallbackQueryHandler(cb_pay, pattern=r"^pay:"))

    # admin callbacks (topup approve/reject)
    app.add_handler(CallbackQueryHandler(cb_admin_topup, pattern=r"^admin:topup:"))

    # proof uploads
    app.add_handler(MessageHandler(filters.PHOTO, on_proof_photo))
