import asyncio
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import TOKEN

bot = Bot(token=TOKEN)
dp = Dispatcher()
OWNER_ID = 1347045944

@dp.message(Command("broadcast"))
async def broadcast(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("Нет прав")
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Нет текста")
        return
    async with aiosqlite.connect("test_bot.db") as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    count = 0
    for (user_id,) in users:
        try:
            await bot.send_message(user_id, text)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"Отправлено {count}")

@dp.message()
async def all_messages(message: types.Message):
    await message.answer("Неизвестная команда")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
