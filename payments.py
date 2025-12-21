import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from utils import send_clean_menu

GCASH_QR_FILE_ID = os.getenv("GCASH_QR_FILE_ID", "").strip()
GOTYME_QR_FILE_ID = os.getenv("GOTYME_QR_FILE_ID", "").strip()

PAYMENT_INSTRUCTIONS = {
    "gcash": "📌 *GCash Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here",
    "gotyme": "📌 *GoTyme Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here",
}

TOPUP_AMOUNTS = [50, 100, 300, 500, 1000]

def payment_method_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💙 GCash", callback_data="pay:gcash")],
        [InlineKeyboardButton("💜 GoTyme", callback_data="pay:gotyme")],
        [InlineKeyboardButton("⬅️ Back", callback_data="pay:back")],
    ])

def topup_amount_menu():
    rows = [
        [InlineKeyboardButton("₱50", callback_data="amt:50"),
         InlineKeyboardButton("₱100", callback_data="amt:100")],
        [InlineKeyboardButton("₱300", callback_data="amt:300"),
         InlineKeyboardButton("₱500", callback_data="amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="amt:1000")],
        [InlineKeyboardButton("⬅️ Change method", callback_data="pay:back")],
    ]
    return InlineKeyboardMarkup(rows)

async def send_payment_qr(update, context, method: str):
    file_id = GCASH_QR_FILE_ID if method == "gcash" else GOTYME_QR_FILE_ID
    text = PAYMENT_INSTRUCTIONS.get(method, "")

    if file_id:
        # Send image QR
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=file_id,
                caption=text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_id,
                caption=text,
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        await send_clean_menu(
            update, context,
            text + "\n\n⚠️ Set GCASH_QR_FILE_ID / GOTYME_QR_FILE_ID in Railway Variables to show QR.",
            parse_mode=ParseMode.MARKDOWN
        )
