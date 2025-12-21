from telegram import Update
from telegram.constants import ParseMode

def fmt_money(n: int) -> str:
    return f"₱{n:,}"

async def safe_answer(callback_query):
    try:
        await callback_query.answer()
    except:
        pass
