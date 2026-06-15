# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Tronic POS desktop app
# Build: pyinstaller tronic_pos.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Flask app sources
        ('app.py',          '.'),
        ('database.py',     '.'),
        ('startup.py',      '.'),
        ('sync_manager.py', '.'),
        # Templates and static assets
        ('templates',       'templates'),
        ('static',          'static'),
        # Firebase service account key (if present)
        # ('serviceAccountKey.json', '.'),
    ],
    hiddenimports=[
        # Flask ecosystem
        'flask',
        'werkzeug',
        'werkzeug.routing',
        'werkzeug.security',
        'werkzeug.middleware.proxy_fix',
        'jinja2',
        'jinja2.ext',
        'click',
        'itsdangerous',
        # Database
        'sqlite3',
        # Date utilities
        'dateutil',
        'dateutil.parser',
        'dateutil.relativedelta',
        # Firebase Admin SDK
        'firebase_admin',
        'firebase_admin.credentials',
        'firebase_admin.db',
        'firebase_admin._http_client',
        'firebase_admin._utils',
        'google.auth',
        'google.auth.transport',
        'google.auth.transport.requests',
        'google.oauth2',
        'google.oauth2.service_account',
        'google.api_core',
        # Requests / urllib
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        # Environment
        'dotenv',
        # pywebview (6.x) — Windows uses EdgeChromium/WebView2 by default
        'webview',
        'webview.window',
        'webview.event',
        'webview.menu',
        'webview.screen',
        'webview.util',
        'webview.js',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'clr',
        'clr_loader',
        # Threading
        'threading',
        'queue',
        'socket',
    ],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'PyQt5',
        'PyQt6',
        'wx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TronicPOS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX can break some DLLs; keep off by default
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # No console window — UI is the webview
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/favicon.ico' if Path('static/favicon.ico').exists() else None,
    version_file=None,
    # Windows manifest — request admin if needed for WebView2 registration
    uac_admin=False,
    uac_uiaccess=False,
)
