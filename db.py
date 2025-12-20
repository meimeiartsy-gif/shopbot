import os
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL")

def now_utc():
    return datetime.now(timezone.utc)

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing.")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Users
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                balance INT NOT NULL DEFAULT 0,
                is_banned BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)

            # Settings: editable texts + QR file_ids + captions
            cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """)

            # Products
            cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                product_id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price INT NOT NULL DEFAULT 0,
                stock INT NOT NULL DEFAULT 0,
                delivery_file_id TEXT,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)

            # Purchases
            cur.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                purchase_id SERIAL PRIMARY KEY,
                tx_id TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                product_id INT NOT NULL REFERENCES products(product_id),
                qty INT NOT NULL DEFAULT 1,
                total INT NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'DELIVERED',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)

            # Topups
            cur.execute("""
            CREATE TABLE IF NOT EXISTS topups (
                topup_db_id SERIAL PRIMARY KEY,
                topup_id TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                amount INT NOT NULL,
                method TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                proof_file_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                approved_by BIGINT,
                approved_at TIMESTAMPTZ
            );
            """)

            conn.commit()

    seed_default_settings()

def seed_default_settings():
    defaults = {
        "welcome_text": "🌙 Luna’s Prem Shop\n──────────────\n💳 Balance: ₱{balance}\n\nChoose an option:",
        "no_products_text": "No products yet.",
        "product_list_header": "📦 *Product List*",
        "add_balance_intro": "➕ *Add Balance*\nChoose amount then choose payment method.\nAfter payment, just send your screenshot here.\n⏳ Waiting for admin approval.\nAdmin: @{admin_username}",
        "topup_instructions": "✅ After payment, send screenshot proof here.\n⏳ Waiting for admin approval.\nAdmin: @{admin_username}",
        "gcash_caption": "✅ *GCash Payment*\n1) Scan QR\n2) Pay exact amount\n3) Send screenshot proof here\n\n⏳ Waiting for admin approval.\nAdmin: @{admin_username}",
        "gotyme_caption": "✅ *GoTyme Payment*\n1) Scan QR\n2) Pay exact amount\n3) Send screenshot proof here\n\n⏳ Waiting for admin approval.\nAdmin: @{admin_username}",
        "gcash_qr_file_id": "",
        "gotyme_qr_file_id": "",
        "chat_admin_text": "💬 Message admin for help: @{admin_username}",
        "announcement_prefix": "📢 *Announcement*\n\n",
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            for k, v in defaults.items():
                cur.execute("""
                INSERT INTO settings(key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO NOTHING;
                """, (k, v))
            conn.commit()

def upsert_user(user_id: int, username: str | None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO users(user_id, username)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username;
            """, (user_id, username))
            conn.commit()

def get_user(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id=%s;", (user_id,))
            return cur.fetchone()

def set_balance(user_id: int, new_balance: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET balance=%s WHERE user_id=%s;", (new_balance, user_id))
            conn.commit()

def add_balance(user_id: int, amount: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s;", (amount, user_id))
            conn.commit()

def get_setting(key: str) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key=%s;", (key,))
            row = cur.fetchone()
            return row["value"] if row else ""

def set_setting(key: str, value: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO settings(key, value) VALUES (%s, %s)
            ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value;
            """, (key, value))
            conn.commit()

def list_products(active_only=True):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if active_only:
                cur.execute("SELECT * FROM products WHERE active=TRUE ORDER BY product_id ASC;")
            else:
                cur.execute("SELECT * FROM products ORDER BY product_id ASC;")
            return cur.fetchall()

def get_product(pid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products WHERE product_id=%s;", (pid,))
            return cur.fetchone()

def create_product(name: str, description: str, price: int, stock: int, delivery_file_id: str | None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO products(name, description, price, stock, delivery_file_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING product_id;
            """, (name, description, price, stock, delivery_file_id))
            pid = cur.fetchone()["product_id"]
            conn.commit()
            return pid

def update_product(pid: int, **fields):
    allowed = {"name", "description", "price", "stock", "delivery_file_id", "active"}
    keys = [k for k in fields.keys() if k in allowed]
    if not keys:
        return
    sets = ", ".join([f"{k}=%s" for k in keys])
    vals = [fields[k] for k in keys] + [pid]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE products SET {sets} WHERE product_id=%s;", vals)
            conn.commit()

def delete_product(pid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE product_id=%s;", (pid,))
            conn.commit()

def create_topup(topup_id: str, user_id: int, amount: int, method: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO topups(topup_id, user_id, amount, method)
            VALUES (%s, %s, %s, %s)
            RETURNING topup_db_id;
            """, (topup_id, user_id, amount, method))
            tid = cur.fetchone()["topup_db_id"]
            conn.commit()
            return tid

def attach_topup_proof(user_id: int, proof_file_id: str):
    # attach proof to latest pending topup for that user
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT topup_db_id FROM topups
            WHERE user_id=%s AND status='PENDING'
            ORDER BY created_at DESC
            LIMIT 1;
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            topup_db_id = row["topup_db_id"]
            cur.execute("UPDATE topups SET proof_file_id=%s WHERE topup_db_id=%s;", (proof_file_id, topup_db_id))
            conn.commit()
            return topup_db_id

def get_topup_by_dbid(topup_db_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM topups WHERE topup_db_id=%s;", (topup_db_id,))
            return cur.fetchone()

def approve_topup(topup_db_id: int, admin_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM topups WHERE topup_db_id=%s;", (topup_db_id,))
            t = cur.fetchone()
            if not t or t["status"] != "PENDING":
                return None

            # add balance
            cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s;", (t["amount"], t["user_id"]))

            cur.execute("""
            UPDATE topups
            SET status='APPROVED', approved_by=%s, approved_at=NOW()
            WHERE topup_db_id=%s;
            """, (admin_id, topup_db_id))

            conn.commit()
            return t

def reject_topup(topup_db_id: int, admin_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM topups WHERE topup_db_id=%s;", (topup_db_id,))
            t = cur.fetchone()
            if not t or t["status"] != "PENDING":
                return None

            cur.execute("""
            UPDATE topups
            SET status='REJECTED', approved_by=%s, approved_at=NOW()
            WHERE topup_db_id=%s;
            """, (admin_id, topup_db_id))

            conn.commit()
            return t

def list_pending_topups(limit=50):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            SELECT t.*, u.username
            FROM topups t
            LEFT JOIN users u ON u.user_id=t.user_id
            WHERE t.status='PENDING'
            ORDER BY t.created_at ASC
            LIMIT %s;
            """, (limit,))
            return cur.fetchall()

def list_users(limit=50000):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE is_banned=FALSE;")
            return cur.fetchall()
