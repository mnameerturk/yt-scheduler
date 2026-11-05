import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
CLIENT_SECRET_FILE = ROOT / os.getenv("CLIENT_SECRET_FILE", "client_secret.json")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
# While the YouTube API audit is pending, uploads are forced private anyway.
TEST_MODE = os.getenv("TEST_MODE", "1") == "1"
# Optional override for every upload: public, unlisted or private (handy for testing after the audit)
UPLOAD_PRIVACY = os.getenv("UPLOAD_PRIVACY", "").strip().lower()
# "shared": all videos stored in one Google account's Drive; "per_channel": each channel's own Drive
DRIVE_MODE = os.getenv("DRIVE_MODE", "shared")
DATA_DIR = ROOT / os.getenv("DATA_DIR", "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "thumbs").mkdir(exist_ok=True)

REDIRECT_URI = BASE_URL + "/oauth/callback"
DRIVE_FOLDER_NAME = "NMR Scheduler"
CHUNK = 16 * 1024 * 1024  # multiple of 256 KB, as Google requires
