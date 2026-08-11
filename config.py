import os

# --- Fill these in (or set as environment variables) ---

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

# Your own Telegram numeric user ID — you'll use this account to confirm payments.
# Get it by messaging @userinfobot on Telegram.
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Card number / phone shown to buyers for Click/Payme/card transfer.
PAYMENT_INSTRUCTIONS = os.getenv(
    "PAYMENT_INSTRUCTIONS",
    "Karta: 8600 XXXX XXXX XXXX (F.I.Sh.)\n"
    "To'lovdan so'ng screenshot yuboring, biz tasdiqlaymiz."
)

VOLUME_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_PATH = os.getenv("DB_PATH", os.path.join(VOLUME_PATH, "lawquiz.db"))
