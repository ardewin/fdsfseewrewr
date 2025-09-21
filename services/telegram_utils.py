import asyncio
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramForbiddenError

async def safe_send(send_func, *args, silent=False, **kwargs):
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            return await send_func(*args, **kwargs)
        except TelegramRetryAfter as e:
            # aiogram 3: FloodWait/RetryAfter
            await asyncio.sleep(getattr(e, 'retry_after', getattr(e, 'timeout', 5)))
        except Exception as e:
            # Ловим любые другие ошибки Telegram, которые могут содержать retry info
            retry = getattr(e, 'retry_after', None) or getattr(e, 'timeout', None)
            if retry:
                await asyncio.sleep(retry)
            elif isinstance(e, (TelegramBadRequest, TelegramForbiddenError)):
                if silent:
                    return None
                raise
            else:
                # Пробрасываем все остальные ошибки
                raise
    if silent:
        return None
    # Если все попытки неудачны — пробрасываем последнюю ошибку
    raise RuntimeError(f"safe_send: не удалось отправить сообщение после {max_attempts} попыток") 