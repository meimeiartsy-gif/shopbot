import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL)

# ================= INIT =================
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
            product_id INTEGER REFERENCES products(id),
            name TEXT,
            price INTEGER,
            telegram_file_id TEXT
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            id SERIAL PRIMARY KEY,
            variant_id INTEGER,
            payload TEXT,
            status TEXT DEFAULT 'available'
        );
        """)

        db.commit()

# ================= USERS =================
def ensure_user(user_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users(user_id) VALUES(%s) ON CONFLICT DO NOTHING",
            (user_id,)
        )
        db.commit()

def get_balance(user_id: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT balance FROM users WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else 0

def add_balance(user_id: int, amount: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE users SET balance = balance + %s WHERE user_id=%s",
            (amount, user_id)
        )
        db.commit()

# ================= PRODUCTS =================
def add_product(name: str) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO products(name) VALUES(%s) RETURNING id",
            (name,)
        )
        pid = cur.fetchone()[0]
        db.commit()
        return pid

def list_products():
    with connect() as db:
        cur = db.cursor()
        cur.execute("SELECT id, name FROM products ORDER BY id")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

# ================= VARIANTS =================
def add_variant(product_id: int, name: str, price: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO variants(product_id, name, price)
            VALUES(%s, %s, %s) RETURNING id
            """,
            (product_id, name, price)
        )
        vid = cur.fetchone()[0]
        db.commit()
        return vid

def list_variants(product_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, name, price, telegram_file_id
            FROM variants WHERE product_id=%s
            """,
            (product_id,)
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "price": r[2],
                "file_id": r[3]
            }
            for r in cur.fetchall()
        ]

def set_variant_file(variant_id: int, file_id: str):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE variants SET telegram_file_id=%s WHERE id=%s",
            (file_id, variant_id)
        )
        db.commit()

def get_variant_file(variant_id: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT price, telegram_file_id FROM variants WHERE id=%s",
            (variant_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "price": row[0],
            "file_id": row[1]
        }
        # ================= STOCK =================
def add_stock_items(variant_id: int, qty: int):
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, payload FROM inventory_items
            WHERE variant_id=%s AND status='available'
            LIMIT %s
            """,
            (variant_id, qty)
        )
        rows = cur.fetchall()

        for r in rows:
            cur.execute(
                "UPDATE inventory_items SET status='sold' WHERE id=%s",
                (r[0],)
            )

        db.commit()
        return [r[1] for r in rows]

def count_stock(variant_id: int) -> int:
    with connect() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM inventory_items WHERE variant_id=%s AND status='available'",
            (variant_id,)
        )
        return cur.fetchone()[0]