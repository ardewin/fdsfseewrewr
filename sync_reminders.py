import asyncio
from api.http import api_auth, api_clients
from db import SessionLocal, ReminderSetting
from config import app_settings, SERVERS_CFG

def get_default_server_cfg():
    return SERVERS_CFG["MAIN"]

async def sync_reminders(server_cfg=None):
    if server_cfg is None:
        total = 0
        for scfg in SERVERS_CFG.values():
            total += await sync_reminders(scfg)
        return total
    cookies = await api_auth(server_cfg)
    clients = await api_clients(server_cfg, cookies)
    count = 0
    async with SessionLocal() as s:
        for c in clients:
            email = c.get("email", "")
            if "_" not in email:
                continue
            tg_id, _ = email.split("_", 1)
            if not tg_id.isdigit():
                continue
            tg_id = int(tg_id)
            obj = await s.get(ReminderSetting, tg_id)
            if not obj:
                obj = ReminderSetting(chat_id=tg_id)
                s.add(obj)
                count += 1
        await s.commit()
    return count

if __name__ == "__main__":
    count = asyncio.run(sync_reminders())
    print(f"Синхронизировано {count} новых пользователей.") 