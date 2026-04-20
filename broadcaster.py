import asyncio
import aiosqlite
from aiogram import Bot
from aiogram_broadcaster import MessageBroadcaster
from config import TOKEN

DB_NAME = "test_bot.db"

async def get_all_users():
    """Возвращает список всех user_id из таблицы users"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    return [user[0] for user in users]

async def main():
    # Создаём объект бота (используем тот же токен, что и в основном боте)
    bot = Bot(token=TOKEN)
    
    # Запрашиваем текст рассылки у администратора
    text = input("Введите текст рассылки: ").strip()
    if not text:
        print("Текст не может быть пустым.")
        return
    
    # Получаем всех пользователей
    users = await get_all_users()
    if not users:
        print("Нет пользователей для рассылки.")
        return
    
    print(f"Начинаем рассылку для {len(users)} пользователей...")
    
    # Используем специальный класс для массовой рассылки (соблюдает лимиты Telegram)
    broadcaster = MessageBroadcaster(users, bot=bot, text=text)
    await broadcaster.run()
    
    print("Рассылка завершена.")
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())