import time
import random
import asyncio
from aiogram import BaseMiddleware
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from collections import defaultdict

USER_RATE_LIMIT = 1  # seconds
CAPTCHA_TTL = 60     # seconds

class TTLDict:
    def __init__(self, ttl):
        self.ttl = ttl
        self._data = {}

    def set(self, key, value):
        self._data[key] = (value, time.time() + self.ttl)

    def get(self, key, default=None):
        v = self._data.get(key)
        if not v:
            return default
        value, expires = v
        if time.time() > expires:
            self._data.pop(key, None)
            return default
        return value

    def cleanup(self):
        now = time.time()
        for k in list(self._data.keys()):
            if self._data[k][1] < now:
                self._data.pop(k, None)

class RateLimitMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self.last_time = TTLDict(USER_RATE_LIMIT * 10)
        self.captcha = TTLDict(CAPTCHA_TTL)

    async def __call__(self, handler, event: Message, data):
        user_id = event.from_user.id
        now = time.time()
        state: FSMContext = data.get("state")
        self.last_time.cleanup()
        self.captcha.cleanup()
        # Проверка капчи
        captcha_answer = self.captcha.get(user_id)
        if captcha_answer is not None:
            # Ожидаем ответ на капчу
            try:
                user_answer = int(event.text.strip())
            except Exception:
                await event.answer("Пожалуйста, введите число — ответ на капчу!")
                return
            if user_answer == captcha_answer:
                self.captcha.set(user_id, None)  # сбросить капчу
                await event.answer("✅ Капча решена. Спасибо!")
            else:
                await event.answer("❌ Неверно. Попробуйте ещё раз!")
                return
        last = self.last_time.get(user_id, 0)
        if now - last < USER_RATE_LIMIT:
            # Сохраняем капчу
            a, b = random.randint(1, 9), random.randint(1, 9)
            answer = a + b
            self.captcha.set(user_id, answer)
            await event.answer(f"Слишком много запросов! Решите капчу: {a} + {b} = ?")
            return  # Не передаём дальше
        self.last_time.set(user_id, now)
        await handler(event, data) 