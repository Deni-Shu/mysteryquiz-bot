import asyncio
import json
import logging
import os
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import TOKEN
from database import init_db, save_user, create_test, get_test, save_attempt, update_user_activity, increment_test_created, add_revenue, get_stats
from questions import DEFAULT_QUESTIONS

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

BOT_USERNAME = None
user_sessions = {}
custom_sessions = {}

OWNER_ID = 1347045944  # Ваш Telegram ID

# ---- Вспомогательные функции для бонусов ----
async def has_free_test(user_id: int) -> bool:
    async with aiosqlite.connect("test_bot.db") as db:
        async with db.execute("SELECT free_test_granted FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row and row[0] == 1

async def grant_free_test(user_id: int):
    async with aiosqlite.connect("test_bot.db") as db:
        await db.execute("UPDATE users SET free_test_granted = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def use_free_test(user_id: int):
    async with aiosqlite.connect("test_bot.db") as db:
        await db.execute("UPDATE users SET free_test_granted = 0 WHERE user_id = ?", (user_id,))
        await db.commit()

# ---------- Команда для скачивания базы данных (только админ) ----------
@dp.message(Command("getdb"))
async def get_database_file(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    try:
        file = FSInputFile("test_bot.db")
        await message.answer_document(file, caption="📁 База данных бота")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ---------- Админские команды ----------
@dp.message(Command("admin_stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Доступ запрещён.")
        return
    stats = await get_stats()
    report = f"""
📊 **Статистика бота**

👥 **Пользователи:**
- Всего: {stats['total_users']}
- Новых сегодня: {stats['new_users_today']}
- Активных сегодня: {stats['active_today']}

📝 **Тесты:**
- Создано обычных: {stats['total_tests']}
- Создано кастомных (платных): {stats['total_custom_tests']}
- Всего пройдено тестов: {stats['total_attempts']}

💰 **Доход:**
- Всего звёзд: {stats['total_revenue']}⭐
"""
    await message.answer(report)

@dp.message(Command("bonus"))
async def give_bonus(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Используй: /bonus user_id")
        return
    try:
        user_id = int(args[1])
        await grant_free_test(user_id)
        await message.answer(f"✅ Бонус (бесплатный тест) выдан пользователю {user_id}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

# ---------- Публичные команды ----------
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

@dp.message(Command("about"))
async def about_bot(message: types.Message):
    about_text = """
ℹ️ *О боте*

Этот бот — шуточный розыгрыш друзей. Ты отправляешь ссылку → друг отвечает на вопросы → ты получаешь его ответы. А друг получает свою ссылку, чтобы разыграть следующего.

🎯 *Возможности:*
• Бесплатный тест (13 вопросов)
• ✨ *Создать свой тест* — 100⭐
• ☕ *Поддержать донатом* (20, 50, 100⭐)

🔞 *18+*

📜 Политика: /privacy

👉 *Как начать?* Отправь /start
"""
    await message.answer(about_text, parse_mode="Markdown")

# ---------- Кнопочные обработчики ----------
@dp.message(lambda message: message.text == "📜 Политика")
async def privacy_button(message: types.Message):
    await cmd_privacy(message)

@dp.message(lambda message: message.text == "🔔 Бонус за подписку")
async def subscribe_bonus(message: types.Message):
    user_id = message.from_user.id
    channel_username = "@po_sekretu_18"
    try:
        member = await bot.get_chat_member(channel_username, user_id)
        if member.status in ["creator", "administrator", "member"]:
            if not await has_free_test(user_id):
                await grant_free_test(user_id)
                await message.answer("✅ Бонус за подписку получен! Вы можете создать один тест бесплатно (нажмите «✨ Создать свой тест»).")
            else:
                await message.answer("Вы уже получали бонус.")
        else:
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{channel_username[1:]}"))
            await message.answer("Подпишитесь на канал, чтобы получить бесплатный тест:", reply_markup=keyboard.as_markup())
    except Exception as e:
        await message.answer(f"Ошибка проверки подписки: {e}. Попробуйте позже.")

@dp.message(lambda message: message.text == "📝 Поделиться историей")
async def ask_story(message: types.Message):
    await message.answer("Напиши свою историю анонимно. Она может быть опубликована в нашем канале. Мы не редактируем, но оставляем право отклонить оскорбительный контент.\n\nОтправь текст одним сообщением:")
    user_sessions[message.from_user.id] = {"waiting_story": True}

@dp.message(lambda message: message.text == "❓ Команды")
async def show_commands(message: types.Message):
    commands_text = """
❓ *Доступные команды*

/start — создать новый тест и получить ссылку для розыгрыша
/privacy — политика конфиденциальности
/about — о боте

📌 *Кнопки меню:*
📜 Политика — правила обработки данных
✨ Создать свой тест — за 100⭐
🔔 Бонус за подписку — бесплатный тест за подписку на канал
📝 Поделиться историей — отправить анонимную историю в наш канал
❓ Команды — этот список

✏️ Во время прохождения теста:
- Выбирай варианты ответов или нажми «Свой вариант»
- В свободных вопросах просто пиши ответ

🔞 Контент 18+
"""
    await message.answer(commands_text, parse_mode="Markdown")

@dp.message(lambda message: message.text == "✨ Создать свой тест")
async def create_custom_test(message: types.Message):
    user_id = message.from_user.id
    if await has_free_test(user_id):
        await use_free_test(user_id)
        await start_custom_test_creation(user_id)
        await message.answer("🔓 Вы использовали бесплатный бонус. Создайте свой тест!")
    else:
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

async def show_main_menu(user_id: int, text: str = "🎉 Главное меню"):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 Политика")],
            [KeyboardButton(text="✨ Создать свой тест")],
            [KeyboardButton(text="🔔 Бонус за подписку")],
            [KeyboardButton(text="📝 Поделиться историей")],
            [KeyboardButton(text="❓ Команды")]
        ],
        resize_keyboard=True
    )
    await bot.send_message(user_id, text, reply_markup=keyboard)

# ---------- Команда /start ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "без username"
    await save_user(user_id, username)
    await update_user_activity(user_id)

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
                "username": message.from_user.username or "пользователь",
                "warned": False
            }
            await send_question(user_id)
            return
        else:
            await message.answer("❌ Такой тест не найден.")
            return

    questions_json = json.dumps(DEFAULT_QUESTIONS, ensure_ascii=False)
    test_id = await create_test(user_id, questions_json)
    link = f"https://t.me/{BOT_USERNAME}?start={test_id}"
    await show_main_menu(user_id, f"🎉 Твой тест готов! Отправь эту ссылку другу:\n{link}\n\nКогда друг пройдёт тест, его ответы придут тебе.")

# ---------- Отправка вопроса ----------
async def send_question(user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        return

    if not session.get("warned", False):
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="Продолжить", callback_data="continue_18"))
        await bot.send_message(
            user_id,
            "🔞 Внимание! Тест содержит вопросы для взрослых (18+). Продолжая, вы подтверждаете, что вам есть 18 лет.",
            reply_markup=keyboard.as_markup()
        )
        session["warned"] = True
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
        sent_msg = await bot.send_message(user_id, text)
        session["last_bot_message_id"] = sent_msg.message_id
    else:
        builder = InlineKeyboardBuilder()
        for opt in q["options"]:
            builder.add(InlineKeyboardButton(text=opt, callback_data=f"ans_{opt}"))
        builder.add(InlineKeyboardButton(text="✍️ Свой вариант", callback_data="ans_custom"))
        builder.adjust(2)
        sent_msg = await bot.send_message(user_id, text, reply_markup=builder.as_markup())
        session["last_bot_message_id"] = sent_msg.message_id

# ---------- Обработка нажатий ----------
@dp.callback_query()
async def handle_answer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = user_sessions.get(user_id)
    data = callback.data

    if data == "donate_show":
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="20⭐", callback_data="donate_20"))
        keyboard.add(InlineKeyboardButton(text="50⭐", callback_data="donate_50"))
        keyboard.add(InlineKeyboardButton(text="100⭐", callback_data="donate_100"))
        await callback.message.answer("Выбери сумму поддержки:", reply_markup=keyboard.as_markup())
        await callback.answer()
        return
    elif data.startswith("donate_"):
        amount = int(data.split("_")[1])
        await send_invoice(callback.message, amount)
        await callback.answer()
        return

    if data == "continue_18":
        if session:
            await callback.message.delete()
            await send_question(user_id)
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

# ---------- Обработка текстовых сообщений (истории, свободные ответы) ----------
@dp.message()
async def handle_text(message: types.Message):
    user_id = message.from_user.id

    # Принудительная обработка команд (страховка)
    if message.text.startswith('/'):
        command = message.text.split()[0].lower()
        if command == '/getdb' and message.from_user.id == OWNER_ID:
            await get_database_file(message)
            return
        if command == '/bonus' and message.from_user.id == OWNER_ID:
            await give_bonus(message)
            return
        if command == '/admin_stats' and message.from_user.id == OWNER_ID:
            await admin_stats(message)
            return
        if command == '/privacy':
            await cmd_privacy(message)
            return
        if command == '/about':
            await about_bot(message)
            return
        if command == '/start':
            await cmd_start(message)
            return
        # Для всех остальных команд – ничего не делаем, просто игнорируем

    # Обработка истории
    session = user_sessions.get(user_id)
    if session and session.get("waiting_story"):
        story = message.text.strip()
        if len(story) < 10:
            await message.answer("Слишком коротко. Напиши хотя бы 10 символов.")
            return
        await bot.send_message(OWNER_ID, f"📝 Новая история от анонима (id {user_id}):\n\n{story}")
        await message.answer("✅ История отправлена на модерацию. Если она будет опубликована, вы получите бонус!")
        del user_sessions[user_id]
        return

    # Сбор кастомного теста
    if user_id in custom_sessions:
        await process_custom_test_creation(message)
        return

    # Свободный ответ при прохождении теста
    if session and session.get("waiting_custom"):
        custom_answer = message.text.strip()
        if custom_answer:
            last_msg_id = session.get("last_bot_message_id")
            if last_msg_id:
                try:
                    await bot.delete_message(user_id, last_msg_id)
                except:
                    pass
            session["answers"].append(custom_answer)
            session["current_q"] += 1
            session["waiting_custom"] = False
            await message.answer("✅ Ответ принят!")
            await send_question(user_id)
        else:
            await message.answer("Пожалуйста, введи текст ответа.")
        return

    # Если ничего не подошло
    await show_main_menu(user_id, "Используй /start, чтобы создать тест или пройти по ссылке.")

# ---------- Платежи ----------
async def send_invoice(message: types.Message, amount: int):
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Поддержка автора ☕",
        description="Спасибо за поддержку!",
        payload=f"donation_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Звёзды", amount=amount)],
        start_parameter="donate",
        need_name=False,
        need_phone_number=False,
        need_email=False,
    )

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(lambda m: m.successful_payment is not None)
async def successful_payment(message: types.Message):
    payment = message.successful_payment
    amount = payment.total_amount
    currency = payment.currency
    username = message.from_user.username or "пользователь"
    payload = payment.invoice_payload

    if payload.startswith("custom_test"):
        await add_revenue(100)
        await start_custom_test_creation(message.from_user.id)
        await message.answer("Оплата прошла успешно! Теперь создадим твой тест.")
    else:
        await add_revenue(amount)
        await bot.send_message(OWNER_ID, f"🎉 Получен донат!\nОт: @{username}\nСумма: {amount} {currency}")
        await message.answer(f"Спасибо за поддержку! ❤️")

# ---------- Кастомные тесты ----------
async def start_custom_test_creation(user_id: int):
    custom_sessions[user_id] = {
        "state": "ask_question_count",
        "total_questions": None,
        "current_q": 0,
        "questions": []
    }
    await bot.send_message(user_id, "Сколько вопросов? (от 1 до 10)\nКаждый вопрос будет с вариантами ответов.")

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
            await message.answer("Введите целое число.")

    elif state == "ask_question_text":
        session["current_question"] = {"text": message.text.strip()}
        session["state"] = "ask_options"
        await message.answer("Введите варианты через запятую (2–6). Пример: Дружба, Любовь, Приключения")

    elif state == "ask_options":
        raw = message.text.strip()
        options = [opt.strip() for opt in raw.split(",") if opt.strip()]
        if len(options) < 2:
            await message.answer("Нужно хотя бы 2 варианта.")
            return
        if len(options) > 6:
            await message.answer("Максимум 6 вариантов.")
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

async def save_custom_test(user_id: int, questions_list):
    questions_json = json.dumps(questions_list, ensure_ascii=False)
    test_id = await create_test(user_id, questions_json)
    await increment_test_created(is_custom=True)
    new_link = f"https://t.me/{BOT_USERNAME}?start={test_id}"
    share_url = f"https://t.me/share/url?url={new_link}"
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📤 Поделиться", url=share_url))
    await bot.send_message(user_id, f"✨ Ваш тест готов! Ссылка:\n{new_link}", reply_markup=keyboard.as_markup())
    await show_main_menu(user_id, "🎉 Тест создан! Можете поделиться ссылкой или создать новый.")

# ---------- Завершение обычного теста ----------
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

    questions_json = json.dumps(questions, ensure_ascii=False)
    new_test_id = await create_test(user_id, questions_json)
    new_link = f"https://t.me/{BOT_USERNAME}?start={new_test_id}"
    share_url = f"https://t.me/share/url?url={new_link}"
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📤 Поделиться", url=share_url))
    keyboard.add(InlineKeyboardButton(text="☕ Поддержать", callback_data="donate_show"))

    await bot.send_message(
        user_id,
        f"😄 Ха-ха, тебя разыграли!\nТеперь твоя ссылка:\n{new_link}\n\nОтправь её другу!",
        reply_markup=keyboard.as_markup()
    )
    await show_main_menu(user_id, "🎉 Ты прошёл тест! Теперь можешь создать свой тест или поделиться ссылкой.")

# ---------- Запуск через webhook ----------
async def health(request):
    return web.Response(text="OK")

async def main():
    global BOT_USERNAME
    await init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    print(f"Бот запущен: @{BOT_USERNAME}")

    # Удаляем старый вебхук и все ожидающие обновления
    await bot.delete_webhook(drop_pending_updates=True)

    # Получаем внешний URL от Render
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        print("Ошибка: RENDER_EXTERNAL_URL не задан. Бот не может установить webhook.")
        return
    webhook_url = f"{render_url}/webhook"
    await bot.set_webhook(webhook_url)
    print(f"Webhook установлен: {webhook_url}")

    # Создаём aiohttp приложение
    app = web.Application()
    app.router.add_get("/", health)
    
    # Обработчик вебхука
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path="/webhook")
    
    # Запускаем веб-сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    print("HTTP-сервер запущен на порту 10000 (webhook)")

    # Бесконечно ждём (вебхук работает сам)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
