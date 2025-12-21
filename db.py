import os
from typing import Optional, List, Any, Dict
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Set it on Railway Variables.")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

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
        thumbnail_file_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS variants (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        price INTEGER NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    -- STOCK LINES (email:pass etc) for each variant
    CREATE TABLE IF NOT EXISTS stocks (
        id SERIAL PRIMARY KEY,
        variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
        payload TEXT NOT NULL,
        is_sold BOOLEAN NOT NULL DEFAULT FALSE,
        sold_at TIMESTAMP,
        sold_to BIGINT,
        order_item_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_stocks_variant_sold ON stocks(variant_id, is_sold);

    CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY,
        order_token TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        total INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'PAID',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS order_items (
        id SERIAL PRIMARY KEY,
        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
        variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE RESTRICT,
        qty INTEGER NOT NULL,
        unit_price INTEGER NOT NULL,
        delivered BOOLEAN NOT NULL DEFAULT FALSE,
        delivered_at TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS topups (
        id SERIAL PRIMARY KEY,
        topup_id TEXT UNIQUE NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        amount INTEGER NOT NULL,
        method TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
        proof_file_id TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        decided_at TIMESTAMP,
        admin_id BIGINT
    );
    CREATE INDEX IF NOT EXISTS idx_topups_status_created ON topups(status, created_at DESC);
    """

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

        # Seed categories
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

def fetch_all(sql: str, params: tuple = ()) -> List[dict]:
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

def fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None

def exec_sql(sql: str, params: tuple = ()) -> None:
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
