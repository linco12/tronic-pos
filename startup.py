"""
Runs at server start (before gunicorn on Railway, or called by main.py on desktop).
Ensures DB directory exists, initialises tables, and creates the admin account.
"""
import os


def do_startup():
    db_path = os.environ.get('DATABASE_PATH', 'tronic_pos.db')
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    from database import init_db
    from app import create_admin
    init_db()
    create_admin()


if __name__ == '__main__':
    do_startup()
    print('Startup complete.')
