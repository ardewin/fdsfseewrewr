from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
from keyboards import user_keyboard
from locales import t
from config import is_admin, app_settings
from loguru import logger
import re
import textwrap
from services import reminders
from services.instructions import send_or_edit
from services.core import get_best_server_cfg, server_manager, delete_user_profile, get_user_traffic, find_user_server
from services.telegram_utils import safe_send
from api.http import api_auth, api_clients, api_create_client, api_delete_client, api_inbounds_list, api_traffic, api_onlines, build_vless
from httpx import HTTPStatusError
import os

class UserFSM(StatesGroup):
    waiting_name = State()

def register_user_handlers(dp, bot):
    @dp.message(Command("start"))
    async def user_start(msg: types.Message, state: FSMContext):
        from services.core import find_user_server, server_manager
        prefix = f"{msg.from_user.id}_"
        cfg, user = await find_user_server(prefix)
        if user:
            link = build_vless(cfg, user["email"])
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Скопировать", copy_text=CopyTextButton(text=link))]
                ]
            )
            await safe_send(msg.answer, f"<code>{link}</code>", disable_web_page_preview=True, reply_markup=kb)
            await send_or_edit(msg.chat.id, bot)
            await state.clear()
            return
        # Проверяем есть ли хотя бы один не заполненный сервер
        has_free = False
        for sid in server_manager.cfgs:
            if not await server_manager.is_full(sid):
                has_free = True
                break
        if not has_free:
            await safe_send(msg.answer, "⛔ Все серверы заполнены")
            await state.clear()
            return
        
        # --- Проверка подписки на канал ---
        CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
        CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/+2UXi1T8ZKEsyNGMy")
        try:
            member = await bot.get_chat_member(CHANNEL_ID, msg.from_user.id)
            if member.status not in ("member", "administrator", "creator"):
                raise Exception("not subscribed")
        except Exception:
            from config import app_settings
            support_url = f"https://t.me/{app_settings.SUPPORT_USERNAME.lstrip('@')}"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")],
                    [InlineKeyboardButton(text="💬 Поддержка", url=support_url)],
                ]
            )
            await safe_send(
                msg.answer,
                "⚠️ <b>Доступ временно ограничен.</b>\n\n",
                reply_markup=kb,
                parse_mode="HTML"
            )
            return
        # --- Конец проверки подписки ---
        
        sent = await safe_send(msg.answer, "🔑 Напиши имя на английском языке (3-20 символов)")
        await state.update_data(intro={"chat_id": sent.chat.id, "msg_id": sent.message_id})
        await state.set_state(UserFSM.waiting_name)

    @dp.message(UserFSM.waiting_name)
    async def process_name(msg: types.Message, state: FSMContext):
        from services.core import ensure_user_profile
        name = msg.text.strip()
        notify_lower = False
        if any(c.isupper() for c in name):
            name = name.lower()
            notify_lower = True
        if not re.fullmatch(r"[a-z]{3,20}", name):
            return await safe_send(msg.answer, "❗️ Имя должно быть 3–20 английских букв (a-z).")
        if notify_lower:
            await safe_send(msg.answer, f"ℹ️ Имя: <code>{name}</code>")
        gen_msg = await safe_send(msg.answer, "⏳ Генерирую ключ…")
        gen_msg_id = gen_msg.message_id
        gen_chat_id = gen_msg.chat.id
        await bot.send_chat_action(msg.chat.id, "typing")
        try:
            cfg, email = await ensure_user_profile(msg.from_user.id, name)
            link = build_vless(cfg, email)
            await safe_send(gen_msg.edit_text, "✅ <b>Ключ готов.</b>")
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Скопировать", copy_text=CopyTextButton(text=link))]
                ]
            )
            await safe_send(msg.answer, f"<code>{link}</code>", disable_web_page_preview=True, reply_markup=kb)
            await send_or_edit(msg.chat.id, bot)
        except RuntimeError as e:
            await safe_send(gen_msg.edit_text, f"⛔ {e}")
        except HTTPStatusError as e:
            logger.error("Ошибка API x-ui: {}", e)
            await safe_send(gen_msg.edit_text, "Сервер x-ui недоступен, попробуйте позже.")
        except TelegramBadRequest as e:
            logger.warning("Telegram API: {}", e)
            # TODO: при необходимости добавить обработку rate-limit
        finally:
            await state.clear()

    @dp.callback_query(F.data == "user_traffic")
    async def user_traffic(query: CallbackQuery):
        await query.answer()
        try:
            prefix = f"{query.from_user.id}_"
            sid = None
            user = None
            for s, cfg in server_manager.cfgs.items():
                clients = await server_manager.list_clients(s)
                user = next((c for c in clients if c["email"].startswith(prefix)), None)
                if user:
                    sid = s
                    break
            if not user or not sid:
                return await safe_send(query.message.answer, "Профиль не найден.")
            stats = await server_manager.get_traffic(sid, user)
            total = stats["uplink"] + stats["downlink"]
            used_gb = total / 1024**3
            up_gb = stats["uplink"] / 1024**3
            down_gb = stats["downlink"] / 1024**3
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="user_menu")]
                ]
            )
            await safe_send(query.message.answer,
                f"Вы израсходовали {used_gb:.2f} ГБ (⬆ {up_gb:.2f} ГБ / ⬇ {down_gb:.2f} ГБ)",
                parse_mode="HTML",
                reply_markup=kb
            )
        except Exception as e:
            await safe_send(query.message.answer, f"Ошибка получения трафика: {e}", reply_markup=user_keyboard())

    @dp.callback_query(F.data == "user_menu")
    async def user_menu(query: CallbackQuery):
        await query.answer()
        from services.core import find_user_server
        prefix = f"{query.from_user.id}_"
        cfg, user = await find_user_server(prefix)
        if user:
            link = build_vless(cfg, user["email"])
            await safe_send(query.message.answer,
                f"<code>{link}</code>",
                disable_web_page_preview=True
            )
            await send_or_edit(query.from_user.id, bot)
        else:
            await safe_send(query.message.answer, "Профиль не найден.", reply_markup=user_keyboard())

    @dp.callback_query(F.data == "toggle_reminder")
    async def cb_toggle(call: CallbackQuery):
        enabled = await reminders.toggle_enabled(call.from_user.id)
        await call.answer(
            "🔔 Включено" if enabled else "🔕 Выключено",
            show_alert=True
        )
        await send_or_edit(call.from_user.id, bot)

    @dp.message(Command("reminder"))
    async def cmd_reminder(msg: types.Message):
        await reminders.toggle_enabled(msg.chat.id)
        await send_or_edit(msg.chat.id, bot)

    @dp.callback_query(F.data == "delete_profile")
    async def delete_profile(query: CallbackQuery):
        await query.answer()
        try:
            from services.core import server_manager
            prefix = f"{query.from_user.id}_"
            found = False
            for sid, cfg in server_manager.cfgs.items():
                clients = await server_manager.list_clients(sid)
                user_clients = [c for c in clients if c["email"].startswith(prefix)]
                inbound_id = int(cfg.INBOUNDS.split(",")[0]) if hasattr(cfg, 'INBOUNDS') else 1
                for c in user_clients:
                    await server_manager.delete_client(sid, inbound_id, c["email"])
                    found = True
                if found:
                    await server_manager.invalidate_cache(sid, "clients")
            if found:
                await query.message.answer("✅ Ваш профиль и все ключи удалены.")
            else:
                await query.message.answer("⚠️ Профиль не найден.")
        except Exception as e:
            await query.message.answer(f"❌ Ошибка удаления профиля: {e}")

    @dp.callback_query(F.data.in_(["reminder_yes", "reminder_no"]))
    async def cb_reminder_answer(call: types.CallbackQuery):
        if call.data == "reminder_yes":
            await reminders.toggle_enabled(call.from_user.id)
            text = "✅ Напоминание включено. Спасибо за поддержку!"
        else:
            await reminders.mark_asked(call.from_user.id)
            text = "Ок, не будем напоминать."
        await call.message.edit_text(text)
        await call.answer()

    @dp.callback_query(F.data == "toggle_reminder")
    async def cb_toggle_reminder(call: types.CallbackQuery):
        enabled = await reminders.toggle_enabled(call.from_user.id)
        text = (
            "🔔 Напоминание включено. Спасибо за поддержку!" if enabled
            else "🔕 Напоминание выключено."
        )
        await call.answer(text, show_alert=True)
        # Обновить инструкцию с новым статусом
        setting = await reminders.get_setting(call.from_user.id)
        reminder_status = (
            "🔔 <b>Напоминание о поддержке:</b> <u>включено</u> ✅"
            if setting and setting.enabled
            else "🔕 <b>Напоминание о поддержке:</b> <u>выключено</u> ❌"
        )
        instr_text = (
            "🎉 <b>Как подключиться:</b>\n\n"
            "1️⃣ Скопируйте ссылку выше 👆\n"
            "2️⃣ Установите приложение\n"
            "3️⃣ Нажмите ➕ в правом верхнем углу\n"
            "4️⃣ Вставьте ссылку из буфера обмена\n"
            "5️⃣ Нажмите «Подключиться»\n\n"
            f"{reminder_status}\n\n"
            "💬 Если нужна помощь, пишите в поддержку."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        "🔔 Оповещать" if not (setting and setting.enabled) else "🔕 Не оповещать",
                        callback_data="toggle_reminder"
                    )
                ]
            ]
        )
        await call.message.edit_text(instr_text, reply_markup=kb, parse_mode="HTML")

    @dp.message(Command("reminder"))
    async def reminder_toggle(msg: types.Message):
        enabled = await reminders.toggle_enabled(msg.chat.id)
        text = (
            "🔔 Напоминание включено. Спасибо за поддержку!" if enabled
            else "🔕 Напоминание выключено."
        )
        await msg.answer(text)

    # --- Новый callback для проверки подписки ---
    @dp.callback_query(F.data == "check_subscription")
    async def check_subscription_callback(call: CallbackQuery, state: FSMContext):
        CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
        CHANNEL_URL = os.getenv("CHANNEL_URL")
        try:
            member = await bot.get_chat_member(CHANNEL_ID, call.from_user.id)
            if member.status not in ("member", "administrator", "creator"):
                raise Exception("not subscribed")
        except Exception:
            await call.answer(
                "⏳ Вас пока не приняли в канал. Попробуйте позже 😊",
                show_alert=True
            )
            return
        # Если подписан, продолжаем сценарий (запрос имени)
        sent = await safe_send(call.message.answer, "🔑 Напиши имя на английском языке (3-20 символов)")
        await state.update_data(intro={"chat_id": sent.chat.id, "msg_id": sent.message_id})
        await state.set_state(UserFSM.waiting_name)
        await call.message.delete()

async def ask_support_reminder(chat_id: int, bot):
    setting = await reminders.get_setting(chat_id)
    if setting and setting.asked:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("✅ Да", callback_data="reminder_yes"),
            InlineKeyboardButton("❌ Нет", callback_data="reminder_no"),
        ]
    ])
    await bot.send_message(
        chat_id,
        "📅 Напомнить раз в месяц о поддержке сервера?",
        reply_markup=kb,
    ) 