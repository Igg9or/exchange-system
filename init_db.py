from db import init_db

if __name__ == "__main__":
    print("Создаём таблицы в базе данных...")
    init_db()
    print("✅ Таблицы успешно созданы!")