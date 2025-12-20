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
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount INTEGER NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            proof_file_id TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

        db.commit()

# ================= USERS =================
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

# ================= SETTINGS =================
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

# ================= TOPUPS =================
def create_topup(user_id: int, amount: int, method: str) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO topups(user_id, amount, method) VALUES(%s,%s,%s) RETURNING id",
            (user_id, amount, method)
        )
        tid = cur.fetchone()[0]
        db.commit()
        return int(tid)

def attach_topup_proof(topup_id: int, proof_file_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute("UPDATE topups SET proof_file_id=%s WHERE id=%s", (proof_file_id, topup_id))
        db.commit()

def get_topup(topup_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, user_id, amount, method, status, proof_file_id FROM topups WHERE id=%s",
            (topup_id,)
        )
        return cur.fetchone()

def approve_topup(topup_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT user_id, amount, status FROM topups WHERE id=%s", (topup_id,))
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
