import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing in Railway Variables")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def ensure_schema():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
                points INTEGER NOT NULL DEFAULT 0,
                is_reseller BOOLEAN NOT NULL DEFAULT FALSE,
                balance INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS variants (
                id SERIAL PRIMARY KEY,
                product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                price INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                delivery_type TEXT NOT NULL DEFAULT 'text',
                bundle_qty INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS file_stocks (
                id SERIAL PRIMARY KEY,
                variant_id INTEGER REFERENCES variants(id) ON DELETE CASCADE,
                file_id TEXT,
                delivery_text TEXT,
                is_sold BOOLEAN NOT NULL DEFAULT FALSE,
                sold_to BIGINT,
                sold_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                variant_id INTEGER REFERENCES variants(id) ON DELETE SET NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                unit_price INTEGER NOT NULL DEFAULT 0,
                total_price INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS reseller_applications (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                full_name TEXT,
                contact TEXT,
                shop_link TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                decided_at TIMESTAMP,
                admin_id BIGINT
            );

            CREATE TABLE IF NOT EXISTS topup_requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                proof_file_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                decided_at TIMESTAMP,
                admin_id BIGINT
            );
            """)

            # migrations
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS points INTEGER NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS joined_at TIMESTAMP NOT NULL DEFAULT NOW();")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_reseller BOOLEAN NOT NULL DEFAULT FALSE;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;")

            cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';")
            cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;")

            cur.execute("ALTER TABLE variants ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;")
            cur.execute("ALTER TABLE variants ADD COLUMN IF NOT EXISTS delivery_type TEXT NOT NULL DEFAULT 'text';")
            cur.execute("ALTER TABLE variants ADD COLUMN IF NOT EXISTS bundle_qty INTEGER NOT NULL DEFAULT 1;")
            cur.execute("ALTER TABLE variants ADD COLUMN IF NOT EXISTS price INTEGER NOT NULL DEFAULT 0;")

            cur.execute("ALTER TABLE file_stocks ADD COLUMN IF NOT EXISTS delivery_text TEXT;")
            cur.execute("ALTER TABLE file_stocks ADD COLUMN IF NOT EXISTS file_id TEXT;")
            try:
                cur.execute("ALTER TABLE file_stocks ALTER COLUMN file_id DROP NOT NULL;")
            except Exception:
                conn.rollback()
                conn.commit()

            cur.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 1;")
            cur.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS unit_price INTEGER NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS total_price INTEGER NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();")

        conn.commit()

def ensure_user(user_id: int, username: str | None):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE users SET username=%s WHERE user_id=%s", (username, user_id))
            else:
                cur.execute("INSERT INTO users(user_id, username) VALUES(%s,%s)", (user_id, username))
        conn.commit()

def fetch_one(sql: str, params=None):
    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()

def fetch_all(sql: str, params=None):
    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()

def exec_sql(sql: str, params=None):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()

def set_setting(key: str, value: str):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO settings(key,value) VALUES(%s,%s)
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value
            """, (key, value))
        conn.commit()

def get_setting(key: str) -> str | None:
    row = fetch_one("SELECT value FROM settings WHERE key=%s", (key,))
    return row["value"] if row else None

def purchase_variant(user_id: int, variant_id: int, qty_units: int):
    """
    qty_units = how many units user buys.
    Need = qty_units * bundle_qty stock rows.
    Deducts balance + marks stocks sold ONLY inside transaction.
    Points: 1 point per purchase order (not per qty).
    """
    with connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT user_id, balance FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
            u = cur.fetchone()
            if not u:
                raise RuntimeError("User not found")

            cur.execute("""
                SELECT v.id, v.name, v.price, v.delivery_type, v.bundle_qty,
                       p.name AS product_name
                FROM variants v
                JOIN products p ON p.id=v.product_id
                WHERE v.id=%s AND v.is_active=TRUE
            """, (variant_id,))
            v = cur.fetchone()
            if not v:
                raise RuntimeError("Variant not found")

            bundle_qty = int(v["bundle_qty"])
            need = qty_units * bundle_qty
            unit_price = int(v["price"])
            total = unit_price * qty_units

            if u["balance"] < total:
                return {"ok": False, "error": "NOT_ENOUGH_BALANCE", "need": total, "have": u["balance"]}

            cur.execute("""
                SELECT id, file_id, delivery_text
                FROM file_stocks
                WHERE variant_id=%s AND is_sold=FALSE
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            """, (variant_id, need))
            stocks = cur.fetchall()

            if len(stocks) < need:
                return {"ok": False, "error": "NOT_ENOUGH_STOCK", "have": len(stocks), "need": need}

            stock_ids = [s["id"] for s in stocks]

            cur.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (total, user_id))

            cur.execute("""
                UPDATE file_stocks
                SET is_sold=TRUE, sold_to=%s, sold_at=NOW()
                WHERE id = ANY(%s)
            """, (user_id, stock_ids))

            cur.execute("""
                INSERT INTO purchases(user_id, variant_id, quantity, unit_price, total_price)
                VALUES(%s,%s,%s,%s,%s)
            """, (user_id, variant_id, qty_units, unit_price, total))

            # 1 order = 1 point
            cur.execute("""
    UPDATE users
    SET
        points = CASE
            WHEN points_updated_at < NOW() - INTERVAL '25 days'
                THEN 1
            ELSE points + 1
        END,
        points_updated_at = NOW()
    WHERE user_id = %s
""", (user_id,))

        conn.commit()

    return {"ok": True, "variant": v, "stocks": stocks, "qty_units": qty_units, "total": total}

