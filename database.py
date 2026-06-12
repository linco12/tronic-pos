import os
import sqlite3

DATABASE = os.environ.get(
    'DATABASE_PATH',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tronic_pos.db')
)


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # ── Core auth tables ──────────────────────────────────────────────────────
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email        TEXT    NOT NULL UNIQUE,
            name         TEXT    NOT NULL DEFAULT '',
            password_hash TEXT   NOT NULL,
            role         TEXT    NOT NULL DEFAULT 'owner',
            is_active    INTEGER NOT NULL DEFAULT 1,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shops (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id               INTEGER NOT NULL REFERENCES users(id),
            name                   TEXT    NOT NULL DEFAULT 'My Shop',
            tagline                TEXT    DEFAULT '',
            address                TEXT    DEFAULT '',
            phone                  TEXT    DEFAULT '',
            email                  TEXT    DEFAULT '',
            zimra_tin              TEXT    DEFAULT '',
            vat_registered         INTEGER DEFAULT 0,
            vat_number             TEXT    DEFAULT '',
            vat_rate               REAL    DEFAULT 15.0,
            currency               TEXT    DEFAULT 'USD',
            ecocash_merchant_code  TEXT    DEFAULT '',
            ecocash_merchant_pin   TEXT    DEFAULT '',
            ecocash_mode           TEXT    DEFAULT 'test',
            receipt_footer         TEXT    DEFAULT 'Thank you for shopping with us!',
            created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    # ── Per-shop data tables ───────────────────────────────────────────────────
    c.executescript('''
        CREATE TABLE IF NOT EXISTS categories (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id  INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            name     TEXT    NOT NULL,
            description TEXT  DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS suppliers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id        INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            name           TEXT    NOT NULL,
            contact_person TEXT    DEFAULT '',
            phone          TEXT    DEFAULT '',
            email          TEXT    DEFAULT '',
            address        TEXT    DEFAULT '',
            payment_terms  TEXT    DEFAULT 'Cash',
            notes          TEXT    DEFAULT '',
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS products (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id         INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            name            TEXT    NOT NULL,
            description     TEXT    DEFAULT '',
            sku             TEXT    DEFAULT '',
            barcode         TEXT    DEFAULT '',
            category_id     INTEGER REFERENCES categories(id),
            supplier_id     INTEGER REFERENCES suppliers(id),
            cost_price      REAL    NOT NULL DEFAULT 0,
            selling_price   REAL    NOT NULL DEFAULT 0,
            stock_quantity  REAL    NOT NULL DEFAULT 0,
            min_stock_level REAL    DEFAULT 5,
            unit            TEXT    DEFAULT 'each',
            tax_type        TEXT    DEFAULT 'standard',
            is_active       INTEGER DEFAULT 1,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(shop_id, sku)
        );

        CREATE TABLE IF NOT EXISTS customers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id         INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            name            TEXT    DEFAULT '',
            phone           TEXT    DEFAULT '',
            email           TEXT    DEFAULT '',
            ecocash_number  TEXT    DEFAULT '',
            address         TEXT    DEFAULT '',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sales (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id         INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            reference       TEXT    UNIQUE,
            customer_id     INTEGER REFERENCES customers(id),
            subtotal        REAL    DEFAULT 0,
            tax_amount      REAL    DEFAULT 0,
            discount_amount REAL    DEFAULT 0,
            total           REAL    DEFAULT 0,
            payment_method  TEXT    DEFAULT 'cash',
            amount_paid     REAL    DEFAULT 0,
            change_given    REAL    DEFAULT 0,
            status          TEXT    DEFAULT 'completed',
            cashier         TEXT    DEFAULT 'Admin',
            notes           TEXT    DEFAULT '',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sale_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id      INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            product_id   INTEGER REFERENCES products(id),
            product_name TEXT    DEFAULT '',
            quantity     REAL    DEFAULT 1,
            unit_price   REAL    DEFAULT 0,
            cost_price   REAL    DEFAULT 0,
            tax_type     TEXT    DEFAULT 'standard',
            tax_rate     REAL    DEFAULT 0,
            tax_amount   REAL    DEFAULT 0,
            discount     REAL    DEFAULT 0,
            total        REAL    DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id         INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            method          TEXT    DEFAULT 'cash',
            amount          REAL    DEFAULT 0,
            ecocash_number  TEXT    DEFAULT '',
            transaction_ref TEXT    DEFAULT '',
            status          TEXT    DEFAULT 'completed',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ecocash_transactions (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id            INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            sale_id            INTEGER REFERENCES sales(id),
            merchant_reference TEXT    UNIQUE,
            customer_msisdn    TEXT    DEFAULT '',
            amount             REAL    DEFAULT 0,
            status             TEXT    DEFAULT 'pending',
            ecocash_reference  TEXT    DEFAULT '',
            initiated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at       TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id      INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            category     TEXT    NOT NULL,
            description  TEXT    DEFAULT '',
            amount       REAL    NOT NULL,
            expense_date DATE,
            receipt_ref  TEXT    DEFAULT '',
            notes        TEXT    DEFAULT '',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS purchase_orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id       INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            reference     TEXT    UNIQUE,
            supplier_id   INTEGER REFERENCES suppliers(id),
            status        TEXT    DEFAULT 'pending',
            subtotal      REAL    DEFAULT 0,
            tax_amount    REAL    DEFAULT 0,
            total         REAL    DEFAULT 0,
            notes         TEXT    DEFAULT '',
            expected_date DATE,
            received_date DATE,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS purchase_order_items (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            po_id             INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
            product_id        INTEGER REFERENCES products(id),
            product_name      TEXT    DEFAULT '',
            quantity          REAL    DEFAULT 1,
            unit_cost         REAL    DEFAULT 0,
            received_quantity REAL    DEFAULT 0,
            total             REAL    DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS stock_adjustments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id         INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            product_id      INTEGER REFERENCES products(id),
            product_name    TEXT    DEFAULT '',
            adjustment_type TEXT    DEFAULT '',
            quantity        REAL    DEFAULT 0,
            notes           TEXT    DEFAULT '',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    conn.commit()
    conn.close()


def migrate_db():
    """
    Run once to upgrade an older single-tenant DB to multi-tenant schema.
    Safe to call on a fresh DB (all CREATE TABLE IF NOT EXISTS).
    """
    init_db()


def seed_default_categories(conn, shop_id):
    """Insert default product categories for a new shop."""
    cats = [
        ('General', 'General merchandise'),
        ('Food & Beverages', 'Edible products and drinks'),
        ('Electronics', 'Electronic devices and accessories'),
        ('Clothing & Apparel', 'Clothes and fashion'),
        ('Hardware & Tools', 'Hardware and tools'),
        ('Medicines', 'Pharmaceutical products (zero-rated)'),
        ('Groceries', 'Basic foodstuffs (zero-rated)'),
    ]
    for name, desc in cats:
        conn.execute(
            "INSERT INTO categories (shop_id, name, description) VALUES (?,?,?)",
            (shop_id, name, desc)
        )
