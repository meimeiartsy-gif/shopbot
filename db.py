# db.py
import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

def _conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def fetch_one(sql, params=None):
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()

def fetch_all(sql, params=None):
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()

def exec_sql(sql, params=None):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()

def ensure_user(user_id: int, username: str | None):
    exec_sql(
        """
        INSERT INTO users(user_id, username)
        VALUES (%s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET username = EXCLUDED.username
        """,
        (user_id, username),
    )

def set_setting(k: str, v: str):
    exec_sql(
        """
        INSERT INTO settings(k,v) VALUES(%s,%s)
        ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v
        """,
        (k, v),
    )

def get_setting(k: str) -> str | None:
    row = fetch_one("SELECT v FROM settings WHERE k=%s", (k,))
    return row["v"] if row else None
