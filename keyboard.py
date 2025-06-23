import logging
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from main import admin_id

def get_main_reply_keyboard(user_telegram_id: int):
    logging.info(f"User  Telegram ID: {user_telegram_id}")
    logging.info(f"Admin IDs: {admin_id}")
    main_keyboard = [
        [KeyboardButton(text="📋 Открыть меню")],
        [KeyboardButton(text="🛒 Корзина")],
        [KeyboardButton(text="🤩 Подписка")],
        [KeyboardButton(text="💬 Поддержка")]
    ]
    if user_telegram_id in admin_id:
        main_keyboard.append([
            KeyboardButton(text="📊 Статистика"),
            KeyboardButton(text="🛒 Заказы"),
            KeyboardButton(text="🛍️ Продукты")
        ])
        main_keyboard.append([
            KeyboardButton(text="➕ Добавить категорию"),
            KeyboardButton(text="➕ Добавить товар"),
            KeyboardButton(text="➕ Активировать товар"),
        ])
    return ReplyKeyboardMarkup(
        keyboard=main_keyboard,
        resize_keyboard=True,
        one_time_keyboard=True
    )