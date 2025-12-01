# bot.py — с коллекциями, поиском, уведомлениями и исправленными ошибками (и сортировкой!)

import os
import json
import asyncio
import re
import math
import random # <--- НОВЫЙ ИМПОРТ: для рандомайзера
from pathlib import Path
from typing import Optional, List, Any
from pytz import timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.filters.callback_data import CallbackData
from asyncio import to_thread # Импорт для асинхронного запуска синхронных функций

# --- Aiogram и другие библиотеки ---
from aiogram import Bot, Dispatcher, types, F, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# --- Доп. импорты для устойчивости ---
AIOFILES_AVAILABLE = False
try:
    import aiofiles
    AIOFILES_AVAILABLE = True
except Exception:
    AIOFILES_AVAILABLE = False

# --- Telegraph ---
TELEGRAPH_AVAILABLE = True
Telegraph = None
tg_exceptions = None
try:
    # Используем СИНХРОННЫЙ клиент, который у вас установлен
    from telegraph import Telegraph, exceptions as tg_exceptions 
except Exception:
    TELEGRAPH_AVAILABLE = False

# --- Константы ---
COMICS_AUTHOR_NAME = "EasyReaderBot"
DATA_JSON = "data.json"
LINKS_JSON = "links.json"
USERS_FILE = "users.json"
TZ_INFO = timezone("Asia/Almaty")
# Максимальное количество кнопок в ряду для глав
CHAPTERS_PER_ROW = 5 

# --- Загрузка .env ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAPH_ENABLED = os.getenv("TELEGRAPH_ENABLED", "1") != "0" 

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не найден в .env. Добавьте его для запуска.")

# --- Инициализация bot и dp ГЛОБАЛЬНО (до хэндлеров) ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- Стейты для FSM ---
class SearchState(StatesGroup):
    waiting_for_query = State()


# --- Callbacks ---
class MenuCallback(CallbackData, prefix="menu"):
    action: str 

class CollectionCallback(CallbackData, prefix="coll"):
    collection_key: str 
    action: str 

class ComicCallback(CallbackData, prefix="comic"):
    collection_key: str
    comic_key: str
    action: str 
    page: int = 1


# --- Вспомогательные функции ---


def natural_sort_key(s: str):
    """
    Возвращает ключ для естественной сортировки, извлекая числа.
    Например, 'chapter_10' будет идти после 'chapter_2', а не до.
    """
    if not isinstance(s, str):
        return s
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]


async def load_json_async(path: str) -> Optional[Any]:
    """Асинхронно загружает JSON файл. Работает и без aiofiles."""
    if AIOFILES_AVAILABLE:
        try:
            async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
                content = await f.read()
                return json.loads(content)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as e:
            print(f"❌ Ошибка при чтении {path}. Файл поврежден: {e}")
            return None
        except Exception as e:
            print(f"❌ Ошибка при чтении {path}: {e}")
            return None
    else:
        # fallback — читаем в отдельном потоке
        def _read_file():
            if not Path(path).exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

        try:
            return await asyncio.to_thread(_read_file)
        except json.JSONDecodeError as e:
            print(f"❌ Ошибка при чтении {path}. Файл поврежден: {e}")
            return None
        except Exception as e:
            print(f"❌ Ошибка при чтении {path}: {e}")
            return None


async def save_json_async(path: str, data: dict | list) -> bool:
    """Асинхронно сохраняет JSON файл. Работает и без aiofiles."""
    if AIOFILES_AVAILABLE:
        try:
            async with aiofiles.open(path, mode="w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception as e:
            print(f"❌ Ошибка при записи в {path}: {e}")
            return False
    else:
        def _write_file():
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        try:
            await asyncio.to_thread(_write_file)
            return True
        except Exception as e:
            print(f"❌ Ошибка при записи в {path}: {e}")
            return False


# Загружает данные только при необходимости
async def get_all_data() -> dict:
    return await load_json_async(DATA_JSON) or {}


# Получает данные о комиксах в коллекции
async def get_comics_data(collection_key: str) -> dict:
    data = await get_all_data()
    return data.get(collection_key, {}).get("comics", {})


# Получает данные о главах в комиксе
async def get_chapters_data(collection_key: str, comic_key: str) -> dict:
    comics = await get_comics_data(collection_key)
    return comics.get(comic_key, {}).get("chapters", {})


# Получает список ссылок на изображения для главы
async def get_links_list(collection_key: str, comic_key: str, chapter_key: str) -> list:
    links_data = await load_json_async(LINKS_JSON) or {}
    return links_data.get(collection_key, {}).get(comic_key, {}).get(chapter_key, [])


# <--- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ РАНДОМАЙЗЕРА --->
async def get_all_comic_identifiers() -> List[tuple[str, str, str]]:
    """Собирает список всех комиксов в формате (collection_key, comic_key, comic_title)
    для обеспечения равномерного случайного выбора."""
    all_data = await get_all_data()
    all_comics = []

    for collection_key, collection_data in all_data.items():
        comics_in_collection = collection_data.get("comics", {})
        for comic_key, comic_data in comics_in_collection.items():
            title = comic_data.get("title", comic_key)
            all_comics.append((collection_key, comic_key, title))
            
    return all_comics
# <--- КОНЕЦ НОВОЙ ВСПОМОГАТЕЛЬНОЙ ФУНКЦИИ --->


# Создание HTML-контента для Telegra.ph
def create_html_content(links_list: List[str]) -> str:
    """Создает простой HTML-контент из списка ссылок на изображения для Telegra.ph."""
    content = ""
    for url in links_list:
        # Добавление <br> для минимального разделения изображений
        content += f'<img src="{url}"><br>' 
    return content


# Добавление/удаление пользователя из списка уведомлений
async def toggle_notification_user(user_id: int) -> bool:
    users = await load_json_async(USERS_FILE) or []
    if not isinstance(users, list):
        users = []
    if user_id in users:
        users.remove(user_id)
        await save_json_async(USERS_FILE, users)
        return False  # Удален
    else:
        users.append(user_id)
        await save_json_async(USERS_FILE, users)
        return True  # Добавлен


# --- Генерация клавиатур (Улучшенный UI) ---


async def get_main_menu_markup(user_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Кнопка поиска
    builder.row(
        types.InlineKeyboardButton(
            text="🔍 Поиск комикса", 
            callback_data=MenuCallback(action="search").pack()
        )
    )

    # <--- КНОПКА РАНДОМАЙЗЕРА --->
    builder.row(
        types.InlineKeyboardButton(
            text="🎲 Случайный комикс", 
            callback_data=MenuCallback(action="random").pack()
        )
    )
    # <--- КОНЕЦ КНОПКИ РАНДОМАЙЗЕРА --->

    # Кнопка коллекций
    builder.row(
        types.InlineKeyboardButton(
            text="📚 Каталог коллекций", 
            callback_data=MenuCallback(action="collections").pack()
        )
    )
    
    # Кнопка уведомлений
    is_subscribed = user_id in (await load_json_async(USERS_FILE) or [])
    notify_icon = "🔔" if is_subscribed else "🔕"
    notify_status = "Вкл" if is_subscribed else "Выкл"
    builder.row(
        types.InlineKeyboardButton(
            text=f"{notify_icon} Уведомления: {notify_status}", 
            callback_data=MenuCallback(action="toggle_notify").pack()
        )
    )

    builder.adjust(1) # Все кнопки в один столбец для чистоты

    return builder.as_markup()


async def get_collections_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    data = await get_all_data()

    if data:
        for key, value in data.items():
            title = value.get("title", key.capitalize())
            icon = value.get("icon", "📖")
            builder.button(
                text=f"{icon} {title}",
                callback_data=CollectionCallback(collection_key=key, action="open").pack(),
            )

        builder.adjust(2)  # Две кнопки в ряду для коллекций

    # Кнопка назад
    builder.row(
        types.InlineKeyboardButton(
            text="🏠 В главное меню", # Изменено на более дружелюбную иконку
            callback_data=MenuCallback(action="back").pack()
        )
    )

    return builder.as_markup()


async def get_comics_markup(collection_key: str, page: int = 1) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    comics_data = await get_comics_data(collection_key)

    if not comics_data:
        builder.row(
            types.InlineKeyboardButton(
                text="⬅️ Назад к коллекциям",
                callback_data=CollectionCallback(collection_key=collection_key, action="back").pack(),
            )
        )
        return builder.as_markup()

    # Сортируем ключи комиксов по алфавиту для стабильности
    comic_keys = sorted(comics_data.keys(), key=lambda k: comics_data[k].get("title", k)) # Сортируем по названию, а не ключу

    # Логика пагинации (10 комиксов на страницу)
    ITEMS_PER_PAGE = 10
    start_index = (page - 1) * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    comics_on_page = comic_keys[start_index:end_index]
    total_pages = math.ceil(len(comic_keys) / ITEMS_PER_PAGE)

    # Добавляем кнопки комиксов
    for key in comics_on_page:
        title = comics_data[key].get("title", key)
        # Добавляем эмодзи для лучшего вида
        builder.row(
            types.InlineKeyboardButton(
                text=f"📜 {title}",
                callback_data=ComicCallback(collection_key=collection_key, comic_key=key, action="open", page=1).pack(),
            )
        )

    # Кнопки пагинации
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text="«", # Более компактный символ
                    callback_data=ComicCallback(collection_key=collection_key, comic_key="placeholder", action="page", page=page - 1).pack(),
                )
            )

        nav_buttons.append(types.InlineKeyboardButton(text=f"Страница {page}/{total_pages}", callback_data="ignore")) # Улучшен текст

        if page < total_pages:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text="»", # Более компактный символ
                    callback_data=ComicCallback(collection_key=collection_key, comic_key="placeholder", action="page", page=page + 1).pack(),
                )
            )

        builder.row(*nav_buttons)

    # Кнопка назад
    builder.row(
        types.InlineKeyboardButton(
            text="⬅️ Назад к коллекциям", 
            callback_data=CollectionCallback(collection_key=collection_key, action="back").pack()
        )
    )

    return builder.as_markup()


async def get_chapter_buttons_markup(collection_key: str, comic_key: str, page: int = 1) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # 1. Получаем заголовок комикса
    comics_data = await get_comics_data(collection_key)
    comic_title = comics_data.get(comic_key, {}).get("title", "Комикс")

    # 2. Извлекаем данные о главах
    chapters_data = await get_chapters_data(collection_key, comic_key)

    if not chapters_data:
        builder.row(
            types.InlineKeyboardButton(
                text="⬅️ Назад к комиксам",
                callback_data=ComicCallback(collection_key=collection_key, comic_key="placeholder", action="back").pack(), # Используем "placeholder"
            )
        )
        return builder.as_markup()

    # Используем natural_sort_key для правильной сортировки
    chapter_keys = sorted(chapters_data.keys(), key=natural_sort_key)

    # Логика пагинации
    ITEMS_PER_PAGE = 20  # 5 кнопок в ряду * 4 ряда = 20
    start_index = (page - 1) * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    chapters_on_page = chapter_keys[start_index:end_index]
    total_chapters = len(chapter_keys)
    total_pages = math.ceil(total_chapters / ITEMS_PER_PAGE)

    # Добавляем кнопки глав
    for i, key in enumerate(chapters_on_page):
        # Номер главы в списке начинается с 1. Используем этот номер для колбэка.
        chapter_number_for_callback = i + 1 + start_index 
        
        # Получаем номер/название из данных для отображения
        title = chapters_data[key]
        
        # Если название главы - это просто "Глава N", то отображаем только N
        display_text = title
        if "глава" in title.lower():
             # Пытаемся отобразить только число, если это возможно, для компактности
            match = re.search(r'\d+', title)
            if match:
                display_text = match.group(0)

        builder.button(
            text=display_text, # Компактный вид
            # Здесь chapter_number_for_callback - это абсолютный порядковый номер главы в отсортированном списке
            callback_data=ComicCallback(collection_key=collection_key, comic_key=comic_key, action="read", page=chapter_number_for_callback).pack(),
        )

    builder.adjust(CHAPTERS_PER_ROW)

    # Кнопки пагинации
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text="«", # Более компактный символ
                    callback_data=ComicCallback(collection_key=collection_key, comic_key=comic_key, action="page", page=page - 1).pack(),
                )
            )

        nav_buttons.append(types.InlineKeyboardButton(text=f"Страница {page}/{total_pages}", callback_data="ignore"))

        if page < total_pages:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text="»", # Более компактный символ
                    callback_data=ComicCallback(collection_key=collection_key, comic_key=comic_key, action="page", page=page + 1).pack(),
                )
            )

        builder.row(*nav_buttons)

    # Кнопка назад
    builder.row(
        types.InlineKeyboardButton(
            text=f"⬅️ К списку комиксов", # Изменен текст для ясности
            callback_data=ComicCallback(collection_key=collection_key, comic_key="placeholder", action="back", page=1).pack(), # comic_key="placeholder" для back_to_comics_handler
        )
    )

    return builder.as_markup()


# --- Хэндлеры команд и сообщений (Улучшенный UI) ---

@dp.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()

    text = (
        "👋 **Добро пожаловать в EasyReaderBot!**\n\n"
        "Я ваш проводник в мир комиксов. Используйте меню ниже, чтобы найти, прочитать или настроить уведомления о новинках."
    )
    markup = await get_main_menu_markup(message.from_user.id)
    await message.answer(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


@dp.callback_query(MenuCallback.filter(F.action == "back"))
async def back_to_main_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "🏠 **Главное меню**\n\n"
        "Выберите одну из опций, чтобы продолжить:"
    )
    markup = await get_main_menu_markup(callback.from_user.id)
    # edit_text может выбрасывать, если сообщение удалено — обернём в try
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await callback.message.answer(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()


# <--- НОВЫЙ ХЭНДЛЕР: РАНДОМАЙЗЕР --->
@dp.callback_query(MenuCallback.filter(F.action == "random"))
async def random_comic_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("🎲 Ищу случайный комикс...", show_alert=False)

    all_comics = await get_all_comic_identifiers()

    if not all_comics:
        await callback.message.edit_text(
            "❌ В боте пока нет комиксов для случайного выбора.",
            reply_markup=InlineKeyboardBuilder().row(
                types.InlineKeyboardButton(text="🏠 В главное меню", callback_data=MenuCallback(action="back").pack())
            ).as_markup(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Равномерный случайный выбор из всего списка
    chosen_comic = random.choice(all_comics)
    collection_key, comic_key, comic_title = chosen_comic
    
    # 1. Получаем данные о главах
    chapters_data = await get_chapters_data(collection_key, comic_key)
    chapters_count = len(chapters_data)
    chapters_info = f"({chapters_count} глав)" if chapters_count else "(Нет глав)"

    # 2. Генерируем клавиатуру глав
    markup = await get_chapter_buttons_markup(collection_key, comic_key)
    
    # 3. Составляем и отправляем сообщение
    text = f"🎉 **Вам выпал случайный комикс!**\n\n📖 **{comic_title}** {chapters_info}\n\nВыберите главу для чтения:"
    
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await callback.message.answer(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

# <--- КОНЕЦ НОВОГО ХЭНДЛЕРА: РАНДОМАЙЗЕР --->


@dp.callback_query(MenuCallback.filter(F.action == "collections"))
async def open_collections_handler(callback: types.CallbackQuery):
    markup = await get_collections_markup()
    await callback.message.edit_text("📚 **Каталог коллекций**\n\nНачните просмотр, выбрав одно из издательств:", reply_markup=markup, parse_mode=ParseMode.MARKDOWN) # Улучшен текст
    await callback.answer()


@dp.callback_query(MenuCallback.filter(F.action == "toggle_notify"))
async def toggle_notify_handler(callback: types.CallbackQuery):
    is_added = await toggle_notification_user(callback.from_user.id)

    if is_added:
        alert_text = "✅ Ежедневные уведомления включены. Вы будете получать новости в 06:00."
    else:
        alert_text = "❌ Ежедневные уведомления выключены."

    # Обновляем клавиатуру, чтобы изменить текст кнопки
    markup = await get_main_menu_markup(callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=markup)
    except Exception:
        # Если редактирование не получилось — просто отправим новое сообщение кратко
        await callback.message.answer("Настройки уведомлений обновлены.", reply_markup=markup)

    await callback.answer(alert_text, show_alert=True)


@dp.callback_query(CollectionCallback.filter(F.action == "open"))
async def open_comics_handler(callback: types.CallbackQuery, callback_data: CollectionCallback):
    collection_key = callback_data.collection_key
    data = await get_all_data()
    collection_title = data.get(collection_key, {}).get("title", "Коллекция")
    icon = data.get(collection_key, {}).get("icon", "📖")

    markup = await get_comics_markup(collection_key)
    text = f"{icon} **{collection_title}**\n\nВыберите комикс для просмотра глав:" # Улучшен текст

    await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()


@dp.callback_query(CollectionCallback.filter(F.action == "back"))
async def back_to_collections_handler(callback: types.CallbackQuery):
    markup = await get_collections_markup()
    await callback.message.edit_text("📚 **Каталог коллекций**\n\nНачните просмотр, выбрав одно из издательств:", reply_markup=markup, parse_mode=ParseMode.MARKDOWN) # Улучшен текст
    await callback.answer()


@dp.callback_query(ComicCallback.filter(F.action == "back"))
async def back_to_comics_handler(callback: types.CallbackQuery, callback_data: ComicCallback):
    # Пагинация для комиксов всегда начинается с 1
    markup = await get_comics_markup(callback_data.collection_key, page=1)

    data = await get_all_data()
    collection_title = data.get(callback_data.collection_key, {}).get("title", "Коллекция")
    icon = data.get(callback_data.collection_key, {}).get("icon", "📖")

    text = f"{icon} **{collection_title}**\n\nВыберите комикс для просмотра глав:" # Улучшен текст

    await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()


@dp.callback_query(ComicCallback.filter(F.action == "page"))
async def paginate_comics_handler(callback: types.CallbackQuery, callback_data: ComicCallback):
    # Этот хэндлер используется как для комиксов, так и для глав.
    if callback_data.comic_key == "placeholder":
        # Пагинация для списка комиксов
        markup = await get_comics_markup(callback_data.collection_key, page=callback_data.page)
        try:
            await callback.message.edit_reply_markup(reply_markup=markup)
        except Exception:
            # Fallback на случай, если сообщение не меняется
            await callback.answer("Перехожу на страницу...", show_alert=False) 
    else:
        # Пагинация для списка глав
        markup = await get_chapter_buttons_markup(callback_data.collection_key, callback_data.comic_key, page=callback_data.page)
        try:
            await callback.message.edit_reply_markup(reply_markup=markup)
        except Exception:
            # Fallback на случай, если сообщение не меняется
            await callback.answer("Перехожу на страницу...", show_alert=False)

    await callback.answer()


@dp.callback_query(ComicCallback.filter(F.action == "open"))
async def open_chapters_handler(callback: types.CallbackQuery, callback_data: ComicCallback):
    collection_key = callback_data.collection_key
    comic_key = callback_data.comic_key

    comics_data = await get_comics_data(collection_key)
    comic_title = comics_data.get(comic_key, {}).get("title", "Комикс")
    chapters_data = await get_chapters_data(collection_key, comic_key)
    chapters_count = len(chapters_data)

    markup = await get_chapter_buttons_markup(collection_key, comic_key)
    
    chapters_info = f"({chapters_count} глав)" if chapters_count else "(Нет глав)"

    text = f"📖 **{comic_title}** {chapters_info}\n\nВыберите главу для чтения:" # Улучшен текст с количеством глав

    await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()


@dp.callback_query(ComicCallback.filter(F.action == "read"))
async def read_chapter_handler(callback: types.CallbackQuery, callback_data: ComicCallback):
    collection_key = callback_data.collection_key
    comic_key = callback_data.comic_key
    
    # ПЕРЕРАБОТАННАЯ ЛОГИКА ПОЛУЧЕНИЯ КЛЮЧА ГЛАВЫ
    chapters_data = await get_chapters_data(collection_key, comic_key)
    chapter_keys = sorted(chapters_data.keys(), key=natural_sort_key)
    
    # Номер главы в списке начинается с 1. Индекс в списке - (page - 1).
    chapter_index = callback_data.page - 1
    
    if chapter_index < 0 or chapter_index >= len(chapter_keys):
        await callback.answer("❌ Неверный номер главы.", show_alert=True)
        return
        
    chapter_key = chapter_keys[chapter_index] 
    
    # 1. Получаем заголовок
    comics_data = await get_comics_data(collection_key)
    comic_title = comics_data.get(comic_key, {}).get("title", "Комикс")
    chapter_title = chapters_data.get(chapter_key, f"Глава {callback_data.page}")

    # 2. Получаем данные о ссылках на изображения
    links_list = await get_links_list(collection_key, comic_key, chapter_key)

    if not links_list:
        await callback.answer("❌ Ссылки для этой главы не найдены.", show_alert=True)
        return

    # получаем telegraph из workflow_data
    telegraph = dp.workflow_data.get("telegraph")

    # 3. Создаем страницу Telegra.ph (если включен и доступен)
    if telegraph:
        # Улучшено: показываем пользователю, что ждём
        await callback.answer("⏳ Создаю страницу Telegra.ph. Это может занять несколько секунд...", show_alert=False) 
        
        # --- Клавиатура для Telegra.ph ---
        markup = InlineKeyboardBuilder()
        markup.row(
            types.InlineKeyboardButton(
                text=f"⬅️ К главам: {comic_title}",
                callback_data=ComicCallback(collection_key=collection_key, comic_key=comic_key, action="open", page=1).pack(),
            )
        )
        
        try:
            # Используем to_thread для синхронного метода create_page
            html_content = create_html_content(links_list)
            
            # Запускаем синхронный метод в отдельном потоке
            response = await to_thread(
                telegraph.create_page,
                title=f"{comic_title} - {chapter_title}",
                author_name=COMICS_AUTHOR_NAME,
                html_content=html_content,
            )
            
            page_url = response.get("url")
            if page_url:
                # --- ИСПРАВЛЕННЫЙ БЛОК: КРАСИВОЕ ОФОРМЛЕНИЕ ССЫЛКИ ---
                link_text = html.link(f"📚 {comic_title} - {chapter_title} (Открыть)", page_url)
                
                # Добавляем кнопку "Перейти к главе"
                markup_link = InlineKeyboardBuilder()
                markup_link.row(
                    types.InlineKeyboardButton(
                        text=f"↗️ Читать главу: {chapter_title}",
                        url=page_url
                    )
                )
                markup_link.row(
                    types.InlineKeyboardButton(
                        text=f"⬅️ К главам: {comic_title}",
                        callback_data=ComicCallback(collection_key=collection_key, comic_key=comic_key, action="open", page=1).pack(),
                    )
                )
                
                await callback.message.edit_text(
                    f"✅ Глава **{chapter_title}** готова!\n\nНажмите кнопку ниже, чтобы начать чтение.",
                    parse_mode=ParseMode.MARKDOWN, # Возвращаем MARKDOWN для выделения жирным
                    reply_markup=markup_link.as_markup(),
                )
                # --- КОНЕЦ ИСПРАВЛЕННОГО БЛОКА ---
                return
        except Exception as e:
            if tg_exceptions and isinstance(e, tg_exceptions.TelegraphException):
                error_message = f"API Telegra.ph: {e}"
            else:
                error_message = f"Неизвестная ошибка: {e}"
            print(f"⚠️ Ошибка создания страницы Telegra.ph для {chapter_title}: {error_message}")

            # Запасной вариант: отправляем прямые ссылки
            try:
                await callback.message.edit_text(
                    f"⚠️ **Не удалось создать страницу Telegra.ph** для главы **{chapter_title}**.\n"
                    f"Причина: `Ошибка Telegra.ph`.\n"
                    f"Ниже — прямые ссылки на изображения.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=markup.as_markup() # Возвращаем кнопку назад
                )
            except Exception:
                pass

            links_chunk = "\n".join(links_list)
            await callback.message.answer(f"Прямые ссылки ({len(links_list)} шт.):\n{links_chunk}", disable_web_page_preview=True)
            return

    # Telegra.ph отключен или не удалось — отправляем прямые ссылки
    markup = InlineKeyboardBuilder()
    markup.row(
        types.InlineKeyboardButton(
            text=f"⬅️ К главам: {comic_title}",
            callback_data=ComicCallback(collection_key=collection_key, comic_key=comic_key, action="open", page=1).pack(),
        )
    )
    
    try:
        await callback.message.edit_text(
            f"📖 **{comic_title} - {chapter_title}**\n\n_Telegra.ph отключен/недоступен_. Вот прямые ссылки на изображения:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup.as_markup()
        )
    except Exception:
        pass

    links_chunk = "\n".join(links_list)
    await callback.message.answer(links_chunk, disable_web_page_preview=True)
    await callback.answer()


@dp.callback_query(MenuCallback.filter(F.action == "search"))
async def start_search_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)

    await callback.message.edit_text(
        "🔍 **Поиск комикса**\n\nВведите полное название или часть названия комикса. Я найду все совпадения:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardBuilder()
        .row(types.InlineKeyboardButton(text="❌ Отмена и назад", callback_data=MenuCallback(action="back").pack()))
        .as_markup(),
    )
    await callback.answer()


@dp.message(SearchState.waiting_for_query)
async def process_search_query(message: types.Message, state: FSMContext):
    query = message.text.lower().strip()
    await state.clear()

    if not query:
        await message.answer(
            f"❌ Вы не ввели запрос. Попробуйте снова.",
            reply_markup=InlineKeyboardBuilder().row(types.InlineKeyboardButton(text="🏠 В главное меню", callback_data=MenuCallback(action="back").pack())).as_markup(),
        )
        return

    all_data = await get_all_data()
    found_comics = []

    if all_data:
        # 1. Проходим по всем коллекциям
        for collection_key, collection_data in all_data.items():
            comics_in_collection = collection_data.get("comics", {})

            # 2. Проходим по всем комиксам в коллекции
            for comic_key, comic_data in comics_in_collection.items():
                title = comic_data.get("title", comic_key)

                # 3. Проверяем, соответствует ли заголовок запросу
                if query in title.lower():
                    found_comics.append({"title": title, "collection_key": collection_key, "comic_key": comic_key})

    if found_comics:
        builder = InlineKeyboardBuilder()
        message_text = f"✅ **Найдено {len(found_comics)} совпадений** по запросу «{message.text}»:\n\n"

        # Сортируем найденные комиксы по названию
        found_comics.sort(key=lambda x: x["title"])

        for item in found_comics:
            builder.row(
                types.InlineKeyboardButton(
                    text=f"📜 {item['title']}",
                    callback_data=ComicCallback(collection_key=item["collection_key"], comic_key=item["comic_key"], action="open", page=1).pack(),
                )
            )

        builder.row(types.InlineKeyboardButton(text="🏠 В главное меню", callback_data=MenuCallback(action="back").pack()))

        await message.answer(message_text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN)
    else:
        await message.answer(
            f"❌ Комиксы по запросу «**{message.text}**» не найдены. Попробуйте ввести часть названия.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardBuilder().row(types.InlineKeyboardButton(text="🏠 В главное меню", callback_data=MenuCallback(action="back").pack())).as_markup(),
        )


# --- Планировщик и ежедневные уведомления (Улучшенный UI) ---


async def send_daily_update(bot_obj: Bot):
    """Ежедневно отправляет уведомление о новых комиксах всем подписчикам."""

    users = await load_json_async(USERS_FILE) or []
    if not users:
        print("INFO: Нет подписчиков для отправки ежедневного обновления.")
        return

    update_message = (
        "✨ **Ежедневное обновление комиксов!**\n\n"
        "📖 Не пропустите новые главы в вашей любимой коллекции! Нажмите кнопку ниже, чтобы перейти к чтению."
    )

    markup = await get_collections_markup() # Получаем клавиатуру коллекций, она же ведет в главное меню

    for user_id in users:
        try:
            await bot_obj.send_message(chat_id=user_id, text=update_message, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            # Сюда попадаем, если пользователь заблокировал бота
            print(f"⚠️ Не удалось отправить сообщение пользователю {user_id} (вероятно, заблокировал): {e}")

    print(f"✅ Ежедневное обновление отправлено {len(users)} подписчикам.")


# --- Главная функция запуска ---


async def main():
    # 1. Планировщик и Telegraph
    scheduler = AsyncIOScheduler(timezone=TZ_INFO)
    telegraph: Optional[Any] = None

    if TELEGRAPH_ENABLED and TELEGRAPH_AVAILABLE:
        # Инициализация и создание аккаунта с to_thread
        telegraph_instance = Telegraph() # Создаем синхронный экземпляр
        try:
            # Запускаем синхронный метод в отдельном потоке
            await to_thread(
                telegraph_instance.create_account, 
                short_name=COMICS_AUTHOR_NAME
            )
            telegraph = telegraph_instance # Если успешно, сохраняем экземпляр
            print("✅ Telegraph готов.")
        except Exception as e:
            if tg_exceptions and isinstance(e, tg_exceptions.TelegraphException):
                print(f"⚠️ Ошибка Telegraph (API/Сеть): {e}")
            else:
                print(f"⚠️ Неизвестная ошибка при инициализации Telegraph: {e}")
            telegraph = None
            print("❌ Telegra.ph отключен из-за ошибки инициализации.")
    else:
        if TELEGRAPH_ENABLED and not TELEGRAPH_AVAILABLE:
            print("⚠️ Библиотека 'telegraph' не найдена. (ImportError). Установлена синхронная версия.")
        print("⚠️ Telegra.ph отключен. Ссылки будут отправляться напрямую.")


    # Сохраняем объект Telegraph (или None) в диспетчере для доступа из хэндлеров
    dp.workflow_data["telegraph"] = telegraph

    # 2. Планировщик
    # Запускаем задачу, передаём глобальный bot
    scheduler.add_job(send_daily_update, "cron", hour=6, minute=0, args=[bot], timezone=TZ_INFO)
    scheduler.start()

    # 3. Запуск бота
    print("🤖 Бот запущен.")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Бот остановлен вручную.")
    except RuntimeError as e:
        if "No BOT_TOKEN" in str(e):
            print(f"🛑 Ошибка запуска: {e}")
        else:
            raise