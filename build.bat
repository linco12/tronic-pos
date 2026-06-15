@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  Tronic POS — Windows Desktop Build Script
REM  Produces: dist\TronicPOS.exe  (self-contained, single file)
REM
REM  Requirements:
REM    Python 3.11+ in PATH
REM    pip install -r requirements_desktop.txt
REM    Microsoft Edge WebView2 Runtime installed on target machine
REM      (download: https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
REM ─────────────────────────────────────────────────────────────────────────────

echo.
echo  ====================================================
echo   Tronic POS — Desktop Build
echo  ====================================================
echo.

REM ── 1. Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found in PATH. Install Python 3.11+ and retry.
    pause & exit /b 1
)

REM ── 2. Install / upgrade desktop dependencies ────────────────────────────────
echo  Installing desktop requirements...
pip install -r requirements_desktop.txt --quiet
if errorlevel 1 (
    echo  ERROR: pip install failed. Check requirements_desktop.txt and your network.
    pause & exit /b 1
)

REM ── 3. Clean previous build ──────────────────────────────────────────────────
echo  Cleaning previous build artefacts...
if exist "build"     rmdir /s /q "build"
if exist "dist"      rmdir /s /q "dist"

REM ── 4. Run PyInstaller ───────────────────────────────────────────────────────
echo  Running PyInstaller...
pyinstaller tronic_pos.spec
if errorlevel 1 (
    echo  ERROR: PyInstaller failed. See output above for details.
    pause & exit /b 1
)

REM ── 5. Post-build: create sample .env next to exe ────────────────────────────
echo  Creating sample .env in dist\ ...
(
    echo # Tronic POS — Firebase configuration
    echo # Place this file next to TronicPOS.exe
    echo # Leave FIREBASE_* values empty to run in fully-offline mode.
    echo.
    echo FIREBASE_DB_URL=
    echo FIREBASE_CREDENTIALS=serviceAccountKey.json
    echo.
    echo # Optional: override admin credentials
    echo ADMIN_EMAIL=lincolnmotiwac@gmail.com
    echo ADMIN_PASSWORD=Admin@Tronic2024!
    echo.
    echo # Secret key for Flask sessions (change this!)
    echo SECRET_KEY=change-me-to-a-long-random-string
) > "dist\.env.sample"

REM ── 6. Copy README ───────────────────────────────────────────────────────────
if exist "DESKTOP_README.txt" copy /y "DESKTOP_README.txt" "dist\" >nul

echo.
echo  ====================================================
echo   BUILD COMPLETE
echo   Executable:  dist\TronicPOS.exe
echo   Sample env:  dist\.env.sample
echo  ====================================================
echo.
echo  NEXT STEPS:
echo   1. Copy dist\TronicPOS.exe to the target machine.
echo   2. Copy dist\.env.sample  → .env  and fill in Firebase details.
echo      (Leave FIREBASE_DB_URL blank to run fully offline.)
echo   3. (Optional) Copy your serviceAccountKey.json next to TronicPOS.exe.
echo   4. Double-click TronicPOS.exe to launch.
echo   5. TronicPOS_Data\tronic_pos.db is created automatically next to the .exe
echo      and persists through updates / reboots.
echo.
pause
