"""First-run setup: writes the .env file."""
import getpass
import secrets
from pathlib import Path

ROOT = Path(__file__).parent
env = ROOT / ".env"
if env.exists():
    print(".env already exists, keeping it.")
    raise SystemExit
print("NMR Scheduler first-time setup\n")
domain = input("ngrok domain [dayroom-cinch-scorer.ngrok-free.dev]: ").strip() or "dayroom-cinch-scorer.ngrok-free.dev"
pw = getpass.getpass("Choose an app password (leave empty to generate one): ").strip()
if not pw:
    pw = secrets.token_urlsafe(12)
    print(f"\nYour app password is:  {pw}\nWrite it down; the phone app and PC tool need it.\n")
tz = input("Your timezone [Asia/Kolkata]: ").strip() or "Asia/Kolkata"
env.write_text(
    f"BASE_URL=https://{domain}\nAPP_PASSWORD={pw}\nTIMEZONE={tz}\n"
    "CLIENT_SECRET_FILE=client_secret.json\nTEST_MODE=1\nDRIVE_MODE=shared\n"
)
print("Saved .env")
