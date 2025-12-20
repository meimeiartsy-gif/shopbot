import os
import psycopg2
from psycopg2.extras import RealDictCursor

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
            amount INTEGER NOT NULL DEFAULT 0,
            method TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDING',
            proof_file_id TEXT,
            created_at TIMESTAMP DEFAULT NOW()
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
        cur.execute(
            "UPDATE users SET balance = balance + %s WHERE user_id=%s",
            (amount, user_id)
        )
        db.commit()


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


# ---------- TOPUPS ----------
def create_topup(user_id: int, amount: int, method: str) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO topups(user_id, amount, method, status) VALUES(%s,%s,%s,'PENDING') RETURNING id",
            (user_id, amount, method)
        )
        topup_id = cur.fetchone()[0]
        db.commit()
        return int(topup_id)


def attach_topup_proof(topup_id: int, proof_file_id: str) -> bool:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE topups SET proof_file_id=%s WHERE id=%s AND status='PENDING'",
            (proof_file_id, topup_id)
        )
        ok = (cur.rowcount == 1)
        db.commit()
        return ok


def get_topup(topup_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, user_id, amount, method, status, proof_file_id, created_at FROM topups WHERE id=%s",
            (topup_id,)
        )
        return cur.fetchone()


def approve_topup(topup_id: int):
    """
    Marks topup APPROVED and credits the user with stored amount.
    Returns (user_id, amount) if success else None.
    """
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT user_id, amount, status FROM topups WHERE id=%s",
            (topup_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        user_id, amount, status = row
        if status != "PENDING":
            return None

        cur.execute("UPDATE topups SET status='APPROVED' WHERE id=%s", (topup_id,))
        cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
        db.commit()
        return int(user_id), int(amount)


def reject_topup(topup_id: int) -> bool:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE topups SET status='REJECTED' WHERE id=%s AND status='PENDING'",
            (topup_id,)
        )
        ok = (cur.rowcount == 1)
        db.commit()
        return ok


def list_pending_topups(limit: int = 10):
    with connect() as db:
        cur = db.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, user_id, amount, method, status, created_at
            FROM topups
            WHERE status='PENDING'
            ORDER BY id DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()
