"""Passenger WSGI Entry Point für Netcup / Plesk Webhosting.

Wird von Phusion Passenger automatisch geladen.

Plesk-Konfiguration:
- Python-Version 3.10 oder 3.11 auswählen
- Application Startup File: passenger_wsgi.py
- Application Entry Point: application

Dieses Script aktiviert AUTOMATISCH die virtuelle Umgebung,
damit Flask & Co. gefunden werden.
"""
import os
import sys

# Verzeichnis dieser Datei (= App-Root)
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _flask_already_importable():
    """Prüft ob Flask im aktuellen Python-Kontext bereits gefunden wird."""
    try:
        import importlib.util
        return importlib.util.find_spec("flask") is not None
    except Exception:
        return False


def _add_vendor_dir():
    """Bindet den 'vendor/' Ordner ein, falls vorhanden.

    Dieser enthält alle Python-Pakete (Flask, SQLAlchemy, ...) als
    lokale Kopie - perfekt für Hosting ohne pip-Zugang (z.B. Netcup-Webhosting).

    Lokal mit Windows-Batch 'build_vendor.bat' erstellen, dann mit hochladen.
    """
    vendor_dir = os.path.join(APP_DIR, "vendor")
    if os.path.isdir(vendor_dir) and vendor_dir not in sys.path:
        # An den Anfang von sys.path setzen damit es Priorität hat
        sys.path.insert(0, vendor_dir)


def _activate_virtualenv():
    """Findet & aktiviert die virtuelle Umgebung (falls Flask noch nicht da ist).

    Sucht in dieser Reihenfolge:
    1. ENV-Variable PYTHON_VENV (wenn gesetzt)
    2. Plesk Standard: /var/www/vhosts/<domain>/.python-venvs/<app>/
    3. Lokale venv im App-Verzeichnis (für Entwicklung)
    """
    # Wenn Flask sowieso schon importierbar ist → nichts tun
    if _flask_already_importable():
        return None

    candidates = []

    # 1. Per Umgebungsvariable explizit
    if os.environ.get("PYTHON_VENV"):
        candidates.append(os.environ["PYTHON_VENV"])

    # 2. Plesk-Standard: bis zu 6 Ebenen hoch suchen nach .python-venvs/
    parent = APP_DIR
    for _ in range(6):
        parent = os.path.dirname(parent)
        if not parent or parent == "/":
            break
        venvs_dir = os.path.join(parent, ".python-venvs")
        if os.path.isdir(venvs_dir):
            for name in sorted(os.listdir(venvs_dir)):
                v = os.path.join(venvs_dir, name)
                if os.path.isdir(v):
                    candidates.append(v)

    # 3. Lokale venv (Entwicklung)
    candidates.append(os.path.join(APP_DIR, "venv"))
    candidates.append(os.path.join(APP_DIR, ".venv"))

    for venv_path in candidates:
        # site-packages-Pfade direkt einbinden (schneller als execl)
        added = False
        for lib_dir in ("lib", "lib64"):
            for py_ver in ("python3.13", "python3.12", "python3.11",
                           "python3.10", "python3.9"):
                site = os.path.join(venv_path, lib_dir, py_ver, "site-packages")
                if os.path.isdir(site) and site not in sys.path:
                    sys.path.insert(0, site)
                    added = True
        if added and _flask_already_importable():
            return venv_path

    # Letzter Versuch: re-exec mit venv-Python falls vorhanden
    for venv_path in candidates:
        py_bin = os.path.join(venv_path, "bin", "python")
        if (os.path.isfile(py_bin)
                and os.path.realpath(sys.executable) != os.path.realpath(py_bin)):
            try:
                os.execl(py_bin, py_bin, *sys.argv)
            except OSError:
                continue
    return None


# Reihenfolge ist wichtig:
# 1. ZUERST vendor/ einbinden (höchste Priorität für eigene Kopie)
_add_vendor_dir()
# 2. DANN venv aktivieren (falls vendor/ leer war)
_activate_virtualenv()

# App-Verzeichnis in sys.path
sys.path.insert(0, APP_DIR)

# .env laden (falls python-dotenv installiert)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(APP_DIR, ".env"))
except ImportError:
    pass

# Flask-App importieren
from app import app as application  # Passenger erwartet 'application'


if __name__ == "__main__":
    application.run()
