import json
import sqlite3
import aiosqlite

DB_NAME = "test_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица users (добавляем first_seen, last_activity, free_test_granted)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                free_test_granted INTEGER DEFAULT 0
            )
        ''')
        # Таблица tests
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tests (
                test_id TEXT PRIMARY KEY,
                creator_id INTEGER,
                questions_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица attempts
        await db.execute('''
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id TEXT,
                user_id INTEGER,
                answers_json TEXT,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица bot_stats для глобальной статистики
        await db.execute('''
            CREATE TABLE IF NOT EXISTS bot_stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_tests_created INTEGER DEFAULT 0,
                total_custom_tests_created INTEGER DEFAULT 0,
                total_revenue_stars INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Если таблица bot_stats пуста, вставляем начальную строку
        await db.execute('''
            INSERT OR IGNORE INTO bot_stats (id) VALUES (1)
        ''')
        await db.commit()

async def save_user(user_id, username):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        await db.commit()

async def update_user_activity(user_id: int):
    """Обновляет время последней активности пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

async def create_test(creator_id, questions_json):
    import uuid
    test_id = str(uuid.uuid4())[:8]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO tests (test_id, creator_id, questions_json) VALUES (?, ?, ?)",
            (test_id, creator_id, questions_json)
        )
        await db.commit()
    # Увеличиваем счётчик обычных тестов
    await increment_test_created(is_custom=False)
    return test_id

async def get_test(test_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT creator_id, questions_json FROM tests WHERE test_id = ?",
            (test_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"creator_id": row[0], "questions_json": row[1]}
    return None

async def save_attempt(test_id, user_id, answers_json):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO attempts (test_id, user_id, answers_json) VALUES (?, ?, ?)",
            (test_id, user_id, answers_json)
        )
        await db.commit()

async def increment_test_created(is_custom: bool = False):
    """Увеличивает счётчик созданных тестов (обычных или кастомных)"""
    async with aiosqlite.connect(DB_NAME) as db:
        if is_custom:
            await db.execute(
                "UPDATE bot_stats SET total_custom_tests_created = total_custom_tests_created + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
            )
        else:
            await db.execute(
                "UPDATE bot_stats SET total_tests_created = total_tests_created + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
            )
        await db.commit()

async def add_revenue(amount: int):
    """Увеличивает общий доход (в звёздах)"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE bot_stats SET total_revenue_stars = total_revenue_stars + ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (amount,)
        )
        await db.commit()

async def get_stats() -> dict:
    """Возвращает словарь со статистикой"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Общее количество пользователей
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_users = (await cursor.fetchone())[0]
        # Новых пользователей за сегодня
        async with db.execute("SELECT COUNT(*) FROM users WHERE DATE(first_seen) = DATE('now')") as cursor:
            new_users_today = (await cursor.fetchone())[0]
        # Активных за сегодня (last_activity)
        async with db.execute("SELECT COUNT(*) FROM users WHERE DATE(last_activity) = DATE('now')") as cursor:
            active_today = (await cursor.fetchone())[0]
        # Статистика из bot_stats
        async with db.execute("SELECT total_tests_created, total_custom_tests_created, total_revenue_stars FROM bot_stats WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            total_tests = row[0] if row else 0
            total_custom = row[1] if row else 0
            total_revenue = row[2] if row else 0
        # Количество пройденных тестов (attempts)
        async with db.execute("SELECT COUNT(*) FROM attempts") as cursor:
            total_attempts = (await cursor.fetchone())[0]
        return {
            "total_users": total_users,
            "new_users_today": new_users_today,
            "active_today": active_today,
            "total_tests": total_tests,
            "total_custom_tests": total_custom,
            "total_attempts": total_attempts,
            "total_revenue": total_revenue,
        }
