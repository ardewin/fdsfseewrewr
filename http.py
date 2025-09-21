import httpx
from datetime import datetime, timedelta
from urllib.parse import quote
import json
from httpx import HTTPStatusError
from config import app_settings, SERVERS_CFG
from aiocache import cached
import backoff

_auth_cache = {}  # теперь кэш по sid

def get_httpx_client(server_cfg):
    return httpx.AsyncClient(
        base_url=server_cfg.BASE_URL,
        verify=server_cfg.VERIFY_SSL,
        timeout=10,
        limits=httpx.Limits(max_connections=app_settings.HTTPX_MAX_CONNECTIONS)
    )

@backoff.on_exception(backoff.expo, (httpx.RequestError, httpx.HTTPStatusError), max_tries=3, jitter=backoff.full_jitter)
async def api_auth(server_cfg, force=False) -> httpx.Cookies:
    sid = getattr(server_cfg, 'SID', 'default')
    now = datetime.now()
    if sid not in _auth_cache:
        _auth_cache[sid] = {"cookies": None, "expires": datetime.fromtimestamp(0)}
    if not force and _auth_cache[sid]["cookies"] and _auth_cache[sid]["expires"] > now:
        return _auth_cache[sid]["cookies"]
    async with get_httpx_client(server_cfg) as client:
        resp = await client.post(
            "/login",
            data={"username": server_cfg.USERNAME, "password": server_cfg.PASSWORD},
            timeout=10,
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError("Авторизация не удалась")
    cookies = resp.cookies
    _auth_cache[sid]["cookies"] = cookies
    _auth_cache[sid]["expires"] = now + timedelta(minutes=30)
    return cookies

@cached(ttl=app_settings.CACHE_TTL_INBOUNDS)
@backoff.on_exception(backoff.expo, (httpx.RequestError, httpx.HTTPStatusError), max_tries=3, jitter=backoff.full_jitter)
async def api_inbounds_list(server_cfg, cookies: httpx.Cookies):
    async with get_httpx_client(server_cfg) as client:
        resp = await client.get(
            "/panel/api/inbounds/list", cookies=cookies, timeout=10
        )
    resp.raise_for_status()
    return resp.json().get("obj", [])

@cached(ttl=app_settings.CACHE_TTL_CLIENTS)
@backoff.on_exception(backoff.expo, (httpx.RequestError, httpx.HTTPStatusError), max_tries=3, jitter=backoff.full_jitter)
async def api_clients(server_cfg, cookies: httpx.Cookies) -> list[dict]:
    items = await api_inbounds_list(server_cfg, cookies)
    clients = [c for ib in items for c in ib.get("clientStats", [])]
    return clients

@backoff.on_exception(backoff.expo, (httpx.RequestError, httpx.HTTPStatusError), max_tries=3, jitter=backoff.full_jitter)
async def api_create_client(
    server_cfg, cookies: httpx.Cookies, inbound_id: int, email: str, tg_id: int
):
    cfg = {
        "id": email,
        "email": email,
        "flow": server_cfg.FLOW,
        "limitIp": 0,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "tgId": tg_id,
        "subId": "",
        "reset": 0,
        "sid": "",
    }
    payload = {"id": inbound_id, "settings": json.dumps({"clients": [cfg]})}
    async with get_httpx_client(server_cfg) as client:
        resp = await client.post(
            "/panel/api/inbounds/addClient",
            json=payload,
            cookies=cookies,
            timeout=10,
        )
    resp.raise_for_status()

@backoff.on_exception(backoff.expo, (httpx.RequestError, httpx.HTTPStatusError), max_tries=3, jitter=backoff.full_jitter)
async def api_delete_client(
    server_cfg, cookies: httpx.Cookies, inbound_id: int, client_id: str
):
    async with get_httpx_client(server_cfg) as client:
        resp = await client.post(
            f"/panel/api/inbounds/{inbound_id}/delClient/{client_id}",
            cookies=cookies,
            timeout=10,
        )
    resp.raise_for_status()

def build_vless(server_cfg, email: str, remark: str = "Vneseti") -> str:
    tag = f"{remark}-{email}"
    return (
        f"vless://{email}@{server_cfg.SERVER_DOMAIN}:{server_cfg.SERVER_PORT}"
        f"?type=tcp&security=reality&pbk={server_cfg.PBK}&fp={server_cfg.FP}"
        f"&sni={server_cfg.SNI}&sid={server_cfg.SID}&spx={server_cfg.SPX}&flow={server_cfg.FLOW}#{tag}"
    )

@backoff.on_exception(backoff.expo, (httpx.RequestError, httpx.HTTPStatusError), max_tries=3, jitter=backoff.full_jitter)
async def api_traffic(server_cfg, cookies: httpx.Cookies, client: dict) -> dict:
    cid = client["id"]
    email = client["email"]
    try:
        async with get_httpx_client(server_cfg) as client_http:
            resp = await client_http.get(
                f"/panel/api/inbounds/getClientTrafficsById/{cid}",
                cookies=cookies, timeout=10
            )
        resp.raise_for_status()
        return resp.json().get("obj", {}) or {"uplink": 0, "downlink": 0, "total": 0}
    except HTTPStatusError as e:
        if e.response.status_code != 404:
            raise
    try:
        async with get_httpx_client(server_cfg) as client_http:
            resp = await client_http.get(
                f"/panel/api/inbounds/getClientTraffics/{quote(email, safe='')}",
                cookies=cookies, timeout=10
            )
        resp.raise_for_status()
        return resp.json().get("obj", {}) or {"uplink": 0, "downlink": 0, "total": 0}
    except HTTPStatusError:
        return {"uplink": 0, "downlink": 0, "total": 0}

@backoff.on_exception(backoff.expo, (httpx.RequestError, httpx.HTTPStatusError), max_tries=3, jitter=backoff.full_jitter)
async def api_onlines(server_cfg, cookies: httpx.Cookies) -> list:
    async with get_httpx_client(server_cfg) as client:
        resp = await client.post(
            "/panel/api/inbounds/onlines", cookies=cookies, timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("obj", []) 