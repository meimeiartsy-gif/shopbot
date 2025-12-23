# db.py
import os
import psycopg2
from contextlib import contextmanager
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def _conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def tx():
    """
    Transaction helper:
    with tx() as (conn, cur):
        ...
    """
    conn = _conn()
    try:
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_one(sql, params=None):
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()


def fetch_all(sql, params=None):
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()


def exec_sql(sql, params=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()


def set_setting(key: str, value: str):
    exec_sql(
        """
        INSERT INTO settings(key, value) VALUES(%s,%s)
        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
        """,
        (key, value),
    )


def get_setting(key: str):
    row = fetch_one("SELECT value FROM settings WHERE key=%s", (key,))
    return row["value"] if row else None


def ensure_user(user_id: int, username: str | None):
    exec_sql(
        """
        INSERT INTO users(user_id, username)
        VALUES(%s,%s)
        ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username
        """,
        (user_id, username),
    )


def ensure_schema():
    """
    Safe schema creator + auto-upgrader (ALTER TABLE if needed).
    Run on boot.
    """
    # base tables
    exec_sql(
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            joined_at TIMESTAMP NOT NULL DEFAULT NOW(),
            points INT NOT NULL DEFAULT 0,
            is_reseller BOOLEAN NOT NULL DEFAULT FALSE,
            balance INT NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS categories(
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS products(
            id SERIAL PRIMARY KEY,
            category_id INT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS variants(
            id SERIAL PRIMARY KEY,
            product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price INT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,

            -- delivery_type:
            -- 'text' = deliver text credentials
            -- 'file' = deliver telegram file_id document
            delivery_type TEXT NOT NULL DEFAULT 'text',
            delivery_text TEXT NOT NULL DEFAULT '',
            delivery_file_id TEXT,

            -- how many stocks to deliver per purchase (bulk bundles)
            bundle_qty INT NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS file_stocks(
            id SERIAL PRIMARY KEY,
            variant_id INT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
            is_sold BOOLEAN NOT NULL DEFAULT FALSE,
            sold_to BIGINT,
            sold_at TIMESTAMP,

            -- ONE of these can be used:
            file_id TEXT,
            delivery_text TEXT
        );

        CREATE TABLE IF NOT EXISTS purchases(
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            variant_id INT NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
            price INT NOT NULL,
            qty INT NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS reseller_applications(
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            username TEXT,
            full_name TEXT NOT NULL,
            contact TEXT NOT NULL,
            shop_link TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            decided_at TIMESTAMP,
            admin_id BIGINT
        );

        CREATE TABLE IF NOT EXISTS topup_requests(
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            username TEXT,
            amount INT NOT NULL,
            proof_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            decided_at TIMESTAMP,
            admin_id BIGINT
        );
        """
    )

    # default categories if empty
    c = fetch_one("SELECT COUNT(*) AS c FROM categories")
    if c and int(c["c"]) == 0:
        exec_sql(
            """
            INSERT INTO categories(name) VALUES
            ('Entertainment Prems'),
            ('Educational Prems'),
            ('Editing Prems'),
            ('VPN Prems'),
            ('Other Prems')
            ON CONFLICT DO NOTHING;
            """
        )
