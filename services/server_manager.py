import asyncio
import httpx
from config import app_settings, ServerSettings
from datetime import datetime, timedelta
from aiocache import caches, cached
import json

class ServerManager:
    def __init__(self, cfgs: dict[str, ServerSettings]):
        self.cfgs = cfgs
        self._auth_cache: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _auth(self, sid: str, force=False) -> httpx.Cookies:
        # Защита от гонок одним asyncio.Lock на sid
        if sid not in self._locks:
            self._locks[sid] = asyncio.Lock()
        async with self._locks[sid]:
            now = datetime.now()
            cache = self._auth_cache.get(sid)
            if not force and cache and cache['cookies'] and cache['expires'] > now:
                return cache['cookies']
            cfg = self.cfgs[sid]
            async with httpx.AsyncClient(verify=cfg.VERIFY_SSL, timeout=10, limits=httpx.Limits(max_connections=app_settings.HTTPX_MAX_CONNECTIONS)) as client:
                resp = await client.post(
                    f"{cfg.BASE_URL}/login",
                    data={"username": cfg.USERNAME, "password": cfg.PASSWORD},
                )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError("Авторизация не удалась")
            cookies = resp.cookies
            self._auth_cache[sid] = {
                "cookies": cookies,
                "expires": now + timedelta(minutes=30)
            }
            return cookies

    def _extract_obj(self, raw):
        # Если словарь — берём .get("obj", {})
        if isinstance(raw, dict):
            return raw.get("obj", {})
        # Если список — берём первый элемент, если он словарь
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            return raw[0]
        return {}

    async def list_clients(self, sid: str, use_cache=True) -> list[dict]:
        cookies = await self._auth(sid)
        cfg = self.cfgs[sid]
        async with httpx.AsyncClient(verify=cfg.VERIFY_SSL, timeout=10, limits=httpx.Limits(max_connections=app_settings.HTTPX_MAX_CONNECTIONS)) as client:
            resp = await client.get(
                f"{cfg.BASE_URL}/panel/api/inbounds/list",
                cookies=cookies,
            )
        resp.raise_for_status()
        items = resp.json().get("obj", [])
        result = []
        for ib in items:
            inbound_id = ib["id"]
            # Парсим settings для получения UUID
            try:
                settings = json.loads(ib["settings"])
                uuid_map = {c["email"]: c["id"] for c in settings.get("clients", [])}
            except Exception:
                uuid_map = {}
            for st in ib.get("clientStats", []):
                email = st["email"]
                st["uuid"] = uuid_map.get(email, "")
                st["inbound_id"] = inbound_id
                # bytes_in/bytes_out для быстрого суммирования
                st["bytes_in"] = st.get("uplink", st.get("up", 0))
                st["bytes_out"] = st.get("downlink", st.get("down", 0))
                result.append(st)
        return result

    async def pick_least_loaded(self) -> str:
        coros = [self.list_clients(sid) for sid in self.cfgs]
        results = await asyncio.gather(*coros, return_exceptions=True)
        loads = [
            (sid, len(clients))
            for sid, clients in zip(self.cfgs, results)
            if isinstance(clients, list)
        ]
        if not loads:
            raise RuntimeError("Нет доступных серверов!")
        min_load = min(cnt for _, cnt in loads)
        least_loaded = [sid for sid, cnt in loads if cnt == min_load]
        import random
        return random.choice(least_loaded)

    async def create_client(self, sid: str, inbound_id: int, email: str, tg_id: int, skip_limit=False):
        clients = await self.list_clients(sid)
        if not skip_limit and len(clients) >= app_settings.MAX_CLIENTS:
            raise RuntimeError(f"Сервер {sid} заполнен")
        cookies = await self._auth(sid)
        cfg = self.cfgs[sid]
        payload = {
            "id": inbound_id,
            "settings": __import__('json').dumps({"clients": [{
                "id": email,
                "email": email,
                "flow": cfg.FLOW,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": tg_id,
                "subId": "",
                "reset": 0,
                "sid": "",
            }]})
        }
        async with httpx.AsyncClient(verify=cfg.VERIFY_SSL, timeout=10, limits=httpx.Limits(max_connections=app_settings.HTTPX_MAX_CONNECTIONS)) as client:
            resp = await client.post(
                f"{cfg.BASE_URL}/panel/api/inbounds/addClient",
                json=payload,
                cookies=cookies,
            )
        resp.raise_for_status()
        # Инвалидация кэша после успешного создания клиента
        await caches.get('default').delete(f"api_clients:{sid}")
        await caches.get('default').delete(f"api_inbounds_list:{sid}")

    async def delete_client(self, sid: str, inbound_id: int, client_id: str):
        cookies = await self._auth(sid)
        cfg = self.cfgs[sid]
        async with httpx.AsyncClient(verify=cfg.VERIFY_SSL, timeout=10, limits=httpx.Limits(max_connections=app_settings.HTTPX_MAX_CONNECTIONS)) as client:
            resp = await client.post(
                f"{cfg.BASE_URL}/panel/api/inbounds/{inbound_id}/delClient/{client_id}",
                cookies=cookies,
            )
        resp.raise_for_status()
        # Инвалидация кэша после успешного удаления клиента
        await caches.get('default').delete(f"api_clients:{sid}")
        await caches.get('default').delete(f"api_inbounds_list:{sid}")

    def _normalize_traffic(self, js: dict) -> dict:
        """Гарантирует поля uplink/downlink в байтах (берёт up/down если нужно)."""
        return {
            "uplink":   js.get("uplink",   js.get("up",   0)),
            "downlink": js.get("downlink", js.get("down", 0)),
        }

    def to_gb(self, bytes_: int) -> float:
        return bytes_ / 1024 ** 3

    async def get_traffic(self, sid: str, client: dict) -> dict:
        cookies = await self._auth(sid)
        cfg = self.cfgs[sid]
        cid = client.get("id")
        inbound_id = client.get("inbound_id")
        email = client.get("email", "")
        obj = {}
        # 1) По ID+inbound
        if inbound_id:
            async with httpx.AsyncClient(cookies=cookies, verify=cfg.VERIFY_SSL, timeout=10) as client_http:
                resp = await client_http.get(
                    f"{cfg.BASE_URL}/panel/api/inbounds/getClientTrafficsById/{cid}?inId={inbound_id}"
                )
            obj = self._extract_obj(resp.json())
        # 2) Если по ID не дали данных — пробуем по email
        if not obj and email:
            async with httpx.AsyncClient(cookies=cookies, verify=cfg.VERIFY_SSL, timeout=10) as client_http:
                resp = await client_http.get(
                    f"{cfg.BASE_URL}/panel/api/inbounds/getClientTraffics/{email}"
                )
            obj = self._extract_obj(resp.json())
        up   = obj.get("uplink", obj.get("up", 0))
        down = obj.get("downlink", obj.get("down", 0))
        return {"uplink": up, "downlink": down}

    async def is_alive(self, sid: str) -> bool:
        try:
            await self._auth(sid)
            return True
        except Exception:
            return False

    @cached(ttl=10)
    async def get_online_clients(self, sid: str) -> list[str]:
        """Возвращает список email'ов онлайн-клиентов через POST /panel/api/inbounds/onlines (TTL 10 сек)."""
        cookies = await self._auth(sid)
        cfg = self.cfgs[sid]
        async with httpx.AsyncClient(cookies=cookies, verify=cfg.VERIFY_SSL, timeout=10) as client:
            resp = await client.post(f"{cfg.BASE_URL}/panel/api/inbounds/onlines")
        if resp.status_code == 404:
            raise RuntimeError("API он-лайн недоступен; проверьте версию X-UI")
        resp.raise_for_status()
        return resp.json()["obj"]

    async def invalidate_cache(self, sid: str, what: str):
        """Инвалидация кэша по типу ('clients', 'inbounds_list', 'onlines')."""
        if what == "clients":
            await caches.get('default').delete(f"api_clients:{sid}")
        elif what == "inbounds_list":
            await caches.get('default').delete(f"api_inbounds_list:{sid}")
        elif what == "onlines":
            await caches.get('default').delete(f"api_inbounds_onlines:{sid}")

    async def is_full(self, sid: str) -> bool:
        return len(await self.list_clients(sid)) >= app_settings.MAX_CLIENTS

    async def refresh_auth_cookies_forever(self, interval_minutes=25):
        import asyncio
        while True:
            for sid in self.cfgs:
                try:
                    await self._auth(sid, force=True)
                except Exception as e:
                    print(f"[auth-refresh] Ошибка обновления куки для {sid}: {e}")
            await asyncio.sleep(interval_minutes * 60)

def _to_gb(bytes_: int, precision: int = 2) -> float:
    """Преобразует байты в гигабайты (1 GB = 1024³ B)."""
    return round(bytes_ / 1024 ** 3, precision) 