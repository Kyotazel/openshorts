import os
import sys

# Make the repo root importable so tests can import the app modules directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests always run the app in self-host (BYOK) mode. app.py freezes
# BILLING_ENABLED at import time and load_dotenv never overrides an existing
# variable, so this must be set here — before any test module imports app — or
# the suite's behavior would depend on the developer's personal .env.
os.environ["BILLING_ENABLED"] = "0"

# Notifikasi tidak boleh keluar sungguhan saat tes. app.py memanggil
# load_dotenv(), jadi tanpa ini .env pengembang memasok token Telegram asli
# dan suite mengirim pesan betulan ke chat sungguhan. Tes yang memang ingin
# menguji jalur "notifikasi hidup" menghapus variabel ini sendiri lewat
# monkeypatch.delenv.
os.environ["TELEGRAM_DISABLED"] = "1"
