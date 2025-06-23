
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
from dotenv import load_dotenv
import os
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, LabeledPrice
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardMarkup

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Subscription, User
from main import async_session_factory

from keyboard import get_main_reply_keyboard


# --------------------------------------------------------------------------- #
#                               Константы                                     #
# --------------------------------------------------------------------------- #

load_dotenv()

SUB_DURATION_DAYS = int(os.getenv('SUB_DURATION_DAYS'))
SUB_PRICE_STARS = int(os.getenv('SUB_PRICE_STARS'))

router = Router()

# --------------------------------------------------------------------------- #
#                                 Статус                                      #
# --------------------------------------------------------------------------- #

invoice_message_ids = {}

@router.message(F.text == "🤩 Подписка")
async def show_subscription(message: Message) -> None:
    async with async_session_factory() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == message.from_user.id)
        )
        
        if not user:
            await message.answer("Не удалось определить пользователя.")
            return

        now = datetime.now(timezone.utc)
        active = user.subscription_end and user.subscription_end > now

        if active:
            remaining = (user.subscription_end - now).days
            text = (
                f"✨ Ваша подписка активна ещё {remaining} дн.\n\n"
                "Хотите продлить на 30 дней за "
                f"<b>{SUB_PRICE_STARS} Stars</b>?\n\n"
                f"Для продления нажмите кнопку <b>Заплатить</b> ⬆️"
            )
        else:
            text = (
                "🤩 Подписка даёт скидку 15% на все заказы.\n"
                f"Стоимость: <b>{SUB_PRICE_STARS} Stars</b> на 30 дней."
            )

    subscription_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main_menu")]
        ]
    )
    prices = [
        LabeledPrice(
            label="Месячная подписка",
            amount=int(SUB_PRICE_STARS)
        )
    ]
    invoice_message = await message.bot.send_invoice(
        chat_id=message.from_user.id,
        title="Оплата подписки",
        description="🤩 Подписка даёт скидку 15% на все заказы.",
        payload="subscription_payment",
        currency="XTR",
        prices=prices,
    )
    invoice_message_ids[message.from_user.id] = invoice_message.message_id
    logging.info(f"Сохранён идентификатор сообщения с формой оплаты: {invoice_message.message_id}")
    await message.answer(text, reply_markup=subscription_keyboard)
    

@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery) -> None:
    await callback.message.delete()
    invoice_msg_id = invoice_message_ids.get(callback.from_user.id)
    logging.info(f"Попытка удалить сообщение с формой оплаты с идентификатором: {invoice_msg_id}")
    if invoice_msg_id:
        try:
            await callback.message.bot.delete_message(
                chat_id=callback.from_user.id,
                message_id=invoice_msg_id
            )
            del invoice_message_ids[callback.from_user.id]
            logging.info("Сообщение с формой оплаты успешно удалено.")
        except Exception as e:
            logging.info(f"Ошибка при удалении сообщения с формой оплаты: {e}")
    else:
        logging.info("Идентификатор сообщения с формой оплаты не найден.")
    await callback.answer()

    main_keyboard = get_main_reply_keyboard(callback.from_user.id)
    await callback.message.bot.send_message(
        chat_id=callback.from_user.id,
        text="Вы вернулись в главное меню.",
        reply_markup=main_keyboard
    )


# --------------------------------------------------------------------------- #
#                               Оплата                                        #
# --------------------------------------------------------------------------- #

async def buy_subscription(message: Message) -> None:
    async with async_session_factory() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == message.chat.id)
        )
        if not user:
            return
        
        now = datetime.now(timezone.utc)
      
        base_date = user.subscription_end if user.subscription_end and user.subscription_end > now else now
        new_end = base_date + timedelta(days=SUB_DURATION_DAYS)
        user.subscription_end = new_end

        session.add(
            Subscription(
                user_id=user.id,
                full_name=user.full_name,
                purchase_date=now,
                expires_at=new_end,
                stars_spent=SUB_PRICE_STARS,
            )
        )
        await session.commit()
    await message.answer(
        f"✅ Подписка активирована до <b>{new_end:%d.%m.%Y}</b>.\n"
        "Скидка 15 % применяется автоматически при заказе."
    )

async def check_sub(user_id: int) -> bool:
    async with async_session_factory() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == user_id)
        )
        if not user:
            return False

        now = datetime.now(timezone.utc)
        return user.subscription_end and user.subscription_end > now