# restructure_links.py

import json
from pathlib import Path

DATA_JSON = "data.json"
LINKS_JSON = "links.json"
BACKUP_LINKS_JSON = "links_backup.json"

def load_json(path):
    """Загружает данные из JSON файла."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ Ошибка: Файл {path} не найден.")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка при чтении {path}. Файл поврежден: {e}")
        return None

def save_json(path, data):
    """Сохраняет данные в JSON файл."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ Успешно сохранено в {path}")
    except Exception as e:
        print(f"❌ Ошибка при записи в {path}: {e}")

def restructure_links():
    """Перестраивает links.json, добавляя верхний уровень коллекций."""
    print("Начинаем процесс перестройки links.json...")
    
    data = load_json(DATA_JSON)
    old_links = load_json(LINKS_JSON)

    if not data or not old_links:
        print("🛑 Прерывание. Не удалось загрузить один из файлов.")
        return

    # Создаем резервную копию на всякий случай
    if Path(LINKS_JSON).exists():
        save_json(BACKUP_LINKS_JSON, old_links)
        print(f"ℹ️ Создана резервная копия: {BACKUP_LINKS_JSON}")


    new_links = {}
    total_comics_moved = 0
    
    # 1. Проходим по коллекциям в data.json (dc, marvel, other)
    for collection_key, collection_data in data.items():
        if not isinstance(collection_data, dict) or 'comics' not in collection_data:
            # Игнорируем невалидные или пустые записи
            continue

        comics_in_collection = collection_data.get('comics', {})
        new_links[collection_key] = {} # Создаем новый словарь для коллекции
        
        # 2. Проходим по комиксам в этой коллекции
        for comic_key in comics_in_collection.keys():
            # 3. Ищем этот комикс в старом links.json
            if comic_key in old_links:
                # 4. Переносим данные (все главы и ссылки) в новую структуру
                new_links[collection_key][comic_key] = old_links.pop(comic_key)
                total_comics_moved += 1

    # Проверяем, остались ли какие-то комиксы, которые не удалось перенести
    if old_links:
        print(f"⚠️ ВНИМАНИЕ: {len(old_links)} комиксов не удалось сопоставить в data.json. Они остались неперемещенными.")
        print(f"Неперемещенные ключи: {list(old_links.keys())[:5]}...")

    # 5. Сохраняем новый links.json
    save_json(LINKS_JSON, new_links)
    print(f"\n🎉 Перестройка завершена! Перемещено {total_comics_moved} комиксов.")
    print("Теперь ваш bot.py должен работать корректно.")


if __name__ == "__main__":
    restructure_links()