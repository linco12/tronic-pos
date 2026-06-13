"""
Runs at server start (before gunicorn).
Ensures the DB directory exists, initialises tables, and creates the admin account.
"""
import os

# Create the database directory if it doesn't exist (needed for Railway volumes)
db_path = os.environ.get('DATABASE_PATH', 'tronic_pos.db')
db_dir = os.path.dirname(db_path)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

from database import init_db
from app import create_admin

init_db()
create_admin()
print("Startup complete.")
