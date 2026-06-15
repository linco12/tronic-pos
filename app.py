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

ADMIN_EMAIL    = os.environ.get('ADMIN_EMAIL',    'lincolnmotiwac@gmail.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Admin@Tronic2024!')
DESKTOP_MODE   = os.environ.get('DESKTOP_MODE')  == '1'


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


def owner_required(f):
    """Login + shop + blocks staff role from management routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        if session.get('role') == 'staff':
            flash('That section is restricted to shop owners.', 'danger')
            return redirect(url_for('pos'))
        if not session.get('shop_id'):
            return redirect(url_for('select_shop'))
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
    role = session.get('role')

    db = get_db()
    if role == 'admin':
        user_shops = [dict(r) for r in db.execute(
            "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id ORDER BY s.name"
        ).fetchall()]
    elif role == 'staff':
        assigned_id = session.get('assigned_shop_id')
        if assigned_id:
            row = db.execute("SELECT * FROM shops WHERE id=?", (assigned_id,)).fetchone()
            if row:
                user_shops = [dict(row)]
    else:
        user_shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? AND is_active=1 ORDER BY name", (session['user_id'],)
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
        'can_create_shops': session.get('can_create_shops', 0),
        'assigned_shop_id': session.get('assigned_shop_id'),
    }
    return dict(shop=shop, user_shops=user_shops,
                low_stock_count=low_stock, current_user=current_user,
                desktop_mode=DESKTOP_MODE)


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
        if user and check_password_hash(user['password_hash'], password):
            user = dict(user)
            session.clear()
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            session['role'] = user['role']
            session['can_create_shops'] = user.get('can_create_shops', 0)
            session['assigned_shop_id'] = user.get('assigned_shop_id')

            c = db.cursor()
            c.execute(
                "INSERT INTO user_sessions (user_id, shop_id, ip_address) VALUES (?,?,?)",
                (user['id'], user.get('assigned_shop_id'), request.remote_addr or '')
            )
            session['session_log_id'] = c.lastrowid
            db.commit()

            if user['role'] == 'staff' and user.get('assigned_shop_id'):
                shop_row = db.execute(
                    "SELECT name FROM shops WHERE id=?", (user['assigned_shop_id'],)
                ).fetchone()
                session['shop_id'] = user['assigned_shop_id']
                session['shop_name'] = shop_row['name'] if shop_row else 'My Shop'
                db.close()
                return redirect(url_for('pos'))

            db.close()
            next_url = request.args.get('next')
            return redirect(next_url or url_for('select_shop'))
        db.close()
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    if 'session_log_id' in session:
        try:
            db = get_db()
            db.execute(
                "UPDATE user_sessions SET logout_at=CURRENT_TIMESTAMP WHERE id=?",
                (session['session_log_id'],)
            )
            db.commit()
            db.close()
        except Exception:
            pass
    session.clear()
    return redirect(url_for('login'))


@app.route('/shops')
@login_required
def select_shop():
    role = session.get('role')
    if role == 'staff':
        if session.get('shop_id'):
            return redirect(url_for('pos'))
        flash('No shop assigned. Contact your manager.', 'warning')
        return redirect(url_for('logout'))

    db = get_db()
    if role == 'admin':
        shops = [dict(r) for r in db.execute(
            "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id ORDER BY s.name"
        ).fetchall()]
    else:
        shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? AND is_active=1 ORDER BY name", (session['user_id'],)
        ).fetchall()]
    db.close()

    if len(shops) == 1 and role != 'admin':
        _set_shop(shops[0])
        return redirect(url_for('pos'))

    return render_template('select_shop.html', shops=shops)


@app.route('/shops/select/<int:shop_id>', methods=['POST'])
@login_required
def switch_shop(shop_id):
    if session.get('role') == 'staff':
        return redirect(url_for('pos'))
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
    role = session.get('role')
    if role == 'staff':
        flash('Access denied.', 'danger')
        return redirect(url_for('pos'))
    if role == 'owner' and not session.get('can_create_shops'):
        flash('You do not have permission to create shops. Ask the admin to create one for you.', 'warning')
        return redirect(url_for('select_shop'))
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
        """SELECT u.*,
                  COUNT(DISTINCT s.id) as shop_count,
                  creator.name as created_by_name
           FROM users u
           LEFT JOIN shops s ON s.owner_id=u.id
           LEFT JOIN users creator ON u.created_by=creator.id
           WHERE u.role != 'admin'
           GROUP BY u.id ORDER BY u.role, u.name"""
    ).fetchall()]
    shops = [dict(r) for r in db.execute(
        """SELECT s.*, u.name as owner_name, u.email as owner_email,
                  (SELECT COUNT(*) FROM sales WHERE shop_id=s.id AND status='completed') as sale_count,
                  (SELECT COUNT(*) FROM products WHERE shop_id=s.id AND is_active=1) as product_count,
                  (SELECT COALESCE(SUM(total),0) FROM sales WHERE shop_id=s.id AND status='completed') as total_revenue
           FROM shops s JOIN users u ON s.owner_id=u.id
           ORDER BY s.is_active DESC, s.name"""
    ).fetchall()]
    db.close()
    return render_template('admin/index.html', users=users, shops=shops)


@app.route('/admin/users/new', methods=['GET', 'POST'])
@admin_required
def admin_user_new():
    db = get_db()
    all_shops = [dict(r) for r in db.execute(
        "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id ORDER BY s.name"
    ).fetchall()]
    if request.method == 'POST':
        f = request.form
        email = f.get('email', '').strip().lower()
        name = f.get('name', '').strip()
        password = f.get('password', '').strip()
        can_create = 1 if f.get('can_create_shops') else 0
        if not email or not password:
            flash('Email and password are required.', 'danger')
        else:
            existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                flash('That email is already registered.', 'danger')
                db.close()
            else:
                cur = db.cursor()
                cur.execute(
                    "INSERT INTO users (email, name, password_hash, role, can_create_shops) VALUES (?,?,?,?,?)",
                    (email, name, generate_password_hash(password), 'owner', can_create)
                )
                new_uid = cur.lastrowid
                # Create each shop specified by the admin
                shop_names = [n.strip() for n in request.form.getlist('shop_name[]') if n.strip()]
                for sname in shop_names:
                    cur.execute(
                        "INSERT INTO shops (owner_id, name, currency) VALUES (?,?,?)",
                        (new_uid, sname, 'USD')
                    )
                    seed_default_categories(db, cur.lastrowid)
                db.commit()
                db.close()
                created = f" with {len(shop_names)} shop(s)" if shop_names else ""
                flash(f'Owner account created for {email}{created}.', 'success')
                return redirect(url_for('admin_index'))
    db.close()
    return render_template('admin/user_form.html', user=None, all_shops=all_shops)


@app.route('/admin/users/<int:uid>/edit', methods=['GET', 'POST'])
@admin_required
def admin_user_edit(uid):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        flash('User not found.', 'danger')
        db.close()
        return redirect(url_for('admin_index'))
    all_shops = [dict(r) for r in db.execute(
        "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id ORDER BY s.name"
    ).fetchall()]
    if request.method == 'POST':
        f = request.form
        new_hash = user['password_hash']
        if f.get('password'):
            new_hash = generate_password_hash(f['password'])
        can_create = 1 if f.get('can_create_shops') else 0
        db.execute(
            "UPDATE users SET name=?, email=?, password_hash=?, can_create_shops=? WHERE id=?",
            (f.get('name', user['name']), f.get('email', user['email']).lower(),
             new_hash, can_create, uid)
        )
        db.commit()
        db.close()
        flash('User updated.', 'success')
        return redirect(url_for('admin_index'))
    db.close()
    return render_template('admin/user_form.html', user=dict(user), all_shops=all_shops)


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
    if session.get('shop_id') == shop_id:
        session.pop('shop_id', None)
        session.pop('shop_name', None)
    flash('Shop and all its records deleted.', 'info')
    return redirect(url_for('admin_index'))


@app.route('/admin/shops/<int:shop_id>/suspend', methods=['POST'])
@admin_required
def admin_shop_suspend(shop_id):
    db = get_db()
    shop = db.execute("SELECT * FROM shops WHERE id=?", (shop_id,)).fetchone()
    if shop:
        new_status = 0 if shop['is_active'] else 1
        db.execute("UPDATE shops SET is_active=? WHERE id=?", (new_status, shop_id))
        db.commit()
        word = 'reactivated' if new_status else 'suspended'
        flash(f'Shop "{shop["name"]}" {word}.', 'success')
        if new_status == 0 and session.get('shop_id') == shop_id:
            session.pop('shop_id', None)
            session.pop('shop_name', None)
    db.close()
    return redirect(url_for('admin_index'))


@app.route('/admin/shops/<int:shop_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_shop_edit(shop_id):
    db = get_db()
    shop = db.execute(
        "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id WHERE s.id=?",
        (shop_id,)
    ).fetchone()
    if not shop:
        flash('Shop not found.', 'danger')
        db.close()
        return redirect(url_for('admin_index'))
    owners = [dict(r) for r in db.execute(
        "SELECT id, name, email FROM users WHERE role='owner' AND is_active=1 ORDER BY name"
    ).fetchall()]
    if request.method == 'POST':
        f = request.form
        db.execute(
            """UPDATE shops SET name=?, tagline=?, address=?, phone=?, email=?,
               zimra_tin=?, vat_registered=?, vat_number=?, vat_rate=?,
               currency=?, ecocash_merchant_code=?, ecocash_merchant_pin=?,
               ecocash_mode=?, receipt_footer=?, owner_id=? WHERE id=?""",
            (f.get('name', ''), f.get('tagline', ''), f.get('address', ''),
             f.get('phone', ''), f.get('email', ''),
             f.get('zimra_tin', ''), 1 if f.get('vat_registered') else 0,
             f.get('vat_number', ''), float(f.get('vat_rate', 15.0)),
             f.get('currency', 'USD'),
             f.get('ecocash_merchant_code', ''), f.get('ecocash_merchant_pin', ''),
             f.get('ecocash_mode', 'test'), f.get('receipt_footer', ''),
             int(f.get('owner_id', shop['owner_id'])), shop_id)
        )
        db.commit()
        db.close()
        flash('Shop updated.', 'success')
        return redirect(url_for('admin_index'))
    db.close()
    return render_template('admin/shop_edit.html', shop=dict(shop), owners=owners)


@app.route('/admin/shops/<int:shop_id>/migrate', methods=['GET', 'POST'])
@admin_required
def admin_shop_migrate(shop_id):
    db = get_db()
    source = db.execute(
        "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id WHERE s.id=?",
        (shop_id,)
    ).fetchone()
    if not source:
        flash('Shop not found.', 'danger')
        db.close()
        return redirect(url_for('admin_index'))
    other_shops = [dict(r) for r in db.execute(
        """SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id
           WHERE s.id!=? ORDER BY s.name""",
        (shop_id,)
    ).fetchall()]
    if request.method == 'POST':
        target_id = int(request.form.get('target_shop_id', 0))
        after_action = request.form.get('after_action', 'suspend')
        if not target_id:
            flash('Select a destination shop.', 'danger')
            db.close()
            return render_template('admin/shop_migrate.html', source=dict(source), shops=other_shops)
        # Migrate all shop-scoped records
        for tbl in ('products', 'categories', 'suppliers', 'customers', 'sales',
                    'expenses', 'purchase_orders', 'stock_adjustments',
                    'ecocash_transactions', 'user_sessions'):
            try:
                db.execute(f"UPDATE {tbl} SET shop_id=? WHERE shop_id=?", (target_id, shop_id))
            except Exception:
                pass
        # Reassign staff
        db.execute("UPDATE users SET assigned_shop_id=? WHERE assigned_shop_id=?",
                   (target_id, shop_id))
        if after_action == 'delete':
            db.execute("DELETE FROM shops WHERE id=?", (shop_id,))
            flash('Records migrated and shop deleted.', 'success')
        else:
            db.execute("UPDATE shops SET is_active=0 WHERE id=?", (shop_id,))
            flash('Records migrated and shop suspended.', 'success')
        db.commit()
        db.close()
        if session.get('shop_id') == shop_id:
            session.pop('shop_id', None)
            session.pop('shop_name', None)
        return redirect(url_for('admin_index'))
    db.close()
    return render_template('admin/shop_migrate.html', source=dict(source), shops=other_shops)


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

    customer_name = None
    if sale['customer_id']:
        c = db.execute("SELECT name FROM customers WHERE id=?", (sale['customer_id'],)).fetchone()
        if c:
            customer_name = c['name']

    db.close()
    sale_dict = dict(sale)
    sale_dict['customer_name'] = customer_name
    return render_template('receipt.html', sale=sale_dict,
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
def suppliers():
    db = get_db()
    sups = [dict(r) for r in db.execute(
        "SELECT * FROM suppliers WHERE shop_id=? ORDER BY name", (sid(),)
    ).fetchall()]
    db.close()
    return render_template('suppliers.html', suppliers=sups)


@app.route('/suppliers/new', methods=['GET', 'POST'])
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
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
@owner_required
def category_delete(cid):
    db = get_db()
    db.execute("DELETE FROM categories WHERE id=? AND shop_id=?", (cid, sid()))
    db.commit()
    db.close()
    flash('Category deleted.', 'info')
    return redirect(url_for('settings'))


# ══════════════════════════════════════════════════════════════════════════════
# Staff Management (owner creates & manages their sales staff)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/staff')
@login_required
def staff_list():
    if session.get('role') not in ('owner', 'admin'):
        flash('Access denied.', 'danger')
        return redirect(url_for('pos'))
    db = get_db()
    uid = session['user_id']
    role = session.get('role')
    if role == 'admin':
        rows = db.execute(
            """SELECT u.*, s.name as shop_name, c.name as created_by_name
               FROM users u
               LEFT JOIN shops s ON u.assigned_shop_id=s.id
               LEFT JOIN users c ON u.created_by=c.id
               WHERE u.role='staff' ORDER BY u.name"""
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT u.*, s.name as shop_name
               FROM users u
               LEFT JOIN shops s ON u.assigned_shop_id=s.id
               WHERE u.created_by=? AND u.role='staff' ORDER BY u.name""",
            (uid,)
        ).fetchall()
    staff_data = []
    for r in rows:
        sd = dict(r)
        stats = db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(total),0) as total FROM sales "
            "WHERE cashier=? AND shop_id=? AND status='completed'",
            (r['name'], r['assigned_shop_id'])
        ).fetchone()
        last_sess = db.execute(
            "SELECT * FROM user_sessions WHERE user_id=? ORDER BY login_at DESC LIMIT 1",
            (r['id'],)
        ).fetchone()
        sd['sale_count'] = stats['cnt']
        sd['sale_total'] = float(stats['total'])
        sd['last_login'] = last_sess['login_at'] if last_sess else None
        sd['last_logout'] = last_sess['logout_at'] if last_sess else None
        sd['active_now'] = bool(last_sess and not last_sess['logout_at'])
        staff_data.append(sd)
    if role == 'admin':
        my_shops = [dict(r) for r in db.execute("SELECT * FROM shops ORDER BY name").fetchall()]
    else:
        my_shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? ORDER BY name", (uid,)
        ).fetchall()]
    db.close()
    return render_template('staff.html', staff_list=staff_data, shops=my_shops)


@app.route('/staff/new', methods=['GET', 'POST'])
@login_required
def staff_new():
    if session.get('role') not in ('owner', 'admin'):
        flash('Access denied.', 'danger')
        return redirect(url_for('pos'))
    db = get_db()
    uid = session['user_id']
    role = session.get('role')
    if role == 'admin':
        my_shops = [dict(r) for r in db.execute("SELECT * FROM shops ORDER BY name").fetchall()]
    else:
        my_shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? ORDER BY name", (uid,)
        ).fetchall()]
    if request.method == 'POST':
        f = request.form
        email = f.get('email', '').strip().lower()
        name = f.get('name', '').strip()
        password = f.get('password', '').strip()
        assigned_shop_id = f.get('assigned_shop_id') or None
        if not email or not name or not password:
            flash('Name, email, and password are required.', 'danger')
        else:
            existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                flash('That email is already registered.', 'danger')
            else:
                db.execute(
                    "INSERT INTO users (email, name, password_hash, role, assigned_shop_id, created_by, can_create_shops) "
                    "VALUES (?,?,?,?,?,?,0)",
                    (email, name, generate_password_hash(password), 'staff', assigned_shop_id, uid)
                )
                db.commit()
                db.close()
                flash(f'Staff account created for {name}.', 'success')
                return redirect(url_for('staff_list'))
    db.close()
    return render_template('staff_form.html', staff=None, shops=my_shops)


@app.route('/staff/<int:uid_>/edit', methods=['GET', 'POST'])
@login_required
def staff_edit(uid_):
    if session.get('role') not in ('owner', 'admin'):
        flash('Access denied.', 'danger')
        return redirect(url_for('pos'))
    db = get_db()
    uid = session['user_id']
    role = session.get('role')
    if role == 'admin':
        staff = db.execute("SELECT * FROM users WHERE id=? AND role='staff'", (uid_,)).fetchone()
        my_shops = [dict(r) for r in db.execute("SELECT * FROM shops ORDER BY name").fetchall()]
    else:
        staff = db.execute(
            "SELECT * FROM users WHERE id=? AND created_by=? AND role='staff'", (uid_, uid)
        ).fetchone()
        my_shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? ORDER BY name", (uid,)
        ).fetchall()]
    if not staff:
        flash('Staff member not found.', 'danger')
        db.close()
        return redirect(url_for('staff_list'))
    if request.method == 'POST':
        f = request.form
        new_hash = staff['password_hash']
        if f.get('password'):
            new_hash = generate_password_hash(f['password'])
        assigned_shop_id = f.get('assigned_shop_id') or None
        db.execute(
            "UPDATE users SET name=?, email=?, password_hash=?, assigned_shop_id=? WHERE id=?",
            (f.get('name', staff['name']),
             f.get('email', staff['email']).strip().lower(),
             new_hash, assigned_shop_id, uid_)
        )
        db.commit()
        db.close()
        flash('Staff account updated.', 'success')
        return redirect(url_for('staff_list'))
    db.close()
    return render_template('staff_form.html', staff=dict(staff), shops=my_shops)


@app.route('/staff/<int:uid_>/toggle', methods=['POST'])
@login_required
def staff_toggle(uid_):
    if session.get('role') not in ('owner', 'admin'):
        flash('Access denied.', 'danger')
        return redirect(url_for('pos'))
    db = get_db()
    uid = session['user_id']
    role = session.get('role')
    if role == 'admin':
        staff = db.execute("SELECT * FROM users WHERE id=? AND role='staff'", (uid_,)).fetchone()
    else:
        staff = db.execute(
            "SELECT * FROM users WHERE id=? AND created_by=? AND role='staff'", (uid_, uid)
        ).fetchone()
    if staff:
        db.execute("UPDATE users SET is_active=? WHERE id=?",
                   (0 if staff['is_active'] else 1, uid_))
        db.commit()
    db.close()
    return redirect(url_for('staff_list'))


@app.route('/staff/<int:uid_>/performance')
@login_required
def staff_performance(uid_):
    if session.get('role') not in ('owner', 'admin'):
        flash('Access denied.', 'danger')
        return redirect(url_for('pos'))
    db = get_db()
    uid = session['user_id']
    role = session.get('role')
    if role == 'admin':
        staff = db.execute("SELECT * FROM users WHERE id=? AND role='staff'", (uid_,)).fetchone()
    else:
        staff = db.execute(
            "SELECT * FROM users WHERE id=? AND created_by=? AND role='staff'", (uid_, uid)
        ).fetchone()
    if not staff:
        flash('Staff member not found.', 'danger')
        db.close()
        return redirect(url_for('staff_list'))
    staff = dict(staff)
    sessions_log = [dict(r) for r in db.execute(
        "SELECT * FROM user_sessions WHERE user_id=? ORDER BY login_at DESC LIMIT 50",
        (uid_,)
    ).fetchall()]
    sales = [dict(r) for r in db.execute(
        "SELECT * FROM sales WHERE cashier=? AND shop_id=? AND status='completed' "
        "ORDER BY created_at DESC LIMIT 100",
        (staff['name'], staff['assigned_shop_id'])
    ).fetchall()]
    stats = dict(db.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(total),0) as total FROM sales "
        "WHERE cashier=? AND shop_id=? AND status='completed'",
        (staff['name'], staff['assigned_shop_id'])
    ).fetchone())
    shop_row = db.execute("SELECT name FROM shops WHERE id=?",
                          (staff['assigned_shop_id'],)).fetchone() if staff['assigned_shop_id'] else None
    db.close()
    return render_template('staff_performance.html', staff=staff,
                           sessions_log=sessions_log, sales=sales, stats=stats,
                           shop_name=shop_row['name'] if shop_row else 'Unknown')


# ══════════════════════════════════════════════════════════════════════════════
# Combined Dashboard (owners with multiple shops)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/dashboard/combined')
@login_required
def dashboard_combined():
    if session.get('role') == 'staff':
        flash('Access denied.', 'danger')
        return redirect(url_for('pos'))
    db = get_db()
    uid = session['user_id']
    role = session.get('role')
    if role == 'admin':
        shops = [dict(r) for r in db.execute(
            "SELECT s.*, u.name as owner_name FROM shops s JOIN users u ON s.owner_id=u.id ORDER BY s.name"
        ).fetchall()]
    else:
        shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE owner_id=? ORDER BY name", (uid,)
        ).fetchall()]

    period = int(request.args.get('period', 30))
    date_from = (date.today() - timedelta(days=period)).isoformat()
    date_to = today_str()

    totals = {'revenue': 0.0, 'expenses': 0.0, 'profit': 0.0, 'sales_count': 0, 'tax': 0.0}
    shops_data = []
    for shop in shops:
        shop_id = shop['id']
        rev_row = db.execute(
            "SELECT COALESCE(SUM(total),0) as rev, COALESCE(SUM(tax_amount),0) as tax, COUNT(*) as cnt "
            "FROM sales WHERE shop_id=? AND status='completed' AND DATE(created_at) BETWEEN ? AND ?",
            (shop_id, date_from, date_to)
        ).fetchone()
        exp_row = db.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM expenses "
            "WHERE shop_id=? AND expense_date BETWEEN ? AND ?",
            (shop_id, date_from, date_to)
        ).fetchone()
        rev = float(rev_row['rev'])
        exp = float(exp_row['total'])
        tax = float(rev_row['tax'])
        profit = rev - exp
        shop['revenue'] = rev
        shop['expenses'] = exp
        shop['profit'] = profit
        shop['sales_count'] = rev_row['cnt']
        shop['tax'] = tax
        totals['revenue'] += rev
        totals['expenses'] += exp
        totals['profit'] += profit
        totals['sales_count'] += rev_row['cnt']
        totals['tax'] += tax
        shops_data.append(shop)
    db.close()
    return render_template('dashboard_combined.html', shops=shops_data, totals=totals,
                           period=period, date_from=date_from, date_to=date_to)


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


@app.route('/api/version')
def api_version():
    """Android APK version check — bump APP_VERSION_CODE in Railway env to trigger update."""
    base_url = request.host_url.rstrip('/')
    return jsonify({
        'version_code':  int(os.environ.get('APP_VERSION_CODE', '1')),
        'version_name':  os.environ.get('APP_VERSION_NAME', '1.0.0'),
        'download_url':  os.environ.get('APP_DOWNLOAD_URL', f'{base_url}/static/downloads/TronicPOS.apk'),
        'changelog':     os.environ.get('APP_CHANGELOG', 'Bug fixes and improvements.'),
        'force_update':  os.environ.get('APP_FORCE_UPDATE', '0') == '1',
    })


@app.route('/api/printer/receipt-data/<int:sale_id>')
@login_required
def api_printer_receipt_data(sale_id):
    """Return full receipt data as JSON for ESC/POS printing from Android."""
    db = get_db()
    if session.get('role') == 'admin':
        sale = db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    else:
        sale = db.execute(
            "SELECT * FROM sales WHERE id=? AND shop_id=?", (sale_id, sid())
        ).fetchone()
    if not sale:
        db.close()
        return jsonify({'error': 'Receipt not found'}), 404

    items    = db.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    payments = db.execute("SELECT * FROM payments   WHERE sale_id=?", (sale_id,)).fetchall()
    shop     = db.execute("SELECT * FROM shops      WHERE id=?", (sale['shop_id'],)).fetchone()

    customer_name = None
    if sale['customer_id']:
        c = db.execute("SELECT name FROM customers WHERE id=?", (sale['customer_id'],)).fetchone()
        if c:
            customer_name = c['name']

    db.close()
    sale_dict = dict(sale)
    sale_dict['customer_name'] = customer_name

    return jsonify({
        'shop':     dict(shop) if shop else {},
        'sale':     sale_dict,
        'items':    [dict(i) for i in items],
        'payments': [dict(p) for p in payments],
    })


@app.route('/manifest.json')
def pwa_manifest():
    """PWA Web App Manifest — required for TWA/installable PWA."""
    manifest = {
        "name": "Tronic POS",
        "short_name": "TronicPOS",
        "description": "Zimbabwe Point-of-Sale — EcoCash, ZIMRA VAT, Inventory",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#1e293b",
        "theme_color": "#1e293b",
        "lang": "en",
        "scope": "/",
        "icons": [
            {"src": "/static/icons/icon-72.png",  "sizes": "72x72",   "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-96.png",  "sizes": "96x96",   "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-128.png", "sizes": "128x128", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-144.png", "sizes": "144x144", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-152.png", "sizes": "152x152", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-384.png", "sizes": "384x384", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "categories": ["business", "finance", "productivity"],
        "screenshots": [],
    }
    return jsonify(manifest)


@app.route('/.well-known/assetlinks.json')
def assetlinks():
    """Android TWA Digital Asset Links — allows APK to run without browser chrome."""
    default_links = json.dumps([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "com.tronic.pos",
            "sha256_cert_fingerprints": [
                "A9:AE:A4:A6:96:0D:91:68:E9:63:39:97:1E:F1:48:D8:58:B0:2E:A3:0B:4B:D8:31:64:3F:E2:10:6D:8F:64:EA"
            ]
        }
    }])
    links = json.loads(os.environ.get('ASSETLINKS_JSON', default_links))
    return jsonify(links)


@app.route('/api/connectivity')
def api_connectivity():
    """Online/offline status for the desktop app."""
    try:
        import sync_manager
        is_online = sync_manager.online()
    except Exception:
        import socket
        try:
            socket.setdefaulttimeout(2)
            socket.getaddrinfo('firebaseio.com', 443)
            is_online = True
        except Exception:
            is_online = False
    return jsonify({'online': is_online, 'desktop_mode': DESKTOP_MODE})


@app.route('/api/sync/push-all', methods=['POST'])
@admin_required
def api_sync_push_all():
    """Push all local data to Firebase (admin only). Desktop use."""
    db = get_db()
    try:
        shops = [dict(r) for r in db.execute(
            "SELECT * FROM shops WHERE is_active=1"
        ).fetchall()]
        counts = {'shops': 0, 'products': 0, 'sales': 0, 'expenses': 0,
                  'suppliers': 0, 'customers': 0}
        for shop in shops:
            sid_ = shop['id']
            firebase_sync.sync_shop(shop)
            counts['shops'] += 1
            for row in db.execute(
                "SELECT * FROM products WHERE shop_id=? AND is_active=1", (sid_,)
            ).fetchall():
                firebase_sync.sync_product(sid_, dict(row))
                counts['products'] += 1
            for row in db.execute(
                "SELECT * FROM sales WHERE shop_id=? AND status='completed' "
                "ORDER BY created_at DESC LIMIT 500", (sid_,)
            ).fetchall():
                firebase_sync.sync_sale(sid_, dict(row))
                counts['sales'] += 1
            for row in db.execute(
                "SELECT * FROM expenses WHERE shop_id=? ORDER BY created_at DESC LIMIT 200",
                (sid_,)
            ).fetchall():
                firebase_sync.sync_expense(sid_, dict(row))
                counts['expenses'] += 1
            for row in db.execute(
                "SELECT * FROM suppliers WHERE shop_id=?", (sid_,)
            ).fetchall():
                firebase_sync.sync_supplier(sid_, dict(row))
                counts['suppliers'] += 1
            for row in db.execute(
                "SELECT * FROM customers WHERE shop_id=?", (sid_,)
            ).fetchall():
                firebase_sync.sync_customer(sid_, dict(row))
                counts['customers'] += 1
        db.close()
        return jsonify({'success': True, 'synced': counts})
    except Exception as ex:
        db.close()
        return jsonify({'error': str(ex)}), 500


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
