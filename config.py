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

# Optional: set these to show the card number as a tap-to-copy code block
# instead of plain text buried inside PAYMENT_INSTRUCTIONS (much easier to
# copy on mobile). If CARD_NUMBER is left blank, the bot falls back to
# showing PAYMENT_INSTRUCTIONS as-is (not copyable), so nothing breaks if
# you don't set these.
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_HOLDER_NAME = os.getenv("CARD_HOLDER_NAME", "")

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

# URL that opens when the user taps the "🎯 Mock" button on the main menu -
# this is what the USER sees (the frontend), which may differ from
# MINIWEB_ADMIN_URL above (that one is only used for server-to-server admin
# calls). Defaults to the same URL if you haven't set a separate one. Must
# be https - Telegram won't open a plain http web_app button.
MINIWEB_APP_URL = os.getenv("MINIWEB_APP_URL", MINIWEB_ADMIN_URL)

# --- Exam countdown ---
# Date of the "milliy sertifikat" exam, shown as a big countdown on /start.
# Format: YYYY-MM-DD (e.g. "2026-12-15"). Leave blank to hide the countdown
# until you know/decide the date.
EXAM_DATE = os.getenv("EXAM_DATE", "")

# --- Public stats channel ---
# Telegram channel where you post live usage numbers (e.g. "N ta talaba
# foydalanmoqda") to build trust. Add the bot as admin of that channel first,
# then set this to the channel's numeric ID (starts with -100...) or its
# @username. Leave blank to disable /postchannel.
STATS_CHANNEL_ID = os.getenv("STATS_CHANNEL_ID", "")

# Minimum number of answered questions in a topic before we're confident
# enough to call it the user's "weak topic" and recommend it.
WEAK_TOPIC_MIN_ANSWERS = int(os.getenv("WEAK_TOPIC_MIN_ANSWERS", "5"))

# How many hours of inactivity before a user gets a "come back" reminder.
INACTIVITY_REMINDER_HOURS = int(os.getenv("INACTIVITY_REMINDER_HOURS", "24"))

# --- AI chat (free, general-purpose assistant button) ---
# Get a free key at https://aistudio.google.com -> "Get API key". No card needed.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Check https://ai.google.dev/gemini-api/docs/models for the current free-tier
# model name if this one ever stops working - Google renames/retires models
# over time, so this is deliberately an env var you can update without a
# code change.
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.0-flash")

# Free questions per user - LIFETIME total, not per day. After this many,
# the AI button redirects to the payment flow instead of answering (the
# remaining count is never shown to free users - they just see the paywall
# once they hit it). Paid/premium users are always unlimited. Change this
# anytime on Railway without a redeploy.
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "5"))
