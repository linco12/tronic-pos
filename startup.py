"""
Run once before gunicorn starts (via render.yaml buildCommand or Procfile pre-hook).
Initialises the DB and creates the admin account.
"""
from database import init_db
from app import create_admin

init_db()
create_admin()
print("Startup complete.")
