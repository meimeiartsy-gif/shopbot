import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Add it in Railway Variables.")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with connect() as db:
        cur = db.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance INTEGER DEFAULT 0
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS variants (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            telegram_file_id TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id SERIAL PRIMARY KEY,
            variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            sold_to_user BIGINT,
            sold_at TIMESTAMP
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            proof_file_id TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        db.commit()

def ensure_user(user_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users(user_id, balance) VALUES(%s, 0) ON CONFLICT (user_id) DO NOTHING",
            (user_id,)
        )
        db.commit()

def get_balance(user_id: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT balance FROM users WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0

def add_balance(user_id: int, amount: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
        db.commit()

def deduct_balance_if_enough(user_id: int, amount: int) -> bool:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET balance = balance - %s WHERE user_id=%s AND balance >= %s",
            (amount, user_id, amount)
        )
        ok = (cur.rowcount == 1)
        if ok:
            db.commit()
        else:
            db.rollback()
        return ok

def set_setting(key: str, value: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            INSERT INTO settings(key, value)
            VALUES(%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (key, value))
        db.commit()

def get_setting(key: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
        row = cur.fetchone()
        return row[0] if row else None