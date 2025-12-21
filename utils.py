import re
from telegram.error import BadRequest

def parse_int_money(text: str) -> int:
    """
    Accepts: "8", "₱8", "PHP 8", "8.00"
    Returns int: 8
    """
    if not text:
        return -1
    digits = re.findall(r"\d+", text.replace(",", ""))
    if not digits:
        return -1
    return int("".join(digits))

async def safe_delete_message(bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest:
        pass
    except Exception:
        pass

async def delete_last_menu(context, chat_id: int):
    msg_id = context.user_data.get("last_menu_msg_id")
    if not msg_id:
        return
    await safe_delete_message(context.bot, chat_id, msg_id)
    context.user_data.pop("last_menu_msg_id", None)

async def send_clean_menu(update, context, text: str, reply_markup=None, parse_mode=None):
    """
    Sends a menu message but deletes the previous menu message to keep chat clean.
    Saves the new menu message_id as last_menu_msg_id.
    """
    chat_id = update.effective_chat.id
    await delete_last_menu(context, chat_id)

    if update.callback_query:
        msg = await update.callback_query.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    else:
        msg = await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )

    context.user_data["last_menu_msg_id"] = msg.message_id
    return msg

def push_screen(context, screen: str, data: dict | None = None):
    stack = context.user_data.get("nav_stack", [])
    stack.append({"screen": screen, "data": data or {}})
    context.user_data["nav_stack"] = stack

def pop_screen(context):
    stack = context.user_data.get("nav_stack", [])
    if stack:
        stack.pop()
    context.user_data["nav_stack"] = stack

def peek_screen(context):
    stack = context.user_data.get("nav_stack", [])
    if not stack:
        return None
    return stack[-1]

