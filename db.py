import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Add it in Railway Variables.")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS topups (
                id SERIAL PRIMARY KEY,
                topup_id TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                method TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
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

            conn.commit()

def upsert_user(user_id: int, username: str | None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (user_id, username)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
            """, (user_id, username))
            conn.commit()

def get_balance(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM users WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            return int(row[0]) if row else 0

def add_balance(user_id: int, amount: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET balance = balance + %s WHERE user_id=%s
            """, (amount, user_id))
            conn.commit()

def set_setting(key: str, value: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO settings (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
            """, (key, value))
            conn.commit()

def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
            row = cur.fetchone()
            return row[0] if row else None

def create_topup(topup_id: str, user_id: int, amount: int, method: str, proof_file_id: str | None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO topups (topup_id, user_id, amount, method, proof_file_id)
                VALUES (%s, %s, %s, %s, %s)
            """, (topup_id, user_id, amount, method, proof_file_id))
            conn.commit()

def list_pending_topups(limit: int = 20):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM topups WHERE status='pending'
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()

def approve_topup(topup_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            # get info
            cur.execute("SELECT user_id, amount FROM topups WHERE topup_id=%s AND status='pending'", (topup_id,))
            row = cur.fetchone()
            if not row:
                return False
            user_id, amount = row[0], row[1]

            # approve
            cur.execute("UPDATE topups SET status='approved' WHERE topup_id=%s", (topup_id,))
            cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
            conn.commit()
            return True

def reject_topup(topup_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE topups SET status='rejected' WHERE topup_id=%s AND status='pending'", (topup_id,))
            conn.commit()
            return cur.rowcount > 0
