import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing in Railway Variables")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def ensure_schema():
    ddl = """
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
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT TRUE
    );

    CREATE TABLE IF NOT EXISTS variants (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        price INTEGER NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        delivery_type TEXT NOT NULL DEFAULT 'text', -- 'file' or 'text'
        delivery_file_id TEXT,
        delivery_text TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS file_stocks (
        id SERIAL PRIMARY KEY,
        variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
        file_id TEXT,
        delivery_text TEXT,
        is_sold BOOLEAN NOT NULL DEFAULT FALSE,
        sold_to BIGINT,
        sold_at TIMESTAMP,
        CONSTRAINT file_stocks_payload_chk CHECK (
          (file_id IS NOT NULL AND length(file_id) > 0)
          OR
          (delivery_text IS NOT NULL AND length(delivery_text) > 0)
        )
    );

    CREATE TABLE IF NOT EXISTS purchases (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE RESTRICT,
        qty INTEGER NOT NULL DEFAULT 1,
        price_each INTEGER NOT NULL,
        total_price INTEGER NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS reseller_applications (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        username TEXT,
        full_name TEXT NOT NULL,
        contact TEXT NOT NULL,
        shop_link TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        decided_at TIMESTAMP,
        admin_id BIGINT
    );
    """
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(ddl)
            db.commit()

        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO categories(name) VALUES
                ('Entertainment Prems'),
                ('Educational Prems'),
                ('Editing Prems'),
                ('VPN Prems'),
                ('Other Prems')
                ON CONFLICT (name) DO NOTHING;
            """)
            db.commit()

def fetch_all(sql, params=()):
    with connect() as db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

def fetch_one(sql, params=()):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None

def exec_sql(sql, params=()):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(sql, params)
            db.commit()

def exec_sql_returning(sql, params=()):
    with connect() as db:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
            db.commit()
            return rows

def set_setting(key: str, value: str):
    exec_sql("""
        INSERT INTO settings(key,value) VALUES(%s,%s)
        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
    """, (key, value))

def get_setting(key: str):
    r = fetch_one("SELECT value FROM settings WHERE key=%s", (key,))
    return r["value"] if r else None

def ensure_user(user_id: int, username: str | None):
    exec_sql("""
        INSERT INTO users(user_id, username) VALUES(%s,%s)
        ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username
    """, (user_id, username))
