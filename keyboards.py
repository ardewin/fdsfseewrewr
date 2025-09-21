from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import app_settings
from services.core import server_manager
import asyncio

def user_keyboard() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="📲 Android", url=app_settings.ANDROID_URL),
            InlineKeyboardButton(text="🍎 iOS", url=app_settings.IOS_URL),
            InlineKeyboardButton(text="💻 Windows", url=app_settings.WINDOWS_URL),
        ],
        [
            InlineKeyboardButton(
                text="📊 Трафик",
                callback_data="user_traffic"
            ),
            InlineKeyboardButton(
                text="🗑️ Удалить профиль",
                callback_data="delete_profile"
            ),
        ],
        [
            InlineKeyboardButton(
                text="💬 Поддержка",
                url=f"https://t.me/{app_settings.SUPPORT_USERNAME.lstrip('@')}"
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# Единая back-кнопка
def back_button(cb="admin_back") -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Назад", callback_data=cb)

def admin_menu_keyboard(sid: str, online: int = 0) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text=f"🟢 Онлайн ({online})", callback_data="admin_onlines"),
            InlineKeyboardButton(text="👥 Все", callback_data="admin_clients"),
        ],
        [
            InlineKeyboardButton(text="📊 Трафик", callback_data="admin_traffic"),
            InlineKeyboardButton(text="🌐 Сервер: {}".format(sid), callback_data="admin_select_server"),
        ],
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="admin_add"),
            InlineKeyboardButton(text="➖ Удалить", callback_data="admin_del"),
        ],
        [
            InlineKeyboardButton(text="🔄 Синхронизация", callback_data="admin_sync_reminders"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_actions_keyboard() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="admin_add"),
            InlineKeyboardButton(text="➖ Удалить", callback_data="admin_del"),
        ],
        [
            InlineKeyboardButton(text="🔄 Синхронизация", callback_data="admin_sync_reminders"),
            back_button(),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def admin_menu_syncing_keyboard(sid: str) -> InlineKeyboardMarkup:
    # Кнопка синхронизации заменена на ⏳
    try:
        await server_manager._auth(sid)
        emoji = "🟢"
    except Exception:
        emoji = "🔴"
    rows = [
        [
            {"text": "🟢 Онлайн",  "cb": "admin_onlines"},
            {"text": "👥 Все",     "cb": "admin_clients"},
        ],
        [
            {"text": "📊 Весь трафик", "cb": "admin_traffic"},
        ],
        [
            {"text": "➕ Добавить", "cb": "admin_add"},
            {"text": "➖ Удалить",  "cb": "admin_del"},
        ],
        [
            {"text": "⏳ Синхро",   "cb": "noop"},
        ],
        [
            {"text": f"{emoji} {sid}",  "cb": "admin_select_server"},
        ],
        [
            back_button(),
        ],
    ]
    kb = []
    for row in rows:
        kb.append([
            InlineKeyboardButton(text=btn["text"], callback_data=btn["cb"]) if isinstance(btn, dict) else btn
            for btn in (row if isinstance(row, list) else [row])
        ])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# Универсальный генератор inline-клавиатуры
def make_inline_keyboard(buttons):
    """
    :param buttons: Список строк, каждая строка — список dict с ключами:
        - text: текст кнопки
        - callback_data: callback_data для кнопки (опционально)
        - url: url для кнопки-ссылки (опционально)
    :return: InlineKeyboardMarkup
    """
    keyboard = []
    for row in buttons:
        keyboard_row = []
        for btn in row:
            keyboard_row.append(
                InlineKeyboardButton(
                    text=btn["text"],
                    callback_data=btn.get("callback_data"),
                    url=btn.get("url")
                )
            )
        keyboard.append(keyboard_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def reminder_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    btn_text = "🔕 Не оповещать" if enabled else "🔔 Оповещать"
    return make_inline_keyboard([
        [{"text": btn_text, "callback_data": "toggle_reminder"}]
    ])

async def admin_menu_for_with_status(selected_sid: str | None = None) -> InlineKeyboardMarkup:
    buttons = []
    for sid, cfg in server_manager.cfgs.items():
        try:
            await server_manager._auth(sid)
            emoji = "🟢"
        except Exception:
            emoji = "🔴"
        text = f"{emoji} {sid} ({cfg.SERVER_DOMAIN})"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"admin_server_{sid}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ... аналогично для других клавиатур ... 