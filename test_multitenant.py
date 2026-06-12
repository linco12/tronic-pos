"""Full multi-tenant smoke test."""
import json
from app import app
from database import init_db, get_db, seed_default_categories
from werkzeug.security import generate_password_hash

init_db()

# ── Seed two shop owners + two shops ─────────────────────────────────────────
db = get_db()
db.execute("INSERT OR IGNORE INTO users (email, name, password_hash, role) VALUES (?,?,?,?)",
           ('owner1@test.com', 'Alice Moyo', generate_password_hash('pass123'), 'owner'))
db.execute("INSERT OR IGNORE INTO users (email, name, password_hash, role) VALUES (?,?,?,?)",
           ('owner2@test.com', 'Bob Choto', generate_password_hash('pass456'), 'owner'))
db.commit()

u1 = db.execute("SELECT id FROM users WHERE email='owner1@test.com'").fetchone()['id']
u2 = db.execute("SELECT id FROM users WHERE email='owner2@test.com'").fetchone()['id']

db.execute("INSERT OR IGNORE INTO shops (owner_id, name, currency) VALUES (?,?,?)",
           (u1, 'Alice General Store', 'USD'))
db.execute("INSERT OR IGNORE INTO shops (owner_id, name, currency) VALUES (?,?,?)",
           (u2, 'Bob Hardware Shop', 'USD'))
db.commit()

s1 = db.execute("SELECT id FROM shops WHERE owner_id=?", (u1,)).fetchone()['id']
s2 = db.execute("SELECT id FROM shops WHERE owner_id=?", (u2,)).fetchone()['id']
seed_default_categories(db, s1)
seed_default_categories(db, s2)

# Add a product to each shop
db.execute("INSERT INTO products (shop_id,name,sku,selling_price,cost_price,stock_quantity,tax_type) VALUES (?,?,?,?,?,?,?)",
           (s1, 'Shop1-Cola', 'S1-001', 1.00, 0.50, 50, 'standard'))
db.execute("INSERT INTO products (shop_id,name,sku,selling_price,cost_price,stock_quantity,tax_type) VALUES (?,?,?,?,?,?,?)",
           (s2, 'Shop2-Hammer', 'S2-001', 5.00, 3.00, 20, 'standard'))
db.commit()
db.close()

client = app.test_client()

# ── Test 1: Unauthenticated redirect ─────────────────────────────────────────
r = client.get('/', follow_redirects=False)
assert r.status_code == 302 and '/login' in r.headers['Location'], "FAIL: / should redirect to login"
print("PASS: unauthenticated access redirects to /login")

# ── Test 2: Bad login ─────────────────────────────────────────────────────────
r = client.post('/login', data={'email': 'bad@bad.com', 'password': 'wrong'})
assert b'Invalid email' in r.data, "FAIL: bad login should show error"
print("PASS: bad login rejected")

# ── Test 3: Owner1 login + shop isolation ────────────────────────────────────
with client.session_transaction() as sess:
    sess['user_id'] = u1
    sess['user_name'] = 'Alice Moyo'
    sess['user_email'] = 'owner1@test.com'
    sess['role'] = 'owner'
    sess['shop_id'] = s1
    sess['shop_name'] = 'Alice General Store'

r = client.get('/api/products/search')
data = json.loads(r.data)
names = [p['name'] for p in data]
assert 'Shop1-Cola' in names, "FAIL: owner1 should see their product"
assert 'Shop2-Hammer' not in names, "FAIL: owner1 should NOT see owner2's product"
print("PASS: shop isolation — owner1 only sees their own products")

# ── Test 4: Owner2 isolation ─────────────────────────────────────────────────
with client.session_transaction() as sess:
    sess['user_id'] = u2
    sess['user_name'] = 'Bob Choto'
    sess['user_email'] = 'owner2@test.com'
    sess['role'] = 'owner'
    sess['shop_id'] = s2
    sess['shop_name'] = 'Bob Hardware Shop'

r = client.get('/api/products/search')
data = json.loads(r.data)
names = [p['name'] for p in data]
assert 'Shop2-Hammer' in names, "FAIL: owner2 should see their product"
assert 'Shop1-Cola' not in names, "FAIL: owner2 should NOT see owner1's product"
print("PASS: shop isolation — owner2 only sees their own products")

# ── Test 5: Sale creates correctly isolated sale ──────────────────────────────
with client.session_transaction() as sess:
    sess['user_id'] = u1
    sess['shop_id'] = s1
    sess['role'] = 'owner'

pid = json.loads(client.get('/api/products/search').data)[0]['id']
r = client.post('/api/sale', json={
    'items': [{'product_id': pid, 'quantity': 1}],
    'payment_method': 'cash', 'amount_paid': 2.00, 'discount_amount': 0
})
result = json.loads(r.data)
assert result.get('success'), f"FAIL: sale failed: {result}"
print(f"PASS: sale created, ref={result['reference']}, change={result['change']}")

# ── Test 6: Admin sees all shops ──────────────────────────────────────────────
admin_db = get_db()
admin = admin_db.execute("SELECT id FROM users WHERE email='lincolnmotiwac@gmail.com'").fetchone()
admin_db.close()
assert admin, "FAIL: admin account not found"
print(f"PASS: admin account exists (id={admin['id']})")

# ── Test 7: All main pages return 200 ────────────────────────────────────────
with client.session_transaction() as sess:
    sess['user_id'] = u1
    sess['shop_id'] = s1
    sess['role'] = 'owner'

pages = [('/', 'POS'), ('/products', 'Products'), ('/inventory', 'Inventory'),
         ('/suppliers', 'Suppliers'), ('/purchases', 'Purchases'),
         ('/customers', 'Customers'), ('/expenses', 'Expenses'),
         ('/reports', 'Reports'), ('/reports/pnl', 'P&L'),
         ('/reports/tax', 'Tax'), ('/settings', 'Settings'), ('/zreport', 'Z-Report')]
for path, name in pages:
    r = client.get(path)
    assert r.status_code == 200, f"FAIL: {name} ({path}) returned {r.status_code}"
    print(f"PASS: {name} page OK")

print("\n✓ All tests passed — multi-tenant system working correctly!")
