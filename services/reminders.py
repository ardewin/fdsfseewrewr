from db import SessionLocal, ReminderSetting
from services.core import server_manager
from config import SERVERS_CFG

async def get_setting(chat_id: int) -> ReminderSetting | None:
    async with SessionLocal() as s:
        return await s.get(ReminderSetting, chat_id)

async def mark_asked(chat_id: int):
    async with SessionLocal() as s:
        obj = await s.get(ReminderSetting, chat_id) or ReminderSetting(chat_id=chat_id)
        obj.asked = True
        s.add(obj)
        await s.commit()

async def toggle_enabled(chat_id: int) -> bool:
    async with SessionLocal() as s:
        obj = await s.get(ReminderSetting, chat_id) or ReminderSetting(chat_id=chat_id)
        obj.enabled = not obj.enabled
        obj.asked   = True
        s.add(obj)
        await s.commit()
        return obj.enabled

async def list_enabled_chat_ids() -> list[int]:
    async with SessionLocal() as s:
        rows = (await s.execute(
            ReminderSetting.__table__.select().where(ReminderSetting.enabled)
        )).all()
        return [r.chat_id for r in rows]

async def save_last_msg_id(chat_id: int, msg_id: int):
    async with SessionLocal() as s:
        obj = await s.get(ReminderSetting, chat_id) or ReminderSetting(chat_id=chat_id)
        obj.last_msg_id = msg_id
        s.add(obj)
        await s.commit()

async def list_all_chat_ids() -> list[int]:
    """Все известные chat_id (не важно, включено ли напоминание)."""
    async with SessionLocal() as s:
        rows = (await s.execute(ReminderSetting.__table__.select())).all()
        return [r.chat_id for r in rows]

async def list_all_clients():
    """
    Возвращает список клиентов вида:
    [{"chat_id": tg_id, "email": email, "uuid": uuid}, ...]
    """
    result = []
    for sid, cfg in server_manager.cfgs.items():
        clients = await server_manager.list_clients(sid)
        for c in clients:
            email = c.get("email")
            uuid = c.get("id")
            # tg_id извлекаем из email, если он в формате <tg_id>_имя
            tg_id = None
            if email and "_" in email:
                tg_id_part = email.split("_", 1)[0]
                if tg_id_part.isdigit():
                    tg_id = int(tg_id_part)
            if tg_id:
                result.append({"chat_id": tg_id, "email": email, "uuid": uuid})
    return result

async def list_active_clients():
    """
    Возвращает список клиентов, которые запускали бота (есть в базе ReminderSetting),
    и для которых найден email/uuid на сервере.
    [{"chat_id": tg_id, "email": email, "uuid": uuid}, ...]
    """
    # Получаем все chat_id из базы
    chat_ids = await list_all_chat_ids()
    result = []
    for sid, cfg in server_manager.cfgs.items():
        clients = await server_manager.list_clients(sid)
        for c in clients:
            email = c.get("email")
            uuid = c.get("id")
            tg_id = None
            if email and "_" in email:
                tg_id_part = email.split("_", 1)[0]
                if tg_id_part.isdigit():
                    tg_id = int(tg_id_part)
            if tg_id and tg_id in chat_ids:
                result.append({"chat_id": tg_id, "email": email, "uuid": uuid})
    return result 