import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Set it in Railway Variables.")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def fetch_all(sql: str, params: tuple = ()):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

def fetch_one(sql: str, params: tuple = ()):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None

def exec_sql(sql: str, params: tuple = ()):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()

def ensure_schema():
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        balance INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE NOT NULL
    );

    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS variants (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        price INTEGER NOT NULL DEFAULT 0,
        thumbnail_file_id TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    -- Each stock row = one deliverable item (email:pass line)
    CREATE TABLE IF NOT EXISTS stock_items (
        id SERIAL PRIMARY KEY,
        variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
        payload TEXT NOT NULL,
        is_sold BOOLEAN NOT NULL DEFAULT FALSE,
        sold_at TIMESTAMP,
        purchase_token TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS purchases (
        id SERIAL PRIMARY KEY,
        purchase_token TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE RESTRICT,
        qty INTEGER NOT NULL,
        unit_price INTEGER NOT NULL,
        total_price INTEGER NOT NULL,
        delivered BOOLEAN NOT NULL DEFAULT FALSE,
        delivered_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS topups (
        id SERIAL PRIMARY KEY,
        topup_id TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        amount INTEGER NOT NULL,
        method TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        proof_file_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        decided_at TIMESTAMP,
        admin_id BIGINT
    );

    CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
    CREATE INDEX IF NOT EXISTS idx_variants_product ON variants(product_id);
    CREATE INDEX IF NOT EXISTS idx_stock_variant_sold ON stock_items(variant_id, is_sold);
    """

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            conn.commit()

        # seed categories if empty
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO categories(name) VALUES
                ('Entertainment Prems'),
                ('Educational Prems'),
                ('Editing Prems'),
                ('VPN Prems'),
                ('Other Prems')
                ON CONFLICT (name) DO NOTHING;
            """)
            conn.commit()
