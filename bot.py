import asyncio
import json
import logging
import os
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import TOKEN
from database import init_db, save_user, create_test, get_test, save_attempt
from questions import DEFAULT_QUESTIONS

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

BOT_USERNAME = None
user_sessions = {}

# ---------- Команда /privacy ----------
@dp.message(Command("privacy"))
async def cmd_privacy(message: types.Message):
    privacy_text = """
📜 Политика конфиденциальности

1. Какие данные мы собираем
   - Ваш публичный username в Telegram.
   - Ваши ответы на вопросы теста.
   Мы не запрашиваем ФИО, адреса, номера телефонов, банковские карты или другую личную информацию.

2. Использование данных
   - Данные не публикуются публично и не передаются третьим лицам.

3. Хранение данных
   - Информация хранится в базе данных бота для обеспечения работы.
   - После успешного прохождения теста ответы удаляются из активного хранения.
   - Вы можете удалить свой аккаунт в Telegram – записи о вас будут удалены автоматически.

4. Безопасность
   - Бот не имеет доступа к вашим личным сообщениям, контактам, геолокации.
   - Мы не используем данные для рекламы.

5. Ваши права
   - Вы можете прекратить использование бота в любой момент.

6. Изменения
   - Политика может обновляться при добавлении новых функций. Об изменениях будет сообщено в боте.

Последнее обновление: март 2026 г.
"""
    await message.answer(privacy_text)

# ---------- Обработчик кнопки "Политика" ----------
@dp.message(lambda message: message.text == "📜 Политика")
async def privacy_button(message: types.Message):
    await cmd_privacy(message)

# ---------- Команда /start ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    await save_user(user_id, username)

    args = message.text.split()
    print(f"DEBUG: /start received, text={message.text}, args={args}")  # <-- добавил

    if len(args) > 1:
        test_id = args[1]
        print(f"DEBUG: Looking for test_id={test_id}")
        test_data = await get_test(test_id)
        if test_data:
            questions = json.loads(test_data["questions_json"])
            user_sessions[user_id] = {
                "test_id": test_id,
                "current_q": 0,
                "answers": [],
                "questions": questions,
                "username": message.from_user.username or "пользователь"
            }
            await send_question(user_id)
            return
        else:
            print(f"DEBUG: Test not found for id={test_id}")
            await message.answer("❌ Такой тест не найден.")
            return

    # Создаём новый тест для пользователя
    print("DEBUG: Creating new test")
    questions_json = json.dumps(DEFAULT_QUESTIONS, ensure_ascii=False)
    test_id = await create_test(user_id, questions_json)
    link = f"https://t.me/{BOT_USERNAME}?start={test_id}"
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📜 Политика")]],
        resize_keyboard=True
    )
    
    await message.answer(
        f"🎉 Твой тест готов! Отправь эту ссылку другу, чтобы разыграть его:\n{link}\n\n"
        "Когда друг пройдёт тест, его ответы придут тебе в личные сообщения.",
        reply_markup=keyboard
    )

# ---------- Отправка вопроса (поддержка свободных вопросов) ----------
async def send_question(user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        return
    q_index = session["current_q"]
    questions = session["questions"]
    if q_index >= len(questions):
        await finish_test(user_id)
        return

    q = questions[q_index]
    text = f"Вопрос {q_index+1} из {len(questions)}:\n{q['text']}"

    if q.get("type") == "free":
        session["waiting_custom"] = True
        await bot.send_message(user_id, text)
    else:
        builder = InlineKeyboardBuilder()
        for opt in q["options"]:
            builder.add(InlineKeyboardButton(text=opt, callback_data=f"ans_{opt}"))
        builder.add(InlineKeyboardButton(text="✍️ Свой вариант", callback_data="ans_custom"))
        await bot.send_message(user_id, text, reply_markup=builder.as_markup())

# ---------- Обработка нажатий на кнопки ----------
@dp.callback_query()
async def handle_answer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await callback.answer("Что-то пошло не так, попробуй /start")
        return

    data = callback.data
    if data.startswith("ans_"):
        answer = data[4:]
        if answer == "custom":
            await callback.message.answer("Напиши свой вариант ответа:")
            session["waiting_custom"] = True
            await callback.answer()
            return
        else:
            session["answers"].append(answer)
            session["current_q"] += 1
            await callback.message.delete()
            await send_question(user_id)
            await callback.answer()
    else:
        await callback.answer()

# ---------- Обработка текстовых сообщений (свободный ответ) ----------
@dp.message()
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if session and session.get("waiting_custom"):
        custom_answer = message.text.strip()
        if custom_answer:
            session["answers"].append(custom_answer)
            session["current_q"] += 1
            session["waiting_custom"] = False
            await message.answer("✅ Ответ принят!")
            await send_question(user_id)
        else:
            await message.answer("Пожалуйста, введи текст ответа.")
        return
    else:
        await message.answer("Используй /start, чтобы создать тест или пройти по ссылке.")

# ---------- Завершение теста, отправка результата и выдача новой ссылки ----------
async def finish_test(user_id: int):
    session = user_sessions.pop(user_id, None)
    if not session:
        return
    test_id = session["test_id"]
    answers = session["answers"]
    questions = session["questions"]
    passing_username = session["username"]

    test_data = await get_test(test_id)
    if not test_data:
        return
    creator_id = test_data["creator_id"]

    answers_json = json.dumps(answers, ensure_ascii=False)
    await save_attempt(test_id, user_id, answers_json)

    result_text = f"🎭 Твой друг @{passing_username} прошёл тест!\n\n"
    for i, (q, ans) in enumerate(zip(questions, answers), 1):
        result_text += f"{i}. {q['text']}\n   ➡️ {ans}\n"
    await bot.send_message(creator_id, result_text)

    # Создаём новую ссылку для прошедшего
    questions_json = json.dumps(questions, ensure_ascii=False)
    new_test_id = await create_test(user_id, questions_json)
    new_link = f"https://t.me/{BOT_USERNAME}?start={new_test_id}"
    await bot.send_message(
        user_id,
        f"😄 Ха-ха, тебя разыграли!\n"
        f"Теперь ты можешь разыграть друга — вот твоя ссылка:\n{new_link}\n\n"
        "Отправь её кому хочешь и получишь его ответы!"
    )

# ---------- HTTP-сервер для Render ----------
async def health(request):
    return web.Response(text="OK")

async def main():
    global BOT_USERNAME
    await init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    print(f"Бот запущен: @{BOT_USERNAME}")

    polling_task = asyncio.create_task(dp.start_polling(bot))

    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    print("HTTP-сервер запущен на порту 10000")

    await polling_task

if __name__ == "__main__":
    asyncio.run(main())
