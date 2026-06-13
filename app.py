import os
import uuid
import json
from dotenv import load_dotenv
load_dotenv()
import csv
import io
from datetime import datetime, date, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, flash, Response, session
)
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db, init_db, seed_default_categories
from ecocash import EcoCashAPI
import firebase_sync

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tronic_pos_zw_2024_change_in_production')

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'lincolnmotiwac@gmail.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Admin@Tronic2024!')


# ══════════════════════════════════════════════════════════════════════════════
# Decorators
# ══════════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def shop_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        if not session.get('shop_id'):
            return redirect(url_for('select_shop'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Access denied — admin only.', 'danger')
            return redirect(url_for('pos'))
        return f(*args, **kwargs)
    return decorated


# ── Helpers ───────────────────────────────────────────────────────────────────

def sid():
    """Current shop_id from session."""
    return session.get('shop_id')


def get_shop():
    """Return current shop row as dict, or {}."""
    shop_id = sid()
    if not shop_id:
        return {}
    db = get_db()
    row = db.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    db.close()
    return dict(row) if row else {}


def vat_rate_for(tax_type):
    shop = get_shop()
    if not shop.get('vat_registered'):
        return 0.0
    base = float(shop.get('vat_rate', 15.0))
    return base if tax_type == 'standard' else 0.0


def make_ref(prefix='SAL'):
    return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def today_str():
    return date.today().isoformat()


# ── Context processor ─────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    if 'user_id' not in session:
        return {}

    shop = get_shop()
    user_shops = []
    low_stock = 0

    db = get_db()
    if session.get('role') == 'admin':
        user_shops = [dict(r) for r in db.execute(
            "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id ORDER BY s.name"
        ).fetchall()]
    else:
        user_shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? ORDER BY name", (session['user_id'],)
        ).fetchall()]

    if sid():
        low_stock = db.execute(
            "SELECT COUNT(*) FROM products WHERE shop_id=? AND stock_quantity<=min_stock_level AND is_active=1",
            (sid(),)
        ).fetchone()[0]
    db.close()

    current_user = {
        'id': session.get('user_id'),
        'name': session.get('user_name'),
        'email': session.get('user_email'),
        'role': session.get('role'),
    }
    return dict(shop=shop, user_shops=user_shops,
                low_stock_count=low_stock, current_user=current_user)


# ══════════════════════════════════════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('select_shop'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE email=? AND is_active=1", (email,)
        ).fetchone()
        db.close()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            session['role'] = user['role']
            next_url = request.args.get('next')
            return redirect(next_url or url_for('select_shop'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/shops')
@login_required
def select_shop():
    db = get_db()
    if session.get('role') == 'admin':
        shops = [dict(r) for r in db.execute(
            "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id ORDER BY s.name"
        ).fetchall()]
    else:
        shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? ORDER BY name", (session['user_id'],)
        ).fetchall()]
    db.close()

    # Non-admin with exactly one shop → auto-select
    if len(shops) == 1 and session.get('role') != 'admin':
        _set_shop(shops[0])
        return redirect(url_for('pos'))

    return render_template('select_shop.html', shops=shops)


@app.route('/shops/select/<int:shop_id>', methods=['POST'])
@login_required
def switch_shop(shop_id):
    db = get_db()
    if session.get('role') == 'admin':
        shop = db.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    else:
        shop = db.execute(
            "SELECT * FROM shops WHERE id=? AND owner_id=?", (shop_id, session['user_id'])
        ).fetchone()
    db.close()
    if shop:
        _set_shop(dict(shop))
        return redirect(url_for('pos'))
    flash('Shop not found.', 'danger')
    return redirect(url_for('select_shop'))


def _set_shop(shop):
    session['shop_id'] = shop['id']
    session['shop_name'] = shop['name']


@app.route('/shops/new', methods=['GET', 'POST'])
@login_required
def shop_new():
    if request.method == 'POST':
        f = request.form
        owner_id = session['user_id']
        if session.get('role') == 'admin' and f.get('owner_id'):
            owner_id = int(f['owner_id'])
        db = get_db()
        c = db.cursor()
        c.execute(
            """INSERT INTO shops (owner_id, name, tagline, address, phone, email, currency, receipt_footer)
               VALUES (?,?,?,?,?,?,?,?)""",
            (owner_id, f['name'], f.get('tagline', ''), f.get('address', ''),
             f.get('phone', ''), f.get('email', ''),
             f.get('currency', 'USD'), f.get('receipt_footer', 'Thank you for shopping with us!'))
        )
        new_shop_id = c.lastrowid
        seed_default_categories(db, new_shop_id)
        db.commit()
        firebase_sync.sync_shop({'id': new_shop_id, 'name': f['name'],
                                  'currency': f.get('currency', 'USD'),
                                  'address': f.get('address', ''),
                                  'phone': f.get('phone', ''),
                                  'vat_registered': 0})
        db.close()
        session['shop_id'] = new_shop_id
        session['shop_name'] = f['name']
        flash(f"Shop \"{f['name']}\" created!", 'success')
        return redirect(url_for('pos'))

    users = []
    if session.get('role') == 'admin':
        db = get_db()
        users = [dict(r) for r in db.execute(
            "SELECT id, name, email FROM users WHERE role='owner' ORDER BY name"
        ).fetchall()]
        db.close()
    return render_template('shop_new.html', users=users)


# ══════════════════════════════════════════════════════════════════════════════
# Admin panel
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/admin')
@admin_required
def admin_index():
    db = get_db()
    users = [dict(r) for r in db.execute(
        """SELECT u.*, COUNT(s.id) as shop_count
           FROM users u LEFT JOIN shops s ON s.owner_id=u.id
           GROUP BY u.id ORDER BY u.created_at DESC"""
    ).fetchall()]
    shops = [dict(r) for r in db.execute(
        """SELECT s.*, u.name as owner_name, u.email as owner_email
           FROM shops s JOIN users u ON s.owner_id=u.id
           ORDER BY s.created_at DESC"""
    ).fetchall()]
    db.close()
    return render_template('admin/index.html', users=users, shops=shops)


@app.route('/admin/users/new', methods=['GET', 'POST'])
@admin_required
def admin_user_new():
    if request.method == 'POST':
        f = request.form
        email = f.get('email', '').strip().lower()
        name = f.get('name', '').strip()
        password = f.get('password', '').strip()
        if not email or not password:
            flash('Email and password are required.', 'danger')
        else:
            db = get_db()
            existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                flash('That email is already registered.', 'danger')
                db.close()
            else:
                db.execute(
                    "INSERT INTO users (email, name, password_hash, role) VALUES (?,?,?,?)",
                    (email, name, generate_password_hash(password), 'owner')
                )
                db.commit()
                db.close()
                flash(f'Account created for {email}. Share their password securely.', 'success')
                return redirect(url_for('admin_index'))
    return render_template('admin/user_form.html', user=None)


@app.route('/admin/users/<int:uid>/edit', methods=['GET', 'POST'])
@admin_required
def admin_user_edit(uid):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        flash('User not found.', 'danger')
        db.close()
        return redirect(url_for('admin_index'))
    if request.method == 'POST':
        f = request.form
        new_hash = user['password_hash']
        if f.get('password'):
            new_hash = generate_password_hash(f['password'])
        db.execute(
            "UPDATE users SET name=?, email=?, password_hash=? WHERE id=?",
            (f.get('name', user['name']), f.get('email', user['email']).lower(), new_hash, uid)
        )
        db.commit()
        db.close()
        flash('User updated.', 'success')
        return redirect(url_for('admin_index'))
    db.close()
    return render_template('admin/user_form.html', user=dict(user))


@app.route('/admin/users/<int:uid>/toggle', methods=['POST'])
@admin_required
def admin_user_toggle(uid):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if user and user['role'] != 'admin':
        db.execute("UPDATE users SET is_active=? WHERE id=?",
                   (0 if user['is_active'] else 1, uid))
        db.commit()
    db.close()
    return redirect(url_for('admin_index'))


@app.route('/admin/shops/<int:shop_id>/delete', methods=['POST'])
@admin_required
def admin_shop_delete(shop_id):
    db = get_db()
    db.execute("DELETE FROM shops WHERE id=?", (shop_id,))
    db.commit()
    db.close()
    # Clear session if admin was operating that shop
    if session.get('shop_id') == shop_id:
        session.pop('shop_id', None)
        session.pop('shop_name', None)
    flash('Shop deleted.', 'info')
    return redirect(url_for('admin_index'))


# ══════════════════════════════════════════════════════════════════════════════
# POS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
@shop_required
def pos():
    db = get_db()
    categories = db.execute(
        "SELECT * FROM categories WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()
    db.close()
    return render_template('pos.html', categories=categories)


@app.route('/api/products/search')
@shop_required
def api_products_search():
    q = request.args.get('q', '').strip()
    cat = request.args.get('category', '')
    db = get_db()
    sql = """
        SELECT p.*, c.name AS category_name
        FROM products p
        LEFT JOIN categories c ON p.category_id=c.id
        WHERE p.shop_id=? AND p.is_active=1
    """
    params = [sid()]
    if q:
        sql += " AND (p.name LIKE ? OR p.sku LIKE ? OR p.barcode LIKE ?)"
        params += [f'%{q}%', f'%{q}%', f'%{q}%']
    if cat:
        sql += " AND p.category_id=?"
        params.append(cat)
    sql += " ORDER BY p.name LIMIT 80"
    products = db.execute(sql, params).fetchall()
    db.close()
    return jsonify([dict(p) for p in products])


@app.route('/api/product/<int:pid>')
@shop_required
def api_product(pid):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id=? AND shop_id=?", (pid, sid())).fetchone()
    db.close()
    if not p:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(p))


@app.route('/api/sale', methods=['POST'])
@shop_required
def api_create_sale():
    data = request.get_json()
    if not data or not data.get('items'):
        return jsonify({'error': 'No items'}), 400

    shop = get_shop()
    ref = make_ref('SAL')
    items = data['items']
    payment_method = data.get('payment_method', 'cash')
    amount_paid = float(data.get('amount_paid', 0))
    discount_amount = float(data.get('discount_amount', 0))
    cashier = data.get('cashier', 'Cashier')

    subtotal = 0.0
    tax_total = 0.0
    line_items = []

    db = get_db()
    for item in items:
        pid = item['product_id']
        qty = float(item['quantity'])
        p = db.execute("SELECT * FROM products WHERE id=? AND shop_id=?", (pid, sid())).fetchone()
        if not p:
            continue
        price = float(p['selling_price'])
        cost = float(p['cost_price'])
        tax_type = p['tax_type']
        rate = vat_rate_for(tax_type)
        total_line = price * qty
        tax_amt = (total_line * rate / (100 + rate)) if rate > 0 else 0.0
        line_sub = total_line - tax_amt
        subtotal += line_sub
        tax_total += tax_amt
        line_items.append({
            'product_id': pid, 'product_name': p['name'],
            'quantity': qty, 'unit_price': price, 'cost_price': cost,
            'tax_type': tax_type, 'tax_rate': rate,
            'tax_amount': tax_amt, 'total': total_line,
        })

    grand_total = max(0.0, subtotal + tax_total - discount_amount)
    change = max(0.0, amount_paid - grand_total)

    try:
        c = db.cursor()
        c.execute(
            """INSERT INTO sales (shop_id, reference, subtotal, tax_amount, discount_amount,
               total, payment_method, amount_paid, change_given, cashier)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sid(), ref, subtotal, tax_total, discount_amount,
             grand_total, payment_method, amount_paid, change, cashier)
        )
        sale_id = c.lastrowid

        for li in line_items:
            c.execute(
                """INSERT INTO sale_items (sale_id, product_id, product_name, quantity,
                   unit_price, cost_price, tax_type, tax_rate, tax_amount, total)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (sale_id, li['product_id'], li['product_name'], li['quantity'],
                 li['unit_price'], li['cost_price'], li['tax_type'],
                 li['tax_rate'], li['tax_amount'], li['total'])
            )
            c.execute(
                "UPDATE products SET stock_quantity=stock_quantity-? WHERE id=? AND shop_id=?",
                (li['quantity'], li['product_id'], sid())
            )

        ecocash_ref = data.get('ecocash_ref', '')
        ecocash_number = data.get('ecocash_number', '')
        if payment_method == 'split':
            cash_amt = float(data.get('cash_amount', 0))
            eco_amt = float(data.get('ecocash_amount', 0))
            if cash_amt > 0:
                c.execute("INSERT INTO payments (sale_id,method,amount,status) VALUES (?,?,?,?)",
                          (sale_id, 'cash', cash_amt, 'completed'))
            if eco_amt > 0:
                c.execute("""INSERT INTO payments (sale_id,method,amount,ecocash_number,
                             transaction_ref,status) VALUES (?,?,?,?,?,?)""",
                          (sale_id, 'ecocash', eco_amt, ecocash_number, ecocash_ref, 'completed'))
        else:
            c.execute("""INSERT INTO payments (sale_id,method,amount,ecocash_number,
                         transaction_ref,status) VALUES (?,?,?,?,?,?)""",
                      (sale_id, payment_method, amount_paid, ecocash_number, ecocash_ref, 'completed'))

        db.commit()
        firebase_sync.sync_sale(sid(), {
            'id': sale_id, 'reference': ref, 'total': grand_total,
            'tax_amount': tax_total, 'payment_method': payment_method,
            'cashier': cashier, 'created_at': datetime.now().isoformat(),
            'status': 'completed',
        })
        db.close()
        return jsonify({'success': True, 'sale_id': sale_id, 'reference': ref, 'change': change})
    except Exception as e:
        db.rollback()
        db.close()
        return jsonify({'error': str(e)}), 500


@app.route('/api/ecocash/initiate', methods=['POST'])
@shop_required
def api_ecocash_initiate():
    data = request.get_json()
    number = data.get('phone', '')
    amount = float(data.get('amount', 0))
    ref = data.get('reference') or make_ref('ECO')
    if not number or amount <= 0:
        return jsonify({'success': False, 'message': 'Phone and amount required'}), 400

    shop = get_shop()
    api = EcoCashAPI(
        merchant_code=shop.get('ecocash_merchant_code', ''),
        merchant_pin=shop.get('ecocash_merchant_pin', ''),
        mode=shop.get('ecocash_mode', 'test'),
    )
    result = api.initiate_push_payment(number, amount, ref)
    if result['success']:
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO ecocash_transactions
               (shop_id, merchant_reference, customer_msisdn, amount, status)
               VALUES (?,?,?,?,?)""",
            (sid(), ref, number, amount, 'pending')
        )
        db.commit()
        db.close()
    return jsonify({**result, 'reference': ref})


@app.route('/api/ecocash/status/<ref>')
@shop_required
def api_ecocash_status(ref):
    shop = get_shop()
    api = EcoCashAPI(
        merchant_code=shop.get('ecocash_merchant_code', ''),
        merchant_pin=shop.get('ecocash_merchant_pin', ''),
        mode=shop.get('ecocash_mode', 'test'),
    )
    result = api.check_payment_status(ref)
    if result.get('status') == 'completed':
        db = get_db()
        db.execute(
            """UPDATE ecocash_transactions
               SET status='completed', ecocash_reference=?, completed_at=CURRENT_TIMESTAMP
               WHERE merchant_reference=? AND shop_id=?""",
            (result.get('ecocash_reference', ''), ref, sid())
        )
        db.commit()
        db.close()
    return jsonify(result)


@app.route('/receipt/<int:sale_id>')
@login_required
def receipt(sale_id):
    db = get_db()
    # Allow if user's shop owns the sale, or admin
    if session.get('role') == 'admin':
        sale = db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    else:
        sale = db.execute(
            "SELECT * FROM sales WHERE id=? AND shop_id=?", (sale_id, sid())
        ).fetchone()
    if not sale:
        db.close()
        flash('Receipt not found.', 'danger')
        return redirect(url_for('pos'))

    items = db.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    payments = db.execute("SELECT * FROM payments WHERE sale_id=?", (sale_id,)).fetchall()

    # For receipt, load the shop that made the sale (not necessarily the current session shop)
    shop = db.execute("SELECT * FROM shops WHERE id=?", (sale['shop_id'],)).fetchone()
    db.close()
    return render_template('receipt.html', sale=dict(sale),
                           items=[dict(i) for i in items],
                           payments=[dict(p) for p in payments],
                           shop=dict(shop) if shop else {})


# ══════════════════════════════════════════════════════════════════════════════
# Sales History
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/sales')
@shop_required
def sales_history():
    db = get_db()
    date_from = request.args.get('from', (date.today() - timedelta(days=30)).isoformat())
    date_to = request.args.get('to', today_str())
    method = request.args.get('method', '')
    sql = """
        SELECT s.* FROM sales s
        WHERE s.shop_id=? AND DATE(s.created_at) BETWEEN ? AND ?
    """
    params = [sid(), date_from, date_to]
    if method:
        sql += " AND s.payment_method=?"
        params.append(method)
    sql += " ORDER BY s.created_at DESC"
    sales = [dict(r) for r in db.execute(sql, params).fetchall()]
    totals = db.execute(
        """SELECT COALESCE(SUM(total),0) as rev, COALESCE(SUM(tax_amount),0) as tax
           FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ?""",
        (sid(), date_from, date_to)
    ).fetchone()
    db.close()
    return render_template('sales_history.html', sales=sales,
                           date_from=date_from, date_to=date_to,
                           method=method, totals=dict(totals))


@app.route('/sales/<int:sale_id>/void', methods=['POST'])
@shop_required
def void_sale(sale_id):
    db = get_db()
    sale = db.execute("SELECT * FROM sales WHERE id=? AND shop_id=?", (sale_id, sid())).fetchone()
    if sale and sale['status'] == 'completed':
        items = db.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
        for item in items:
            db.execute(
                "UPDATE products SET stock_quantity=stock_quantity+? WHERE id=? AND shop_id=?",
                (item['quantity'], item['product_id'], sid())
            )
        db.execute("UPDATE sales SET status='voided' WHERE id=?", (sale_id,))
        db.commit()
        flash('Sale voided and stock restored.', 'success')
    db.close()
    return redirect(url_for('sales_history'))


# ══════════════════════════════════════════════════════════════════════════════
# Products
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/products')
@shop_required
def products():
    db = get_db()
    cat_filter = request.args.get('category', '')
    sql = """
        SELECT p.*, c.name as cat_name, s.name as sup_name
        FROM products p
        LEFT JOIN categories c ON p.category_id=c.id
        LEFT JOIN suppliers s ON p.supplier_id=s.id
        WHERE p.shop_id=?
    """
    params = [sid()]
    if cat_filter:
        sql += " AND p.category_id=?"
        params.append(cat_filter)
    sql += " ORDER BY p.name"
    prods = [dict(r) for r in db.execute(sql, params).fetchall()]
    cats = [dict(r) for r in db.execute(
        "SELECT * FROM categories WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('products.html', products=prods, categories=cats, cat_filter=cat_filter)


@app.route('/products/new', methods=['GET', 'POST'])
@shop_required
def product_new():
    db = get_db()
    if request.method == 'POST':
        f = request.form
        sku = f.get('sku', '').strip() or f"SKU-{uuid.uuid4().hex[:6].upper()}"
        try:
            cur = db.execute(
                """INSERT INTO products (shop_id, name, description, sku, barcode,
                   category_id, supplier_id, cost_price, selling_price,
                   stock_quantity, min_stock_level, unit, tax_type)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid(), f['name'], f.get('description', ''), sku, f.get('barcode', ''),
                 f.get('category_id') or None, f.get('supplier_id') or None,
                 float(f.get('cost_price', 0)), float(f.get('selling_price', 0)),
                 float(f.get('stock_quantity', 0)), float(f.get('min_stock_level', 5)),
                 f.get('unit', 'each'), f.get('tax_type', 'standard'))
            )
            db.commit()
            firebase_sync.sync_product(sid(), {
                'id': cur.lastrowid, 'name': f['name'], 'sku': sku,
                'selling_price': float(f.get('selling_price', 0)),
                'cost_price': float(f.get('cost_price', 0)),
                'stock_quantity': float(f.get('stock_quantity', 0)),
                'tax_type': f.get('tax_type', 'standard'),
                'is_active': 1, 'unit': f.get('unit', 'each'),
            })
            flash('Product added.', 'success')
            db.close()
            return redirect(url_for('products'))
        except Exception as e:
            flash(f'Error: {e}', 'danger')
    cats = [dict(r) for r in db.execute(
        "SELECT * FROM categories WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    sups = [dict(r) for r in db.execute(
        "SELECT * FROM suppliers WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('product_form.html', product=None, categories=cats, suppliers=sups)


@app.route('/products/<int:pid>/edit', methods=['GET', 'POST'])
@shop_required
def product_edit(pid):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id=? AND shop_id=?", (pid, sid())).fetchone()
    if not p:
        flash('Product not found.', 'danger')
        db.close()
        return redirect(url_for('products'))
    if request.method == 'POST':
        f = request.form
        try:
            is_active = 1 if f.get('is_active') else 0
            db.execute(
                """UPDATE products SET name=?, description=?, sku=?, barcode=?,
                   category_id=?, supplier_id=?, cost_price=?, selling_price=?,
                   stock_quantity=?, min_stock_level=?, unit=?, tax_type=?, is_active=?
                   WHERE id=? AND shop_id=?""",
                (f['name'], f.get('description', ''), f.get('sku', ''), f.get('barcode', ''),
                 f.get('category_id') or None, f.get('supplier_id') or None,
                 float(f.get('cost_price', 0)), float(f.get('selling_price', 0)),
                 float(f.get('stock_quantity', 0)), float(f.get('min_stock_level', 5)),
                 f.get('unit', 'each'), f.get('tax_type', 'standard'),
                 is_active, pid, sid())
            )
            db.commit()
            firebase_sync.sync_product(sid(), {
                'id': pid, 'name': f['name'], 'sku': f.get('sku', ''),
                'selling_price': float(f.get('selling_price', 0)),
                'cost_price': float(f.get('cost_price', 0)),
                'stock_quantity': float(f.get('stock_quantity', 0)),
                'tax_type': f.get('tax_type', 'standard'),
                'is_active': is_active, 'unit': f.get('unit', 'each'),
            })
            flash('Product updated.', 'success')
            db.close()
            return redirect(url_for('products'))
        except Exception as e:
            flash(f'Error: {e}', 'danger')
    cats = [dict(r) for r in db.execute(
        "SELECT * FROM categories WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    sups = [dict(r) for r in db.execute(
        "SELECT * FROM suppliers WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('product_form.html', product=dict(p), categories=cats, suppliers=sups)


@app.route('/products/<int:pid>/delete', methods=['POST'])
@shop_required
def product_delete(pid):
    db = get_db()
    db.execute("UPDATE products SET is_active=0 WHERE id=? AND shop_id=?", (pid, sid()))
    db.commit()
    firebase_sync.remove_product(sid(), pid)
    db.close()
    flash('Product deactivated.', 'info')
    return redirect(url_for('products'))


# ══════════════════════════════════════════════════════════════════════════════
# Inventory
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/inventory')
@shop_required
def inventory():
    db = get_db()
    prods = [dict(r) for r in db.execute(
        """SELECT p.*, c.name as cat_name FROM products p
           LEFT JOIN categories c ON p.category_id=c.id
           WHERE p.shop_id=? AND p.is_active=1 ORDER BY p.name""", (sid(),)
    ).fetchall()]
    adjustments = [dict(r) for r in db.execute(
        "SELECT * FROM stock_adjustments WHERE shop_id=? ORDER BY created_at DESC LIMIT 50", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('inventory.html', products=prods, adjustments=adjustments)


@app.route('/inventory/adjust', methods=['POST'])
@shop_required
def inventory_adjust():
    f = request.form
    pid = int(f['product_id'])
    adj_type = f['adjustment_type']
    qty = float(f['quantity'])
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id=? AND shop_id=?", (pid, sid())).fetchone()
    if not p:
        flash('Product not found.', 'danger')
        db.close()
        return redirect(url_for('inventory'))
    if adj_type in ('damage', 'theft', 'correction_minus'):
        db.execute("UPDATE products SET stock_quantity=stock_quantity-? WHERE id=? AND shop_id=?",
                   (qty, pid, sid()))
    else:
        db.execute("UPDATE products SET stock_quantity=stock_quantity+? WHERE id=? AND shop_id=?",
                   (qty, pid, sid()))
    db.execute(
        """INSERT INTO stock_adjustments (shop_id, product_id, product_name, adjustment_type, quantity, notes)
           VALUES (?,?,?,?,?,?)""",
        (sid(), pid, p['name'], adj_type, qty, f.get('notes', ''))
    )
    db.commit()
    db.close()
    flash('Stock adjusted.', 'success')
    return redirect(url_for('inventory'))


# ══════════════════════════════════════════════════════════════════════════════
# Suppliers
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/suppliers')
@shop_required
def suppliers():
    db = get_db()
    sups = [dict(r) for r in db.execute(
        "SELECT * FROM suppliers WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('suppliers.html', suppliers=sups)


@app.route('/suppliers/new', methods=['GET', 'POST'])
@shop_required
def supplier_new():
    if request.method == 'POST':
        f = request.form
        db = get_db()
        cur = db.execute(
            """INSERT INTO suppliers (shop_id, name, contact_person, phone, email, address, payment_terms, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (sid(), f['name'], f.get('contact_person', ''), f.get('phone', ''),
             f.get('email', ''), f.get('address', ''), f.get('payment_terms', 'Cash'), f.get('notes', ''))
        )
        db.commit()
        firebase_sync.sync_supplier(sid(), {
            'id': cur.lastrowid, 'name': f['name'],
            'contact_person': f.get('contact_person', ''),
            'phone': f.get('phone', ''), 'email': f.get('email', ''),
            'address': f.get('address', ''),
        })
        db.close()
        flash('Supplier added.', 'success')
        return redirect(url_for('suppliers'))
    return render_template('supplier_form.html', supplier=None)


@app.route('/suppliers/<int:sid_>/edit', methods=['GET', 'POST'])
@shop_required
def supplier_edit(sid_):
    db = get_db()
    sup = db.execute("SELECT * FROM suppliers WHERE id=? AND shop_id=?", (sid_, sid())).fetchone()
    if not sup:
        flash('Supplier not found.', 'danger')
        db.close()
        return redirect(url_for('suppliers'))
    if request.method == 'POST':
        f = request.form
        db.execute(
            """UPDATE suppliers SET name=?, contact_person=?, phone=?, email=?,
               address=?, payment_terms=?, notes=? WHERE id=? AND shop_id=?""",
            (f['name'], f.get('contact_person', ''), f.get('phone', ''),
             f.get('email', ''), f.get('address', ''), f.get('payment_terms', 'Cash'),
             f.get('notes', ''), sid_, sid())
        )
        db.commit()
        firebase_sync.sync_supplier(sid(), {
            'id': sid_, 'name': f['name'],
            'contact_person': f.get('contact_person', ''),
            'phone': f.get('phone', ''), 'email': f.get('email', ''),
            'address': f.get('address', ''),
        })
        db.close()
        flash('Supplier updated.', 'success')
        return redirect(url_for('suppliers'))
    db.close()
    return render_template('supplier_form.html', supplier=dict(sup))


@app.route('/suppliers/<int:sid_>/delete', methods=['POST'])
@shop_required
def supplier_delete(sid_):
    db = get_db()
    db.execute("DELETE FROM suppliers WHERE id=? AND shop_id=?", (sid_, sid()))
    db.commit()
    firebase_sync.remove_supplier(sid(), sid_)
    db.close()
    flash('Supplier deleted.', 'info')
    return redirect(url_for('suppliers'))


# ══════════════════════════════════════════════════════════════════════════════
# Purchase Orders
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/purchases')
@shop_required
def purchases():
    db = get_db()
    orders = [dict(r) for r in db.execute(
        """SELECT po.*, s.name as supplier_name FROM purchase_orders po
           LEFT JOIN suppliers s ON po.supplier_id=s.id
           WHERE po.shop_id=? ORDER BY po.created_at DESC""", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('purchases.html', orders=orders)


@app.route('/purchases/new', methods=['GET', 'POST'])
@shop_required
def purchase_new():
    db = get_db()
    if request.method == 'POST':
        f = request.form
        ref = make_ref('PO')
        product_ids = request.form.getlist('product_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_costs = request.form.getlist('unit_cost[]')
        try:
            subtotal = sum(float(q) * float(uc) for q, uc in zip(quantities, unit_costs))
            c = db.cursor()
            c.execute(
                """INSERT INTO purchase_orders (shop_id, reference, supplier_id, subtotal, total, expected_date, notes)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid(), ref, f.get('supplier_id') or None, subtotal, subtotal,
                 f.get('expected_date') or None, f.get('notes', ''))
            )
            po_id = c.lastrowid
            for pid, qty, uc in zip(product_ids, quantities, unit_costs):
                if pid and float(qty) > 0:
                    p = db.execute("SELECT name FROM products WHERE id=? AND shop_id=?",
                                   (pid, sid())).fetchone()
                    c.execute(
                        """INSERT INTO purchase_order_items
                           (po_id, product_id, product_name, quantity, unit_cost, total)
                           VALUES (?,?,?,?,?,?)""",
                        (po_id, pid, p['name'] if p else '', float(qty), float(uc),
                         float(qty) * float(uc))
                    )
            db.commit()
            flash('Purchase order created.', 'success')
            db.close()
            return redirect(url_for('purchases'))
        except Exception as e:
            flash(f'Error: {e}', 'danger')
    sups = [dict(r) for r in db.execute(
        "SELECT * FROM suppliers WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    prods = [dict(r) for r in db.execute(
        "SELECT * FROM products WHERE shop_id=? AND is_active=1 ORDER BY name", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('purchase_form.html', suppliers=sups, products=prods)


@app.route('/purchases/<int:po_id>')
@shop_required
def purchase_detail(po_id):
    db = get_db()
    po = db.execute(
        """SELECT po.*, s.name as supplier_name FROM purchase_orders po
           LEFT JOIN suppliers s ON po.supplier_id=s.id
           WHERE po.id=? AND po.shop_id=?""", (po_id, sid())
    ).fetchone()
    if not po:
        flash('Purchase order not found.', 'danger')
        db.close()
        return redirect(url_for('purchases'))
    items = [dict(r) for r in db.execute(
        "SELECT * FROM purchase_order_items WHERE po_id=?", (po_id,)
    ).fetchall()]
    db.close()
    return render_template('purchase_detail.html', po=dict(po), items=items)


@app.route('/purchases/<int:po_id>/receive', methods=['POST'])
@shop_required
def purchase_receive(po_id):
    db = get_db()
    po = db.execute(
        "SELECT * FROM purchase_orders WHERE id=? AND shop_id=?", (po_id, sid())
    ).fetchone()
    if not po or po['status'] == 'received':
        flash('Already received or not found.', 'warning')
        db.close()
        return redirect(url_for('purchases'))
    items = db.execute(
        "SELECT * FROM purchase_order_items WHERE po_id=?", (po_id,)
    ).fetchall()
    for item in items:
        recv = float(request.form.get(f'recv_{item["id"]}', 0))
        if recv > 0:
            db.execute("UPDATE purchase_order_items SET received_quantity=received_quantity+? WHERE id=?",
                       (recv, item['id']))
            db.execute("UPDATE products SET stock_quantity=stock_quantity+?, cost_price=? WHERE id=? AND shop_id=?",
                       (recv, item['unit_cost'], item['product_id'], sid()))
    remaining = db.execute(
        "SELECT COALESCE(SUM(quantity-received_quantity),0) FROM purchase_order_items WHERE po_id=?",
        (po_id,)
    ).fetchone()[0]
    status = 'received' if remaining <= 0 else 'partial'
    db.execute("UPDATE purchase_orders SET status=?, received_date=? WHERE id=?",
               (status, today_str(), po_id))
    db.commit()
    db.close()
    flash('Stock updated from purchase order.', 'success')
    return redirect(url_for('purchase_detail', po_id=po_id))


# ══════════════════════════════════════════════════════════════════════════════
# Customers
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/customers')
@shop_required
def customers():
    db = get_db()
    custs = [dict(r) for r in db.execute(
        """SELECT c.*, COUNT(s.id) as sale_count, COALESCE(SUM(s.total),0) as total_spent
           FROM customers c LEFT JOIN sales s ON s.customer_id=c.id AND s.shop_id=?
           WHERE c.shop_id=? GROUP BY c.id ORDER BY c.name""", (sid(), sid())
    ).fetchall()]
    db.close()
    return render_template('customers.html', customers=custs)


@app.route('/customers/new', methods=['POST'])
@shop_required
def customer_new():
    f = request.form
    db = get_db()
    cur = db.execute(
        "INSERT INTO customers (shop_id, name, phone, email, ecocash_number, address) VALUES (?,?,?,?,?,?)",
        (sid(), f.get('name', ''), f.get('phone', ''), f.get('email', ''),
         f.get('ecocash_number', ''), f.get('address', ''))
    )
    db.commit()
    firebase_sync.sync_customer(sid(), {
        'id': cur.lastrowid, 'name': f.get('name', ''),
        'phone': f.get('phone', ''), 'email': f.get('email', ''),
        'ecocash_number': f.get('ecocash_number', ''),
    })
    db.close()
    flash('Customer added.', 'success')
    return redirect(url_for('customers'))


@app.route('/customers/<int:cid>/delete', methods=['POST'])
@shop_required
def customer_delete(cid):
    db = get_db()
    db.execute("DELETE FROM customers WHERE id=? AND shop_id=?", (cid, sid()))
    db.commit()
    firebase_sync.remove_customer(sid(), cid)
    db.close()
    flash('Customer deleted.', 'info')
    return redirect(url_for('customers'))


# ══════════════════════════════════════════════════════════════════════════════
# Expenses
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/expenses')
@shop_required
def expenses():
    db = get_db()
    date_from = request.args.get('from', (date.today() - timedelta(days=30)).isoformat())
    date_to = request.args.get('to', today_str())
    exps = [dict(r) for r in db.execute(
        "SELECT * FROM expenses WHERE shop_id=? AND expense_date BETWEEN ? AND ? ORDER BY expense_date DESC",
        (sid(), date_from, date_to)
    ).fetchall()]
    totals = [dict(r) for r in db.execute(
        """SELECT category, SUM(amount) as total FROM expenses
           WHERE shop_id=? AND expense_date BETWEEN ? AND ? GROUP BY category""",
        (sid(), date_from, date_to)
    ).fetchall()]
    db.close()
    return render_template('expenses.html', expenses=exps, totals=totals,
                           date_from=date_from, date_to=date_to)


@app.route('/expenses/add', methods=['POST'])
@shop_required
def expense_add():
    f = request.form
    db = get_db()
    exp_date = f.get('expense_date') or today_str()
    cur = db.execute(
        """INSERT INTO expenses (shop_id, category, description, amount, expense_date, receipt_ref, notes)
           VALUES (?,?,?,?,?,?,?)""",
        (sid(), f['category'], f.get('description', ''), float(f['amount']),
         exp_date, f.get('receipt_ref', ''), f.get('notes', ''))
    )
    db.commit()
    firebase_sync.sync_expense(sid(), {
        'id': cur.lastrowid, 'category': f['category'],
        'description': f.get('description', ''),
        'amount': float(f['amount']), 'expense_date': exp_date,
    })
    db.close()
    flash('Expense recorded.', 'success')
    return redirect(url_for('expenses'))


@app.route('/expenses/<int:eid>/delete', methods=['POST'])
@shop_required
def expense_delete(eid):
    db = get_db()
    db.execute("DELETE FROM expenses WHERE id=? AND shop_id=?", (eid, sid()))
    db.commit()
    firebase_sync.remove_expense(sid(), eid)
    db.close()
    flash('Expense deleted.', 'info')
    return redirect(url_for('expenses'))


# ══════════════════════════════════════════════════════════════════════════════
# Reports
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/reports')
@shop_required
def reports():
    db = get_db()
    today = today_str()
    start_month = date.today().replace(day=1).isoformat()
    s = sid()

    def scalar(sql, params):
        return float(db.execute(sql, params).fetchone()[0] or 0)

    summary = {
        'today_sales':    scalar("SELECT COALESCE(SUM(total),0) FROM sales WHERE shop_id=? AND DATE(created_at)=? AND status='completed'", (s, today)),
        'month_sales':    scalar("SELECT COALESCE(SUM(total),0) FROM sales WHERE shop_id=? AND DATE(created_at)>=? AND status='completed'", (s, start_month)),
        'today_expenses': scalar("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE shop_id=? AND expense_date=?", (s, today)),
        'month_expenses': scalar("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE shop_id=? AND expense_date>=?", (s, start_month)),
        'today_cogs':     scalar("""SELECT COALESCE(SUM(si.cost_price*si.quantity),0) FROM sale_items si
                                    JOIN sales sa ON sa.id=si.sale_id
                                    WHERE sa.shop_id=? AND DATE(sa.created_at)=? AND sa.status='completed'""", (s, today)),
        'month_cogs':     scalar("""SELECT COALESCE(SUM(si.cost_price*si.quantity),0) FROM sale_items si
                                    JOIN sales sa ON sa.id=si.sale_id
                                    WHERE sa.shop_id=? AND DATE(sa.created_at)>=? AND sa.status='completed'""", (s, start_month)),
    }
    summary['today_profit'] = summary['today_sales'] - summary['today_cogs'] - summary['today_expenses']
    summary['month_profit'] = summary['month_sales'] - summary['month_cogs'] - summary['month_expenses']

    chart_labels, chart_sales, chart_profits = [], [], []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        chart_labels.append(d[5:])
        rev  = scalar("SELECT COALESCE(SUM(total),0) FROM sales WHERE shop_id=? AND DATE(created_at)=? AND status='completed'", (s, d))
        cogs = scalar("""SELECT COALESCE(SUM(si.cost_price*si.quantity),0) FROM sale_items si
                         JOIN sales sa ON sa.id=si.sale_id
                         WHERE sa.shop_id=? AND DATE(sa.created_at)=? AND sa.status='completed'""", (s, d))
        exp  = scalar("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE shop_id=? AND expense_date=?", (s, d))
        chart_sales.append(round(rev, 2))
        chart_profits.append(round(rev - cogs - exp, 2))

    top_products = [dict(r) for r in db.execute(
        """SELECT si.product_name, SUM(si.quantity) as qty, SUM(si.total) as revenue
           FROM sale_items si JOIN sales sa ON sa.id=si.sale_id
           WHERE sa.shop_id=? AND DATE(sa.created_at)>=? AND sa.status='completed'
           GROUP BY si.product_name ORDER BY revenue DESC LIMIT 10""", (s, start_month)
    ).fetchall()]
    low_stock = [dict(r) for r in db.execute(
        "SELECT * FROM products WHERE shop_id=? AND stock_quantity<=min_stock_level AND is_active=1 ORDER BY stock_quantity",
        (s,)
    ).fetchall()]
    db.close()

    return render_template('reports/index.html', summary=summary,
                           chart_labels=json.dumps(chart_labels),
                           chart_sales=json.dumps(chart_sales),
                           chart_profits=json.dumps(chart_profits),
                           top_products=top_products, low_stock=low_stock)


@app.route('/reports/sales')
@shop_required
def report_sales():
    db = get_db()
    date_from = request.args.get('from', (date.today() - timedelta(days=30)).isoformat())
    date_to = request.args.get('to', today_str())
    group_by = request.args.get('group', 'day')
    s = sid()
    date_fmt = {"month": "strftime('%Y-%m',created_at)",
                "week":  "strftime('%Y-W%W',created_at)"}.get(group_by, "DATE(created_at)")
    rows = [dict(r) for r in db.execute(
        f"""SELECT {date_fmt} as period, COUNT(id) as transactions,
               SUM(total) as revenue, SUM(tax_amount) as tax, SUM(discount_amount) as discounts
           FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ? AND status='completed'
           GROUP BY period ORDER BY period""", (s, date_from, date_to)
    ).fetchall()]
    payment_breakdown = [dict(r) for r in db.execute(
        """SELECT payment_method, COUNT(id) as count, SUM(total) as total
           FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ? AND status='completed'
           GROUP BY payment_method""", (s, date_from, date_to)
    ).fetchall()]
    product_breakdown = [dict(r) for r in db.execute(
        """SELECT si.product_name, SUM(si.quantity) as qty, SUM(si.total) as revenue
           FROM sale_items si JOIN sales sa ON sa.id=si.sale_id
           WHERE sa.shop_id=? AND DATE(sa.created_at) BETWEEN ? AND ? AND sa.status='completed'
           GROUP BY si.product_name ORDER BY revenue DESC LIMIT 20""", (s, date_from, date_to)
    ).fetchall()]
    db.close()
    return render_template('reports/sales.html', rows=rows,
                           payment_breakdown=payment_breakdown,
                           product_breakdown=product_breakdown,
                           date_from=date_from, date_to=date_to, group_by=group_by)


@app.route('/reports/pnl')
@shop_required
def report_pnl():
    db = get_db()
    date_from = request.args.get('from', date.today().replace(day=1).isoformat())
    date_to = request.args.get('to', today_str())
    s = sid()

    def scalar(sql, p): return float(db.execute(sql, p).fetchone()[0] or 0)

    revenue     = scalar("SELECT COALESCE(SUM(total),0) FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ? AND status='completed'", (s, date_from, date_to))
    cogs        = scalar("""SELECT COALESCE(SUM(si.cost_price*si.quantity),0) FROM sale_items si JOIN sales sa ON sa.id=si.sale_id WHERE sa.shop_id=? AND DATE(sa.created_at) BETWEEN ? AND ? AND sa.status='completed'""", (s, date_from, date_to))
    tax_collected = scalar("SELECT COALESCE(SUM(tax_amount),0) FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ? AND status='completed'", (s, date_from, date_to))
    gross_profit = revenue - cogs

    expenses_by_cat = [dict(r) for r in db.execute(
        """SELECT category, SUM(amount) as total FROM expenses
           WHERE shop_id=? AND expense_date BETWEEN ? AND ? GROUP BY category ORDER BY total DESC""",
        (s, date_from, date_to)
    ).fetchall()]
    total_expenses = sum(float(e['total']) for e in expenses_by_cat)
    net_profit   = gross_profit - total_expenses
    gross_margin = (gross_profit / revenue * 100) if revenue > 0 else 0
    net_margin   = (net_profit / revenue * 100) if revenue > 0 else 0
    db.close()
    return render_template('reports/pnl.html',
                           revenue=revenue, cogs=cogs, gross_profit=gross_profit,
                           tax_collected=tax_collected, expenses_by_cat=expenses_by_cat,
                           total_expenses=total_expenses, net_profit=net_profit,
                           gross_margin=gross_margin, net_margin=net_margin,
                           date_from=date_from, date_to=date_to)


@app.route('/reports/tax')
@shop_required
def report_tax():
    db = get_db()
    date_from = request.args.get('from', date.today().replace(day=1).isoformat())
    date_to = request.args.get('to', today_str())
    s = sid()
    shop = get_shop()
    tax_summary = [dict(r) for r in db.execute(
        """SELECT si.tax_type, si.tax_rate,
               SUM(si.total) as gross_sales, SUM(si.tax_amount) as tax_collected,
               SUM(si.total - si.tax_amount) as net_sales
           FROM sale_items si JOIN sales sa ON sa.id=si.sale_id
           WHERE sa.shop_id=? AND DATE(sa.created_at) BETWEEN ? AND ? AND sa.status='completed'
           GROUP BY si.tax_type, si.tax_rate""", (s, date_from, date_to)
    ).fetchall()]
    total_tax   = float(db.execute(
        """SELECT COALESCE(SUM(si.tax_amount),0) FROM sale_items si JOIN sales sa ON sa.id=si.sale_id
           WHERE sa.shop_id=? AND DATE(sa.created_at) BETWEEN ? AND ? AND sa.status='completed'""",
        (s, date_from, date_to)
    ).fetchone()[0])
    total_sales = float(db.execute(
        "SELECT COALESCE(SUM(total),0) FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ? AND status='completed'",
        (s, date_from, date_to)
    ).fetchone()[0])
    db.close()
    return render_template('reports/tax.html', tax_summary=tax_summary,
                           total_tax=total_tax, total_sales=total_sales,
                           vat_rate=float(shop.get('vat_rate', 15)),
                           settings=shop, date_from=date_from, date_to=date_to)


@app.route('/reports/export/sales')
@shop_required
def export_sales_csv():
    db = get_db()
    date_from = request.args.get('from', (date.today() - timedelta(days=30)).isoformat())
    date_to = request.args.get('to', today_str())
    sales = db.execute(
        """SELECT reference, created_at, subtotal, tax_amount, discount_amount,
                  total, payment_method, amount_paid, change_given, cashier, status
           FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ? ORDER BY created_at""",
        (sid(), date_from, date_to)
    ).fetchall()
    db.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Reference','Date','Subtotal','Tax','Discount','Total',
                     'Payment Method','Amount Paid','Change','Cashier','Status'])
    for s in sales:
        writer.writerow(list(s))
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=sales_{date_from}_{date_to}.csv'})


# ══════════════════════════════════════════════════════════════════════════════
# Z-Report
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/zreport')
@shop_required
def zreport():
    db = get_db()
    day = request.args.get('date', today_str())
    s = sid()
    sales = [dict(r) for r in db.execute(
        "SELECT * FROM sales WHERE shop_id=? AND DATE(created_at)=? AND status='completed' ORDER BY created_at",
        (s, day)
    ).fetchall()]
    totals = dict(db.execute(
        """SELECT COUNT(id) as count, COALESCE(SUM(total),0) as rev,
                  COALESCE(SUM(tax_amount),0) as tax, COALESCE(SUM(discount_amount),0) as discounts
           FROM sales WHERE shop_id=? AND DATE(created_at)=? AND status='completed'""",
        (s, day)
    ).fetchone())
    by_payment = [dict(r) for r in db.execute(
        """SELECT payment_method, COUNT(id) as count, SUM(total) as total
           FROM sales WHERE shop_id=? AND DATE(created_at)=? AND status='completed'
           GROUP BY payment_method""", (s, day)
    ).fetchall()]
    cogs = float(db.execute(
        """SELECT COALESCE(SUM(si.cost_price*si.quantity),0) FROM sale_items si
           JOIN sales sa ON sa.id=si.sale_id
           WHERE sa.shop_id=? AND DATE(sa.created_at)=? AND sa.status='completed'""", (s, day)
    ).fetchone()[0])
    exps = float(db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE shop_id=? AND expense_date=?", (s, day)
    ).fetchone()[0])
    db.close()
    return render_template('zreport.html', today=day, sales=sales,
                           totals=totals, by_payment=by_payment,
                           cogs=cogs, expenses=exps)


# ══════════════════════════════════════════════════════════════════════════════
# Settings (shop-specific)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/settings', methods=['GET', 'POST'])
@shop_required
def settings():
    db = get_db()
    if request.method == 'POST':
        f = request.form
        db.execute(
            """UPDATE shops SET name=?, tagline=?, address=?, phone=?, email=?,
               zimra_tin=?, vat_registered=?, vat_number=?, vat_rate=?,
               currency=?, ecocash_merchant_code=?, ecocash_merchant_pin=?,
               ecocash_mode=?, receipt_footer=?
               WHERE id=? AND owner_id=?""",
            (f.get('name',''), f.get('tagline',''), f.get('address',''),
             f.get('phone',''), f.get('email',''),
             f.get('zimra_tin',''), 1 if f.get('vat_registered') else 0,
             f.get('vat_number',''), float(f.get('vat_rate', 15.0)),
             f.get('currency','USD'),
             f.get('ecocash_merchant_code',''), f.get('ecocash_merchant_pin',''),
             f.get('ecocash_mode','test'), f.get('receipt_footer',''),
             sid(), session['user_id'])
        )
        # Admin can edit any shop
        if session.get('role') == 'admin':
            db.execute(
                """UPDATE shops SET name=?, tagline=?, address=?, phone=?, email=?,
                   zimra_tin=?, vat_registered=?, vat_number=?, vat_rate=?,
                   currency=?, ecocash_merchant_code=?, ecocash_merchant_pin=?,
                   ecocash_mode=?, receipt_footer=? WHERE id=?""",
                (f.get('name',''), f.get('tagline',''), f.get('address',''),
                 f.get('phone',''), f.get('email',''),
                 f.get('zimra_tin',''), 1 if f.get('vat_registered') else 0,
                 f.get('vat_number',''), float(f.get('vat_rate', 15.0)),
                 f.get('currency','USD'),
                 f.get('ecocash_merchant_code',''), f.get('ecocash_merchant_pin',''),
                 f.get('ecocash_mode','test'), f.get('receipt_footer',''), sid())
            )
        db.commit()
        firebase_sync.sync_shop({
            'id': sid(), 'name': f.get('name', ''),
            'currency': f.get('currency', 'USD'),
            'address': f.get('address', ''),
            'phone': f.get('phone', ''),
            'vat_registered': 1 if f.get('vat_registered') else 0,
        })
        session['shop_name'] = f.get('name', session.get('shop_name', ''))
        flash('Settings saved.', 'success')
        db.close()
        return redirect(url_for('settings'))

    s = dict(db.execute("SELECT * FROM shops WHERE id=?", (sid(),)).fetchone())
    cats = [dict(r) for r in db.execute(
        "SELECT * FROM categories WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('settings.html', settings=s, categories=cats)


@app.route('/settings/categories/add', methods=['POST'])
@shop_required
def category_add():
    f = request.form
    db = get_db()
    db.execute("INSERT INTO categories (shop_id, name, description) VALUES (?,?,?)",
               (sid(), f['name'], f.get('description', '')))
    db.commit()
    db.close()
    flash('Category added.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/categories/<int:cid>/delete', methods=['POST'])
@shop_required
def category_delete(cid):
    db = get_db()
    db.execute("DELETE FROM categories WHERE id=? AND shop_id=?", (cid, sid()))
    db.commit()
    db.close()
    flash('Category deleted.', 'info')
    return redirect(url_for('settings'))


# ══════════════════════════════════════════════════════════════════════════════
# Startup
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/sw.js')
def service_worker():
    resp = app.send_static_file('sw.js')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@app.route('/offline')
def offline_page():
    return render_template('offline.html')


@app.route('/api/sync/status')
@shop_required
def api_sync_status():
    """Returns basic shop data for offline cache warming."""
    db = get_db()
    products = [dict(r) for r in db.execute(
        "SELECT id,name,sku,selling_price,cost_price,stock_quantity,unit,tax_type FROM products WHERE shop_id=? AND is_active=1",
        (sid(),)
    ).fetchall()]
    db.close()
    return jsonify({'shop_id': sid(), 'products': products, 'online': True})


def create_admin():
    """Ensure the super-admin account exists on first run."""
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO users (email, name, password_hash, role) VALUES (?,?,?,?)",
            (ADMIN_EMAIL, 'Super Admin', generate_password_hash(ADMIN_PASSWORD), 'admin')
        )
        db.commit()
        print(f"  Admin account created: {ADMIN_EMAIL}")
    db.close()


if __name__ == '__main__':
    init_db()
    create_admin()
    port = int(os.environ.get('PORT', 5000))
    print('\n' + '=' * 60)
    print('  Tronic POS System — Multi-Tenant Edition')
    print(f'  Admin login: {ADMIN_EMAIL}')
    print(f'  Open: http://localhost:{port}')
    print('=' * 60 + '\n')
    app.run(debug=False, host='0.0.0.0', port=port)
