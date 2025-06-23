import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv
import os
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from aiogram.client.bot import DefaultBotProperties

from commands import set_commands

#--------------------------------------------------------------------------- #
# 1. Настройка логирования                                                   #
#--------------------------------------------------------------------------- #

logger = logging.getLogger()
logger.setLevel(logging.INFO) 


formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

file_handler = logging.FileHandler('bot.log', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

logger.handlers = []

logger.addHandler(file_handler)
logger.addHandler(console_handler)

#--------------------------------------------------------------------------- #
# 2. Загрузка переменных окружения                                           #
#--------------------------------------------------------------------------- #
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
admin_id = list(map(int, os.getenv('admin_id').split(',')))

# --------------------------------------------------------------------------- #
# 3. Движок базы данных и фабрика сессий                                      #
# --------------------------------------------------------------------------- #
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session_factory = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)

async def init_db() -> None:
    from models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --------------------------------------------------------------------------- #
# 4. Запуск приложения                                                        #
# --------------------------------------------------------------------------- #

async def on_startup(bot: Bot) -> None:
    await init_db()
    await set_commands(bot, admin_id)
    logger.info("База данных инициализирована")

async def main() -> None:
    session = AiohttpSession()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="html"), 
              session=session)
    dp = Dispatcher(storage=MemoryStorage())

    from routers.admin import router as admin_router
    from routers.user import router as user_router
    from routers.subscriptions import router as subscriptions_router

    dp.include_router(admin_router)
    dp.include_router(user_router)
    dp.include_router(subscriptions_router)
    await bot.delete_webhook(drop_pending_updates=True)
    await on_startup(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
