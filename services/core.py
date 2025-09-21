from api.http import api_auth, api_clients, api_create_client, build_vless
from config import app_settings, SERVERS_CFG
import httpx
import random
from loguru import logger
from services.server_manager import ServerManager

server_manager = ServerManager(SERVERS_CFG)

def get_default_server_cfg():
    return SERVERS_CFG["MAIN"]

# --- Балансировка: минимальная загрузка -> случай ---
async def pick_server_by_load() -> str:
    """
    Возвращает sid сервера с минимальной загрузкой (по количеству клиентов).
    Если несколько серверов с одинаковой минимальной загрузкой — выбирает случайно.
    Требует реализации функций api_auth(scfg) и api_clients(scfg, cookies).
    """
    loads = []
    for sid, scfg in SERVERS_CFG.items():
        try:
            cookies = await api_auth(scfg)
            clients = await api_clients(scfg, cookies)
            loads.append((sid, len(clients)))
        except httpx.ConnectTimeout:
            logger.warning(f"Сервер {sid} недоступен (ConnectTimeout)")
            continue
        except Exception as e:
            logger.warning(f"Ошибка при опросе сервера {sid}: {e}")
            continue
    if not loads:
        logger.error("❌ Нет доступных серверов для выдачи конфига! Fallback на random.")
        return random.choice(list(SERVERS_CFG.keys()))
    min_load = min(cnt for _, cnt in loads)
    least_loaded = [sid for sid, cnt in loads if cnt == min_load]
    return random.choice(least_loaded)

async def get_best_server_cfg():
    sid = await server_manager.pick_least_loaded()
    return server_manager.cfgs[sid]

async def get_or_create_user_key(tg_id, desired_name):
    sid = await server_manager.pick_least_loaded()
    email_prefix = f"{tg_id}_"
    clients = await server_manager.list_clients(sid)
    user_clients = [c for c in clients if c["email"].startswith(email_prefix)]
    if user_clients:
        return server_manager.cfgs[sid], user_clients[0]["email"]
    email = email_prefix + desired_name
    inbound_id = int(server_manager.cfgs[sid].INBOUNDS.split(",")[0])
    await server_manager.create_client(sid, inbound_id, email, tg_id)
    return server_manager.cfgs[sid], email

async def delete_user_profile(tg_id):
    sid = await server_manager.pick_least_loaded()
    email_prefix = f"{tg_id}_"
    clients = await server_manager.list_clients(sid)
    user_clients = [c for c in clients if c["email"].startswith(email_prefix)]
    inbound_id = int(server_manager.cfgs[sid].INBOUNDS.split(",")[0])
    for c in user_clients:
        await server_manager.delete_client(sid, inbound_id, c["email"])
    return len(user_clients)

async def get_user_traffic(tg_id):
    sid = await server_manager.pick_least_loaded()
    email_prefix = f"{tg_id}_"
    clients = await server_manager.list_clients(sid)
    user = next((c for c in clients if c["email"].startswith(email_prefix)), None)
    if not user:
        return None
    return await server_manager.get_traffic(sid, user)

async def validate_inbounds():
    # ... реализация из main.py ...
    pass

async def ensure_user_profile(tg_id: int, desired_name: str):
    """
    1) Ищет клиента на всех серверах.
    2) Если найден – возвращает cfg, email.
    3) Если не найден – создаёт профиль с учётом лимита.
    """
    email_prefix = f"{tg_id}_"
    cfg, user = await find_user_server(email_prefix)
    if user:
        return cfg, user["email"]
    sid = await server_manager.pick_least_loaded()
    if await server_manager.is_full(sid):
        raise RuntimeError("Все серверы заполнены")
    email = email_prefix + desired_name
    inbound_id = int(server_manager.cfgs[sid].INBOUNDS.split(",")[0])
    await server_manager.create_client(sid, inbound_id, email, tg_id)
    return server_manager.cfgs[sid], email

async def find_user_server(user_id_prefix, prefer_domain=None):
    for sid, cfg in server_manager.cfgs.items():
        clients = await server_manager.list_clients(sid)
        user = next((c for c in clients if c["email"].startswith(user_id_prefix)), None)
        if user:
            return cfg, user
    return None, None

# ... другие функции бизнес-логики ... 