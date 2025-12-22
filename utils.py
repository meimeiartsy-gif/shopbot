import os
from telegram import ReplyKeyboardMarkup

def parse_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return set()
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            out.add(int(x))
    return out

def is_admin(user_id: int, admin_ids: set[int]) -> bool:
    return user_id in admin_ids

def fmt_money(n: int) -> str:
    return f"₱{n:,}"

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Shop", "💳 Add Balance"],
        ["💰 Balance", "📜 History"],
        ["🆘 Help", "🔐 Admin"],
    ],
    resize_keyboard=True,
    is_persistent=True,      # keeps it stable
    one_time_keyboard=False  # don’t disappear
)
