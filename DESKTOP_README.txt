=====================================================
  TRONIC POS — Desktop App Setup Guide
  Zimbabwe Point-of-Sale  |  Offline-First
=====================================================

WHAT YOU GET
  TronicPOS.exe — single-file Windows app.
  No server, no internet required to start.
  All data stored locally in TronicPOS_Data\ next to the .exe.
  Syncs to Firebase automatically when online.

--------------------
  SYSTEM REQUIREMENTS
--------------------
  • Windows 10 or 11 (64-bit)
  • Microsoft Edge WebView2 Runtime
      Download: https://developer.microsoft.com/microsoft-edge/webview2/
      (Most Windows 11 machines already have this installed.)
  • No Python installation needed on end-user machines — .exe is self-contained.

--------------------
  FIRST-TIME SETUP
--------------------
  1. Create a folder, e.g. C:\TronicPOS\

  2. Copy TronicPOS.exe into that folder.

  3. Copy .env.sample → .env in the same folder and edit it:

       FIREBASE_DB_URL=https://your-project.firebaseio.com
       FIREBASE_CREDENTIALS=serviceAccountKey.json
       ADMIN_EMAIL=your@email.com
       ADMIN_PASSWORD=YourStrongPassword!
       SECRET_KEY=any-long-random-string-here

     If you want to run FULLY OFFLINE (no Firebase at all):
       Leave FIREBASE_DB_URL empty.

  4. (If using Firebase) Copy your serviceAccountKey.json into the same folder.

  5. Double-click TronicPOS.exe.
     On first run:
       • TronicPOS_Data\ folder is created automatically.
       • Database (tronic_pos.db) is initialised.
       • Admin account is created from .env credentials.
       • App opens in a native window.

  6. Log in with the admin email/password from your .env.

--------------------
  FOLDER STRUCTURE
--------------------
  C:\TronicPOS\
    TronicPOS.exe          ← the app
    .env                   ← your config (keep private!)
    serviceAccountKey.json ← Firebase key (optional)
    TronicPOS_Data\
      tronic_pos.db        ← local SQLite database (all your data)

--------------------
  OFFLINE / ONLINE BEHAVIOUR
--------------------
  OFFLINE:
    • All sales, products, inventory work normally.
    • Data saved to tronic_pos.db only.
    • Status badge in top bar shows "Offline".

  ONLINE:
    • App detects internet every 30 seconds.
    • Badge switches to "Online".
    • New sales/records automatically synced to Firebase.
    • Admin can press "Sync" button to push ALL local data to Firebase.

  POWER-OFF / REBOOT:
    • tronic_pos.db persists — all data is safe.
    • App resumes exactly where it left off.
    • Previously logged-in users are remembered.

--------------------
  THERMAL RECEIPT PRINTER
--------------------
  • Use any 80mm USB/Bluetooth thermal receipt printer.
  • In Windows, install the printer driver and set it as default.
  • When a sale completes, the receipt window opens and auto-prints.
  • If auto-print doesn't trigger, click the "Print Receipt" button.
  • Recommended paper width setting in printer driver: 80mm / 72mm printable.

--------------------
  UPDATES
--------------------
  • Replace TronicPOS.exe with the new version.
  • TronicPOS_Data\ (your database) is untouched.
  • Settings in .env are untouched.

--------------------
  TROUBLESHOOTING
--------------------
  App won't open:
    → Install Edge WebView2 Runtime (see requirements above).

  White screen / crashes immediately:
    → Check that .env exists next to TronicPOS.exe.
    → Make sure SECRET_KEY is set.

  Firebase not syncing:
    → Check FIREBASE_DB_URL and serviceAccountKey.json are correct.
    → App works fine without Firebase — just stays local.

  Receipt not printing:
    → Set your thermal printer as the Windows default printer.
    → In browser print dialog, select "No margins" and "80mm" paper size.

  Lost admin password:
    → Edit ADMIN_PASSWORD in .env and restart TronicPOS.exe.

=====================================================
  Support: lincolnmotiwac@gmail.com
  Powered by Tronic POS
=====================================================
