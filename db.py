import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing (Railway → Variables)")

def connect():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# =========================
# INIT + AUTO MIGRATION
# =========================
def init_db():
    with connect() as db:
        cur = db.cursor()

        # USERS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0
        );
        """)

        # PRODUCTS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT ''
        );
        """)

        # VARIANTS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS variants (
            id SERIAL PRIMARY KEY,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            telegram_file_id TEXT
        );
        """)

        # STOCK / ACCOUNTS
        cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id SERIAL PRIMARY KEY,
            variant_id INTEGER REFERENCES variants(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            sold_to BIGINT,
            sold_at TIMESTAMP
        );
        """)

        # TOPUPS (IMPORTANT)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id SERIAL PRIMARY KEY,
            topup_id TEXT UNIQUE,
            user_id BIGINT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            method TEXT NOT NULL DEFAULT 'gcash',
            status TEXT NOT NULL DEFAULT 'PENDING',
            proof_file_id TEXT,
            admin_id BIGINT,
            decided_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

        # SETTINGS (TEXT, QR, ANNOUNCEMENTS)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        db.commit()

    migrate_db()

# =========================
# AUTO MIGRATION (NO SHELL)
# =========================
def migrate_db():
    with connect() as db:
        cur = db.cursor()

        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS topup_id TEXT;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS amount INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS method TEXT DEFAULT 'gcash';")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS proof_file_id TEXT;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS admin_id BIGINT;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP;")

        cur.execute("""
        UPDATE topups
        SET topup_id = 'shopnluna:TU' || SUBSTRING(MD5(RANDOM()::text) FROM 1 FOR 10)
        WHERE topup_id IS NULL;
        """)

        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS topups_topup_id_uq
        ON topups(topup_id);
        """)

        db.commit()

# =========================
# USERS
# =========================
def ensure_user(user_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (user_id, balance) VALUES (%s, 0) ON CONFLICT DO NOTHING",
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
        cur.execute(
            "UPDATE users SET balance = balance + %s WHERE user_id=%s",
            (amount, user_id)
        )
        db.commit()

# =========================
# SETTINGS (TEXT / QR)
# =========================
def set_setting(key: str, value: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("""
            INSERT INTO settings (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value
        """, (key, value))
        db.commit()

def get_setting(key: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
        row = cur.fetchone()
        return row[0] if row else None