from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChatAdministrators

async def set_commands(bot: Bot, BOT_ADMINS: set[int]) -> None:
  
    await bot.set_my_commands(
        commands=[
            BotCommand(command="start", description="Запуск бота"),
            BotCommand(command="menu", description="Открыть меню"),
            BotCommand(command="cart", description="Открыть корзину"),
        ],
        scope=BotCommandScopeDefault()
    )
    
    for chat_id in BOT_ADMINS:
        try:
            await bot.set_my_commands(
                commands=[
                    BotCommand(command="start", description="Запуск бота"),
                    BotCommand(command="menu", description="Открыть меню"),
                    BotCommand(command="cart", description="Открыть корзину"),
                    BotCommand(command="add_category", description="Добавить категорию"),
                    BotCommand(command="add_product", description="Добавить товар"),
                    BotCommand(command="products", description="Список товаров"),
                    BotCommand(command="orders", description="Заказы"),
                    BotCommand(command="stats", description="Статистика заказов"),
                ],
                scope=BotCommandScopeChatAdministrators(chat_id=BOT_ADMINS),
            )
        except Exception:
            pass
