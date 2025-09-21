#!/usr/bin/env python3
"""
main.py — Telegram-бот для управления VLESS-клиентами (x-ui/3x-ui, aiogram 3.7+)
"""

import re
import json
import asyncio
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, ConfigDict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from httpx import HTTPStatusError
import textwrap
from aiocache import cached
from aiogram.exceptions import TelegramBadRequest
from loguru import logger
from dotenv import load_dotenv
import random

# Импорты вместо дублирования

from config import app_settings, is_admin, SERVERS_CFG
from services.core import pick_server_by_load, get_best_server_cfg, server_manager, ensure_user_profile
from keyboards import user_keyboard, admin_menu_keyboard
from api.http import api_auth, api_clients, api_create_client, api_delete_client, api_inbounds_list, api_traffic, api_onlines, build_vless
from handlers.user import register_user_handlers, find_user_server
from handlers import register_admin_handlers
from scheduler import start_scheduler
from db import init_models
from services import reminders
from services.instructions import send_or_edit
from sync_reminders import sync_reminders
from middlewares.rate_limit import RateLimitMiddleware
from services.telegram_utils import safe_send
from handlers.admin import ensure_admin_sid

# -------------------- 3. FSM -------------------- #
class UserFSM(StatesGroup):
    waiting_name = State()

class AdminAddClient(StatesGroup):
    waiting_all = State()

class AdminDelClient(StatesGroup):
    waiting_clientid = State()

# -------------------- 5. Бот и хендлеры -------------------- #
bot = Bot(token=app_settings.TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

async def get_or_create_user_key(server_cfg, cookies, tg_id, desired_name):
    prefix = f"{tg_id}_"
    all_clients = await api_clients(server_cfg, cookies)
    user_clients = [c for c in all_clients if c["email"].startswith(prefix)]
    if user_clients:
        return user_clients[0]["email"]
    email = prefix + desired_name
    inbound_id = int(server_cfg.INBOUNDS.split(",")[0])
    await api_create_client(server_cfg, cookies, inbound_id, email, tg_id)
    return email

async def validate_inbounds():
    for sid, server_cfg in SERVERS_CFG.items():
        count = len(server_cfg.INBOUNDS)
        if count > 15:
            msg = (
                f"⚠️ Внимание: в конфиге указано {count} INBOUNDS, "
                "рекомендуемый максимум — 15."
            )
            logger.warning(msg)
            for admin in app_settings.ADMIN_IDS:
                if admin.isdigit():
                    await safe_send(bot.send_message, int(admin), text=msg)

# -------------------- 6. Запуск -------------------- #
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
    # Запуск фонового обновления куки
    asyncio.create_task(server_manager.refresh_auth_cookies_forever())
    start_scheduler(bot)
    await init_models()
    count = await sync_reminders()
    logger.info(f"Синхронизировано {count} пользователей с сервера в базу данных.")

async def on_shutdown(_):
    logger.info("🛑 Bot stopped")
    from scheduler import scheduler
    scheduler.shutdown(wait=False)

def sensitive_filter(record):
    msg = record["message"]
    sensitive_patterns = [
        "password", "token", "cookies", "Authorization",
        r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+",  # email
    ]
    for pat in sensitive_patterns:
        if re.search(pat, msg, re.IGNORECASE):
            return False
    return True

def mask_sensitive(record):
    msg = record["message"]
    msg = re.sub(r"(?i)(token|password|Authorization)[=: ]+([^\s,;]+)", r"\1=***", msg)
    msg = re.sub(r"(?i)cookies[=: ]+([^\s,;]+)", "cookies=***", msg)
    msg = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+", "***@***", msg)
    record["message"] = msg
    return record

logger = logger.patch(mask_sensitive)

async def main():
    logger.add("bot.log", level="INFO", rotation="10 MB", retention="10 days", compression="zip",
               filter=sensitive_filter,
               backtrace=True, diagnose=False)
    register_user_handlers(dp, bot)
    register_admin_handlers(dp)
    dp.startup.register(validate_inbounds)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await bot.delete_webhook(drop_pending_updates=True)
    dp.message.middleware(RateLimitMiddleware())
    await dp.start_polling(bot)

if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
