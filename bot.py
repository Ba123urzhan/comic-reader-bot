# bot.py — стабильная версия с поддержкой Telegra.ph (через внешние ссылки) + fallback в чат
import os
import json
import asyncio
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import FSInputFile
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Попробуем импортировать telegraph.aio (установите: pip install git+https://github.com/python273/telegraph.git)
TELEGRAPH_AVAILABLE = True
try:
    from telegraph.aio import Telegraph
except Exception:
    Telegraph = None
    TELEGRAPH_AVAILABLE = False

# --- Константы / настройки ---
COMICS_AUTHOR_NAME = "EasyReaderBot"
DATA_JSON = "data.json"
LINKS_JSON = "links.json" 
COMICS_DIR = Path("comics")
PROGRESS_FILE = "progress.json"

# Загружаем .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAPH_ENABLED = os.getenv("TELEGRAPH_ENABLED", "1") != "0"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

# ✅ Новая версия aiogram требует использование DefaultBotProperties
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Загружаем data.json
if not Path(DATA_JSON).exists():
    print(f"⚠️ {DATA_JSON} не найден. Создайте файл и заполните каталог комиксов.")
    comics_data = {}
else:
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        comics_data = json.load(f)

# Загружаем links.json (внешние ссылки)
if not Path(LINKS_JSON).exists():
    print(f"⚠️ {LINKS_JSON} не найден. Telegra.ph не будет работать.")
    comics_links = {}
else:
    with open(LINKS_JSON, "r", encoding="utf-8") as f:
        comics_links = json.load(f)
# -----------------------

# -----------------------
# Вспомогательные функции
# -----------------------
def get_chapter_folder(comic_key: str, chapter_key: str) -> Path:
    return COMICS_DIR / comic_key / chapter_key


def list_pages(folder: Path):
    """
    Возвращает список локальных страниц (для режима 'Читать в чате').
    """
    if not folder.exists():
        return []
    files = sorted([
        p.name for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    ])
    return files


# -----------------------
# Обработчики
# -----------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    for key, meta in comics_data.items():
        builder.button(text=meta.get("title", key), callback_data=f"comic:{key}")
    builder.adjust(1)
    await message.answer("📚 Выберите комикс:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("comic:"))
async def choose_chapter(callback: types.CallbackQuery):
    await callback.answer()
    comic_key = callback.data.split(":", 1)[1]
    if comic_key not in comics_data:
        await callback.message.edit_text("⚠️ Комикс не найден.")
        return

    comic = comics_data[comic_key]
    builder = InlineKeyboardBuilder()
    for chapter_key, title in comic.get("chapters", {}).items():
        builder.button(text=title, callback_data=f"chapter_menu:{comic_key}:{chapter_key}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"📖 {comic.get('title','Комикс')} — выберите главу:",
        reply_markup=builder.as_markup()
    )


@dp.callback_query(F.data.startswith("chapter_menu:"))
async def chapter_menu(callback: types.CallbackQuery):
    await callback.answer()
    _, comic_key, chapter_key = callback.data.split(":", 2)
    comic = comics_data.get(comic_key, {})
    title = comic.get("chapters", {}).get(chapter_key, "Глава")
    builder = InlineKeyboardBuilder()
    if TELEGRAPH_AVAILABLE and TELEGRAPH_ENABLED:
        builder.button(
            text="🌐 Читать онлайн (Telegra.ph)",
            callback_data=f"read_telegraph:{comic_key}:{chapter_key}"
        )
    builder.button(
        text="💬 Читать в чате",
        callback_data=f"read_chat:{comic_key}:{chapter_key}"
    )
    builder.button(text="🔙 Назад", callback_data=f"comic:{comic_key}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"📖 {comic.get('title','Комикс')} — {title}\nВыберите способ чтения:",
        reply_markup=builder.as_markup()
    )


# --- Чтение через Telegraph (исправлено на внешние ссылки) ---
telegraph = None


@dp.callback_query(F.data.startswith("read_telegraph:"))
async def read_via_telegraph(callback: types.CallbackQuery):
    await callback.answer("⏳ Подготавливаю страницу Telegra.ph...")
    _, comic_key, chapter_key = callback.data.split(":", 2)
    
    # Пытаемся получить внешние ссылки из comics_links
    try:
        # comics_links имеет структуру: {"comic_key": {"chapter_key": [url1, url2, ...]}}
        image_links = comics_links[comic_key][chapter_key]
    except KeyError:
        await callback.message.answer("⚠️ Внешние ссылки для этой главы не найдены в links.json. Пожалуйста, убедитесь, что links.json настроен правильно.")
        return

    if not image_links:
        await callback.message.answer("⚠️ В этой главе нет внешних ссылок на изображения в links.json.")
        return
    
    global telegraph
    if telegraph is None:
        try:
            telegraph = Telegraph()
        except Exception as e:
            print(f"[telegraph init error] {e}")
            telegraph = None

    if telegraph is None:
        await callback.message.answer("⚠️ Telegra.ph недоступен.")
        return

    # Используем image_links напрямую для создания HTML-контента
    content_html = "".join(f'<figure><img src="{link}"></figure>' for link in image_links)
    
    try:
        page = await telegraph.create_page(
            title=f"{comics_data.get(comic_key,{}).get('title','Комикс')} — {comics_data.get(comic_key,{}).get('chapters',{}).get(chapter_key,'Глава')}",
            author_name=COMICS_AUTHOR_NAME,
            html_content=content_html
        )
        
        # --- НОВЫЙ БЛОК ПРОВЕРКИ ---
        if isinstance(page, dict) and 'error' in page:
             error_message = page.get('error', 'Неизвестная ошибка Telegra.ph API')
             print(f"[Telegraph create_page API error] {error_message}")
             await callback.message.answer(f"⚠️ Не удалось создать страницу на Telegra.ph: {error_message}")
             return
        # --- КОНЕЦ НОВОГО БЛОКА ПРОВЕРКИ ---

    except Exception as e:
        print(f"[Telegraph create_page Exception] {e}")
        await callback.message.answer(f"⚠️ Не удалось создать страницу на Telegra.ph (Исключение): {e}")
        return

    builder = InlineKeyboardBuilder()
    # ИСПРАВЛЕНО: Доступ по ключу (page['url']) вместо доступа по атрибуту (page.url)
    builder.button(text="📖 Читать онлайн", url=page['url']) 
    # Предлагаем fallback на чтение в чате, если пользователь предпочитает его
    builder.button(text="💬 Читать в чате", callback_data=f"read_chat:{comic_key}:{chapter_key}")
    builder.button(text="🔙 К главам", callback_data=f"comic:{comic_key}")
    builder.adjust(1)
    # ИСПРАВЛЕНО: Доступ по ключу (page['title']) вместо доступа по атрибуту (page.title)
    await callback.message.answer(f"✅ Страница готова: <b>{page['title']}</b>", reply_markup=builder.as_markup()) 
    try:
        await callback.message.delete()
    except Exception:
        pass


# --- Отправка первой страницы в чат + навигация (использует локальные файлы) ---
async def send_first_page_in_chat(callback: types.CallbackQuery, comic_key: str, chapter_key: str, pages: list):
    page_num = 0
    folder = get_chapter_folder(comic_key, chapter_key)
    # Проверка на существование файла перед созданием FSInputFile
    if not (folder / pages[page_num]).exists():
        await callback.message.answer(f"⚠️ Локальный файл {pages[page_num]} не найден. Убедитесь, что папка 'comics' настроена.")
        return
        
    page_path = folder / pages[page_num]
    caption = (
        f"{comics_data.get(comic_key,{}).get('title','Комикс')} — "
        f"{comics_data.get(comic_key,{}).get('chapters',{}).get(chapter_key,'Глава')}\n"
        f"Страница {page_num+1}/{len(pages)}"
    )
    photo = FSInputFile(str(page_path))

    builder = InlineKeyboardBuilder()
    if len(pages) > 1:
        builder.button(text="➡️ Следующая", callback_data=f"page:{comic_key}:{chapter_key}:{page_num+1}")
    builder.button(text="🔙 К главам", callback_data=f"comic:{comic_key}")
    builder.adjust(2)
    await callback.message.answer_photo(photo=photo, caption=caption, reply_markup=builder.as_markup())
    try:
        await callback.message.delete()
    except Exception:
        pass


@dp.callback_query(F.data.startswith("read_chat:"))
async def read_chat(callback: types.CallbackQuery):
    _, comic_key, chapter_key = callback.data.split(":", 2)
    folder = get_chapter_folder(comic_key, chapter_key)
    pages = list_pages(folder)
    if not pages:
        await callback.message.answer("⚠️ В этой главе нет локальных изображений. Убедитесь, что папка 'comics' настроена.")
        return
    return await send_first_page_in_chat(callback, comic_key, chapter_key, pages)


@dp.callback_query(F.data.startswith("page:"))
async def page_navigation(callback: types.CallbackQuery):
    await callback.answer()
    _, comic_key, chapter_key, page_str = callback.data.split(":", 3)
    page_num = int(page_str)
    folder = get_chapter_folder(comic_key, chapter_key)
    pages = list_pages(folder)
    if not pages:
        await callback.message.answer("⚠️ В этой главе нет локальных изображений.")
        return

    if page_num < 0 or page_num >= len(pages):
        await callback.message.answer("⚠️ Неверный номер страницы.")
        return
        
    # Проверка на существование файла перед созданием FSInputFile
    if not (folder / pages[page_num]).exists():
        await callback.message.answer(f"⚠️ Локальный файл {pages[page_num]} не найден.")
        return

    page_path = folder / pages[page_num]
    photo = FSInputFile(str(page_path))
    caption = (
        f"{comics_data.get(comic_key,{}).get('title','Комикс')} — "
        f"{comics_data.get(comic_key,{}).get('chapters',{}).get(chapter_key,'Глава')}\n"
        f"Страница {page_num+1}/{len(pages)}"
    )

    builder = InlineKeyboardBuilder()
    if page_num > 0:
        builder.button(text="⬅️ Назад", callback_data=f"page:{comic_key}:{chapter_key}:{page_num-1}")
    if page_num < len(pages) - 1:
        builder.button(text="➡️ Следующая", callback_data=f"page:{comic_key}:{chapter_key}:{page_num+1}")
    builder.button(text="🔙 К главам", callback_data=f"comic:{comic_key}")
    builder.adjust(2)

    # Используем answer_photo вместо edit_message_media
    await callback.message.answer_photo(photo=photo, caption=caption, reply_markup=builder.as_markup())
    try:
        await callback.message.delete()
    except Exception:
        pass


# -----------------------
# Запуск
# -----------------------
async def main():
    global telegraph
    if TELEGRAPH_AVAILABLE and TELEGRAPH_ENABLED:
        try:
            telegraph = Telegraph()
            try:
                # Попытка создания/авторизации аккаунта Telegra.ph
                await telegraph.create_account(short_name=COMICS_AUTHOR_NAME)
            except Exception:
                pass
            print("✅ Telegraph готов (для внешних ссылок).")
        except Exception as e:
            print(f"⚠️ Telegraph init failed: {e}")
            telegraph = None
    else:
        # Если в .env TELEGRAPH_ENABLED='0' или библиотека недоступна
        print("ℹ️ Telegraph не доступен/отключён, работаем в режиме 'чтение в чате'")

    print("🤖 Бот запущен.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())