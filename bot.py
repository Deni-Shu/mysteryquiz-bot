import asyncio
import json
import logging
import os
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

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

# ---------- Бонусные функции ----------
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

# ---------- Админские команды ----------
@dp.message(Command("admin_stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    stats = await get_stats()
    report = f"""
📊 Статистика бота
Пользователей: {stats['total_users']}
Новых сегодня: {stats['new_users_today']}
Активных сегодня: {stats['active_today']}
Обычных тестов: {stats['total_tests']}
Кастомных тестов: {stats['total_custom_tests']}
Пройдено тестов: {stats['total_attempts']}
Доход: {stats['total_revenue']}⭐
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
        await message.answer(f"✅ Бонус выдан пользователю {user_id}")
    except:
        await message.answer("Ошибка")

@dp.message(Command("broadcast"))
async def broadcast(message: types.Message):
    if message.from_user.id != OWNER_ID:
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
            await bot.send_message(user_id, f"📢 {text}")
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"Отправлено {count} пользователям")

# ---------- Публичные команды ----------
@dp.message(Command("privacy"))
async def cmd_privacy(message: types.Message):
    await message.answer("📜 Политика конфиденциальности: мы не храним личные данные. Подробнее /about")

@dp.message(Command("about"))
async def about_bot(message: types.Message):
    await message.answer("ℹ️ Бот для розыгрыша друзей. Создай свой тест за 100⭐. Подпишись на канал @po_sekretu_18 и получи бонус.")

# ---------- Кнопки ----------
@dp.message(lambda message: message.text == "📜 Политика")
async def privacy_button(message: types.Message):
    await cmd_privacy(message)

@dp.message(lambda message: message.text == "🔔 Бонус за подписку")
async def subscribe_bonus(message: types.Message):
    user_id = message.from_user.id
    channel = "@po_sekretu_18"
    try:
        member = await bot.get_chat_member(channel, user_id)
        if member.status in ["creator", "administrator", "member"]:
            if not await has_free_test(user_id):
                await grant_free_test(user_id)
                await message.answer("✅ Бонус получен! Нажми «✨ Создать свой тест» бесплатно.")
            else:
                await message.answer("Вы уже получали бонус.")
        else:
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(text="Подписаться", url="https://t.me/po_sekretu_18"))
            await message.answer("Подпишись на канал, чтобы получить бонус:", reply_markup=keyboard.as_markup())
    except:
        await message.answer("Ошибка проверки подписки. Убедитесь, что бот админ канала.")

@dp.message(lambda message: message.text == "📝 Поделиться историей")
async def ask_story(message: types.Message):
    await message.answer("Напиши свою историю анонимно. Отправь текст одним сообщением:")
    user_sessions[message.from_user.id] = {"waiting_story": True}

@dp.message(lambda message: message.text == "❓ Команды")
async def show_commands(message: types.Message):
    await message.answer("❓ /start - начать\n/privacy - политика\n/about - о боте\n/bonus - админ\n/broadcast - админ")

@dp.message(lambda message: message.text == "✨ Создать свой тест")
async def create_custom_test(message: types.Message):
    user_id = message.from_user.id
    if await has_free_test(user_id):
        await use_free_test(user_id)
        await start_custom_test_creation(user_id)
    else:
        await bot.send_invoice(
            chat_id=message.chat.id,
            title="Создание теста",
            description="Создай свой тест за 100⭐",
            payload="custom_test_100",
            currency="XTR",
            prices=[LabeledPrice(label="Тест", amount=100)],
            start_parameter="custom_test",
        )

async def show_main_menu(user_id: int, text: str = "Меню"):
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

# ---------- /start ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "no name"
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
                "username": username,
                "warned": False
            }
            await send_question(user_id)
            return
        else:
            await message.answer("Тест не найден")
            return

    questions_json = json.dumps(DEFAULT_QUESTIONS, ensure_ascii=False)
    test_id = await create_test(user_id, questions_json)
    link = f"https://t.me/{BOT_USERNAME}?start={test_id}"
    await show_main_menu(user_id, f"Твой тест готов! Ссылка:\n{link}\nОтправь другу.")

# ---------- Отправка вопроса ----------
async def send_question(user_id: int):
    session = user_sessions.get(user_id)
    if not session:
        return
    if not session.get("warned", False):
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="Продолжить", callback_data="continue_18"))
        await bot.send_message(user_id, "🔞 Тест 18+. Продолжая, вы подтверждаете возраст.", reply_markup=keyboard.as_markup())
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
        builder.add(InlineKeyboardButton(text="Свой вариант", callback_data="ans_custom"))
        builder.adjust(2)
        sent_msg = await bot.send_message(user_id, text, reply_markup=builder.as_markup())
        session["last_bot_message_id"] = sent_msg.message_id

# ---------- Обработка callback ----------
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
        await callback.message.answer("Выбери сумму:", reply_markup=keyboard.as_markup())
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
        await callback.answer("Ошибка")
        return

    if data.startswith("ans_"):
        answer = data[4:]
        if answer == "custom":
            await callback.message.answer("Напиши свой ответ:")
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

# ---------- Обработка текста ----------
@dp.message()
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)

    if session and session.get("waiting_story"):
        story = message.text.strip()
        if len(story) < 10:
            await message.answer("Слишком коротко")
            return
        await bot.send_message(OWNER_ID, f"Новая история от {user_id}:\n{story}")
        await message.answer("История отправлена на модерацию")
        del user_sessions[user_id]
        return

    if user_id in custom_sessions:
        await process_custom_test_creation(message)
        return

    if session and session.get("waiting_custom"):
        ans = message.text.strip()
        if ans:
            last_msg_id = session.get("last_bot_message_id")
            if last_msg_id:
                try:
                    await bot.delete_message(user_id, last_msg_id)
                except:
                    pass
            session["answers"].append(ans)
            session["current_q"] += 1
            session["waiting_custom"] = False
            await message.answer("Ответ принят!")
            await send_question(user_id)
        else:
            await message.answer("Введите текст")
        return

    await show_main_menu(user_id, "Используй /start")

# ---------- Платежи ----------
async def send_invoice(message: types.Message, amount: int):
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Поддержка",
        description="Спасибо!",
        payload=f"donation_{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Звёзды", amount=amount)],
        start_parameter="donate",
    )

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(lambda m: m.successful_payment is not None)
async def successful_payment(message: types.Message):
    amount = message.successful_payment.total_amount
    username = message.from_user.username or "anon"
    await add_revenue(amount)
    await bot.send_message(OWNER_ID, f"Донат {amount}⭐ от @{username}")
    await message.answer("Спасибо за поддержку!")

# ---------- Кастомные тесты ----------
async def start_custom_test_creation(user_id: int):
    custom_sessions[user_id] = {"state": "ask_count", "total": 0, "current": 0, "questions": []}
    await bot.send_message(user_id, "Сколько вопросов? (1-10)")

async def process_custom_test_creation(message: types.Message):
    user_id = message.from_user.id
    s = custom_sessions[user_id]
    if s["state"] == "ask_count":
        try:
            total = int(message.text.strip())
            if 1 <= total <= 10:
                s["total"] = total
                s["state"] = "ask_text"
                s["current"] = 1
                await message.answer(f"Вопрос 1 из {total}. Введите текст вопроса:")
            else:
                await message.answer("Число от 1 до 10")
        except:
            await message.answer("Введите число")
    elif s["state"] == "ask_text":
        s["current_question"] = {"text": message.text.strip()}
        s["state"] = "ask_options"
        await message.answer("Введите варианты через запятую (2-6). Пример: Да, Нет, Возможно")
    elif s["state"] == "ask_options":
        raw = message.text.strip()
        opts = [x.strip() for x in raw.split(",") if x.strip()]
        if len(opts) < 2:
            await message.answer("Хотя бы 2 варианта")
            return
        if len(opts) > 6:
            await message.answer("Максимум 6 вариантов")
            return
        s["current_question"]["options"] = opts
        s["questions"].append(s["current_question"])
        s["current"] += 1
        if s["current"] > s["total"]:
            await save_custom_test(user_id, s["questions"])
            del custom_sessions[user_id]
        else:
            s["state"] = "ask_text"
            await message.answer(f"Вопрос {s['current']} из {s['total']}. Введите текст вопроса:")

async def save_custom_test(user_id: int, questions_list):
    questions_json = json.dumps(questions_list, ensure_ascii=False)
    test_id = await create_test(user_id, questions_json)
    await increment_test_created(is_custom=True)
    link = f"https://t.me/{BOT_USERNAME}?start={test_id}"
    await bot.send_message(user_id, f"Тест готов! Ссылка:\n{link}")
    await show_main_menu(user_id, "Тест создан!")

# ---------- Завершение теста ----------
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

    await save_attempt(test_id, user_id, json.dumps(answers))
    result = f"🎭 Друг @{passing_username} прошёл тест!\n"
    for i, (q, a) in enumerate(zip(questions, answers), 1):
        result += f"{i}. {q['text']} → {a}\n"
    await bot.send_message(creator_id, result)

    new_questions = json.dumps(questions, ensure_ascii=False)
    new_test_id = await create_test(user_id, new_questions)
    new_link = f"https://t.me/{BOT_USERNAME}?start={new_test_id}"
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="Поделиться", url=f"https://t.me/share/url?url={new_link}"))
    keyboard.add(InlineKeyboardButton(text="Поддержать", callback_data="donate_show"))
    await bot.send_message(user_id, f"😄 Тебя разыграли! Твоя ссылка:\n{new_link}", reply_markup=keyboard.as_markup())
    await show_main_menu(user_id, "Ты прошёл тест!")

# ---------- Health check ----------
async def health(request):
    return web.Response(text="OK")

# ---------- Main ----------
async def main():
    global BOT_USERNAME
    await init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    print(f"Бот запущен: @{BOT_USERNAME}")

    # Удаляем вебхук на всякий случай
    await bot.delete_webhook(drop_pending_updates=True)
    print("Webhook удалён")

    # Запускаем polling
    polling_task = asyncio.create_task(dp.start_polling(bot))

    # Запускаем HTTP-сервер для Render
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    print("HTTP-сервер на порту 10000")

    await polling_task

if __name__ == "__main__":
    asyncio.run(main())
