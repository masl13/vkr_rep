from __future__ import annotations
import logging
from decimal import Decimal
import types
from typing import Dict
import re
from dotenv import load_dotenv
import os
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from main import admin_id, async_session_factory
from models import Category, Order, OrderItem, Product, User
from routers.subscriptions import buy_subscription, check_sub
from keyboard import get_main_reply_keyboard


router = Router()

# --------------------------------------------------------------------------- #
#                               Константы / FSM                               #
# --------------------------------------------------------------------------- #

load_dotenv()

PAY_PROVIDER_TOKEN = os.getenv('PAY_PROVIDER_TOKEN')


class RegisterSG(StatesGroup):
    waiting_for_phone = State()


class CartSG(StatesGroup):
    waiting_for_address = State()
    waiting_for_comment = State()
    waiting_for_payment_method = State()


# --------------------------------------------------------------------------- #
#                              Вспомогательные ф‑ции                          #
# --------------------------------------------------------------------------- #


def _get_cart(data: dict) -> Dict[int, int]:
    return data.setdefault("cart", {})


async def _sync_user(message: Message) -> User:
    async with async_session_factory() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == message.chat.id)
        )
        if not user:
            user = User(
                tg_id=message.chat.id,
                full_name=message.chat.full_name,
            )
            session.add(user)
            await session.commit()

    return user


# --------------------------------------------------------------------------- #
#                                 /start                                      #
# --------------------------------------------------------------------------- #
    
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await _sync_user(message)
    logging.info(f"User:  {user}, Phone: {user.phone}")
    if user.phone is None:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await message.answer(
            "Для использования бота поделитесь своим номером телефона:",
            reply_markup=kb,
        )
        await state.set_state(RegisterSG.waiting_for_phone)
        return
    await message.answer("👋 Добро пожаловать!", reply_markup=get_main_reply_keyboard(message.from_user.id))


@router.message(RegisterSG.waiting_for_phone, F.contact)
async def save_phone(message: Message, state: FSMContext) -> None:
    if not message.contact or message.contact.user_id != message.from_user.id:
        await message.answer("Пожалуйста, используйте кнопку для отправки контакта.")
        return
    async with async_session_factory() as session:
        user = await session.scalar(select(User).where(User.tg_id == message.from_user.id))
        if user:
            user.phone = message.contact.phone_number
            await session.commit()
    await message.answer("✅ Телефон сохранён. Спасибо!", reply_markup=ReplyKeyboardRemove())
    await state.clear()
    await message.answer("Теперь можете открыть меню:", reply_markup=get_main_reply_keyboard(message.from_user.id))


# --------------------------------------------------------------------------- #
#                                 /menu                                       #
# --------------------------------------------------------------------------- #
@router.message(Command("menu"))
@router.message(lambda message: message.text == "📋 Открыть меню")
async def cmd_menu(message: Message) -> None:
    async with async_session_factory() as session:
        categories = await session.scalars(select(Category).order_by(Category.id))
        categories = categories.all()

    if not categories:
        await message.answer("Меню пока пусто. Попробуйте позже.")
        return

    kb = InlineKeyboardBuilder()
    buttons = []
    for cat in categories:
        buttons.append(
            InlineKeyboardButton(
                text=cat.title,
                callback_data=f"cat_{cat.id}",
            )
        )
    kb.add(*buttons)
    kb.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="exit_menu",
        )
    )

    kb.adjust(2)
    await message.answer("Выберите категорию:\nНажмите ⬅️ чтобы вернуться на главную страницу.", reply_markup=kb.as_markup())


@router.callback_query(F.data == "exit_menu")
async def cb_exit_menu(call: CallbackQuery) -> None:
    await call.message.delete()
    await call.message.answer("👋 Добро пожаловать!", reply_markup=get_main_reply_keyboard(call.from_user.id))


# --------------------------------------------------------------------------- #
#                             Добавление в корзину                            #
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("cat_"))
async def cb_open_category(call: CallbackQuery) -> None:
    cat_id = int(call.data.split("_")[1])
    async with async_session_factory() as session:
        products = await session.scalars(
            select(Product)
            .where(Product.category_id == cat_id, Product.is_active.is_(True))
            .order_by(Product.id)
        )
        products = products.all()

    if not products:
        await call.answer("Пустая категория 🙁", show_alert=True)
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    product_kb = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(
            text=prod.title,
            callback_data=f"show_product_details:{prod.id}",
        ) for prod in products
    ]
    for i in range(0, len(buttons), 2):
        product_kb.row(*buttons[i:i+2])
    if call.from_user.id in admin_id:
        product_kb.row(
            InlineKeyboardButton(
                text="Редактировать категорию",
                callback_data=f"edit_cat:{cat_id}",
            )
        )
        product_kb.row(
            InlineKeyboardButton(
                text="Удалить категорию",
                callback_data=f"remove_cat:{cat_id}",
            )
        )
    product_kb.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="exit_cart",
        )
    )
    await call.message.answer(
        "Выберите товар:",
        reply_markup=product_kb.as_markup(resize_keyboard=True),
    )
    await call.answer()



@router.callback_query(F.data.startswith("show_product_details:"))
async def show_product_details(call: CallbackQuery) -> None:
    prod_id = int(call.data.split(":")[1])
    async with async_session_factory() as session:
        product = await session.get(Product, prod_id)
        if not product:
            await call.answer("Товар не найден.", show_alert=True)
            return
    try:
        await call.message.delete()
    except Exception:
        pass
    kb = InlineKeyboardBuilder()
    kb.add(
        InlineKeyboardButton(
            text=f"Цена: {product.price} ₽", 
            callback_data=f"prod_{product.id}", 
        ),
        InlineKeyboardButton(
            text="⬅️ Назад к товарам",
            callback_data=f"cat_{product.category_id}",
        ),
    )
    kb.adjust(1)

    if product.photo_file_id:
        await call.message.answer_photo(
            photo=product.photo_file_id,
            caption=(
                f"<b>{product.title}</b>\n"
                f"{product.description}\n"
            ),
            reply_markup=kb.as_markup(),
        )
    else:
        await call.message.answer(
            text=(
                f"<b>{product.title}</b>\n"
                f"{product.description}\n"
            ),
            reply_markup=kb.as_markup(),
        )
    
    await call.answer()


# --------------------------------------------------------------------------- #
#                             Добавление в корзину                            #
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("prod_"))
@router.callback_query(F.data.startswith("reallyprod_"))
async def cb_add_product(call: CallbackQuery, state: FSMContext) -> None:
    prod_id = int(call.data.split("_")[1])

    try:
        await call.message.delete()
    except Exception:
        pass

    async with async_session_factory() as session:
        product = await session.get(Product, prod_id)

    if not product or not product.is_active:
        await call.answer("Товар недоступен", show_alert=True)
        return

    if call.from_user.id in admin_id and call.data.startswith("prod_"):
        kb = InlineKeyboardBuilder()
        kb.add(
            InlineKeyboardButton(
                text="Редактировать",
                callback_data=f"edit_product:{product.id}",
            ),
            InlineKeyboardButton(
                text="Удалить",
                callback_data=f"remove_product:{product.id}",
            ),
            InlineKeyboardButton(
                text="Добавить в корзину",
                callback_data=f"reallyprod_{product.id}",
            )
        )
        kb.adjust(1)
        await call.message.answer(
            f"Выберите действие с товаром «{product.title}»:",
            reply_markup=kb.as_markup(),
        )
        return


    data = await state.get_data()
    cart = _get_cart(data)
    cart[prod_id] = cart.get(prod_id, 0) + 1
    await state.update_data(cart=cart)

    await call.answer(f"Добавили «{product.title}» в корзину!")
    await cmd_cart(call.message, state)
   

# --------------------------------------------------------------------------- #
#                                   /cart                                     #
# --------------------------------------------------------------------------- #


@router.callback_query(F.data == "cart")
async def cb_cart(call: CallbackQuery, state: FSMContext) -> None:
    await cmd_cart(call.message, state)
    await call.answer()      

@router.message(Command("cart"))
@router.message(lambda message: message.text == "🛒 Корзина")
async def cmd_cart(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cart = _get_cart(data)

    kb = InlineKeyboardBuilder()
    if not cart:
        kb.add(InlineKeyboardButton(text="📋 Меню", callback_data="exit_cart"))
        kb.add(InlineKeyboardButton(text="🏠 Главная страница", callback_data="exit_menu"))
        await message.answer("Ваша корзина пуста.\nНажмите 📋 чтобы открыть меню.\nНажмите 🏠 чтобы открыть главную страницу.", reply_markup=kb.as_markup())
        return
        
    async with async_session_factory() as session:
        products: Dict[int, Product] = {
            p.id: p
            async for p in await session.stream_scalars(
                select(Product).where(Product.id.in_(cart.keys()))
            )
        }

    lines = []
    total = Decimal(0)
    for pid, qty in cart.items():
        product = products.get(pid)
        if not product:
            continue
        item_sum = product.price * qty
        total += item_sum
        lines.append(f"<b>{product.title}</b> × {qty} = {item_sum} ₽")
        kb.add(
            InlineKeyboardButton(text="➖", callback_data=f"dec_{pid}"),
            InlineKeyboardButton(text="➕", callback_data=f"inc_{pid}"),
            InlineKeyboardButton(text="❌", callback_data=f"del_{pid}"),
        )
    kb.adjust(3)

    kb.row(InlineKeyboardButton(text="➕ Добавить в заказ", callback_data="exit_cart"))
    kb.row(InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout"))
    kb.row(InlineKeyboardButton(text="🏠 Главная страница", callback_data="exit_menu")) 

    sale_total = (total * Decimal("0.85")).quantize(Decimal("0.01"))
    total_text = (
        f"\n\n<b>Итого: {total} ₽</b>"
        if not await check_sub(message.chat.id)
        else f"\n\n<b>Итого: <s>{total}</s> {sale_total} ₽</b>"
    )
    text = "\n".join(lines) + total_text

    try:
        await message.edit_text(text, reply_markup=kb.as_markup())
    except:
        await message.answer(text, reply_markup=kb.as_markup())



@router.callback_query(F.data == "exit_cart")
async def cb_exit_cart(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.delete()  
    await cmd_menu(call.message) 
    await call.answer()


@router.callback_query(F.data.startswith(("inc_", "dec_", "del_")))
async def cb_edit_cart(call: CallbackQuery, state: FSMContext) -> None:
    action, pid_str = call.data.split("_", 1)
    pid = int(pid_str)
    data = await state.get_data()
    cart = _get_cart(data)

    if pid not in cart:
        await call.answer("Товар не найден в корзине", show_alert=True)
        return

    if action == "inc":
        cart[pid] += 1
    elif action == "dec":
        cart[pid] -= 1
        if cart[pid] <= 0:
            del cart[pid]
    elif action == "del":
        del cart[pid]

    await state.update_data(cart=cart)
    if not cart:
        kb = InlineKeyboardBuilder()
        kb.add(InlineKeyboardButton(text="📋 Меню", callback_data="exit_cart"))
        kb.add(InlineKeyboardButton(text="🏠 Главная страница", callback_data="exit_menu"))
        try:
            await call.message.edit_text("Ваша корзина пуста.\nНажмите 📋 чтобы открыть меню.\nНажмите 🏠 чтобы открыть главную страницу.", reply_markup=kb.as_markup())
        except:
            await call.message.answer("Ваша корзина пуста.\nНажмите 📋 чтобы открыть меню.\nНажмите 🏠 чтобы открыть главную страницу.", reply_markup=kb.as_markup())
        await call.answer("Корзина пуста")
        return

    await call.answer("Корзина обновлена")
    await cmd_cart(call.message, state) 


# --------------------------------------------------------------------------- #
#                            Шаг: ввод адреса доставки                         #
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.in_(["checkout"]))
async def cb_checkout(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart = _get_cart(data)

    async with async_session_factory() as session:
        products: dict[int, Product] = {
            p.id: p
            async for p in await session.stream_scalars(
                select(Product).where(Product.id.in_(cart.keys()))
            )
        }

    total = Decimal(0)
    for pid, qty in cart.items():
        product = products.get(pid)
        if product:
            item_sum = product.price * qty
            total += item_sum
    user_id = call.from_user.id
    has_subscription = await check_sub(user_id)
    discount_rate = Decimal("0.15") if has_subscription else Decimal("0.0")

    total_after_discount = total * (1 - discount_rate)

    if total_after_discount < Decimal("1000"):
        await call.message.answer(
            "❗️ <b>Минимальная сумма заказа — 1000 рублей.</b>\n"
            "Пожалуйста, добавьте товары в корзину."
        )
        return

    kb_back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cart_del_cart")]
    ])

    kb_map = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺️ Открыть карту Яндекс", 
                                url="https://yandex.ru/map-widget/v1/?um=constructor%3Ab1cf7a1661b223dd719b0e8013361fb7fad90c6e3fede5df2f3c232c5ae5de40&amp;source=constructor")]
    ])                        
    
    instructions_text = (
        "📍 <b>Пожалуйста, укажите адрес доставки</b>\n\n"
        "<b>Формат:</b> Город, улица, дом, квартира (если есть)\n\n"
        "<b>Примеры:</b>\n"
        "• Москва, ул. Ленина, д. 10, кв. 5\n"
        "• Казань, бул. Ушакова, д. 3\n"
        "• Екатеринбург, ул. Малышева, д. 12\n\n"
        "<b>Обратите внимание:</b>\n"
        "– Город с заглавной буквы\n"
        "– Префиксы улиц: ул., просп., пер. и т.д.\n"
        "– Дом указан с номером: д. 10\n"
        "– Квартира — опционально: кв. 5\n"
    )

    address_msg = await call.message.answer(
        text=instructions_text,
        parse_mode="HTML",
        reply_markup=kb_back
    )

    map_msg = await call.message.answer(
        text="Ниже вы можете открыть интерактивную карту для уточнения зоны действия доставки:",
        reply_markup=kb_map
    )

    await state.set_state(CartSG.waiting_for_address)
    await call.message.delete()
    await state.update_data(address_message_id=address_msg.message_id, map_message_id=map_msg.message_id)

@router.callback_query(F.data == "cart_del_cart")
async def cb_cart(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    map_message_id = data.get("map_message_id")

    if map_message_id:
        try:
            await call.bot.delete_message(chat_id=call.from_user.id, message_id=map_message_id)
        except Exception as e:
           logging.info(f"Ошибка при удалении сообщения с картой: {e}")

    await cmd_cart(call.message, state)
    await call.answer()      


@router.message(CartSG.waiting_for_address)
async def set_address(message: types.Message, state: FSMContext) -> None:
    address = message.text.strip()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cart_del_cart")]
    ])

    data = await state.get_data()
    address_message_id = data.get("address_message_id")
    if address_message_id:
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=address_message_id,
                reply_markup=None
            )
        except Exception:
            pass 

    if not is_valid_address(address):
        address_msg = await message.answer(
            "Адрес был введен неверно!\nВведите его повторно!\n",
            parse_mode="HTML",
            reply_markup=kb
        )
        await state.update_data(address_message_id=address_msg.message_id)
        return
    await state.update_data(address=address)

    kb_builder = InlineKeyboardBuilder()
    kb_builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="checkout"))

    comment_message = await message.answer(
        "Пожалуйста, введите комментарий к заказу (если нет введите - ):",
        reply_markup=kb_builder.as_markup()
    )
    await state.update_data(comment_message_id=comment_message.message_id)
    await state.set_state(CartSG.waiting_for_comment)


@router.message(CartSG.waiting_for_comment)
async def set_comment(message: types.Message, state: FSMContext) -> None:
    comment = message.text.strip()
    await state.update_data(comment=comment) 

    data = await state.get_data()
    comment_message_id = data.get("comment_message_id")
    if comment_message_id:
        try:
            await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=comment_message_id, reply_markup=None)
        except Exception:
            pass

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(
        text="💳 Оплатить онлайн",
        callback_data="pay_online",
    ))
    kb.add(InlineKeyboardButton(
        text="💵 Наличные / карта курьеру",
        callback_data="pay_cash",
    ))
    kb.add(InlineKeyboardButton(
        text="⬅️ Изменить адрес и комментарий",
        callback_data="checkout",
    ))
    kb.add(InlineKeyboardButton(
        text="❌ Отмена заказа",
        callback_data="cancel_order",
    ))
    kb.adjust(1)

    payment_message = await message.answer(
        "Выберите способ оплаты:",
        reply_markup=kb.as_markup(),
    )
    await state.update_data(payment_message_id=payment_message.message_id)
    await state.set_state(CartSG.waiting_for_payment_method)


@router.callback_query(F.data == "cancel_order")
async def cb_cart(call: CallbackQuery, state: FSMContext) -> None:
    await cmd_cart(call.message, state)
    await call.answer("Отменено.")
    await state.clear()
    await call.message.delete()
    await call.message.answer("Создание заказа отменено!", reply_markup=get_main_reply_keyboard(call.from_user.id))


def is_valid_address(address: str) -> bool:
    pattern = re.compile(
        r'^\s*'                             
        r'([А-ЯЁ][а-яё]+(?:\s[А-ЯЁ][а-яё]+)*)\s*,?\s*'  
        r'(ул\.?|улица|просп\.?|пер\.?|бул\.?|шоссе|пр-т|переулок)\s+[А-ЯЁа-яё\s\d\-]+,?\s*'  
        r'д\.?\s*\d+[а-яА-ЯёЁ]?(?:,?\s*)?'      
        r'(кв\.?\s*\d+)?\s*$'              
        , re.IGNORECASE | re.UNICODE)

    match = pattern.match(address)
    return bool(match)


# --------------------------------------------------------------------------- #
#                Шаг: выбор способа оплаты (онлайн / наличные)                #
# --------------------------------------------------------------------------- #

@router.callback_query(CartSG.waiting_for_payment_method)
async def choose_payment(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    cart = _get_cart(data)

    payment_message_id = data.get("payment_message_id")
    if payment_message_id:
        try:
            await call.message.bot.delete_message(chat_id=call.from_user.id, message_id=payment_message_id)
        except Exception:
            pass  

    if call.data == "pay_cash":
        await _finalize_order(
            call.message, state, pay_online=False, successful=True
        )
        return

    user_id = call.from_user.id
    has_subscription = await check_sub(user_id)
    discount_rate = Decimal("0.15") if has_subscription else Decimal("0.0")

    async with async_session_factory() as session:
        products: Dict[int, Product] = {
            p.id: p
            async for p in await session.stream_scalars(
                select(Product).where(Product.id.in_(cart.keys()))
            )
        }

    prices: list[LabeledPrice] = []
    for pid, qty in cart.items():
        prod = products[pid]
        item_total = prod.price * qty
        if has_subscription:
            item_total *= (1 - discount_rate)

        prices.append(
            LabeledPrice(
                label=f"{prod.title} × {qty}",
                amount=int(item_total * 100),  
            )
        )

    invoice_message = await call.message.bot.send_invoice(
        chat_id=call.from_user.id,
        title="Оплата заказа",
        description="Food Delivery",
        payload="food-delivery-payload",
        provider_token=PAY_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        need_email=False,
        need_phone_number=False,
        photo_url="https://i.fbcd.co/products/original/d32491363c1d52ac365372cd2df281d6a7cf44f8873fa0900cd4a78a1528628c.jpg",
        photo_size=51200,    
        photo_width=640,     
        photo_height=480     
    )

    await state.update_data(invoice_message_id=invoice_message.message_id)


# --------------------------------------------------------------------------- #
#                      pre_checkout_query & successful_payment                #
# --------------------------------------------------------------------------- #


@router.pre_checkout_query()
async def pre_checkout_qh(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(lambda m: m.successful_payment is not None)
async def successful_payment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    invoice_message_id = data.get("invoice_message_id")
    if invoice_message_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=invoice_message_id)
        except Exception:
            pass  
    if message.successful_payment.currency == "XTR":
        await buy_subscription(message)
        return
    
    await _finalize_order(message, state, pay_online=True, successful=True)

# --------------------------------------------------------------------------- #
#                          Финализация заказа и уведомления                   #
# --------------------------------------------------------------------------- #

async def _finalize_order(
    message: Message,
    state: FSMContext,
    *,
    pay_online: bool,
    successful: bool,
) -> None:
    data = await state.get_data()
    cart = _get_cart(data)
    address = data["address"]
    user_id = message.chat.id  
    has_subscription = await check_sub(user_id) 
    discount_rate = Decimal("0.15") if has_subscription else Decimal("0.0") 
    
    async with async_session_factory() as session:
        products_map: Dict[int, Product] = {
            p.id: p
            async for p in await session.stream_scalars(
                select(Product).where(Product.id.in_(cart.keys()))
            )
        }
        total_without_discount = Decimal(
            sum(products_map[pid].price * qty for pid, qty in cart.items())
        )
        total_with_discount = (total_without_discount * (Decimal("1") - discount_rate)).quantize(Decimal("0.01"))

        total = total_with_discount if has_subscription else total_without_discount
         
        db_user = await session.scalar(
            select(User).where(User.tg_id == message.chat.id)
        )

        comment = data.get("comment", "Комментарий не указан.")
        product_titles = [products_map[pid].title for pid in cart.keys()]
        title = ", ".join(product_titles) if product_titles else "Нет названия"

        order = Order(
            user_id=db_user.id,
            status="принят в обработку",
            payment_method="оплачен онлайн" if pay_online else "оплата оффлайн",
            total_price=total,
            comment=comment,
            title=title,
            address=address,
        )
        session.add(order)
        await session.flush()  

        for pid, qty in cart.items():
            prod = products_map[pid]
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=pid,
                    qty=qty,
                    item_price=prod.price,  
                    title=prod.title,
                )
            )

        await session.commit()

    product_list = "\n".join(
        f"{products_map[pid].title} x {qty} шт." for pid, qty in cart.items()
    )
    main_keyboard = get_main_reply_keyboard(user_id)
    await message.answer(
        "🎉 Заказ оформлен!\n\n"
        f"🔢 Ваш номер заказа #{order.id}\n\n"
        f"🛍️ Продукты:\n{product_list}\n\n"
        f"💰 Сумма без скидки: {total_without_discount} ₽\n"
        f"💸 Сумма со скидкой: {total_with_discount} ₽\n\n"
        f"📝 Комментарий к заказу: {comment}\n\n"
        f"🏠 Адрес доставки: {address}\n\n"
        f"💳 Способ оплаты: {'Онлайн' if pay_online else 'При получении'}",
        reply_markup=main_keyboard
    )
    notify_text = (
        f"🆕 Новый заказ #{order.id}\n\n"
        f"👤 Пользователь: {message.chat.full_name} ({message.chat.id})\n\n"
        f"📞 Телефон: {db_user.phone}\n\n"
        f"🛍️ Продукты:\n{product_list}\n\n"
        f"💰 Сумма без скидки: {total_without_discount} ₽\n"
        f"💸 Сумма со скидкой: {total_with_discount} ₽\n\n"
        f"📝 Комментарий к заказу: {comment}\n\n"
        f"🏠 Адрес: {address}\n\n"
        f"💳 Оплата: {'Онлайн' if pay_online else 'При получении'}"
    )
    logging.info(f"Admin IDs: {admin_id}")  
    for admin_ids in admin_id:
        try:
            await message.bot.send_message(admin_ids, notify_text)
        except Exception as e:
            logging.info(f"Ошибка отправки админу {admin_ids}: {e}")
            pass
    await state.clear()

@router.message(F.text == "💬 Поддержка")
async def send_help_info(message: Message):
    help_text = (
        "Здравствуйте! 🤖\n"
        "Если у вас возникли вопросы или нужна помощь, свяжитесь с нашей поддержкой:\n"
        "📧 Email: support@example.com\n"
        "📱 Telegram: @makaaarov_13\n"
        "📞 Телефон: +7 (999) 123-45-67\n\n"
        "Мы всегда рады помочь вам!"
    )

    HELP_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="exit_menu")]
    ])
        
    await message.answer(help_text, reply_markup=HELP_KEYBOARD)
