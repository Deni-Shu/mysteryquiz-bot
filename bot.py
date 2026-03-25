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

print("Бот стартует, регистрируем команды...")

bot = Bot(token=TOKEN)
dp = Dispatcher()

BOT_USERNAME = None
user_sessions = {}
custom_sessions = {}

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

# ---------- Обработчик кнопки "Сообщить об ошибке" ----------
@dp.message(lambda message: message.text == "⚠️ Сообщить об ошибке")
async def report_error(message: types.Message):
    await message.answer("Опишите проблему кратко. Мы постараемся исправить как можно скорее.")
    user_sessions[message.from_user.id] = {"waiting_report": True}

# ---------- Обработка текстовых сообщений ----------
@dp.message()
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)

    # Если пользователь ждёт отправки репорта
    if session and session.get("waiting_report"):
        report_text = message.text.strip()
        if report_text:
            await bot.send_message(
                OWNER_ID,
                f"⚠️ Сообщение об ошибке от @{message.from_user.username or 'пользователь'} (id {user_id}):\n{report_text}"
            )
            await message.answer("✅ Спасибо, сообщение отправлено!")
            del user_sessions[user_id]
        else:
            await message.answer("Пожалуйста, напишите текст ошибки.")
        return

    # Приоритет 1: сбор кастомного теста
    if user_id in custom_sessions:
        await process_custom_test_creation(message)
        return

    # Приоритет 2: прохождение обычного/кастомного теста
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

# ---------- Команда /start ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    print("DEBUG: cmd_start called")   # отладка
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    await save_user(user_id, username)

    args = message.text.split()
    print(f"DEBUG: args={args}")   # отладка
    if len(args) > 1:
        test_id = args[1]
        print(f"DEBUG: looking for test_id={test_id}")
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
            print(f"DEBUG: test not found")
            await message.answer("❌ Такой тест не найден.")
            return

    # Создаём обычный тест для пользователя
    print("DEBUG: creating new test")
    questions_json = json.dumps(DEFAULT_QUESTIONS, ensure_ascii=False)
    test_id = await create_test(user_id, questions_json)
    link = f"https://t.me/{BOT_USERNAME}?start={test_id}"
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 Политика")],
            [KeyboardButton(text="✨ Создать свой тест")],
            [KeyboardButton(text="⚠️ Сообщить об ошибке")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"🎉 Твой тест готов! Отправь эту ссылку другу, чтобы разыграть его:\n{link}\n\n"
        "Когда друг пройдёт тест, его ответы придут тебе в личные сообщения.",
        reply_markup=keyboard
    )

# ---------- ПЛАТНЫЙ ОБРАБОТЧИК КНОПКИ "Создать свой тест" ----------
@dp.message(lambda message: message.text == "✨ Создать свой тест")
async def create_custom_test(message: types.Message):
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Создание своего теста ✨",
        description="Вы сможете задать до 10 вопросов с вариантами ответов.",
        payload="custom_test_100",
        currency="XTR",
        prices=[LabeledPrice(label="Создание теста", amount=100)],
        start_parameter="custom_test",
        need_name=False,
        need_phone_number=False,
        need_email=False,
    )

# ---------- Отправка вопроса ----------
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

    # Обработка доната: отправляем новое сообщение, не трогаем исходное
    if data == "donate_show":
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="20⭐", callback_data="donate_20"))
        keyboard.add(InlineKeyboardButton(text="50⭐", callback_data="donate_50"))
        keyboard.add(InlineKeyboardButton(text="100⭐", callback_data="donate_100"))
        await callback.message.answer(
            "Выбери сумму поддержки (в Telegram Stars):",
            reply_markup=keyboard.as_markup()
        )
        await callback.answer()
        return
    elif data.startswith("donate_"):
        amount = int(data.split("_")[1])
        await send_invoice(callback.message, amount)
        await callback.answer()
        return

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

# ---------- Отправка счёта (инвойса) на Telegram Stars (для доната) ----------
async def send_invoice(message: types.Message, amount: int):
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Поддержка автора ☕",
        description="Спасибо, что хотите поддержать проект!",
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
    payload = payment.invoice_payload

    if payload.startswith("custom_test"):
        await start_custom_test_creation(message.from_user.id)
        await message.answer("Оплата прошла успешно! Теперь создадим твой тест.")
    else:
        await bot.send_message(
            OWNER_ID,
            f"🎉 Получен донат!\nОт: @{username} (id {message.from_user.id})\nСумма: {amount} {currency}"
        )
        await message.answer(f"Спасибо за поддержку! ❤️ Ваши {amount} звёзд помогут развитию бота.")

# ---------- Начало сбора кастомного теста (вызывается после оплаты) ----------
async def start_custom_test_creation(user_id: int):
    custom_sessions[user_id] = {
        "state": "ask_question_count",
        "total_questions": None,
        "current_q": 0,
        "questions": []
    }
    await bot.send_message(
        user_id,
        "Сколько вопросов будет в тесте? (от 1 до 10)\n\n"
        "Каждый вопрос будет с вариантами ответов. Пользователь также сможет написать свой вариант."
    )

async def process_custom_test_creation(message: types.Message):
    user_id = message.from_user.id
    session = custom_sessions[user_id]
    state = session["state"]

    if state == "ask_question_count":
        try:
            count = int(message.text.strip())
            if 1 <= count <= 10:
                session["total_questions"] = count
                session["state"] = "ask_question_text"
                session["current_q"] = 1
                await message.answer(f"Вопрос 1 из {count}. Введите текст вопроса:")
            else:
                await message.answer("Введите число от 1 до 10.")
        except ValueError:
            await message.answer("Пожалуйста, введите целое число.")

    elif state == "ask_question_text":
        session["current_question"] = {"text": message.text.strip()}
        session["state"] = "ask_options"
        await message.answer(
            "Введите варианты ответов через запятую (от 2 до 6 вариантов).\n\n"
            "Пример: Дружба, Любовь, Приключения"
        )

    elif state == "ask_options":
        raw = message.text.strip()
        options = [opt.strip() for opt in raw.split(",") if opt.strip()]
        if len(options) < 2:
            await message.answer("Нужно хотя бы 2 варианта. Попробуйте ещё раз.")
            return
        if len(options) > 6:
            await message.answer("Максимум 6 вариантов. Пожалуйста, введите не больше 6.")
            return
        session["current_question"]["options"] = options
        session["questions"].append(session["current_question"])
        session["current_q"] += 1
        if session["current_q"] > session["total_questions"]:
            await save_custom_test(user_id, session["questions"])
            del custom_sessions[user_id]
        else:
            session["state"] = "ask_question_text"
            await message.answer(f"Вопрос {session['current_q']} из {session['total_questions']}. Введите текст вопроса:")

# ---------- Сохранение кастомного теста и выдача ссылки ----------
async def save_custom_test(user_id: int, questions_list):
    questions_json = json.dumps(questions_list, ensure_ascii=False)
    test_id = await create_test(user_id, questions_json)
    new_link = f"https://t.me/{BOT_USERNAME}?start={test_id}"
    share_url = f"https://t.me/share/url?url={new_link}"
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📤 Поделиться", url=share_url))
    await bot.send_message(
        user_id,
        f"✨ Ваш тест готов! Отправьте эту ссылку другу, чтобы разыграть его:\n{new_link}\n\n"
        "Когда друг пройдёт тест, его ответы придут вам в личные сообщения.",
        reply_markup=keyboard.as_markup()
    )

# ---------- Завершение обычного теста, отправка результата и выдача новой ссылки + кнопка доната ----------
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

    # Создаём новую ссылку для прошедшего (обычный тест)
    questions_json = json.dumps(questions, ensure_ascii=False)
    new_test_id = await create_test(user_id, questions_json)
    new_link = f"https://t.me/{BOT_USERNAME}?start={new_test_id}"

    share_url = f"https://t.me/share/url?url={new_link}"
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📤 Поделиться", url=share_url))
    keyboard.add(InlineKeyboardButton(text="☕ Поддержать", callback_data="donate_show"))

    await bot.send_message(
        user_id,
        f"😄 Ха-ха, тебя разыграли!\n"
        f"Теперь ты можешь разыграть друга — вот твоя ссылка:\n{new_link}\n\n"
        "Отправь её кому хочешь и получишь его ответы!",
        reply_markup=keyboard.as_markup()
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

    print("Запускаем поллинг...")
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
