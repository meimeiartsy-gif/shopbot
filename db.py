import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing (Railway Variables)")

def connect():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def fetchone(sql: str, params=()):
    with connect() as db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

def fetchall(sql: str, params=()):
    with connect() as db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

def exec(sql: str, params=()):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(sql, params)
        db.commit()

def init_db():
    # Create tables
    with connect() as db:
        cur = db.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,               -- "Category | Product"
            description TEXT NOT NULL DEFAULT ''
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
            sold_to BIGINT,
            sold_at TIMESTAMP
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id SERIAL PRIMARY KEY,
            topup_id TEXT UNIQUE,
            user_id BIGINT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            method TEXT NOT NULL DEFAULT 'gcash',
            status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/APPROVED/REJECTED
            proof_file_id TEXT,
            admin_id BIGINT,
            decided_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        db.commit()

    migrate_db()

def migrate_db():
    # Safe migration (works even if tables already exist)
    with connect() as db:
        cur = db.cursor()
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';")

        cur.execute("ALTER TABLE variants ADD COLUMN IF NOT EXISTS telegram_file_id TEXT;")

        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS topup_id TEXT;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS amount INTEGER NOT NULL DEFAULT 0;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS method TEXT NOT NULL DEFAULT 'gcash';")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS proof_file_id TEXT;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS admin_id BIGINT;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP;")
        cur.execute("ALTER TABLE topups ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")

        cur.execute("""
        UPDATE topups
        SET topup_id = 'shopnluna:TU' || SUBSTRING(MD5(RANDOM()::text) FROM 1 FOR 10)
        WHERE topup_id IS NULL;
        """)
        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS topups_topup_id_uq ON topups(topup_id);
        """)

        db.commit()

# ---------------- USERS ----------------
def ensure_user(user_id: int):
    exec("INSERT INTO users(user_id,balance) VALUES(%s,0) ON CONFLICT (user_id) DO NOTHING", (user_id,))

def get_balance(user_id: int) -> int:
    row = fetchone("SELECT balance FROM users WHERE user_id=%s", (user_id,))
    return int(row["balance"]) if row else 0

def add_balance(user_id: int, amount: int):
    exec("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))

def deduct_balance_if_enough(user_id: int, amount: int) -> bool:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET balance = balance - %s WHERE user_id=%s AND balance >= %s",
            (amount, user_id, amount)
        )
        ok = (cur.rowcount == 1)
        db.commit()
        return ok

# ---------------- SETTINGS ----------------
def set_setting(key: str, value: str):
    exec("""
    INSERT INTO settings(key,value) VALUES(%s,%s)
    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
    """, (key, value))

def get_setting(key: str):
    row = fetchone("SELECT value FROM settings WHERE key=%s", (key,))
    return row["value"] if row else None

# ---------------- PRODUCTS ----------------
def add_product(name: str, description: str = "") -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute("INSERT INTO products(name,description) VALUES(%s,%s) RETURNING id", (name, description))
        pid = cur.fetchone()[0]
        db.commit()
        return int(pid)

def list_products():
    return fetchall("SELECT id,name,description FROM products ORDER BY id ASC")

def get_product(product_id: int):
    return fetchone("SELECT id,name,description FROM products WHERE id=%s", (product_id,))

def add_variant(product_id: int, name: str, price: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO variants(product_id,name,price) VALUES(%s,%s,%s) RETURNING id",
            (product_id, name, price)
        )
        vid = cur.fetchone()[0]
        db.commit()
        return int(vid)

def list_variants(product_id: int):
    return fetchall("""
        SELECT id,name,price,telegram_file_id
        FROM variants
        WHERE product_id=%s
        ORDER BY id ASC
    """, (product_id,))

def set_variant_file(variant_id: int, telegram_file_id: str):
    exec("UPDATE variants SET telegram_file_id=%s WHERE id=%s", (telegram_file_id, variant_id))

# ---------------- STOCK ----------------
def add_stock_items(variant_id: int, items: list[str]) -> int:
    with connect() as db:
        cur = db.cursor()
        for it in items:
            cur.execute(
                "INSERT INTO inventory_items(variant_id,payload,status) VALUES(%s,%s,'available')",
                (variant_id, it)
            )
        db.commit()
        return len(items)

def count_stock(variant_id: int) -> int:
    row = fetchone("SELECT COUNT(*) AS c FROM inventory_items WHERE variant_id=%s AND status='available'", (variant_id,))
    return int(row["c"]) if row else 0

def take_stock_items(variant_id: int, qty: int, buyer_id: int) -> list[str] | None:
    with connect() as db:
        cur = db.cursor()
        cur.execute("BEGIN;")
        cur.execute("""
            SELECT id,payload
            FROM inventory_items
            WHERE variant_id=%s AND status='available'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        """, (variant_id, qty))
        rows = cur.fetchall()
        if len(rows) != qty:
            db.rollback()
            return None

        now = "NOW()"
        ids = [r[0] for r in rows]
        cur.execute("""
            UPDATE inventory_items
            SET status='sold', sold_to=%s, sold_at=NOW()
            WHERE id = ANY(%s)
        """, (buyer_id, ids))

        db.commit()
        return [r[1] for r in rows]

# ---------------- TOPUPS ----------------
def create_topup(topup_id: str, user_id: int, amount: int, method: str):
    exec("""
    INSERT INTO topups(topup_id,user_id,amount,method,status)
    VALUES(%s,%s,%s,%s,'PENDING')
    """, (topup_id, user_id, amount, method))

def attach_topup_proof(topup_id: str, proof_file_id: str):
    exec("""
    UPDATE topups SET proof_file_id=%s
    WHERE topup_id=%s AND status='PENDING'
    """, (proof_file_id, topup_id))

def get_topup(topup_id: str):
    return fetchone("SELECT * FROM topups WHERE topup_id=%s", (topup_id,))

def list_pending_topups(limit: int = 20):
    return fetchall("""
        SELECT topup_id,user_id,amount,method,created_at
        FROM topups
        WHERE status='PENDING' AND proof_file_id IS NOT NULL
        ORDER BY created_at ASC
        LIMIT %s
    """, (limit,))

def approve_topup(topup_id: str, admin_id: int) -> int | None:
    with connect() as db:
        cur = db.cursor()
        cur.execute("BEGIN;")
        cur.execute("SELECT user_id, amount, status FROM topups WHERE topup_id=%s FOR UPDATE", (topup_id,))
        row = cur.fetchone()
        if not row:
            db.rollback()
            return None
        user_id, amount, status = row
        if status != "PENDING":
            db.rollback()
            return None

        cur.execute("UPDATE topups SET status='APPROVED', admin_id=%s, decided_at=NOW() WHERE topup_id=%s",
                    (admin_id, topup_id))
        cur.execute("UPDATE users SET balance = balance + %s WHERE user_id=%s", (amount, user_id))
        db.commit()
        return int(user_id)

def reject_topup(topup_id: str, admin_id: int) -> int | None:
    with connect() as db:
        cur = db.cursor()
        cur.execute("BEGIN;")
        cur.execute("SELECT user_id, status FROM topups WHERE topup_id=%s FOR UPDATE", (topup_id,))
        row = cur.fetchone()
        if not row:
            db.rollback()
            return None
        user_id, status = row
        if status != "PENDING":
            db.rollback()
            return None

        cur.execute("UPDATE topups SET status='REJECTED', admin_id=%s, decided_at=NOW() WHERE topup_id=%s",
                    (admin_id, topup_id))
        db.commit()
        return int(user_id)
