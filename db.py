import aiosqlite

DB_PATH = "shop.db"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  balance INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  price INTEGER NOT NULL,
  stock_mode TEXT NOT NULL DEFAULT 'pool'
);

CREATE TABLE IF NOT EXISTS inventory_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id INTEGER NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'available',
  sold_to_user INTEGER,
  sold_at TEXT
);

CREATE TABLE IF NOT EXISTS topups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  created_at TEXT NOT NULL,
  proof_file_id TEXT,
  approved_amount INTEGER,
  approved_by INTEGER,
  approved_at TEXT
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  variant_id INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  amount INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'PAID',
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

async def ensure_user(db, user_id: int):
    await db.execute(
        "INSERT OR IGNORE INTO users(user_id, balance) VALUES (?, 0)",
        (user_id,)
    )

async def get_balance(db, user_id: int) -> int:
    cur = await db.execute(
        "SELECT balance FROM users WHERE user_id=?",
        (user_id,)
    )
    row = await cur.fetchone()
    return row[0] if row else 0

from datetime import datetime

async def create_topup(db, user_id: int) -> int:
    cur = await db.execute(
        "INSERT INTO topups(user_id,status,created_at) VALUES(?,?,?)",
        (user_id, "PENDING", datetime.utcnow().isoformat())
    )
    return cur.lastrowid

async def attach_topup_proof(db, topup_id: int, proof_file_id: str):
    await db.execute(
        "UPDATE topups SET proof_file_id=? WHERE id=? AND status='PENDING'",
        (proof_file_id, topup_id)
    )

async def get_topup(db, topup_id: int):
    cur = await db.execute(
        "SELECT id,user_id,status,proof_file_id FROM topups WHERE id=?",
        (topup_id,)
    )
    return await cur.fetchone()

async def approve_topup(db, topup_id: int, amount: int, admin_id: int) -> int:
    cur = await db.execute(
        "SELECT user_id,status FROM topups WHERE id=?",
        (topup_id,)
    )
    row = await cur.fetchone()
    if not row:
        return 0
    user_id, status = row
    if status != "PENDING":
        return 0

    await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    await db.execute(
        "UPDATE topups SET status='APPROVED', approved_amount=?, approved_by=?, approved_at=? WHERE id=?",
        (amount, admin_id, datetime.utcnow().isoformat(), topup_id)
    )
    return user_id

async def add_product(db, name: str) -> int:
    cur = await db.execute("INSERT INTO products(name) VALUES(?)", (name,))
    return cur.lastrowid

async def add_variant(db, product_id: int, name: str, price: int) -> int:
    cur = await db.execute(
        "INSERT INTO variants(product_id,name,price,stock_mode) VALUES(?,?,?,'pool')",
        (product_id, name, price)
    )
    return cur.lastrowid

async def add_stock_items(db, variant_id: int, items: list[str]) -> int:
    n = 0
    for payload in items:
        await db.execute(
            "INSERT INTO inventory_items(variant_id,payload,status) VALUES(?,?,'available')",
            (variant_id, payload)
        )
        n += 1
    return n

async def count_stock(db, variant_id: int) -> int:
    cur = await db.execute(
        "SELECT COUNT(*) FROM inventory_items WHERE variant_id=? AND status='available'",
        (variant_id,)
    )
    (n,) = await cur.fetchone()
    return n

async def list_variants_page(db, page: int, page_size: int):
    cur = await db.execute("""
        SELECT v.id, p.name, v.name, v.price
        FROM variants v
        JOIN products p ON p.id = v.product_id
        ORDER BY v.id ASC
        LIMIT ? OFFSET ?
    """, (page_size, (page-1)*page_size))
    return await cur.fetchall()

async def count_variants(db) -> int:
    cur = await db.execute("SELECT COUNT(*) FROM variants")
    (n,) = await cur.fetchone()
    return n

async def set_setting(db, key: str, value: str):
    await db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value)
    )

async def get_setting(db, key: str):
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row[0] if row else None

