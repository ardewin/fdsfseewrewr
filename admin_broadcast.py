from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.utils.chat_action import ChatActionSender
from aiogram.types import BufferedInputFile
import asyncio, io, textwrap
from loguru import logger
from aiolimiter import AsyncLimiter

from config import is_admin, app_settings
from db import SessionLocal, Broadcast, BroadcastErrorLog        # понадобится для статистики ошибок
from services import reminders
from services.telegram_utils import safe_send
from services.reminders import list_active_clients

router = Router()

# ---------- /send ----------
@router.message(Command("send"))
async def cmd_send(msg: types.Message):
    if not is_admin(msg.from_user):
        return

    #  /send <uid|@username> <text…>
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        return await msg.answer("Использование: <code>/send user_id|@username текст</code>", parse_mode="HTML")

    _cmd, target, text = parts
    # --- превращаем @username → user_id -------------
    if target.startswith("@"):
        try:
            chat = await msg.bot.get_chat(target)
            target_id = chat.id
        except TelegramBadRequest:
            return await msg.answer("⛔️ Пользователь не найден")
    else:
        try:
            target_id = int(target)
        except ValueError:
            return await msg.answer("⛔️ Некорректный user_id")

    try:
        await safe_send(msg.bot.send_message, target_id, text)
        await msg.answer("✅ Отправлено")
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await msg.answer(f"⚠️ Не удалось: {e.message}")

# ---------- /bc ----------
BATCH, PAUSE = 25, 1.2
limiter = AsyncLimiter(29, 1)

def get_progress_bar(current, total, length=10):
    percent = current / total if total else 0
    filled = int(percent * length)
    bar = '▇' * filled + '▂' * (length - filled)
    percent_str = f"{int(percent * 100)}%"
    return f"{bar} {percent_str}"

@router.message(Command("bc"))
async def cmd_bc(msg: types.Message):
    if not is_admin(msg.from_user):
        return

    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        return await msg.answer("Использование: <code>/bc текст</code>", parse_mode="HTML")
    text = parts[1]

    ids = await reminders.list_all_chat_ids()
    if not ids:
        return await msg.answer("В базе нет получателей")

    total = len(ids); ok = err = 0
    failed: list[tuple[int, str]] = []

    progress = await msg.answer(f"🚀 Рассылка… 0 / {total}\n{get_progress_bar(0, total)}")

    async with ChatActionSender.typing(msg.bot, msg.chat.id):
        for i, cid in enumerate(ids, 1):
            try:
                async with limiter:
                    await safe_send(msg.bot.send_message, cid, text)
                ok += 1
            except Exception as e:
                err += 1
                failed.append((cid, e.__class__.__name__))

            if i % 5 == 0 or i == total:
                bar = get_progress_bar(i, total)
                async with limiter:
                    await safe_send(progress.edit_text,
                        f"🚀 Рассылка… {i} / {total}\n{bar}\n"
                        f"✅ {ok}  ⚠️ {err}",
                        silent=True)

            if i % BATCH == 0:
                await asyncio.sleep(PAUSE)

    await safe_send(progress.edit_text,
        f"🏁 Готово!\n✅ <b>{ok}</b>  ⚠️ <b>{err}</b>",
        parse_mode="HTML", silent=True)

    # лог ошибок в БД (если нужен)
    if failed:
        async with SessionLocal() as s:
            s.add_all([BroadcastErrorLog(bc_id=None, chat_id=c, reason=r) for c, r in failed])
            await s.commit()

        # краткий список в чат
        details = "\n".join(f"{cid} — {reason}" for cid, reason in failed)
        if len(details) < app_settings.MAX_MSG_LEN - 100:
            await msg.answer(f"<b>Не доставлено:</b>\n{details}", parse_mode="HTML")
        else:
            buf = io.BytesIO(details.encode()); buf.name = "failed.txt"
            await msg.bot.send_document(msg.chat.id, BufferedInputFile(buf.read(), buf.name),
                                         caption="Не доставлено") 