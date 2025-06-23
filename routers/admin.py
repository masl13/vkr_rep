
from __future__ import annotations

from sqlalchemy.orm import selectinload
from decimal import Decimal

import pandas as pd
from io import BytesIO
import os
import tempfile
import json

from PIL import Image
import aiohttp

import pytz

from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram import types
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from models import Category, Order, Product, User, OrderItem
from main import admin_id, async_session_factory, BOT_TOKEN


from keyboard import get_main_reply_keyboard
# --------------------------------------------------------------------------- #
#                         Фильтр допуска только админов                       #
# --------------------------------------------------------------------------- #


class AdminFilter(Filter):
    async def __call__(self, entity: Message | CallbackQuery) -> bool:  # noqa: D401
        message = entity if isinstance(entity, Message) else entity.message
        return message.chat and message.chat.id in admin_id


# --------------------------------------------------------------------------- #
#                                Состояния FSM                                #
# --------------------------------------------------------------------------- #


class AddProductSG(StatesGroup):
    waiting_for_category = State()
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_price = State()
    waiting_for_photo = State()
    confirmation = State()


class AddCategorySG(StatesGroup):
    waiting_for_title = State()


class EditProductSG(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_price = State()
    waiting_for_photo = State()
    confirmation = State()


class EditCategorySG(StatesGroup):
    waiting_for_title = State()


# --------------------------------------------------------------------------- #
#                                  Роутер                                     #
# --------------------------------------------------------------------------- #

router = Router()
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

# ================================
# /add_category
# ================================

@router.message(Command("add_category"))
@router.message(F.text == "➕ Добавить категорию")
async def add_category_start(message: Message, state: FSMContext) -> None:
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_category"))
    await message.answer("Введите название новой категории:", reply_markup=builder.as_markup())
    await state.set_state(AddCategorySG.waiting_for_title)


@router.callback_query(F.data == "cancel_add_category")
async def add_category_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Отменено.")
    await state.clear()
    await call.message.delete()
    await call.message.answer("Добавление категории отменено.")


@router.message(AddCategorySG.waiting_for_title)
async def add_category_save(message: Message, state: FSMContext) -> None:
    title = message.text.strip()

    if not title:
        await message.answer("Название не может быть пустым, попробуйте ещё раз.")
        return

    async with async_session_factory() as session:
        exists = await session.scalar(
            select(func.count()).select_from(Category).where(Category.title == title)
        )
        if exists:
            await message.answer("Такая категория уже существует.")
            await state.clear()
            return
        session.add(Category(title=title))
        await session.commit()
    await message.answer(f"✅ Категория «{title}» добавлена.")
    await state.clear()

# ================================
# /add_product
# ================================


@router.message(Command("add_product"))
@router.message(F.text == "➕ Добавить товар")
async def add_product_start(message: Message, state: FSMContext) -> None:
    async with async_session_factory() as session:
        categories = (await session.scalars(select(Category))).all()
        if not categories:
            await message.answer(
                "Сначала создайте хотя бы одну категорию напрямую в базе или командой /add_category."
            )
            return
        markup = InlineKeyboardBuilder()
        for cat in categories:
            markup.add(InlineKeyboardButton(text=cat.title, callback_data=f"cat_{cat.id}"))
        markup.adjust(2)

        markup.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_product"))
    await message.answer(
        "🗂 Выберите категорию для нового товара:",
        reply_markup=markup.as_markup(),
    )
    await state.set_state(AddProductSG.waiting_for_category)


@router.callback_query(F.data == "cancel_add_product")
async def cancel_add_product(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Отменено.")
    await state.clear()
    await call.message.delete()
    await call.message.answer("Добавление продукта отменено.")


@router.callback_query(AddProductSG.waiting_for_category, F.data.startswith("cat_"))
async def add_product_set_category(
    call: CallbackQuery, state: FSMContext
) -> None:
    await call.answer()
    category_id = int(call.data.split("_")[1])
    async with async_session_factory() as session:
        category = await session.get(Category, category_id)
        category_name = category.title if category else "Неизвестная категория"
    await state.update_data(category_id=category_id)
    await call.message.answer(f"Вы выбрали категорию: <b>{category_name}</b>.\nВведите название товара:", parse_mode="HTML")
    await state.set_state(AddProductSG.waiting_for_title)


@router.message(AddProductSG.waiting_for_title)
async def add_product_set_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await message.answer("Введите описание (или '-' чтобы пропустить):")
    await state.set_state(AddProductSG.waiting_for_description)


@router.message(AddProductSG.waiting_for_description)
async def add_product_set_description(message: Message, state: FSMContext) -> None:
    desc = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(description=desc)
    await message.answer("Введите цену (число, например 249.90):")
    await state.set_state(AddProductSG.waiting_for_price)


@router.message(AddProductSG.waiting_for_price)
async def add_product_set_price(message: Message, state: FSMContext) -> None:
    try:
        price = Decimal(message.text.replace(",", "."))
        if price <= 0:
            raise ValueError
    except Exception:
        await message.answer("❌ Неверный формат. Введите число, например 199.00")
        return

    await state.update_data(price=price)
    await message.answer("Пришлите фотографию товара одним изображением:")
    await state.set_state(AddProductSG.waiting_for_photo)

@router.message(AddProductSG.waiting_for_photo, F.photo)
async def add_product_set_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id  
    await state.update_data(photo_file_id=file_id)

    
    data = await state.get_data()
    text_preview = (
        "<b>Предпросмотр товара:</b>\n\n"
        f"Категория ID: {data['category_id']}\n"
        f"Название: <i>{data['title']}</i>\n"
        f"Описание: {data['description'] or '—'}\n"
        f"Цена: <b>{data['price']} ₽</b>\n"
        f"Фото прикреплено: {'✅' if data.get('photo_file_id') else '❌'}\n\n"
        "Сохранить?"
    )
    
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="save_product"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_product"),
    )
    
    await message.answer(text_preview, reply_markup=builder.as_markup())
    await state.set_state(AddProductSG.confirmation)


@router.callback_query(AddProductSG.confirmation, F.data == "cancel_product")
async def add_product_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Отменено.")
    await state.clear()
    await call.message.delete()
    await call.message.answer("Добавление продукта отменено.")


@router.callback_query(AddProductSG.confirmation, F.data == "save_product")
async def add_product_save(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    async with async_session_factory() as session:
        product = Product(
            category_id=data["category_id"],
            title=data["title"],
            description=data["description"],
            photo_file_id=data["photo_file_id"],
            price=data["price"],
        )
        session.add(product)
        await session.commit()
    await call.message.edit_text("✅ Товар сохранён.")
    await state.clear()


# ================================
# /products
# ================================

@router.message(Command("products"))
@router.message(F.text == "🛍️ Продукты")
async def list_products(message: Message) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Product)
            .where(Product.is_active.is_(True))
            .order_by(Product.category_id, Product.id) 
            .options(selectinload(Product.category)) 
        )
        products = result.scalars().all()

    if not products:
        await message.answer("Список товаров пуст.")
        return
    category_dict = {}
    for product in products:
        category_title = product.category.title
        if category_title not in category_dict:
            category_dict[category_title] = []
        category_dict[category_title].append(product)
    lines = []
    for category_title, items in category_dict.items():
        items_lines = [f"{item.title} — {item.price} ₽" for item in items]
        lines.append(f"<b>{category_title}:</b>\n" + "\n".join(items_lines))

    await message.answer("\n\n".join(lines))


# ================================
# edit_product:<id>
# ================================

@router.callback_query(F.data.startswith("edit_product:"))
async def edit_product_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    pid = int(call.data.split(":", 1)[1])

    async with async_session_factory() as session:
        product = await session.get(Product, pid)
        if not product:
            await call.message.answer("Товар не найден.")
            return
    await state.update_data(
        pid=pid,
        title=product.title,
        description=product.description,
        price=product.price,
        photo_file_id=product.photo_file_id,
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_edit_product"
                )
            ]
        ]
    )
    await call.message.answer(
        f"Текущее название: <b>{product.title}</b>\n"
        "Введите новое название или «-» чтобы оставить без изменений:",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )
    await state.set_state(EditProductSG.waiting_for_title)

@router.callback_query(F.data == "cancel_edit_product")
async def cancel_edit_product_handler(call: CallbackQuery, state: FSMContext):
    await call.answer("Отменено.")
    await state.clear()
    await call.message.edit_reply_markup()
    await call.message.delete()

@router.message(EditProductSG.waiting_for_title)
async def edit_product_title(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text != "-":
        await state.update_data(title=text)

    await message.answer(
        "Введите новое описание или «-» чтобы оставить без изменений:"
    )
    await state.set_state(EditProductSG.waiting_for_description)


@router.message(EditProductSG.waiting_for_description)
async def edit_product_desc(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text != "-":
        await state.update_data(description=None if text == "-" else text)

    await message.answer("Введите новую цену (число) или «-» чтобы пропустить:")
    await state.set_state(EditProductSG.waiting_for_price)


@router.message(EditProductSG.waiting_for_price)
async def edit_product_price(message: Message, state: FSMContext) -> None:
    txt = message.text.strip()
    if txt != "-":
        try:
            price = Decimal(txt.replace(",", "."))
            if price <= 0:
                raise ValueError
        except Exception:
            await message.answer("❌ Неверный формат. Введите число или «-».")
            return
        await state.update_data(price=price)

    await message.answer(
        "Пришлите новую фотографию или «-» текстом чтобы оставить прежнюю:"
    )
    await state.set_state(EditProductSG.waiting_for_photo)


@router.message(EditProductSG.waiting_for_photo, F.photo | F.text)
async def edit_product_photo(message: Message, state: FSMContext) -> None:
    if message.photo:
        await state.update_data(photo_file_id=message.photo[-1].file_id)

    data = await state.get_data()
    text_preview = (
        "<b>Итоговые данные товара:</b>\n\n"
        f"📝 Название: <i>{data['title']}</i>\n"
        f"📖 Описание: {data['description'] or '—'}\n"
        f"💵 Цена: <b>{data['price']} ₽</b>\n\n"
        "Сохранить изменения?"
    )
    kb = InlineKeyboardBuilder()
    kb.add(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="save_edit"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit"),
    )
    await message.answer(text_preview, reply_markup=kb.as_markup(), parse_mode="HTML")
    await state.set_state(EditProductSG.confirmation)


@router.callback_query(EditProductSG.confirmation, F.data == "cancel_edit")
async def edit_product_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Отменено.")
    await state.clear()
    await call.message.edit_reply_markup()
    await call.message.delete()


@router.callback_query(EditProductSG.confirmation, F.data == "save_edit")
async def edit_product_save(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = await state.get_data()
    pid = data["pid"]

    async with async_session_factory() as session:
        product = await session.get(Product, pid)
        if not product:
            await call.message.answer("Товар не найден.")
            await state.clear()
            return
        
        product.title = data["title"]
        product.description = data["description"]
        product.price = data["price"]
        product.photo_file_id = data["photo_file_id"]
        await session.commit()
    await call.message.edit_text("✅ Изменения сохранены.")
    await state.clear()


# ================================
# edit_cat:<id>
# ================================

@router.callback_query(F.data.startswith("edit_cat:"))
async def edit_category_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    cid = int(call.data.split(":", 1)[1])
    
    async with async_session_factory() as session:
        cat = await session.get(Category, cid)
        if not cat:
            await call.message.answer("Категория не найдена.")
            return
    await state.update_data(cid=cid)
    
    await call.message.answer(
        f"Текущее название: <b>{cat.title}</b>\n"
        "Введите новое название или «-» чтобы оставить без изменений:",
        parse_mode="HTML",
    )
    await state.set_state(EditCategorySG.waiting_for_title)


@router.message(EditCategorySG.waiting_for_title)
async def edit_category_title(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text == "-":
        await message.answer("Изменений не внесено.")
        await state.clear()
        return

    data = await state.get_data()
    cid = data["cid"]

    async with async_session_factory() as session:
        cat = await session.get(Category, cid)
        if not cat:
            await message.answer("Категория не найдена.")
            await state.clear()
            return
        cat.title = text
        await session.commit()

    await message.answer("✅ Название категории обновлено.")
    await state.clear()

# ================================
# remove_product:<id>  |  remove_cat:<id>
# ================================


@router.callback_query(F.data.startswith("remove_product:"))
async def delete_product(call: CallbackQuery) -> None:
    parts = call.data.split(":", maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await call.answer("Некорректный идентификатор товара.", show_alert=True)
        return

    pid = int(parts[1])

    async with async_session_factory() as session:
        product = await session.get(Product, pid)
        if not product:
            await call.message.answer("Товар не найден.")
            return

        product_data = {
            "id": product.id,
            "title": product.title,
            "description": product.description,
            "price": product.price,
            "photo_file_id": product.photo_file_id,
        }

        product.is_active = False
        await session.commit()

    text_preview = (
        "<b>Товар отключён:</b>\n\n"
        f"<b>ID:</b> {product_data['id']}\n"
        f"<b>Название:</b> <i>{product_data['title']}</i>\n"
        f"<b>Описание:</b> {product_data['description'] or '—'}\n"
        f"<b>Цена:</b> <b>{product_data['price']} ₽</b>\n"
        f"<b>Фото:</b> {'✅' if product_data['photo_file_id'] else '❌'}\n\n"
        "Вы можете включить товар обратно: ➕ Активировать товар"
    )

    if product_data['photo_file_id']:
        await call.message.answer_photo(
            photo=product_data['photo_file_id'],
            caption=text_preview,
            parse_mode="HTML",
            
        )
    else:
        await call.message.answer(
            text_preview,
            parse_mode="HTML",
            
        )

    await call.message.delete_reply_markup()


@router.message(F.text == "➕ Активировать товар")
async def show_disabled_products(message: Message, state: FSMContext) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(Product).where(Product.is_active == False))
        disabled_products = result.scalars().all()

    if not disabled_products:
        await message.answer("Все товары активны. Нет отключённых товаров.")
        return

    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(
            text=product.title,
            callback_data=f"showdetails:{product.id}"
        )
        for product in disabled_products
    ]
    for i in range(0, len(buttons), 2):
        builder.row(*buttons[i:i+2])
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="back_to_main_menu"
        )
    )

    sent_msg = await message.answer(
        "📦 Выберите товар, чтобы посмотреть детали и включить его:",
        reply_markup=builder.as_markup(resize_keyboard=True),
    )
    await state.update_data(disabled_products_list_message_id=sent_msg.message_id)


@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    main_keyboard = get_main_reply_keyboard(user_id)
    await call.message.delete()
    await call.message.answer("Вы вернулись в главное меню.", reply_markup=main_keyboard)
    await call.answer()  


@router.callback_query(F.data.startswith("showdetails:"))
async def show_product_details(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":", maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await call.answer("Некорректный идентификатор товара.", show_alert=True)
        return

    pid = int(parts[1])

    async with async_session_factory() as session:
        product = await session.get(Product, pid)
        if not product:
            await call.message.answer("Товар не найден.")
            return
        categories_result = await session.execute(select(Category))
        categories = categories_result.scalars().all()

    text_preview = (
        "<b>📋 Информация о товаре</b>\n\n"
        f"<b>ID:</b> {product.id}\n"
        f"<b>Название:</b> <i>{product.title}</i>\n"
        f"<b>Описание:</b> {product.description or '—'}\n"
        f"<b>Цена:</b> <b>{product.price} ₽</b>\n"
        f"<b>Статус:</b> {'Активен' if product.is_active else 'Отключён'}"
    )

    builder = InlineKeyboardBuilder()
    if categories:
        buttons = [
            InlineKeyboardButton(
                text=category.title, 
                callback_data=f"select_category:{product.id}:{category.id}"
            )
            for category in categories
        ]
        for i in range(0, len(buttons), 2):
            builder.row(*buttons[i:i+2])
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад к списку",
            callback_data="back_to_disabled_list",
        ),
    )

    if product.photo_file_id:
        await call.message.answer_photo(
            photo=product.photo_file_id,
            caption=text_preview,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    else:
        await call.message.answer(
            text_preview,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    await call.message.delete_reply_markup()
    await call.answer()



@router.callback_query(F.data.startswith("select_category:"))
async def select_category(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await call.answer("Некорректные данные.", show_alert=True)
        return

    pid = int(parts[1])
    cid = int(parts[2])

    async with async_session_factory() as session:
        product = await session.get(Product, pid)
        if not product:
            await call.message.answer("Товар не найден.")
            return

        product.is_active = True
        product.category_id = cid 
        await session.commit()

    await call.message.answer(f"✅ Товар <b>{product.title}</b> (ID {pid}) успешно активирован и добавлен в категорию ID {cid}.",
        parse_mode="HTML")
    await call.message.delete_reply_markup()


@router.callback_query(F.data == "back_to_disabled_list")
async def back_to_disabled_list(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    list_msg_id = data.get("disabled_products_list_message_id")
    try:
        await call.message.delete()
    except Exception:
        pass  
    if list_msg_id:
        try:
            await call.message.chat.delete_message(list_msg_id)
        except Exception:
            pass 
    await show_disabled_products(call.message, state) 


@router.callback_query(F.data.startswith("remove_cat:"))
async def delete_category(call: CallbackQuery) -> None:
    parts = call.data.split(":", maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await call.answer("Некорректный идентификатор категории.", show_alert=True)
        return
    
    cid = int(parts[1])

    async with async_session_factory() as session:
        cat = await session.get(Category, cid)
        if not cat:
            await call.message.answer("Категория не найдена.")
            return
        products_result = await session.execute(
            select(Product).where(Product.category_id == cid)
        )
        products = products_result.scalars().all()
        for product in products:
            product.is_active = False
            product.category_id = None 
        await session.delete(cat)
        await session.commit()

    await call.message.answer(f"✅ Категория ID {cid} удалена, товары деактивированы и отвязаны.")
    await call.message.delete_reply_markup()


@router.message(Command("orders"))
@router.message(F.text == "🛒 Заказы")
async def list_orders(message: Message) -> None:
    async with async_session_factory() as session:
        orders = (
            await session.execute(
                select(Order)
                .order_by(Order.id.desc())
                .limit(20)
            )
        ).scalars().all()

    if not orders:
        await message.answer("Заказов пока нет.")
        return

    kb = InlineKeyboardBuilder()
    text_lines = []
    for order in orders:
        text_lines.append(
            f"#{order.id} — {order.total_price} ₽ — {order.payment_method}"
        )
        kb.add(
            InlineKeyboardButton(
                text=f"#{order.id} — {order.status}",
                callback_data=f"order:{order.id}",
            )
        )

    kb.adjust(1)
    await message.answer(
        "<b>Последние заказы:</b>\n" + "\n".join(text_lines),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )


async def send_orders_list(chat_id: int, bot) -> None:
    async with async_session_factory() as session:
        orders = (
            await session.execute(
                select(Order)
                .order_by(Order.id.desc())
                .limit(20)
            )
        ).scalars().all()

    if not orders:
        await bot.send_message(chat_id, "Заказов пока нет.")
        return

    kb = InlineKeyboardBuilder()
    text_lines = []
    for order in orders:
        text_lines.append(
            f"#{order.id} — {order.total_price} ₽ — {order.payment_method}"
        )
        kb.add(
            InlineKeyboardButton(
                text=f"#{order.id} — {order.status}",
                callback_data=f"order:{order.id}",
            )
        )

    kb.adjust(1)
    await bot.send_message(
        chat_id,
        "<b>Последние заказы:</b>\n" + "\n".join(text_lines),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "back_to_orders")
async def back_to_orders(call: CallbackQuery) -> None:
    await call.answer()
    
    if call.message:
        await call.message.delete()
    await send_orders_list(call.message.chat.id, call.bot)



@router.callback_query(F.data.startswith("order:"))
async def order_details(call: CallbackQuery) -> None:
    await call.answer()
    order_id = int(call.data.split(":", 1)[1])

    async with async_session_factory() as session:
        order: Order | None = await session.get(Order, order_id)
        if not order:
            await call.message.answer("Заказ не найден.")
            return

        user: User = await session.get(User, order.user_id)

        items = (
            await session.execute(
                select(OrderItem.qty, Product.title, OrderItem.item_price)
                .join(Product, Product.id == OrderItem.product_id)
                .where(OrderItem.order_id == order.id)
            )
        ).all()

    item_lines = [
        f"{title} × {qty} = {price * qty} ₽" for qty, title, price in items
    ]
    local_tz = pytz.timezone('Europe/Moscow') 
    created_at_local = order.created_at.astimezone(local_tz) 
        
    text = (
        f"🧾 <b>Заказ #{order.id}</b>\n"
        f"💳 Способ оплаты: {order.payment_method}\n"
        f"⚙️ Статус: {order.status}\n"
        f"👤 Пользователь: {user.full_name or user.tg_id}\n"
        f"📞 Телефон: {user.phone or 'не указан'}\n"
        f"🏠 Адрес: {order.address or 'не указан'}\n"
        f"📝 Комментарий: {order.comment or 'не указан'}\n"
        f"📅 Дата: {created_at_local.strftime('%d.%m.%Y %H:%M')}\n"
        f"💰 Сумма: {order.total_price} ₽\n\n"
        "📦 <b>Состав:</b>\n" + "\n".join(item_lines)
    )
    kb = InlineKeyboardBuilder()

    if order.status == "выполнен" or order.status == "отменен":
        kb.add(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_to_orders",
            )
        )
        await call.message.edit_text(
            text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    else:
        if order.status == "в процессе":
            kb.row(
                InlineKeyboardButton(
                    text="✅ Выполнен",
                    callback_data=f"order_done:{order.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"order_cancel:{order.id}",
                )
            )
        else:
            kb.add(
                InlineKeyboardButton(
                    text="🕒 В процессе",
                    callback_data=f"order_process:{order.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"order_cancel:{order.id}",
                )
            )
        kb.add(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="back_to_orders",
            )
        )

        await call.message.edit_text(
            text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )

@router.callback_query(F.data.startswith("order_process:"))
async def process_order(call: CallbackQuery) -> None:
    await call.answer()
    order_id = int(call.data.split(":", 1)[1])

    async with async_session_factory() as session:
        order: Order | None = await session.get(Order, order_id)
        if not order:
            await call.message.answer("Заказ не найден.")
            return

        order.status = "в процессе"
        await session.commit()
        user: User = await session.get(User, order.user_id)
        await call.bot.send_message(
            chat_id=user.tg_id,
            text=f"Ваш заказ #<b>{order.id}</b> уже готовится 🍽️\n",
            parse_mode="HTML"
        )
    await order_details(call)


@router.callback_query(F.data.startswith("order_done:"))
async def complete_order(call: CallbackQuery) -> None:
    await call.answer()
    order_id = int(call.data.split(":", 1)[1])

    async with async_session_factory() as session:
        order: Order | None = await session.get(Order, order_id)
        if not order:
            await call.message.answer("Заказ не найден.")
            return
        order.status = "выполнен"
        await session.commit()
        user: User = await session.get(User, order.user_id)
        await call.bot.send_message(
            chat_id=user.tg_id,
            text=f"Ваш заказ #{order.id} был успешно доставлен! 🎉\n"
                 f"Спасибо за покупку!",
            parse_mode="HTML"
        )
    await call.message.answer(
            f"Вы отметили заказ #{order.id} как выполненный. ✅"
        )
    await order_details(call)


@router.callback_query(F.data.startswith("order_cancel:"))
async def cancel_order(call: CallbackQuery) -> None:
    await call.answer()
    order_id = int(call.data.split(":", 1)[1])

    async with async_session_factory() as session:
        order: Order | None = await session.get(Order, order_id)
        if not order:
            await call.message.answer("Заказ не найден.")
            return
        order.status = "отменен"
        await session.commit()
        user: User = await session.get(User, order.user_id)
        await call.bot.send_message(
            chat_id=user.tg_id,
            text=f"Ваш заказ #<b>{order.id}</b> был отменен ❌\n Если у вас есть вопросы, обратитесь в нашу поддержку!",
            parse_mode="HTML"
        )
    await call.message.answer(
            f"Вы отметили заказ #{order.id} как отмененный ❌"
        )
    await order_details(call)

@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def bot_stats(message: Message) -> None:
    today = datetime.now().date()

    async with async_session_factory() as session:     
        users_cnt = await session.scalar(select(func.count()).select_from(User).where(User.id.isnot(None)))
        orders_cnt = await session.scalar(select(func.count()).select_from(Order))
        revenue = await session.scalar(
            select(func.coalesce(func.sum(Order.total_price), 0))
        )
        new_users_cnt = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= today)
        )
        in_progress_cnt = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "в процессе")
        )
        completed_cnt = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "выполнен")
        )
        canceled_cnt = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "отменен")
        )
        average_order_value = revenue / orders_cnt if orders_cnt > 0 else 0

    text = (
        "<b>Статистика бота</b>\n\n"
        f"👤 Всего пользователей: {users_cnt}\n"
        f"📦 Всего заказов: {orders_cnt}\n"
        f"💰 Сумма заказов: {revenue} ₽\n"
        f"🆕 Новых пользователей сегодня: {new_users_cnt}\n\n"
        f"📊 Заказы по статусам:\n"
        f"  - В процессе: {in_progress_cnt}\n"
        f"  - Выполнен: {completed_cnt}\n"
        f"  - Отменен: {canceled_cnt}\n\n"
        f"📈 Средний чек: {average_order_value:.2f} ₽\n"
    )
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="📥 Выгрузить в Excel и JSON", callback_data="export_stats_data"))

    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


def serialize_decimal(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")

@router.callback_query(F.data == "export_stats_data")
async def export_stats_data(call: CallbackQuery) -> None:
    await call.answer() 
    
    today = datetime.now().date()

    async with async_session_factory() as session:     
        users_cnt = await session.scalar(select(func.count()).select_from(User).where(User.id.isnot(None)))
        orders_cnt = await session.scalar(select(func.count()).select_from(Order))
        revenue = await session.scalar(
            select(func.coalesce(func.sum(Order.total_price), 0))
        )
        new_users_cnt = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= today)
        )
        
        in_progress_cnt = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "в процессе")
        )
        completed_cnt = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "выполнен")
        )
        canceled_cnt = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "отменен")
        )
        
        average_order_value = revenue / orders_cnt if orders_cnt > 0 else 0

    data = {
        "Показатель": [
            "Всего пользователей",
            "Всего заказов",
            "Сумма заказов",
            "Новых пользователей сегодня",
            "Заказы в процессе",
            "Выполненные заказы",
            "Отмененные заказы",
            "Средний чек"
        ],
        "Значение": [
            users_cnt,
            orders_cnt,
            revenue,
            new_users_cnt,
            in_progress_cnt,
            completed_cnt,
            canceled_cnt,
            f"{average_order_value:.2f} ₽"
        ]
    }

    df = pd.DataFrame(data)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as temp_file:
        with pd.ExcelWriter(temp_file.name, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Статистика')
        
        input_file_excel = FSInputFile(temp_file.name, filename="bot_statistics.xlsx")

    json_data = {
        "statistics": {
            "users_count": users_cnt,
            "orders_count": orders_cnt,
            "revenue": revenue,
            "new_users_today": new_users_cnt,
            "in_progress_orders": in_progress_cnt,
            "completed_orders": completed_cnt,
            "canceled_orders": canceled_cnt,
            "average_order_value": average_order_value
        }
    }

    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix=".json", encoding='utf-8') as temp_json_file:
        json.dump(json_data, temp_json_file, default=serialize_decimal, ensure_ascii=False, indent=4)
        temp_json_file.flush()  

        input_file_json = FSInputFile(temp_json_file.name, filename="bot_statistics.json")

    await call.message.answer_document(
        document=input_file_excel,
        caption="📊 Сводная статистика бота (Excel)"
    )
    await call.message.answer_document(
        document=input_file_json,
        caption="📊 Сводная статистика бота (JSON)"
    )
    os.remove(temp_file.name)
    os.remove(temp_json_file.name)
