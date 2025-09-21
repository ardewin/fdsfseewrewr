from aiogram.exceptions import TelegramBadRequest
from services import reminders
from keyboards import user_keyboard
from services.telegram_utils import safe_send

def build_instruction(enabled: bool) -> str:
    INSTR_TEXTS = {
        "title": "🎉 <b>Как подключиться:</b>\n\n",
        "step_1": "1️⃣ Скопируйте ссылку выше 👆",
        "step_2": "2️⃣ Установите приложение",
        "step_3": "3️⃣ Нажмите ➕ в правом верхнем углу",
        "step_4": "4️⃣ Вставьте ссылку из буфера обмена",
        "step_5": "5️⃣ Нажмите «Подключиться»",
        "reminder_on": "🔔 <b>Напоминание о поддержке:</b> <u>включено</u> ✅",
        "reminder_off": "🔕 <b>Напоминание о поддержке:</b> <u>выключено</u> ❌",
        "support": "💬 Если нужна помощь, пишите в поддержку.",
    }
    INSTR_TEMPLATE = (
        "{title}"
        "{step_1}\n"
        "{step_2}\n"
        "{step_3}\n"
        "{step_4}\n"
        "{step_5}\n\n"
        "{reminder_status}\n\n"
        "{support}"
    )
    reminder_status = INSTR_TEXTS["reminder_on"] if enabled else INSTR_TEXTS["reminder_off"]
    return INSTR_TEMPLATE.format(
        title=INSTR_TEXTS["title"],
        step_1=INSTR_TEXTS["step_1"],
        step_2=INSTR_TEXTS["step_2"],
        step_3=INSTR_TEXTS["step_3"],
        step_4=INSTR_TEXTS["step_4"],
        step_5=INSTR_TEXTS["step_5"],
        reminder_status=reminder_status,
        support=INSTR_TEXTS["support"],
    )

async def send_or_edit(chat_id: int, bot):
    setting = await reminders.get_setting(chat_id)
    enabled = bool(setting and setting.enabled)
    text = build_instruction(enabled)
    kb = user_keyboard()

    if setting and setting.last_msg_id:
        try:
            await safe_send(
                bot.edit_message_text,
                text,
                chat_id=chat_id,
                message_id=setting.last_msg_id,
                reply_markup=kb,
                parse_mode="HTML"
            )
            return
        except TelegramBadRequest:
            pass  # сообщение удалили → отправим новое

    sent = await safe_send(bot.send_message, chat_id,
        text, parse_mode="HTML", reply_markup=kb
    )
    await reminders.save_last_msg_id(chat_id, sent.message_id) 