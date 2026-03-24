import asyncio
import json
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import TOKEN
from database import init_db, save_user, create_test, get_test, save_attempt
from questions import DEFAULT_QUESTIONS

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

BOT_USERNAME = None
user_sessions = {}

# Словарь для тех, кто ждёт ввода суммы доната
awaiting_donation = {}

# --- Твой Telegram ID для уведомлений о донатах ---
OWNER_ID = 1347045944  # ЗАМЕНИ НА СВОЙ ID

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
    if len(args) > 1:
        test_id = args[1]
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
            await message.answer("❌ Такой тест не найден.")
            return

    # Создаём новый тест
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
    data = callback.data

    if data == "donate":
        # Кнопка "Поддержать" – включаем режим ожидания суммы
        awaiting_donation[user_id] = True
        await callback.message.answer("Введите сумму в Telegram Stars (целое число от 1 до 1000):")
        await callback.answer()
        return

    # Если пользователь не в сессии теста – не обрабатываем другие callback
    if not session:
        await callback.answer("Что-то пошло не так, попробуй /start")
        return

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

# ---------- Обработка текстовых сообщений ----------
@dp.message()
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)

    # Сначала проверяем, ждёт ли пользователь ввода суммы доната
    if user_id in awaiting_donation:
        try:
            amount = int(message.text.strip())
            if 1 <= amount <= 1000:
                await send_invoice(message, amount)
                # Удаляем из ожидания
                del awaiting_donation[user_id]
            else:
                await message.answer("Сумма должна быть от 1 до 1000. Попробуйте ещё раз.")
        except ValueError:
            await message.answer("Пожалуйста, введите целое число (от 1 до 1000).")
        return

    # Если пользователь в процессе теста и ждёт свободный ответ
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

    # Если ничего из вышеперечисленного
    await message.answer("Используй /start, чтобы создать тест или пройти по ссылке.")

# ---------- Отправка счёта (инвойса) на Telegram Stars ----------
async def send_invoice(message: types.Message, amount: int):
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Поддержка автора ☕",
        description="Спасибо, что хотите поддержать проект! Это поможет развитию бота.",
        payload=f"donation_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Звёзды", amount=amount)],
        start_parameter="donate",
        need_name=False,
        need_phone_number=False,
        need_email=False,
    )

# ---------- Обработка предварительного запроса на оплату ----------
@dp.pre_checkout_query()
async def pre_checkout(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

# ---------- Обработка успешной оплаты ----------
@dp.message(lambda m: m.successful_payment is not None)
async def successful_payment(message: types.Message):
    payment = message.successful_payment
    amount = payment.total_amount
    currency = payment.currency
    username = message.from_user.username or "пользователь"
    await bot.send_message(
        OWNER_ID,
        f"🎉 Получен донат!\n"
        f"От: @{username} (id {message.from_user.id})\n"
        f"Сумма: {amount} {currency}"
    )
    await message.answer(
        f"Спасибо за поддержку! ❤️ Ваши {amount} звёзд помогут развитию бота."
    )

# ---------- Завершение теста, отправка результата и выдача новой ссылки + кнопка доната ----------
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
    
    # Клавиатура с кнопкой доната
    donate_keyboard = InlineKeyboardBuilder()
    donate_keyboard.add(InlineKeyboardButton(text="☕ Поддержать проект", callback_data="donate"))
    await bot.send_message(
        user_id,
        f"😄 Ха-ха, тебя разыграли!\n"
        f"Теперь ты можешь разыграть друга — вот твоя ссылка:\n{new_link}\n\n"
        "Отправь её кому хочешь и получишь его ответы!",
        reply_markup=donate_keyboard.as_markup()
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
