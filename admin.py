from aiogram import types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from keyboards import admin_menu_keyboard, admin_menu_syncing_keyboard, back_button, admin_menu_for_with_status, admin_actions_keyboard
from locales import t
from config import is_admin, app_settings, SERVERS_CFG
from loguru import logger
import textwrap
import asyncio
from services.telegram_utils import safe_send
from db import get_selected, set_selected
from sync_reminders import sync_reminders
from services.core import get_best_server_cfg, get_or_create_user_key, delete_user_profile, get_user_traffic, server_manager
from services import admin_settings
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
from services.server_manager import _to_gb
from dataclasses import dataclass
from api.http import build_vless, api_auth, api_clients, api_create_client, api_delete_client, api_inbounds_list, api_traffic, api_onlines
from httpx import HTTPStatusError
from .admin_broadcast import router as admin_broadcast_router

class AdminFSM(StatesGroup):
    selecting_server = State()
    waiting_add = State()
    waiting_del = State()

@dataclass
class ClientCard:
    uuid: str
    email: str
    inbound_id: int
    page: int

async def get_admin_selected_sid(state: FSMContext, uid: int) -> str:
    sid = (await state.get_data()).get("selected_server")
    if sid:
        return sid
    sid = await admin_settings.get_selected(uid) or await server_manager.pick_least_loaded()
    await state.update_data(selected_server=sid)
    return sid

async def ensure_admin_sid(state: FSMContext, uid: int) -> str:
    sid = (await state.get_data()).get("selected_server")
    if not sid:
        sid = await admin_settings.get_selected(uid) or await server_manager.pick_least_loaded()
        await state.update_data(selected_server=sid)
    return sid

def register_admin_handlers(dp):
    dp.include_router(admin_broadcast_router)
    @dp.message(Command("admin"))
    async def admin_menu(msg: types.Message, state: FSMContext):
        if not is_admin(msg.from_user):
            return await msg.answer("⛔ Доступ запрещён.")
        sid = await get_admin_selected_sid(state, msg.from_user.id)
        try:
            online = len(await server_manager.get_online_clients(sid))
        except Exception:
            online = 0
        menu_title = f"Меню администратора (сервер: {sid})"
        kb = admin_menu_keyboard(sid, online)
        await msg.answer(menu_title, reply_markup=kb)

    @dp.callback_query(F.data == "admin_clients")
    async def cb_admin_clients(query: CallbackQuery, state: FSMContext):
        await query.answer()
        sid = await get_admin_selected_sid(state, query.from_user.id)
        try:
            clients = await server_manager.list_clients(sid)
            # logger.info(f"Клиенты-ответ ({sid}): {clients}")
            if not clients:
                text = "Нет клиентов."
            else:
                lines = [f"{i+1}. <code>{c['email']}</code>" for i, c in enumerate(clients)]
                text = "<b>Все клиенты:</b>\n" + "\n".join(lines)
            kb = InlineKeyboardMarkup(inline_keyboard=[[back_button()]])
            await safe_send(query.message.answer, text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Ошибка списка клиентов ({sid}): {e}")
            kb = InlineKeyboardMarkup(inline_keyboard=[[back_button()]])
            await safe_send(query.message.answer, f"Ошибка: {e}", reply_markup=kb)

    @dp.callback_query(F.data == "admin_traffic")
    async def cb_admin_traffic(q: CallbackQuery, state: FSMContext):
        await q.answer()
        sid = await get_admin_selected_sid(state, q.from_user.id)
        placeholder = await safe_send(q.message.edit_text, "⏳ Синхронизация…", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        try:
            clients = await server_manager.list_clients(sid)
        except Exception as e:
            return await placeholder.edit_text(f"Ошибка получения списка клиентов: {e}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        if not clients:
            return await placeholder.edit_text("❗ Клиентов нет.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        try:
            stats = await asyncio.gather(*(server_manager.get_traffic(sid, c) for c in clients))
        except Exception as e:
            return await placeholder.edit_text(f"Ошибка получения трафика: {e}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        total_up = total_dn = 0
        rows = []
        for c, stat in zip(clients, stats):
            up, dn = stat["uplink"], stat["downlink"]
            total_up += up; total_dn += dn
            name = c.get('email') or c.get('username') or c.get('remark', 'unknown')
            rows.append(f"• <code>{name}</code> ⬆ {server_manager.to_gb(up):.2f} ГБ ⬇ {server_manager.to_gb(dn):.2f} ГБ")
        head = (
            f"<b>Трафик {sid}</b>\n"
            f"Σ ⬆ {server_manager.to_gb(total_up):.2f} ГБ ⬇ {server_manager.to_gb(total_dn):.2f} ГБ\n\n"
        )
        await placeholder.edit_text(
            head + "\n".join(rows),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]),
        )

    @dp.callback_query(F.data == "admin_select_server")
    async def admin_select_server(query: CallbackQuery, state: FSMContext):
        await query.answer()
        kb = await admin_menu_for_with_status()
        try:
            await query.message.edit_text("Выберите сервер:", reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                raise

    @dp.callback_query(F.data.startswith("admin_server_"))
    async def admin_server_chosen(query: CallbackQuery, state: FSMContext):
        await query.answer()
        sid = query.data.replace("admin_server_", "")
        await admin_settings.set_selected(query.from_user.id, sid)
        await state.update_data(selected_server=sid)
        menu_title = f"Меню администратора (сервер: {sid})"
        kb = admin_menu_keyboard(sid)
        try:
            await query.message.edit_text(menu_title, reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                raise

    @dp.callback_query(F.data == "admin_sync_reminders")
    async def admin_sync_reminders(query: CallbackQuery, state: FSMContext):
        await query.answer()
        placeholder = await safe_send(query.message.edit_text, "⏳ Синхронизация…", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        total = 0
        for s in SERVERS_CFG:
            try:
                count = await sync_reminders(SERVERS_CFG[s])
                total += count
            except Exception as e:
                logger.warning(f"Ошибка синхронизации {s}: {e}")
        await placeholder.edit_text(f"Синхронизировано {total} новых пользователей.")

    @dp.callback_query(F.data == "admin_menu")
    async def cb_admin_menu(query: CallbackQuery, state: FSMContext):
        await query.answer()
        try:
            sid = await get_admin_selected_sid(state, query.from_user.id)
            try:
                online = len(await server_manager.get_online_clients(sid))
            except Exception:
                online = 0
            await query.message.edit_text("Меню администратора:", reply_markup=admin_menu_keyboard(sid, online))
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                await query.answer("Вы уже в главном меню.", show_alert=True)
            else:
                raise

    @dp.callback_query(F.data == "admin_onlines")
    async def cb_admin_onlines(q: CallbackQuery, state: FSMContext):
        await q.answer()
        sid = await get_admin_selected_sid(state, q.from_user.id)
        try:
            online_list = await server_manager.get_online_clients(sid)
            online = len(online_list)
        except Exception as e:
            return await q.message.edit_text(f"Ошибка получения онлайна: {e}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        if not online_list:
            text = f"Нет подключённых клиентов на {sid}."
        else:
            names = "\n".join(f"• {email}" for email in online_list)
            text = f"Он-лайн на {sid} ({online}):\n{names}"
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))

    @dp.callback_query(F.data == "admin_actions_menu")
    async def admin_actions_menu(query: CallbackQuery, state: FSMContext):
        await query.answer()
        try:
            await query.message.edit_text("Действия:", reply_markup=admin_actions_keyboard())
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                raise

    @dp.callback_query(F.data == "admin_back")
    async def admin_back(query: CallbackQuery, state: FSMContext):
        await query.answer()
        sid = await get_admin_selected_sid(state, query.from_user.id)
        try:
            online = len(await server_manager.get_online_clients(sid))
        except Exception:
            online = 0
        menu_title = f"Меню администратора (сервер: {sid})"
        try:
            await query.message.edit_text(menu_title, reply_markup=admin_menu_keyboard(sid, online))
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                await query.answer("Вы уже в главном меню.", show_alert=True)
            else:
                raise

    @dp.callback_query(F.data == "admin_add")
    async def admin_add_start(query: CallbackQuery, state: FSMContext):
        await query.answer()
        sid = await get_admin_selected_sid(state, query.from_user.id)
        clients = await server_manager.list_clients(sid)
        if len(clients) >= app_settings.MAX_CLIENTS and not is_admin(query.from_user):
            return await query.message.edit_text("⛔ Сервер заполнен.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        await state.set_state(AdminFSM.waiting_add)
        await state.update_data(selected_server=sid)
        await query.message.edit_text(
            "Введите email нового клиента:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]])
        )

    @dp.message(AdminFSM.waiting_add)
    async def admin_add_process(msg: types.Message, state: FSMContext):
        data = await state.get_data()
        sid = data.get("selected_server")
        email = msg.text.strip().lower()
        # Валидация имени (как у пользователя)
        import re
        if not re.fullmatch(r"[a-z]{3,20}", email):
            return await safe_send(msg.answer, "❗️ Имя должно быть 3–20 английских букв (a-z).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        clients = await server_manager.list_clients(sid)
        # Проверка уникальности имени/email
        if any(c["email"].lower() == email for c in clients):
            return await safe_send(msg.answer, f"❗️ Клиент с именем <code>{email}</code> уже существует.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        placeholder = await safe_send(msg.answer, "⏳ Добавляю клиента…", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        inbound_id = clients[0]["inbound_id"] if clients else 1
        try:
            await server_manager.create_client(sid, inbound_id, email, 0, skip_limit=is_admin(msg.from_user))
            server_cfg = SERVERS_CFG[sid]
            vless_link = build_vless(server_cfg, email)
            await safe_send(placeholder.edit_text, f"✅ Клиент <code>{email}</code> добавлен.", parse_mode="HTML")
            await safe_send(msg.answer, f"<code>{vless_link}</code>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        except Exception as e:
            await safe_send(placeholder.edit_text, f"Ошибка добавления: {e}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        await state.clear()

    @dp.callback_query(F.data == "admin_del")
    async def admin_del_start(query: CallbackQuery, state: FSMContext):
        await query.answer()
        sid = await get_admin_selected_sid(state, query.from_user.id)
        all_clients = await server_manager.list_clients(sid)
        if not all_clients:
            return await query.message.edit_text("Нет клиентов для удаления.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button()]]))
        cards = [ClientCard(
            uuid=c["uuid"],
            email=c["email"],
            inbound_id=c["inbound_id"],
            page=idx // 5
        ) for idx, c in enumerate(all_clients)]
        pages = {}
        for card in cards:
            pages.setdefault(card.page, []).append(card)
        await state.update_data(del_pages=pages, del_cur=0, del_sid=sid)
        await show_delete_page(query.message, pages[0], 0, len(pages))

    @dp.callback_query(F.data.startswith("prev_del:"))
    async def cb_prev_del(q: CallbackQuery, state: FSMContext):
        page = int(q.data.split(":")[1])
        data = await state.get_data()
        await state.update_data(del_cur=page)
        await show_delete_page(q.message, data["del_pages"][page], page, len(data["del_pages"]))

    @dp.callback_query(F.data.startswith("next_del:"))
    async def cb_next_del(q: CallbackQuery, state: FSMContext):
        page = int(q.data.split(":")[1])
        data = await state.get_data()
        await state.update_data(del_cur=page)
        await show_delete_page(q.message, data["del_pages"][page], page, len(data["del_pages"]))

    @dp.callback_query(F.data.startswith("del_"))
    async def cb_del_client(q: CallbackQuery, state: FSMContext):
        uuid = q.data.split("_", 1)[1]
        data = await state.get_data()
        page = data["del_cur"]
        sid = data["del_sid"]
        card = next((c for c in data["del_pages"][page] if str(c.uuid) == str(uuid)), None)
        if not card:
            # Попробуем обновить список и найти клиента во всех страницах
            all_clients = await server_manager.list_clients(sid)
            cards = [ClientCard(
                uuid=c["uuid"],
                email=c["email"],
                inbound_id=c["inbound_id"],
                page=idx // 5
            ) for idx, c in enumerate(all_clients)]
            pages = {}
            for card_ in cards:
                pages.setdefault(card_.page, []).append(card_)
            found = False
            for p, cards_on_page in pages.items():
                card = next((c for c in cards_on_page if str(c.uuid) == str(uuid)), None)
                if card:
                    page = p
                    found = True
                    break
            if not found:
                return await q.answer("Клиент не найден", show_alert=True)
            await state.update_data(del_pages=pages, del_cur=page)
        try:
            await server_manager.delete_client(sid, card.inbound_id, card.uuid)
            await server_manager.invalidate_cache(sid, "clients")
            # Всплывающее окно с именем клиента
            await q.answer(f"✅ Клиент {card.email} удалён", show_alert=True)
            # Обновляем список после удаления
            all_clients = await server_manager.list_clients(sid)
            cards = [ClientCard(
                uuid=c["uuid"],
                email=c["email"],
                inbound_id=c["inbound_id"],
                page=idx // 5
            ) for idx, c in enumerate(all_clients)]
            pages = {}
            for card_ in cards:
                pages.setdefault(card_.page, []).append(card_)
            total = len(pages)
            new_page = min(page, total-1) if total else 0
            await state.update_data(del_pages=pages, del_cur=new_page)
            if total:
                await show_delete_page(q.message, pages[new_page], new_page, total)
            else:
                await state.clear()
                try:
                    online = len(await server_manager.get_online_clients(sid))
                except Exception:
                    online = 0
                menu_title = f"Меню администратора (сервер: {sid})"
                await q.message.edit_text(menu_title, reply_markup=admin_menu_keyboard(sid, online))
        except Exception as e:
            await q.answer(f"Ошибка удаления: {e}", show_alert=True)

def humanize_last_seen(ts):
    if not ts:
        return "никогда"
    dt = datetime.fromtimestamp(ts)
    delta = datetime.utcnow() - dt
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "только что"
    elif mins == 1:
        return "1 мин назад"
    elif mins < 60:
        return f"{mins} мин назад"
    else:
        hours = mins // 60
        return f"{hours} ч назад"

async def on_startup():
    logger.info("✅ Bot started")
    any_success = False
    for sid, server_cfg in SERVERS_CFG.items():
        try:
            await api_auth(server_cfg)
            logger.info(f"Авторизация успешна на сервере {sid}")
            any_success = True
        except Exception as e:
            logger.warning(f"Сервер {sid} недоступен: {e}")
    if not any_success:
        logger.error("❌ Ни один сервер не доступен для авторизации!")
    # Синхронизация по всем серверам
    total = 0
    for sid, server_cfg in SERVERS_CFG.items():
        try:
            count = await sync_reminders(server_cfg)
            total += count
        except Exception as e:
            logger.warning(f"Ошибка синхронизации {sid}: {e}")
    logger.info(f"Синхронизировано {total} пользователей со всех серверов в базу данных.")
    start_scheduler(bot)
    await init_models() 

def make_del_kb(cards: list, page: int, total: int):
    rows = [[InlineKeyboardButton(text=c.email, callback_data=f"del_{c.uuid}")] for c in cards]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⏪", callback_data=f"prev_del:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total}", callback_data="noop"))
    if page < total-1:
        nav.append(InlineKeyboardButton(text="⏩", callback_data=f"next_del:{page+1}"))
    rows.append(nav)
    rows.append([back_button("admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_delete_page(msg, cards, page, total):
    kb = make_del_kb(cards, page, total)
    text = "Выберите клиента для удаления:"
    await msg.edit_text(text, reply_markup=kb) 