"""
Optional Firebase Realtime Database sync.
Configure via env vars:
  FIREBASE_CREDENTIALS_JSON  — full JSON string of the service-account key
  FIREBASE_CREDENTIALS_PATH  — path to the service-account JSON file
  FIREBASE_DATABASE_URL      — defaults to bhini-6fd48-default-rtdb.firebaseio.com

If neither credential var is set, all calls are silent no-ops — the app
continues to work normally using SQLite only.
"""
import os
import json
import threading

_initialized = False
_enabled = False


def _init():
    global _initialized, _enabled
    if _initialized:
        return _enabled
    _initialized = True

    creds_json = os.environ.get('FIREBASE_CREDENTIALS_JSON', '').strip()
    creds_path = os.environ.get('FIREBASE_CREDENTIALS_PATH', '').strip()
    db_url = os.environ.get(
        'FIREBASE_DATABASE_URL',
        'https://bhini-6fd48-default-rtdb.firebaseio.com'
    )

    if not creds_json and not creds_path:
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            if creds_json:
                cred = credentials.Certificate(json.loads(creds_json))
            else:
                cred = credentials.Certificate(creds_path)
            firebase_admin.initialize_app(cred, {'databaseURL': db_url})

        _enabled = True
        print('Firebase Realtime DB: sync ENABLED')
    except Exception as e:
        print(f'Firebase sync DISABLED: {e}')

    return _enabled


def _write(path, data):
    try:
        from firebase_admin import db
        ref = db.reference(path)
        if data is None:
            ref.delete()
        else:
            ref.set(data)
    except Exception as e:
        print(f'Firebase write error [{path}]: {e}')


def _async(path, data):
    if not _init():
        return
    threading.Thread(target=_write, args=(path, data), daemon=True).start()


# ── Public API ────────────────────────────────────────────────────────────────

def sync_shop(shop: dict):
    _async(f"shops/{shop['id']}/_info", {
        'name': shop.get('name', ''),
        'currency': shop.get('currency', 'USD'),
        'address': shop.get('address', ''),
        'phone': shop.get('phone', ''),
        'vat_registered': bool(shop.get('vat_registered')),
    })


def sync_product(shop_id: int, product: dict):
    _async(f"shops/{shop_id}/products/{product['id']}", {
        'name': product.get('name', ''),
        'sku': product.get('sku', ''),
        'selling_price': float(product.get('selling_price', 0)),
        'cost_price': float(product.get('cost_price', 0)),
        'stock_quantity': float(product.get('stock_quantity', 0)),
        'tax_type': product.get('tax_type', 'standard'),
        'is_active': bool(product.get('is_active', 1)),
        'unit': product.get('unit', 'each'),
    })


def remove_product(shop_id: int, product_id: int):
    _async(f"shops/{shop_id}/products/{product_id}", None)


def sync_sale(shop_id: int, sale: dict):
    _async(f"shops/{shop_id}/sales/{sale['id']}", {
        'reference': sale.get('reference', ''),
        'total': float(sale.get('total', 0)),
        'tax_amount': float(sale.get('tax_amount', 0)),
        'payment_method': sale.get('payment_method', ''),
        'cashier': sale.get('cashier', ''),
        'created_at': str(sale.get('created_at', '')),
        'status': sale.get('status', 'completed'),
    })


def sync_expense(shop_id: int, expense: dict):
    _async(f"shops/{shop_id}/expenses/{expense['id']}", {
        'category': expense.get('category', ''),
        'description': expense.get('description', ''),
        'amount': float(expense.get('amount', 0)),
        'expense_date': str(expense.get('expense_date', '')),
    })


def remove_expense(shop_id: int, expense_id: int):
    _async(f"shops/{shop_id}/expenses/{expense_id}", None)


def sync_supplier(shop_id: int, supplier: dict):
    _async(f"shops/{shop_id}/suppliers/{supplier['id']}", {
        'name': supplier.get('name', ''),
        'contact_person': supplier.get('contact_person', ''),
        'phone': supplier.get('phone', ''),
        'email': supplier.get('email', ''),
        'address': supplier.get('address', ''),
    })


def remove_supplier(shop_id: int, supplier_id: int):
    _async(f"shops/{shop_id}/suppliers/{supplier_id}", None)


def sync_customer(shop_id: int, customer: dict):
    _async(f"shops/{shop_id}/customers/{customer['id']}", {
        'name': customer.get('name', ''),
        'phone': customer.get('phone', ''),
        'email': customer.get('email', ''),
        'ecocash_number': customer.get('ecocash_number', ''),
    })


def remove_customer(shop_id: int, customer_id: int):
    _async(f"shops/{shop_id}/customers/{customer_id}", None)
