from apscheduler.schedulers.asyncio import AsyncIOScheduler
from services import reminders
from aiogram import Bot
from config import app_settings
from loguru import logger
from services.telegram_utils import safe_send
from aiogram.utils.chat_action import ChatActionSender
import asyncio

scheduler = AsyncIOScheduler(timezone="Europe/Amsterdam")

def start_scheduler(bot: Bot):
    async def _job():
        chat_ids = await reminders.list_enabled_chat_ids()
        async def send_reminder(cid):
            try:
                async with ChatActionSender.typing(bot, cid):
                    await safe_send(bot.send_message, cid,
                        "🔔 Привет! Пришло время поддержать наш сервер, чтобы он продолжал стабильно работать. Благодарим за вашу помощь! 🙏"
                    )
            except Exception as e:
                logger.error("Ошибка при отправке напоминания в {}: {}", cid, e)
        await asyncio.gather(*(send_reminder(cid) for cid in chat_ids))

    # Напоминание приходит всем пользователям с включенными уведомлениями
    # каждое 10-е число месяца в 17:00 по Moscow time,
    # независимо от даты получения конфига
    scheduler.add_job(_job, "cron", day=10, hour=17, minute=0, id="support_monthly", max_instances=1)
    #scheduler.add_job(_job, "interval", minutes=1, id="support_test_minutely", max_instances=1)
    scheduler.start() 