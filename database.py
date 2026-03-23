import json
import sqlite3
import aiosqlite

DB_NAME = "test_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tests (
                test_id TEXT PRIMARY KEY,
                creator_id INTEGER,
                questions_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id TEXT,
                user_id INTEGER,
                answers_json TEXT,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def save_user(user_id, username):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
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