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

# Shown next to payment info and the daily-limit paywall, for trust and support questions.
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@xakimov_63")

# Money-back guarantee window, counted from the day the purchase is CONFIRMED
# (not the day it was requested). Admin processes refunds manually with /refund.
REFUND_DAYS = int(os.getenv("REFUND_DAYS", "3"))

VOLUME_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_PATH = os.getenv("DB_PATH", os.path.join(VOLUME_PATH, "lawquiz.db"))

# One-time price for full access to ALL topics (not per-topic — a single payment unlocks everything).
FULL_ACCESS_PRICE_UZS = int(os.getenv("FULL_ACCESS_PRICE_UZS", "12000"))

# --- Mini App sync ---
# When the admin confirms or refunds a payment here, the bot pings the Mini
# App's backend so premium status stays in sync on both surfaces automatically.
# MINIWEB_ADMIN_URL is the Mini App backend's base URL (no trailing slash).
# MINIWEB_ADMIN_KEY must match the ADMIN_KEY set on that Render service.
MINIWEB_ADMIN_URL = os.getenv("MINIWEB_ADMIN_URL", "https://telegram-mini-ai.onrender.com")
MINIWEB_ADMIN_KEY = os.getenv("MINIWEB_ADMIN_KEY", "")
