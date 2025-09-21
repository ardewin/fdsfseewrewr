from dotenv import load_dotenv
load_dotenv()

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, ConfigDict
from aiogram import types
import os

class AppSettings(BaseSettings):
    TELEGRAM_TOKEN: str = Field(default=None, env="TELEGRAM_TOKEN")
    ANDROID_URL: str = Field(default=None, env="ANDROID_URL")
    IOS_URL: str = Field(default=None, env="IOS_URL")
    WINDOWS_URL: str = Field(default=None, env="WINDOWS_URL")
    SUPPORT_USERNAME: str = Field(default=None, env="SUPPORT_USERNAME")
    ADMIN_IDS: set[str] = Field(default_factory=set, env="ADMIN_IDS")
    MAX_MSG_LEN: int = Field(default=4000, env="MAX_MSG_LEN")
    MAX_INBOUNDS: int = Field(default=15, env="MAX_INBOUNDS")
    HTTPX_MAX_CONNECTIONS: int = Field(default=20, env="HTTPX_MAX_CONNECTIONS")
    CACHE_TTL_INBOUNDS: int = Field(default=60, env="CACHE_TTL_INBOUNDS")
    CACHE_TTL_CLIENTS: int = Field(default=60, env="CACHE_TTL_CLIENTS")
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./data.sqlite", env="DATABASE_URL")
    MAX_CLIENTS: int = Field(default=15, env="MAX_CLIENTS")

    model_config = ConfigDict(extra='allow', json_encoders={set: list})

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, set):
            return v
        if isinstance(v, list):
            return set(str(i).strip().lstrip("@").lower() for i in v if str(i).strip())
        if isinstance(v, str):
            items = [i.strip().lstrip("@").lower() for i in v.split(",") if i.strip()]
            return set(items)
        if isinstance(v, int):
            return {str(v)}
        return set()

app_settings = AppSettings()

def is_admin(user: types.User) -> bool:
    return (
        str(user.id) in app_settings.ADMIN_IDS or
        (user.username and user.username.lower() in app_settings.ADMIN_IDS)
    )

class ServerSettings(BaseSettings):
    BASE_URL: str
    USERNAME: str
    PASSWORD: str
    INBOUNDS: str
    SERVER_DOMAIN: str
    SERVER_PORT: int
    FLOW: str
    PBK: str
    SNI: str
    SID: str
    FP: str = "random"
    SPX: str = "/"
    VERIFY_SSL: bool = False

    model_config = {"extra": "allow"}

def build_all_servers() -> dict[str, ServerSettings]:
    ids = [i.strip() for i in os.getenv("SERVERS", "").split(",") if i.strip()]
    if not ids:
        raise RuntimeError("SERVERS пуст — укажите хотя бы один ID")
    configs = {}
    for sid in ids:
        configs[sid] = ServerSettings(_env_prefix=f"{sid}_")
    return configs

SERVERS_CFG: dict[str, ServerSettings] = build_all_servers()