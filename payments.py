import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

GCASH_QR_FILE_ID = os.getenv("GCASH_QR_FILE_ID", "").strip()
GOTYME_QR_FILE_ID = os.getenv("GOTYME_QR_FILE_ID", "").strip()

PAYMENT_TEXT = {
    "gcash": "📌 *GCash Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here",
    "gotyme": "📌 *GoTyme Instructions*\n\n1) Scan the QR\n2) Pay\n3) Send screenshot here",
}

def payment_methods_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💙 GCash", callback_data="pay:gcash")],
        [InlineKeyboardButton("💜 GoTyme", callback_data="pay:gotyme")],
        [InlineKeyboardButton("⬅️ Back", callback_data="pay:back")],
    ])

def amounts_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("₱50", callback_data="amt:50"), InlineKeyboardButton("₱100", callback_data="amt:100")],
        [InlineKeyboardButton("₱300", callback_data="amt:300"), InlineKeyboardButton("₱500", callback_data="amt:500")],
        [InlineKeyboardButton("₱1000", callback_data="amt:1000")],
        [InlineKeyboardButton("⬅️ Change method", callback_data="pay:back")],
    ])

async def send_qr(context, chat_id: int, method: str):
    file_id = GCASH_QR_FILE_ID if method == "gcash" else GOTYME_QR_FILE_ID
    text = PAYMENT_TEXT.get(method, "")

    if file_id:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await context.bot.send_document(chat_id=chat_id, document=file_id, caption=text, parse_mode=ParseMode.MARKDOWN)
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text + "\n\n⚠️ Set GCASH_QR_FILE_ID / GOTYME_QR_FILE_ID in Railway Variables to show QR.",
            parse_mode=ParseMode.MARKDOWN
        )
