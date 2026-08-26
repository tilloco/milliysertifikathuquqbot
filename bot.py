import asyncio
import datetime
import logging
import random
import re

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    TelegramObject,
    BufferedInputFile,
    WebAppInfo,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import database as db

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

MODULE_SIZE = 10

BTN_TESTLAR = "📝 Testlar"
BTN_TAKLIF = "💡 Taklif-fikr"
BTN_REYTING = "🏆 Reyting"
BTN_TARIX = "🕐 Tarix"
BTN_AI = "🤖 AI yordamchi"
BTN_TOLOV = "💳 To'lov"
BTN_MOCK = "🎯 Mock"

AI_SYSTEM_PROMPT = (
    "Siz do'stona, umumiy AI yordamchisiz. Har qanday savolga - huquq, ta'lim, "
    "kundalik hayot, umumiy bilim, maslahat va h.k. - tabiiy va tushunarli o'zbek "
    "tilida javob bering. Javobni juda uzun qilmang - Telegram xabari sifatida "
    "o'qish qulay bo'lsin."
)

# Shown wherever a buyer is deciding whether to pay - the guarantee and a
# real person to ask reduce the "will I get scammed" hesitation that's the
# biggest reason someone with money in hand still doesn't buy.
TRUST_NOTE = (
    f"🛡 <b>Kafolat:</b> xarid qilgan kundan boshlab {config.REFUND_DAYS} kun ichida "
    f"biron sababga ko'ra qoniqmasangiz — pulingiz to'liq va so'zsiz qaytariladi.\n"
    f"❓ Savollar uchun: {config.SUPPORT_USERNAME}"
)


def payment_card_block():
    """Card details for a payment message. If CARD_NUMBER is set, the number
    renders as a <code> block - on mobile Telegram, tapping a code block
    copies it instantly, much easier than long-pressing plain text. Falls
    back to the legacy PAYMENT_INSTRUCTIONS text if CARD_NUMBER isn't set."""
    if config.CARD_NUMBER:
        holder_line = f"\n👤 Karta egasi: {config.CARD_HOLDER_NAME}" if config.CARD_HOLDER_NAME else ""
        return f"💳 Karta raqami (bosib nusxalang):\n<code>{config.CARD_NUMBER}</code>{holder_line}"
    return config.PAYMENT_INSTRUCTIONS

# Rotated in the 24h-inactivity reminder so it doesn't feel like a copy-paste bot message.
MOTIVATIONAL_LINES = [
    "Maqsadlar harakat bilan zafar topadi. Bugun yana bir qadam tashla! 💪",
    "Har kuni 10 daqiqa — imtihon kunida katta farq qiladi. Davom etaylik!",
    "Bilim — eng ishonchli sarmoya. Bugun ozgina vaqt ajrat!",
    "Kichik qadamlar katta natijalarga olib keladi. Hoziroq boshla!",
]


async def sync_premium_to_miniweb(telegram_id: int, is_premium: bool) -> bool:
    """Tells the Mini App backend to update this user's premium status, so a
    payment confirmed/refunded here takes effect there too without any manual
    step. Never raises - a sync failure shouldn't break the confirm/refund
    flow in the bot itself; admin can retry with /syncpremium if it fails."""
    if not config.MINIWEB_ADMIN_KEY:
        logging.warning("MINIWEB_ADMIN_KEY not set - skipping Mini App premium sync")
        return False
    url = f"{config.MINIWEB_ADMIN_URL}/admin/set-premium?key={config.MINIWEB_ADMIN_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"telegram_id": telegram_id, "is_premium": is_premium},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logging.warning(f"Mini App premium sync failed ({resp.status}): {body}")
                    return False
                return True
    except Exception as e:
        logging.warning(f"Mini App premium sync error: {e}")
        return False


class AddQuestion(StatesGroup):
    waiting_quiz_id = State()
    waiting_question = State()
    waiting_options = State()
    waiting_correct = State()
    waiting_explanation = State()
    waiting_bulk_file = State()


class Feedback(StatesGroup):
    waiting_text = State()


class AIChat(StatesGroup):
    waiting_question = State()


class Onboarding(StatesGroup):
    waiting_name = State()
    waiting_reason = State()
    waiting_target = State()
    waiting_level = State()
    waiting_time = State()


def parse_bulk_questions(text):
    """Parse a '===' separated block of questions into a list of dicts.
    An optional 'Mavzu: <tag>' line (any position) tags the question with
    which topic/branch it represents - used by the assessment test to
    report per-branch weak points."""
    option_pattern = re.compile(r'^[A-D]\)\s*')
    questions = []
    for block in text.split("==="):
        lines = [l.rstrip() for l in block.strip("\n").split("\n") if l.strip()]
        if not lines:
            continue
        question_lines, options, explanation_lines = [], [], []
        correct_index = None
        topic_tag = None
        mode = "question"
        for line in lines:
            if option_pattern.match(line):
                mode = "options"
                options.append(line.strip())
            elif line.lower().startswith("javob:"):
                letter = line.split(":", 1)[1].strip().upper()
                if letter:
                    correct_index = ord(letter[0]) - ord('A')
                mode = "after_javob"
            elif line.lower().startswith("izoh:"):
                mode = "explanation"
                rest = line.split(":", 1)[1].strip()
                if rest:
                    explanation_lines.append(rest)
            elif line.lower().startswith("mavzu:"):
                topic_tag = line.split(":", 1)[1].strip() or None
            elif mode == "question":
                question_lines.append(line)
            elif mode == "explanation":
                explanation_lines.append(line)
        if not options or correct_index is None or not question_lines:
            continue
        questions.append({
            "question_text": "\n".join(question_lines).strip(),
            "options": options,
            "correct_index": correct_index,
            "explanation": "\n".join(explanation_lines).strip() or None,
            "topic_tag": topic_tag,
        })
    return questions


# ---------------- helpers ----------------

def main_reply_keyboard(paid=False):
    """Single 'To'lov' button always shown - it doubles as purchase entry point
    and payment-status check, whether or not the user has paid yet."""
    keyboard = [
        [KeyboardButton(text=BTN_TESTLAR), KeyboardButton(text=BTN_TAKLIF)],
        [KeyboardButton(text=BTN_REYTING), KeyboardButton(text=BTN_TARIX)],
        [KeyboardButton(text=BTN_AI), KeyboardButton(text=BTN_TOLOV)],
    ]
    if config.MINIWEB_APP_URL:
        keyboard.append([
            KeyboardButton(text=BTN_MOCK, web_app=WebAppInfo(url=config.MINIWEB_APP_URL))
        ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


async def ask_gemini(question: str):
    """Returns the AI's answer as text, or None if the call failed (missing
    key, network error, bad response, etc.) - caller shows a friendly
    fallback message in that case rather than crashing.

    Tries config.AI_MODEL first, then falls back to a couple of other
    common model names if that one 404s (Google renames/retires models
    over time, so a stale model name shouldn't take the whole feature down -
    an auth error (401/403, meaning the key itself is wrong) will still fail
    on every model, which is expected."""
    if not config.GEMINI_API_KEY:
        return None

    models_to_try = [config.AI_MODEL, "gemini-2.0-flash", "gemini-1.5-flash"]
    seen = set()
    payload = {
        "contents": [
            {"parts": [{"text": f"{AI_SYSTEM_PROMPT}\n\nSavol: {question}"}]}
        ]
    }

    for model in models_to_try:
        if model in seen:
            continue
        seen.add(model)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={config.GEMINI_API_KEY}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logging.warning(f"Gemini API error on model '{model}' ({resp.status}): {body}")
                        if resp.status in (401, 403):
                            # Bad/missing API key - trying other models won't help.
                            return None
                        continue  # try the next model name
                    data = await resp.json()
                    try:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    except (KeyError, IndexError, TypeError):
                        logging.warning(f"Unexpected Gemini response shape from '{model}': {data}")
                        continue
        except Exception as e:
            logging.warning(f"Gemini API call failed on model '{model}': {e}")
            continue

    return None


def exam_countdown_line():
    """Big banner shown at the top of /start. Returns '' if EXAM_DATE isn't
    configured yet or is invalid/in the past, so nothing breaks if you haven't
    set it."""
    if not config.EXAM_DATE:
        return ""
    try:
        exam_date = datetime.date.fromisoformat(config.EXAM_DATE)
    except ValueError:
        return ""
    days_left = (exam_date - datetime.date.today()).days
    if days_left < 0:
        return ""
    if days_left == 0:
        return "🔥 <b>BUGUN — Milliy sertifikat imtihoni kuni!</b>\n\n"
    word = "kun" if days_left != 1 else "kun"
    return f"⏳ <b>Milliy sertifikatgacha: {days_left} {word} qoldi!</b>\n\n"


def progress_bar(done, total, length=10):
    """Renders a filled/empty block bar, e.g. ▰▰▰▰▱▱▱▱▱▱ for 4/10."""
    if total <= 0:
        return "▱" * length
    filled = round(length * done / total)
    filled = max(0, min(length, filled))
    return "▰" * filled + "▱" * (length - filled)


LEVEL_LABELS = {"boshlangich": "🟢 Boshlang'ich", "orta": "🟡 O'rta", "expert": "🔴 Expert"}


def compute_level(pct):
    if pct >= 80:
        return "expert"
    if pct >= 50:
        return "orta"
    return "boshlangich"


async def send_assessment_result(chat_id, user_id, attempt_id):
    breakdown = db.get_assessment_breakdown(attempt_id)
    if not breakdown:
        await bot.send_message(chat_id, "Natijani hisoblab bo'lmadi. Qaytadan urinib ko'ring.")
        return

    thinking = await bot.send_message(chat_id, "🧠 Natijalaringiz tahlil qilinmoqda...")

    total = sum(row["total"] for row in breakdown)
    correct = sum(row["correct"] or 0 for row in breakdown)
    pct = int(100 * correct / total) if total else 0
    level_code = compute_level(pct)
    db.set_level(user_id, level_code)

    lines = [
        "📊 <b>Darajani aniqlash natijasi</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"✅ Umumiy natija: <b>{correct}/{total}</b> ({pct}%)",
        f"🏅 Darajangiz: <b>{LEVEL_LABELS[level_code]}</b>",
        "",
        "📉 <b>Sohalar bo'yicha tahlil:</b>",
    ]
    for row in breakdown:
        row_pct = int(100 * (row["correct"] or 0) / row["total"]) if row["total"] else 0
        mark = "🔴" if row_pct < 50 else ("🟡" if row_pct < 80 else "🟢")
        lines.append(f"{mark} {row['tag']}: {row['correct'] or 0}/{row['total']} ({row_pct}%)")

    weakest = breakdown[0]
    if weakest["total"]:
        weakest_pct = int(100 * (weakest["correct"] or 0) / weakest["total"])
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"🎯 <b>Eng zaif tomoningiz:</b> {weakest['tag']} ({weakest_pct}%)\n"
            f"Tavsiya: shu sohadagi testlarni birinchi navbatda ishlang — "
            f"aynan shu yer sizni eng ko'p yiqitadi!"
        )

    try:
        await thinking.edit_text("\n".join(lines), parse_mode="HTML")
    except Exception:
        await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    await bot.send_message(chat_id, "Endi asosiy menyudan davom etishingiz mumkin 👇")
    await send_welcome_menu(chat_id, user_id)


async def offer_assessment(chat_id, state: FSMContext):
    """Shown right after onboarding finishes - not forced, since a brand new
    user hasn't seen any content yet and might rather just start browsing."""
    assessment = db.get_assessment_quiz()
    if not assessment:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Darajamni hoziroq bilib olaman", callback_data=f"startassess:{assessment['id']}")
    ]])
    await bot.send_message(
        chat_id,
        "🎯 <b>Eng birinchi qadam</b>\n\n"
        "15 ta savoldan iborat qisqa test orqali hozirgi bilim darajangizni "
        "va eng zaif tomoningizni aniqlab olamiz — shundan keyin qayerdan "
        "boshlashni aniq bilasiz 👇",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("startassess:"))
async def on_start_assessment(callback: CallbackQuery):
    quiz_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    db.start_attempt(user_id, quiz_id, module_number=1)
    attempt = db.get_active_attempt(user_id, quiz_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("📊 Darajani aniqlash testi boshlandi! 15 ta savol.")
    await send_question(callback.message.chat.id, quiz_id, attempt, user_id)
    await callback.answer()


LEARN_REASON_OPTIONS = [
    ("soham", "💼 Soham (mutaxassisligim/kasbim)"),
    ("qiziqish", "🤔 Qiziqaman, kerak bo'ladi"),
]

TARGET_GRADE_OPTIONS = [
    ("c", "C"),
    ("b", "B"),
    ("a", "A"),
    ("aplus", "A+"),
]

LEVEL_OPTIONS = [
    ("boshlangich", "🟢 Boshlang'ich"),
    ("orta", "🟡 O'rta"),
    ("expert", "🔴 Expert"),
]

PREP_TIME_OPTIONS = [
    ("1oy", "1 oy"),
    ("2oy", "2 oy"),
    ("4oy", "4 oy"),
    ("6oyplus", "6 oy+"),
]

# code -> display label, for rendering /userinfo and /onboardingstats output
REASON_LABELS = dict(LEARN_REASON_OPTIONS)
TARGET_LABELS = dict(TARGET_GRADE_OPTIONS)
PREP_TIME_LABELS = dict(PREP_TIME_OPTIONS)


def onboarding_reason_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for code, label in LEARN_REASON_OPTIONS:
        kb.inline_keyboard.append([InlineKeyboardButton(text=label, callback_data=f"onbreason:{code}")])
    return kb


def onboarding_target_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for code, label in TARGET_GRADE_OPTIONS:
        kb.inline_keyboard.append([InlineKeyboardButton(text=label, callback_data=f"onbtarget:{code}")])
    return kb


def onboarding_level_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for code, label in LEVEL_OPTIONS:
        kb.inline_keyboard.append([InlineKeyboardButton(text=label, callback_data=f"onblevel:{code}")])
    return kb


def onboarding_time_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for code, label in PREP_TIME_OPTIONS:
        kb.inline_keyboard.append([InlineKeyboardButton(text=label, callback_data=f"onbtime:{code}")])
    return kb


async def start_onboarding(chat_id, state: FSMContext):
    """The whole 5-question survey lives in ONE message that gets edited in
    place at every step, instead of piling up 5+ separate messages in the
    chat - much cleaner. We remember its message_id so the one text-answer
    step (the name) can edit it too, since that reply has no callback to
    hang the edit off of."""
    await state.set_state(Onboarding.waiting_name)
    sent = await bot.send_message(
        chat_id,
        "Xush kelibsiz! Sizni yaxshiroq tushunish uchun 5 ta qisqa savol 👇\n\n"
        "1️⃣ Ismingiz?",
    )
    await state.update_data(survey_chat_id=chat_id, survey_msg_id=sent.message_id)


async def send_welcome_menu(chat_id, user_id):
    paid = db.has_any_confirmed_purchase(user_id)
    profile = db.get_user_profile(user_id)
    name = profile.get("onboarding_name") if profile else None
    greeting = f"Xush kelibsiz, {name}! 👋" if name else "Xush kelibsiz! 👋"
    await bot.send_message(
        chat_id,
        f"{exam_countdown_line()}"
        f"{greeting}\n\n"
        "Bu yerda DTM va Milliy sertifikatga eng qulay tarzda tayyorlanasiz — "
        "mavzu bo'yicha testlar, shaxsiy tahlil va AI yordamchi bilan.\n\n"
        "Boshlash uchun pastdagi menyudan tanlang 👇\n\n"
        "🏆 Har oy TOP-3 faolga pul bonusi!",
        reply_markup=main_reply_keyboard(paid=paid),
        parse_mode="HTML",
    )


def weak_topic_line(user_id):
    """One or two lines pointing the user at their weakest topic, or '' if we
    don't yet have enough answered questions to be confident about it."""
    weak = db.get_weak_topic(user_id, min_answers=config.WEAK_TOPIC_MIN_ANSWERS)
    if not weak or not weak["total"]:
        return ""
    correct = weak["correct"] or 0
    pct = int(100 * correct / weak["total"])
    return (
        f"📉 <b>Tahlil:</b> \"{weak['title']}\" mavzusida siz hozircha {pct}% to'g'ri "
        f"javob berdingiz — bu sizning eng zaif mavzungiz. Aynan shu yerga ko'proq "
        f"e'tibor bering!\n\n"
    )


async def send_referral_info(message: Message):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    count = db.count_referrals(message.from_user.id)
    remaining = max(0, 3 - count)
    if remaining == 0:
        status = "Tabriklaymiz! Sizga 20% chegirma faollashtirildi. Keyingi to'lovda shu chegirma qo'llanadi."
    else:
        status = f"Sizga yana {remaining} ta do'stingiz kerak — 20% chegirma uchun."
    await message.answer(
        f"🎁 Do'stlaringizni taklif qiling!\n\n"
        f"Sizning havolangiz:\n{link}\n\n"
        f"3 ta do'stingiz botdan foydalansa, keyingi xaridingizga 20% chegirma olasiz.\n\n"
        f"Hozirgi taklif qilganlar soni: {count}\n{status}"
    )


def topics_keyboard():
    """Regular topic browser - the assessment test is deliberately excluded
    here since it's a special one-shot flow (offered after onboarding, or
    via /darajam), not just another topic to pick and grind through."""
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for q in db.list_quizzes():
        if q["is_assessment"]:
            continue
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"📘 {q['title']}", callback_data=f"topic:{q['id']}")
        ])
    return kb


def modules_keyboard(quiz_id, total_modules, completed, daily_locked=False):
    """daily_locked=True marks every not-yet-completed module with a lock icon
    (shown when a non-paying user has used up today's free question quota)."""
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for m in range(1, total_modules + 1):
        if m in completed:
            mark = " ✅"
        elif daily_locked:
            mark = " 🔒"
        else:
            mark = ""
        label = db.get_module_label(quiz_id, m)
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"{label}{mark}", callback_data=f"module:{quiz_id}:{m}")
        ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="📚 Mavzular", callback_data="topics")])
    return kb


def module_result_keyboard(quiz_id, module_number, attempt_id, has_next):
    buttons = [[InlineKeyboardButton(text="📊 Tahlil", callback_data=f"tahlil:{attempt_id}")]]
    if has_next:
        buttons.append([InlineKeyboardButton(
            text="➡️ Keyingi test", callback_data=f"module:{quiz_id}:{module_number + 1}"
        )])
    buttons.append([InlineKeyboardButton(text="📚 Mavzular", callback_data="topics")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


MONTHLY_BONUS_UZS = [150_000, 125_000, 100_000]


async def send_leaderboard(chat_id):
    rows = db.get_leaderboard(limit=10)
    if not rows:
        await bot.send_message(chat_id, "Hozircha bu oy reytingda hech kim yo'q. Birinchi bo'ling! 🚀")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Shu oylik TOP reyting</b> (pullik foydalanuvchilar orasida)\n"]
    for i, r in enumerate(rows):
        name = r["first_name"] or (f"@{r['username']}" if r["username"] else f"ID {r['user_id']}")
        prefix = medals[i] if i < 3 else f"{i + 1}."
        bonus = f"  — <b>{MONTHLY_BONUS_UZS[i]:,} so'm bonus!</b> 🎁" if i < 3 else ""
        lines.append(f"{prefix} {name} — {r['total_correct']}/{r['total_answered']} to'g'ri{bonus}")
    lines.append(
        "\nOy oxirida TOP-3 aniqlanadi: 1-o'rin 150 000, 2-o'rin 125 000, "
        "3-o'rin 100 000 so'm bonus oladi. Yangi oy — yangi reyting!"
    )
    await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


async def send_history(chat_id, user_id):
    quizzes = db.list_quizzes()
    if not quizzes:
        await bot.send_message(chat_id, "Hozircha testlar mavjud emas.")
        return

    lines = ["🕐 <b>Sizning tarixingiz</b>", ""]
    total_done, total_all = 0, 0
    total_correct, total_answered = 0, 0
    any_progress = False

    for q in quizzes:
        total_modules = db.count_modules(q["id"])
        completed = db.get_completed_modules(user_id, q["id"])
        done = len(completed)
        total_done += done
        total_all += total_modules

        lines.append(f"📘 <b>{q['title']}</b>")
        lines.append(f"{progress_bar(done, total_modules)}  {done}/{total_modules} test")

        acc = db.get_quiz_accuracy(user_id, q["id"])
        if acc:
            any_progress = True
            pct = int(100 * acc["correct"] / acc["total"])
            lines.append(f"✅ To'g'ri javoblar: {acc['correct']}/{acc['total']} ({pct}%)")
            total_correct += acc["correct"]
            total_answered += acc["total"]
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("📊 <b>Umumiy natija</b>")
    lines.append(f"{progress_bar(total_done, total_all)}  {total_done}/{total_all} test yakunlangan")
    if total_answered:
        overall_pct = int(100 * total_correct / total_answered)
        lines.append(f"🎯 Aniqlik: {total_correct}/{total_answered} to'g'ri ({overall_pct}%)")

    if not any_progress:
        lines.append("\nHali birorta savol yechmadingiz. \"📝 Testlar\" tugmasini bosib boshlang!")

    await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


def question_keyboard(quiz_id, q_index, options):
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for i, opt in enumerate(options):
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=opt, callback_data=f"ans:{quiz_id}:{q_index}:{i}")
        ])
    return kb


async def send_daily_limit_prompt(chat_id, quiz, user_id):
    """Shown when a non-paying user has used up today's free questions (any module, any topic)."""
    if db.has_any_pending_purchase(user_id):
        await bot.send_message(chat_id, "Bugungi bepul savollaringiz tugadi. To'lovingiz hali tasdiqlanmoqda. Iltimos kuting.")
        return
    price = config.FULL_ACCESS_PRICE_UZS
    discount_note = ""
    if db.has_discount(user_id):
        price = int(price * 0.8)
        discount_note = " (20% taklif chegirmasi qo'llandi! 🎉)"
    db.request_purchase(user_id, quiz["id"], price_uzs=price)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 To'lash", callback_data=f"paycard:{quiz['id']}")
    ]])
    await bot.send_message(
        chat_id,
        f"🎯 Bugungi {db.DAILY_FREE_LIMIT} ta bepul savolingiz tugadi!\n\n"
        f"{weak_topic_line(user_id)}"
        f"Ertaga yana {db.DAILY_FREE_LIMIT} ta bepul savolga ega bo'lasiz — yoki hoziroq bir martalik "
        f"to'lov bilan <b>barcha mavzulardagi cheksiz testlarga</b> umrbod kirish huquqiga ega bo'ling. "
        f"DTM va milliy sertifikatga eng qulay tayyorgarlik yo'li! 🚀\n\n"
        f"💰 Narxi: <b>{price:,} so'm</b>{discount_note}\n\n"
        f"{TRUST_NOTE}",
        reply_markup=kb,
        parse_mode="HTML",
    )


async def send_question(chat_id, quiz_id, attempt, user_id):
    questions = db.get_questions(quiz_id)
    idx = attempt["current_index"]
    quiz = db.get_quiz(quiz_id)
    module_number = attempt["module_number"]
    bounds = db.get_module_bounds(quiz_id, module_number)
    if bounds:
        module_start, module_end = bounds
    else:
        module_start = (module_number - 1) * MODULE_SIZE
        module_end = min(module_start + MODULE_SIZE, len(questions))

    if idx >= module_end:
        db.finish_attempt(attempt["id"])

        if quiz["is_assessment"]:
            await send_assessment_result(chat_id, user_id, attempt["id"])
            return

        score = attempt["score"]
        total = module_end - module_start
        total_modules = db.count_modules(quiz_id)
        next_module = module_number + 1
        has_next_module = next_module <= total_modules
        paid = db.has_any_confirmed_purchase(user_id)

        kb = module_result_keyboard(quiz_id, module_number, attempt["id"], has_next_module)
        label = db.get_module_label(quiz_id, module_number)
        await bot.send_message(
            chat_id,
            f"{label} tugadi!\nNatija: {score}/{total}",
            reply_markup=kb,
        )

        if not paid:
            remaining = db.daily_free_remaining(user_id)
            if remaining > 0:
                await bot.send_message(chat_id, f"🎁 Bugun yana {remaining} ta bepul savolingiz bor - istalgan mavzudan davom eting!")
            else:
                await send_daily_limit_prompt(chat_id, quiz, user_id)
        return

    paid = db.has_any_confirmed_purchase(user_id)
    if not quiz["is_assessment"] and not paid and db.get_daily_free_used(user_id) >= db.DAILY_FREE_LIMIT:
        db.finish_attempt(attempt["id"])
        await send_daily_limit_prompt(chat_id, quiz, user_id)
        return

    q = questions[idx]
    await bot.send_message(
        chat_id,
        f"Savol {idx - module_start + 1}/{module_end - module_start}:\n\n{q['question_text']}",
        reply_markup=question_keyboard(quiz_id, idx, q["options"]),
    )


# ---------------- activity tracking middleware ----------------

class ActivityMiddleware:
    """Stamps last_active on every incoming message/callback, so the 24h
    inactivity-reminder job knows who's gone quiet."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = None
        if getattr(event, "message", None) is not None:
            user = event.message.from_user
        elif getattr(event, "callback_query", None) is not None:
            user = event.callback_query.from_user
        if user is not None:
            db.touch_last_active(user.id)
        return await handler(event, data)


dp.update.outer_middleware(ActivityMiddleware())


# ---------------- user commands ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    referred_by = None
    if command.args and command.args.strip().isdigit():
        ref_id = int(command.args.strip())
        if ref_id != message.from_user.id:
            referred_by = ref_id
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name, referred_by)

    if not db.has_completed_onboarding(message.from_user.id):
        await start_onboarding(message.chat.id, state)
        return

    await send_welcome_menu(message.chat.id, message.from_user.id)


@dp.message(StateFilter(Onboarding.waiting_name))
async def on_onboarding_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Iltimos, ismingizni matn ko'rinishida yozing.")
        return
    db.set_onboarding_name(message.from_user.id, name[:100])
    await state.set_state(Onboarding.waiting_reason)
    data = await state.get_data()
    survey_chat_id = data.get("survey_chat_id")
    survey_msg_id = data.get("survey_msg_id")
    text = f"Rahmat, {name}! 2️⃣ Huquqni nima uchun o'rganyapsiz?"
    kb = onboarding_reason_keyboard()
    if survey_msg_id:
        try:
            await bot.edit_message_text(
                text, chat_id=survey_chat_id, message_id=survey_msg_id, reply_markup=kb
            )
            return
        except Exception:
            pass
    # Fallback if the original card couldn't be edited for any reason.
    sent = await message.answer(text, reply_markup=kb)
    await state.update_data(survey_chat_id=message.chat.id, survey_msg_id=sent.message_id)


@dp.callback_query(F.data.startswith("onbreason:"))
async def on_onboarding_reason(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    db.set_learn_reason(callback.from_user.id, code)
    await state.set_state(Onboarding.waiting_target)
    await callback.message.edit_text(
        "3️⃣ Milliy sertifikat bo'yicha maqsadingiz?",
        reply_markup=onboarding_target_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("onbtarget:"))
async def on_onboarding_target(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    db.set_target_grade(callback.from_user.id, code)
    await state.set_state(Onboarding.waiting_level)
    await callback.message.edit_text(
        "4️⃣ Huquq fanidan hozirgi bilim darajangiz qanday?",
        reply_markup=onboarding_level_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("onblevel:"))
async def on_onboarding_level(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    db.set_level(callback.from_user.id, code)
    await state.set_state(Onboarding.waiting_time)
    await callback.message.edit_text(
        "5️⃣ Tayyorgarlik uchun qancha vaqtingiz bor?",
        reply_markup=onboarding_time_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("onbtime:"))
async def on_onboarding_time(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    db.set_prep_time(callback.from_user.id, code)
    db.mark_onboarding_done(callback.from_user.id)
    await callback.message.edit_text("✅ Rahmat! Endi boshlashimiz mumkin 🚀")
    await state.clear()
    await send_welcome_menu(callback.message.chat.id, callback.from_user.id)
    await offer_assessment(callback.message.chat.id, state)
    await callback.answer()


@dp.message(F.text == BTN_TESTLAR)
async def on_testlar_pressed(message: Message, state: FSMContext):
    await state.clear()
    quizzes = db.list_quizzes()
    if not quizzes:
        await message.answer("Hozircha testlar mavjud emas. Tez orada qo'shiladi!")
        return
    await message.answer("Mavzuni tanlang 👇", reply_markup=topics_keyboard())


@dp.message(F.text == BTN_REYTING)
async def on_reyting_button(message: Message, state: FSMContext):
    await state.clear()
    await send_leaderboard(message.chat.id)


@dp.message(F.text == BTN_TARIX)
async def on_tarix_button(message: Message, state: FSMContext):
    await state.clear()
    await send_history(message.chat.id, message.from_user.id)


@dp.message(F.text == BTN_TAKLIF)
async def on_taklif_button(message: Message, state: FSMContext):
    await state.set_state(Feedback.waiting_text)
    await message.answer(
        "💡 Bot haqida fikringiz, taklifingiz yoki shikoyatingiz bormi?\n\n"
        "Yozib yuboring — botni yanada foydali qilishda albatta hisobga olamiz.\n\n"
        f"Tezroq javob kerak bo'lsa, to'g'ridan-to'g'ri {config.SUPPORT_USERNAME} ga yozishingiz ham mumkin."
    )


@dp.message(StateFilter(Feedback.waiting_text))
async def on_feedback_received(message: Message, state: FSMContext):
    await state.clear()
    if not message.text:
        await message.answer("Iltimos, fikringizni matn ko'rinishida yuboring.")
        return
    if config.ADMIN_ID:
        await bot.send_message(
            config.ADMIN_ID,
            f"💡 Yangi taklif/fikr:\n\n"
            f"Foydalanuvchi: {message.from_user.full_name} (@{message.from_user.username})\n"
            f"ID: {message.from_user.id}\n\n"
            f"{message.text}",
        )
    await message.answer("Rahmat! Fikringiz uchun rahmat 🙏 Botni yanada yaxshilashda albatta hisobga olamiz.")


@dp.message(F.text == BTN_AI)
async def on_ai_button(message: Message, state: FSMContext):
    if not config.GEMINI_API_KEY:
        await message.answer(
            "🤖 AI yordamchi hozircha sozlanmoqda. Tez orada ishga tushadi!"
        )
        return
    user_id = message.from_user.id
    paid = db.has_any_confirmed_purchase(user_id)
    if not paid and db.get_ai_total_used(user_id) >= config.AI_DAILY_LIMIT:
        await send_ai_paywall_prompt(message.chat.id, user_id)
        return
    await state.set_state(AIChat.waiting_question)
    await message.answer(
        "🤖 Men sizning yordamchingizman. Savollaringiz bo'lsa, yozing.",
    )


_MENU_BUTTON_TEXTS = {BTN_TESTLAR, BTN_TAKLIF, BTN_REYTING, BTN_TARIX, BTN_AI, BTN_TOLOV}


async def send_ai_paywall_prompt(chat_id, user_id):
    """Shown once a free (non-premium) user hits their lifetime AI question
    cap - never tells them the exact count, just that premium unlocks
    unlimited AI (plus everything else premium already includes)."""
    quizzes = db.list_quizzes()
    quiz_id = quizzes[0]["id"] if quizzes else None
    price = config.FULL_ACCESS_PRICE_UZS
    discount_note = ""
    if db.has_discount(user_id):
        price = int(price * 0.8)
        discount_note = " (20% taklif chegirmasi qo'llandi! 🎉)"
    if quiz_id is not None and not db.has_any_pending_purchase(user_id):
        db.request_purchase(user_id, quiz_id, price_uzs=price)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 To'lash", callback_data=f"paycard:{quiz_id}")
    ]])
    await bot.send_message(
        chat_id,
        "🤖 Bepul AI savollaringiz tugadi.\n\n"
        "Premium bilan AI yordamchidan <b>cheksiz</b> foydalanasiz, "
        "shuningdek barcha mavzulardagi testlarga ham umrbod kirish huquqiga "
        "ega bo'lasiz! 🚀\n\n"
        f"💰 Narxi: <b>{price:,} so'm</b>{discount_note}\n\n"
        f"{TRUST_NOTE}",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.message(StateFilter(AIChat.waiting_question), ~F.text.in_(_MENU_BUTTON_TEXTS))
async def on_ai_question(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Iltimos, savolingizni matn ko'rinishida yozing.")
        return

    user_id = message.from_user.id
    paid = db.has_any_confirmed_purchase(user_id)
    if not paid and db.get_ai_total_used(user_id) >= config.AI_DAILY_LIMIT:
        await state.clear()
        await send_ai_paywall_prompt(message.chat.id, user_id)
        return

    thinking = await message.answer("🤖 O'ylayapman...")
    answer = await ask_gemini(message.text)
    db.record_ai_message(user_id)

    if answer is None:
        await thinking.edit_text(
            "Kechirasiz, hozir javob bera olmadim. Birozdan so'ng qayta urinib ko'ring."
        )
        return

    try:
        await thinking.edit_text(answer, parse_mode="HTML")
    except Exception:
        await message.answer(answer, parse_mode="HTML")
    # Stay in AIChat.waiting_question so the user can keep asking follow-ups
    # without pressing the button again each time.


@dp.message(F.text == BTN_TOLOV)
async def on_tolov_button(message: Message, state: FSMContext):
    """Always shows the payment details first, then the current payment status
    right under it - every single time this button is pressed, no matter how
    many times, and no matter whether a screenshot was ever sent."""
    await state.clear()
    user_id = message.from_user.id

    if db.has_any_confirmed_purchase(user_id):
        purchase = db.get_confirmed_purchase(user_id)
        days_left_note = ""
        if purchase:
            elapsed = db.days_since_confirmed(user_id, purchase["quiz_id"])
            if elapsed is not None:
                remaining_days = max(0, config.REFUND_DAYS - elapsed)
                if remaining_days > 0:
                    days_left_note = (
                        f"\n\n🛡 Pul qaytarish kafolati muddati: yana {remaining_days} kun. "
                        f"Savol bo'lsa {config.SUPPORT_USERNAME} ga yozing."
                    )
        await message.answer(
            f"✅ Siz premiumdasiz — barcha testlardan bemalol foydalaning! 🎉{days_left_note}",
            reply_markup=main_reply_keyboard(paid=True),
        )
        return

    quizzes = db.list_quizzes()
    quiz_id = quizzes[0]["id"] if quizzes else None  # one confirmed purchase unlocks every topic

    price = config.FULL_ACCESS_PRICE_UZS
    discount_note = ""
    if db.has_discount(user_id):
        price = int(price * 0.8)
        discount_note = " (20% taklif chegirmasi qo'llandi! 🎉)"

    # Make sure there's an open purchase request tied to this user so a
    # screenshot they send afterwards is matched correctly.
    if quiz_id is not None and not db.has_any_pending_purchase(user_id):
        db.request_purchase(user_id, quiz_id, price_uzs=price)

    if db.has_any_pending_purchase(user_id):
        status_line = "⏳ <b>Holat:</b> to'lovingiz tekshirilmoqda. Iltimos, biroz kuting."
    else:
        status_line = "❌ <b>Holat:</b> hali to'lov qilmagansiz."

    await message.answer(
        f"💳 <b>To'lov ma'lumotlari</b>\n\n"
        f"{payment_card_block()}\n\n"
        f"💰 Narxi: <b>{price:,} so'm</b>{discount_note}\n\n"
        f"To'lov qilgach, screenshotni shu botga rasm qilib yuboring 📸\n\n"
        f"{status_line}\n\n"
        f"{TRUST_NOTE}",
        parse_mode="HTML",
    )


@dp.message(Command("reyting"))
async def cmd_reyting(message: Message):
    await send_leaderboard(message.chat.id)


@dp.callback_query(F.data == "leaderboard")
async def on_leaderboard_pressed(callback: CallbackQuery):
    await send_leaderboard(callback.message.chat.id)
    await callback.answer()


@dp.callback_query(F.data == "topics")
async def on_topics_pressed(callback: CallbackQuery):
    quizzes = db.list_quizzes()
    if not quizzes:
        await callback.message.answer("Hozircha testlar mavjud emas. Tez orada qo'shiladi!")
        await callback.answer()
        return
    await callback.message.answer("Mavzuni tanlang 👇", reply_markup=topics_keyboard())
    await callback.answer()


@dp.message(Command("taklif"))
async def cmd_referral(message: Message):
    await send_referral_info(message)


@dp.message(Command("darajam"))
async def cmd_darajam(message: Message, state: FSMContext):
    """Lets any user (re)take the level-placement test on demand, not just
    right after onboarding."""
    assessment = db.get_assessment_quiz()
    if not assessment:
        await message.answer("Darajani aniqlash testi hali sozlanmagan.")
        return
    await offer_assessment(message.chat.id, state)


@dp.message(Command("kunlik"))
async def cmd_daily(message: Message):
    today = datetime.date.today().isoformat()
    last = db.get_last_daily_date(message.from_user.id)
    if last == today:
        await message.answer("Bugungi bepul savolingizni allaqachon oldingiz. Ertaga qayta urinib ko'ring!")
        return
    q = db.get_random_question()
    if q is None:
        await message.answer("Hozircha savollar mavjud emas.")
        return
    db.set_last_daily_date(message.from_user.id, today)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for i, opt in enumerate(q["options"]):
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=opt, callback_data=f"dans:{q['id']}:{i}")
        ])
    await message.answer(f"🎁 Kunlik bepul savol:\n\n{q['question_text']}", reply_markup=kb)


@dp.callback_query(F.data.startswith("dans:"))
async def on_daily_answer(callback: CallbackQuery):
    _, q_id, chosen = callback.data.split(":")
    q_id, chosen = int(q_id), int(chosen)
    with_db_question = db.get_question_by_id(q_id)
    if with_db_question is None:
        await callback.answer()
        return
    correct = (chosen == with_db_question["correct_index"])
    mark = "✅" if correct else "❌"
    await callback.answer("✅ To'g'ri!" if correct else "❌ Noto'g'ri")

    result_text = (
        f"{mark} {with_db_question['question_text']}\n\n"
        f"To'g'ri javob: {with_db_question['options'][with_db_question['correct_index']]}\n"
    )
    if with_db_question.get("explanation"):
        result_text += f"\nℹ️ <b>Izoh:</b>\n<blockquote>{with_db_question['explanation']}</blockquote>"
    result_text += "\n\nErtaga yana bepul savol oling! Barcha testlarni ko'rish uchun /start bosing."

    try:
        await callback.message.edit_text(
            result_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
    except Exception:
        await callback.message.answer(result_text, parse_mode="HTML")


@dp.callback_query(F.data.startswith("topic:"))
async def on_topic_selected(callback: CallbackQuery):
    quiz_id = int(callback.data.split(":")[1])
    quiz = db.get_quiz(quiz_id)
    user_id = callback.from_user.id

    if db.has_any_pending_purchase(user_id) and not db.has_any_confirmed_purchase(user_id):
        await callback.message.answer("To'lovingiz hali tasdiqlanmoqda. Iltimos kuting.")
        await callback.answer()
        return

    total_modules = db.count_modules(quiz_id)
    if total_modules == 0:
        await callback.message.answer("Bu mavzuda hali savollar yo'q.")
        await callback.answer()
        return

    completed = db.get_completed_modules(user_id, quiz_id)
    paid = db.has_any_confirmed_purchase(user_id)
    daily_locked = (not paid) and db.get_daily_free_used(user_id) >= db.DAILY_FREE_LIMIT
    status_line = ""
    if not paid:
        remaining = db.daily_free_remaining(user_id)
        status_line = (
            f"\n\n🎁 Bugun yana {remaining} ta bepul savolingiz bor."
            if remaining > 0
            else f"\n\n🔒 Bugungi bepul limitingiz tugadi. Ertaga qayting yoki hoziroq to'lang."
        )
    await callback.message.answer(
        f"{quiz['title']} — testni tanlang 👇{status_line}",
        reply_markup=modules_keyboard(quiz_id, total_modules, completed, daily_locked=daily_locked),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("module:"))
async def on_module_selected(callback: CallbackQuery):
    _, quiz_id, module_number = callback.data.split(":")
    quiz_id, module_number = int(quiz_id), int(module_number)
    quiz = db.get_quiz(quiz_id)
    user_id = callback.from_user.id

    if db.has_any_pending_purchase(user_id) and not db.has_any_confirmed_purchase(user_id):
        await callback.message.answer("To'lovingiz hali tasdiqlanmoqda. Iltimos kuting.")
        await callback.answer()
        return

    if not db.has_any_confirmed_purchase(user_id) and db.get_daily_free_used(user_id) >= db.DAILY_FREE_LIMIT:
        await send_daily_limit_prompt(callback.message.chat.id, quiz, user_id)
        await callback.answer()
        return

    db.start_attempt(user_id, quiz_id, module_number)
    attempt = db.get_active_attempt(user_id, quiz_id)
    label = db.get_module_label(quiz_id, module_number)
    await callback.message.answer(f"{quiz['title']} — {label} boshlandi!")
    await send_question(callback.message.chat.id, quiz_id, attempt, user_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("tahlil:"))
async def on_tahlil_pressed(callback: CallbackQuery):
    attempt_id = int(callback.data.split(":")[1])
    answers = db.get_attempt_answers(attempt_id)
    if not answers:
        await callback.message.answer("Tahlil topilmadi.")
        await callback.answer()
        return
    lines = ["📊 <b>Tahlil</b>\n"]
    for i, a in enumerate(answers, 1):
        mark = "✅" if a["is_correct"] else "❌"
        lines.append(f"{i}. {a['question_text']}")
        lines.append(f"Sizning javobingiz: {a['selected_text']} {mark}")
        if not a["is_correct"]:
            lines.append(f"To'g'ri javob: {a['correct_text']}")
        if a["explanation"]:
            lines.append(f"ℹ️ {a['explanation']}")
        lines.append("")
    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("paycard:"))
async def on_paycard_pressed(callback: CallbackQuery):
    await callback.message.answer(
        f"{payment_card_block()}\n\n"
        f"To'lov qilgach, screenshotni shu botga rasm qilib yuboring 📸\n\n"
        f"{TRUST_NOTE}",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(F.photo)
async def on_payment_screenshot(message: Message):
    # Forward the screenshot to the admin with one-tap confirm/reject buttons.
    # Find which quiz this user has a pending purchase for.
    quiz_id = db.get_pending_quiz_id(message.from_user.id)
    caption = (
        f"To'lov screenshoti\n"
        f"Foydalanuvchi: {message.from_user.full_name} (@{message.from_user.username})\n"
        f"ID: {message.from_user.id}"
    )
    if config.ADMIN_ID:
        if quiz_id is not None:
            quiz = db.get_quiz(quiz_id)
            price = db.purchase_price(message.from_user.id, quiz_id) or config.FULL_ACCESS_PRICE_UZS
            caption += f"\nMavzu: {quiz['title']}\nKutilayotgan narx: {price:,} so'm"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"confirm:{message.from_user.id}:{quiz_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject:{message.from_user.id}:{quiz_id}"),
            ]])
            await bot.send_photo(config.ADMIN_ID, message.photo[-1].file_id, caption=caption, reply_markup=kb)
        else:
            caption += "\n\n(Kutilayotgan to'lov topilmadi)"
            await bot.send_photo(config.ADMIN_ID, message.photo[-1].file_id, caption=caption)
    await message.answer("Rahmat! To'lovingiz tekshirilmoqda, tez orada tasdiqlaymiz.")


@dp.callback_query(F.data.startswith("confirm:"))
async def on_confirm_pressed(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer()
        return
    _, user_id, quiz_id = callback.data.split(":")
    user_id, quiz_id = int(user_id), int(quiz_id)
    db.confirm_purchase(user_id, quiz_id)
    synced = await sync_premium_to_miniweb(user_id, True)
    sync_note = "" if synced else "\n⚠️ Mini App sinxronlanmadi - /syncpremium buyrug'i bilan qo'lda urinib ko'ring."
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ TASDIQLANDI" + sync_note)
    await bot.send_message(
        user_id,
        f"✅ To'lovingiz tasdiqlandi!\n\nEndi barcha mavzulardagi testlarga umrbod kirish huquqingiz bor "
        f"— bot ichida ham, Mini App'da ham.\n\n"
        f"{TRUST_NOTE}",
        reply_markup=main_reply_keyboard(paid=True),
        parse_mode="HTML",
    )
    await callback.answer("Tasdiqlandi")


@dp.callback_query(F.data.startswith("reject:"))
async def on_reject_pressed(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer()
        return
    _, user_id, quiz_id = callback.data.split(":")
    user_id, quiz_id = int(user_id), int(quiz_id)
    db.reject_purchase(user_id, quiz_id)
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ RAD ETILDI")
    await bot.send_message(user_id, "Kechirasiz, to'lovingiz tasdiqlanmadi. Iltimos, qaytadan tekshirib, to'g'ri screenshot yuboring.")
    await callback.answer("Rad etildi")


@dp.callback_query(F.data.startswith("ans:"))
async def on_answer(callback: CallbackQuery):
    _, quiz_id, q_index, chosen = callback.data.split(":")
    quiz_id, q_index, chosen = int(quiz_id), int(q_index), int(chosen)
    user_id = callback.from_user.id

    attempt = db.get_active_attempt(user_id, quiz_id)
    if attempt is None or attempt["current_index"] != q_index:
        await callback.answer("Bu savol eskirgan.")
        return

    questions = db.get_questions(quiz_id)
    q = questions[q_index]
    correct = (chosen == q["correct_index"])

    db.record_answer(attempt["id"], q["id"], chosen, correct)
    db.advance_attempt(attempt["id"], correct)
    if not db.has_any_confirmed_purchase(user_id) and not db.is_assessment_quiz(quiz_id):
        db.record_daily_free_answer(user_id)

    # Small toast that auto-dismisses - no blocking popup to interrupt them.
    await callback.answer("✅ To'g'ri!" if correct else "❌ Noto'g'ri")

    bounds = db.get_module_bounds(quiz_id, attempt["module_number"])
    module_start = bounds[0] if bounds else (attempt["module_number"] - 1) * MODULE_SIZE
    q_number = q_index - module_start + 1
    mark = "✅" if correct else "❌"

    result_text = (
        f"{mark} <b>{q_number}-savol</b>\n"
        f"{q['question_text']}\n\n"
        f"Javobingiz: {q['options'][chosen]}\n"
        f"To'g'ri javob: {q['options'][q['correct_index']]}\n"
    )
    if q.get("explanation"):
        result_text += f"\nℹ️ <b>Izoh:</b>\n<blockquote>{q['explanation']}</blockquote>"

    # Edit the question in place instead of sending a new message - this
    # doesn't fire a fresh notification the way a new message does. Passing
    # an explicit empty keyboard clears the old answer-choice buttons -
    # edit_text alone leaves stale buttons attached (unlike edit_reply_markup,
    # which Telegram clears by default when called with no markup).
    try:
        await callback.message.edit_text(
            result_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
    except Exception:
        await callback.message.answer(result_text, parse_mode="HTML")

    updated_attempt = db.get_active_attempt(user_id, quiz_id)
    if updated_attempt is None:
        # attempt just got marked finished mid-flight in edge cases
        return
    await send_question(callback.message.chat.id, quiz_id, updated_attempt, user_id)


# ---------------- inactivity reminders (background job) ----------------

async def send_inactivity_reminders():
    """Runs periodically (see scheduler setup in main()). Pings any user whose
    last activity crossed config.INACTIVITY_REMINDER_HOURS and who hasn't
    already been reminded since then."""
    inactive_ids = db.get_inactive_users(hours=config.INACTIVITY_REMINDER_HOURS)
    for telegram_id in inactive_ids:
        line = random.choice(MOTIVATIONAL_LINES)
        try:
            await bot.send_message(
                telegram_id,
                f"👋 Sizni sog'indik!\n\n{line}\n\nDavom etish uchun pastdagi \"📝 Testlar\" tugmasini bosing yoki /start yozing.",
            )
            db.mark_reminder_sent(telegram_id)
        except Exception as e:
            # Most common cause: user blocked the bot. Mark as reminded anyway
            # so we don't retry them every hour forever.
            logging.warning(f"Inactivity reminder failed for {telegram_id}: {e}")
            db.mark_reminder_sent(telegram_id)


# ---------------- admin: channel stats ----------------

@dp.message(Command("postchannel"))
async def cmd_postchannel(message: Message):
    """Posts a short usage snapshot to config.STATS_CHANNEL_ID - run this
    manually whenever you want to refresh the numbers shown in your info
    channel (e.g. once a day)."""
    if message.from_user.id != config.ADMIN_ID:
        return
    if not config.STATS_CHANNEL_ID:
        await message.answer(
            "STATS_CHANNEL_ID sozlanmagan.\n\n"
            "1) Kanal yarating, botni admin qiling.\n"
            "2) Railway'da STATS_CHANNEL_ID environment variable'ini kanal ID "
            "(-100 bilan boshlanadi) yoki @username bilan to'ldiring."
        )
        return
    total_users = db.count_users()
    by_status = db.count_purchases_by_status()
    paid_count = by_status.get("confirmed", 0)
    text = (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
        f"✅ Premium foydalanuvchilar: <b>{paid_count}</b>\n\n"
        "Har kuni yangilanadi. Botga qo'shilib, bepul savollarni sinab ko'ring! 🚀"
    )
    try:
        await bot.send_message(config.STATS_CHANNEL_ID, text, parse_mode="HTML")
        await message.answer("✅ Kanalga yuborildi.")
    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}\n\nBot kanalda admin ekanligini tekshiring.")


# ---------------- admin commands ----------------

@dp.message(Command("confirm"))
async def cmd_confirm(message: Message, command: CommandObject):
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args:
        await message.answer("Foydalanish: /confirm <user_id> <quiz_id>")
        return
    parts = command.args.split()
    if len(parts) != 2:
        await message.answer("Foydalanish: /confirm <user_id> <quiz_id>")
        return
    user_id, quiz_id = int(parts[0]), int(parts[1])
    db.confirm_purchase(user_id, quiz_id)
    synced = await sync_premium_to_miniweb(user_id, True)
    sync_note = "" if synced else "\n⚠️ Mini App sinxronlanmadi - /syncpremium bilan qo'lda urinib ko'ring."
    await message.answer(f"Tasdiqlandi: user {user_id}, quiz {quiz_id}{sync_note}")
    await bot.send_message(
        user_id,
        f"✅ To'lovingiz tasdiqlandi!\n\nEndi barcha mavzulardagi testlarga umrbod kirish huquqingiz bor "
        f"— bot ichida ham, Mini App'da ham.\n\n"
        f"{TRUST_NOTE}",
        reply_markup=main_reply_keyboard(paid=True),
        parse_mode="HTML",
    )


@dp.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject):
    """Admin helper: /refund <user_id> <quiz_id>

    Use this ONLY AFTER you've manually sent the money back to the buyer's
    card. This revokes their access immediately and sends them a confirmation
    message. Refunds are honored any time within the guarantee window
    advertised to buyers (config.REFUND_DAYS), but nothing here enforces that
    automatically - it's on you to check /pending-style context or the
    buyer's message before sending the money back.
    """
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args:
        await message.answer("Foydalanish: /refund <user_id> <quiz_id>")
        return
    parts = command.args.split()
    if len(parts) != 2:
        await message.answer("Foydalanish: /refund <user_id> <quiz_id>")
        return
    user_id, quiz_id = int(parts[0]), int(parts[1])

    purchase = db.get_purchase(user_id, quiz_id)
    if not purchase or purchase["status"] != "confirmed":
        await message.answer("Bu foydalanuvchida shu test uchun tasdiqlangan xarid topilmadi.")
        return

    db.refund_purchase(user_id, quiz_id)
    synced = await sync_premium_to_miniweb(user_id, False)
    sync_note = "" if synced else "\n⚠️ Mini App sinxronlanmadi - /syncpremium bilan qo'lda urinib ko'ring."
    await message.answer(f"✅ Pul qaytarildi deb belgilandi: user {user_id}, quiz {quiz_id}. Kirish huquqi bekor qilindi.{sync_note}")
    await bot.send_message(
        user_id,
        "💸 Pulingiz to'liq qaytarildi.\n\n"
        "Agar kelajakda fikringiz o'zgarsa, botimiz doim tayyor turadi.\n\n"
        f"Savollaringiz bo'lsa, {config.SUPPORT_USERNAME} ga yozishingiz mumkin.",
    )


@dp.message(Command("syncpremium"))
async def cmd_syncpremium(message: Message, command: CommandObject):
    """Manual fallback: /syncpremium <user_id> <on|off>
    Use this if the automatic sync (shown after /confirm or /refund) failed -
    e.g. the Mini App backend was briefly down when the payment was processed."""
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args:
        await message.answer("Foydalanish: /syncpremium <user_id> <on|off>")
        return
    parts = command.args.split()
    if len(parts) != 2 or parts[1] not in ("on", "off"):
        await message.answer("Foydalanish: /syncpremium <user_id> <on|off>")
        return
    user_id = int(parts[0])
    is_premium = parts[1] == "on"
    synced = await sync_premium_to_miniweb(user_id, is_premium)
    if synced:
        await message.answer(f"✅ Sinxronlandi: user {user_id} -> premium={is_premium}")
    else:
        await message.answer("❌ Sinxronlash muvaffaqiyatsiz. MINIWEB_ADMIN_URL / MINIWEB_ADMIN_KEY sozlamalarini tekshiring.")


@dp.message(Command("pending"))
async def cmd_pending(message: Message):
    # Safety net: lists every unconfirmed payment with user_id + quiz_id, in
    # case a screenshot notification was ever missed (e.g. ADMIN_ID misconfigured
    # at the time, or a delivery hiccup). Use /confirm <user_id> <quiz_id> from here.
    if message.from_user.id != config.ADMIN_ID:
        return
    rows = db.list_pending_purchases()
    if not rows:
        await message.answer("✅ Kutilayotgan to'lovlar yo'q.")
        return
    lines = ["⏳ <b>Kutilayotgan to'lovlar:</b>\n"]
    for r in rows:
        name = r["first_name"] or (f"@{r['username']}" if r["username"] else "Noma'lum")
        price = f"{r['price_uzs']:,} so'm" if r["price_uzs"] else "narx noma'lum"
        lines.append(
            f"👤 {name} (ID: {r['user_id']})\n"
            f"   Mavzu: {r['quiz_title']} (quiz_id: {r['quiz_id']})\n"
            f"   Narx: {price} | So'ralgan: {r['requested_at']}\n"
            f"   Tasdiqlash: /confirm {r['user_id']} {r['quiz_id']}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("resetuser"))
async def cmd_resetuser(message: Message, command: CommandObject):
    # Admin/testing helper: wipe a user's purchase + onboarding history so they
    # hit the paywall and the onboarding survey again.
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /resetuser <user_id>\n\nO'zingizni sinash uchun o'z ID'ingizni yuboring.")
        return
    user_id = int(command.args.strip())
    db.reset_user_purchases(user_id)
    db.reset_user_onboarding(user_id)
    await message.answer(f"✅ {user_id} uchun barcha xaridlar va so'rovnoma tozalandi. Endi u qaytadan boshlaydi.")


@dp.message(Command("userinfo"))
async def cmd_userinfo(message: Message, command: CommandObject):
    """Admin: /userinfo <user_id> - shows one user's onboarding survey
    answers plus purchase/activity status, all in one place."""
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /userinfo <user_id>")
        return
    user_id = int(command.args.strip())
    profile = db.get_user_profile(user_id)
    if not profile:
        await message.answer("Bunday foydalanuvchi topilmadi.")
        return

    name = profile.get("onboarding_name") or profile.get("first_name") or "Noma'lum"
    username = f"@{profile['username']}" if profile.get("username") else "—"
    premium_line = "✅ Premium" if profile["is_premium"] else "❌ Bepul foydalanuvchi"

    lines = [
        f"👤 <b>{name}</b> ({username})",
        f"ID: <code>{user_id}</code>",
        premium_line,
        "",
    ]

    if not profile.get("onboarding_done"):
        lines.append("⚠️ So'rovnomani hali to'ldirmagan.")
    else:
        lines.append("📋 <b>So'rovnoma javoblari:</b>")
        reason = REASON_LABELS.get(profile.get("learn_reason"), "—")
        target = TARGET_LABELS.get(profile.get("target_grade"), "—")
        level = LEVEL_LABELS.get(profile.get("level"), "—")
        prep_time = PREP_TIME_LABELS.get(profile.get("prep_time"), "—")
        lines.append(f"• Sabab: {reason}")
        lines.append(f"• Maqsad (sertifikat): {target}")
        lines.append(f"• Daraja: {level}")
        lines.append(f"• Tayyorgarlik vaqti: {prep_time}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("onboardingstats"))
async def cmd_onboardingstats(message: Message):
    """Admin: /onboardingstats - aggregate breakdown across every field in
    the onboarding survey, for understanding your user base at a glance."""
    if message.from_user.id != config.ADMIN_ID:
        return
    stats = db.get_onboarding_stats()
    if stats["total_completed"] == 0:
        await message.answer("Hali hech kim so'rovnomani to'ldirmagan.")
        return

    def format_group(title, rows, label_map):
        if not rows:
            return f"{title}: ma'lumot yo'q"
        parts = [f"{title}:"]
        for code, count in rows:
            label = label_map.get(code, code)
            parts.append(f"   {label}: {count}")
        return "\n".join(parts)

    lines = [
        f"📋 <b>So'rovnoma statistikasi</b>",
        f"Jami to'ldirganlar: <b>{stats['total_completed']}</b>",
        "",
        format_group("🎯 Sabab", stats["reason"], REASON_LABELS),
        "",
        format_group("🏅 Maqsad (sertifikat)", stats["target"], TARGET_LABELS),
        "",
        format_group("📊 Daraja", stats["level"], LEVEL_LABELS),
        "",
        format_group("⏳ Tayyorgarlik vaqti", stats["prep_time"], PREP_TIME_LABELS),
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        return
    total_users = db.count_users()
    started = db.count_attempts_started()
    finished = db.count_attempts_finished()
    by_status = db.count_purchases_by_status()
    revenue = db.sum_confirmed_revenue()
    popularity = db.quiz_popularity()

    lines = [
        f"👥 Jami /start bosgan foydalanuvchilar: {total_users}",
        f"📝 Boshlangan testlar: {started}",
        f"✅ Tugatilgan testlar: {finished}",
        "",
        f"💰 To'lovlar:",
        f"   Tasdiqlangan: {by_status.get('confirmed', 0)}",
        f"   Kutilmoqda: {by_status.get('pending', 0)}",
        f"   Rad etilgan: {by_status.get('rejected', 0)}",
        f"   Pul qaytarilgan: {by_status.get('refunded', 0)}",
        f"   Jami tushum: {revenue:,} so'm",
        "",
        "📊 Testlar bo'yicha qiziqish:",
    ]
    for row in popularity:
        lines.append(f"   {row['title']}: {row['attempts']} marta boshlangan")

    await message.answer("\n".join(lines))


@dp.message(Command("addquiz"))
async def cmd_addquiz(message: Message, command: CommandObject):
    # Admin helper: /addquiz Title | description | price | free_questions(optional, default 10)
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args or "|" not in command.args:
        await message.answer("Foydalanish: /addquiz Sarlavha | Tavsif | Narx | Bepul savollar soni (ixtiyoriy)")
        return
    parts = [p.strip() for p in command.args.split("|")]
    title, description, price = parts[0], parts[1], int(parts[2])
    free_questions = int(parts[3]) if len(parts) > 3 else 10
    quiz_id = db.add_quiz(title, description, price, free_questions=free_questions)
    await message.answer(
        f"Test qo'shildi. ID = {quiz_id}. Bepul savollar: {free_questions} ta.\n"
        f"Endi /addquestion {quiz_id} orqali savollarni qo'shing."
    )


@dp.message(Command("clearquestions"))
async def cmd_clearquestions(message: Message, command: CommandObject):
    # Admin helper: /clearquestions <quiz_id> - wipes ALL questions for that quiz (irreversible).
    # Use this before re-uploading files in the correct order (e.g. to fix article numbering).
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /clearquestions <test_id>\n\n⚠️ Bu shu testdagi BARCHA savollarni butunlay o'chiradi!")
        return
    quiz_id = int(command.args.strip())
    quiz = db.get_quiz(quiz_id)
    if not quiz:
        await message.answer("Bunday ID li test topilmadi.")
        return
    n_before = db.count_questions(quiz_id)
    db.delete_questions_for_quiz(quiz_id)
    await message.answer(
        f"🗑️ \"{quiz['title']}\" testidagi {n_before} ta savol o'chirildi.\n"
        f"Endi /bulkadd {quiz_id} orqali fayllarni to'g'ri tartibda qaytadan yuklang."
    )


@dp.message(Command("backupdata"))
async def cmd_backupdata(message: Message):
    """Admin: /backupdata - exports every quiz's questions back into the same
    .txt bulk-upload format (=== separated, with Javob/Izoh/Mavzu lines), and
    sends it as a downloadable file. Keep a copy of this on your own computer
    now and then - it's your insurance if the Railway volume is ever lost,
    separate from just paying your Railway bill on time."""
    if message.from_user.id != config.ADMIN_ID:
        return
    quizzes = db.list_quizzes()
    if not quizzes:
        await message.answer("Hozircha hech qanday test yo'q.")
        return

    parts = []
    total_questions = 0
    for quiz in quizzes:
        questions = db.get_questions(quiz["id"])
        if not questions:
            continue
        parts.append(f"# QUIZ: {quiz['title']} (id={quiz['id']}, narx={quiz['price_uzs']})")
        for q in questions:
            block = [q["question_text"]]
            for i, opt in enumerate(q["options"]):
                # options already include their "A) " prefix from storage
                block.append(opt if re.match(r'^[A-D]\)', opt) else f"{chr(65+i)}) {opt}")
            correct_letter = chr(65 + q["correct_index"])
            block.append(f"Javob: {correct_letter}")
            if q.get("explanation"):
                block.append(f"Izoh: {q['explanation']}")
            if q.get("topic_tag"):
                block.append(f"Mavzu: {q['topic_tag']}")
            parts.append("\n".join(block))
            total_questions += 1
        parts.append("===END-OF-QUIZ===")

    backup_text = "\n===\n".join(parts)
    filename = f"backup_{datetime.date.today().isoformat()}.txt"
    file = BufferedInputFile(backup_text.encode("utf-8"), filename=filename)
    await message.answer_document(
        file,
        caption=f"📦 Backup: {len(quizzes)} ta test, {total_questions} ta savol. "
        f"Bu faylni kompyuteringizga saqlab qo'ying.",
    )


@dp.message(Command("listquizzes"))
async def cmd_listquizzes(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        return
    quizzes = db.list_quizzes()
    if not quizzes:
        await message.answer("Hozircha testlar yo'q. /addquiz orqali qo'shing.")
        return
    lines = []
    for q in quizzes:
        n = db.count_questions(q["id"])
        tag = " 📊 [DARAJANI ANIQLASH]" if q["is_assessment"] else ""
        lines.append(f"ID {q['id']}: {q['title']} — {q['price_uzs']:,} so'm — {n} ta savol — bepul: {q['free_questions']}{tag}")
    await message.answer("\n".join(lines))


@dp.message(Command("setassessment"))
async def cmd_setassessment(message: Message, command: CommandObject):
    """Admin: /setassessment <quiz_id> - marks that quiz as THE 15-question
    level-placement test shown right after onboarding. Only one at a time;
    setting a new one unmarks the previous."""
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /setassessment <test_id>\n\n/listquizzes orqali ID'larni ko'ring.")
        return
    quiz_id = int(command.args.strip())
    quiz = db.get_quiz(quiz_id)
    if not quiz:
        await message.answer("Bunday ID li test topilmadi.")
        return
    n = db.count_questions(quiz_id)
    db.set_assessment_quiz(quiz_id)
    note = "" if n == 15 else f"\n⚠️ Eslatma: bu testda {n} ta savol bor, tavsiya etilgan 15 ta emas."
    await message.answer(f"✅ \"{quiz['title']}\" endi Darajani aniqlash testi sifatida belgilandi.{note}")


# ---------------- admin: add question conversation ----------------

@dp.message(Command("bulkadd"))
async def cmd_bulkadd(message: Message, command: CommandObject, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /bulkadd <test_id>\n\nMasalan: /bulkadd 1")
        return
    quiz_id = int(command.args.strip())
    quiz = db.get_quiz(quiz_id)
    if quiz is None:
        await message.answer("Bunday ID li test topilmadi. /listquizzes orqali tekshiring.")
        return
    await state.update_data(quiz_id=quiz_id)
    await state.set_state(AddQuestion.waiting_bulk_file)
    await message.answer(
        f"\"{quiz['title']}\" uchun .txt fayl yuboring. Format:\n\n"
        "Savol matni?\n"
        "A) Variant 1\n"
        "B) Variant 2\n"
        "C) Variant 3\n"
        "D) Variant 4\n"
        "Javob: B\n"
        "Izoh: ixtiyoriy izoh\n"
        "===\n"
        "(keyingi savol shu tartibda, har biri === bilan ajratilgan)"
    )


@dp.message(StateFilter(AddQuestion.waiting_bulk_file), F.document)
async def addq_got_bulk_file(message: Message, state: FSMContext):
    data = await state.get_data()
    quiz_id = data["quiz_id"]
    file = await bot.get_file(message.document.file_id)
    file_bytes = await bot.download_file(file.file_path)
    try:
        text = file_bytes.read().decode("utf-8")
    except UnicodeDecodeError:
        await message.answer("Faylni o'qib bo'lmadi. Fayl UTF-8 formatida .txt bo'lishi kerak.")
        return

    parsed = parse_bulk_questions(text)
    if not parsed:
        await message.answer(
            "Hech qanday savol topilmadi. Formatni tekshiring va qaytadan yuboring, "
            "yoki /done deb tugating."
        )
        return

    start_index = db.count_questions(quiz_id)
    for i, q in enumerate(parsed):
        db.add_question(
            quiz_id=quiz_id,
            question_text=q["question_text"],
            options=q["options"],
            correct_index=q["correct_index"],
            order_index=start_index + i,
            explanation=q["explanation"],
            topic_tag=q.get("topic_tag"),
        )
    total = db.count_questions(quiz_id)
    await state.clear()
    await message.answer(
        f"✅ {len(parsed)} ta savol qo'shildi! (Jami: {total} ta)\n\n"
        f"Yana fayl qo'shish uchun /bulkadd {quiz_id} deb qayta yozing."
    )


@dp.message(StateFilter(AddQuestion.waiting_bulk_file))
async def addq_bulk_wrong_type(message: Message, state: FSMContext):
    if message.text and message.text.strip().lower() == "/done":
        await state.clear()
        await message.answer("Fayl yuklash bekor qilindi.")
        return
    await message.answer("Iltimos, .txt faylni hujjat (document) sifatida yuboring.")


@dp.message(Command("addquestion"))
async def cmd_addquestion(message: Message, command: CommandObject, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID:
        return
    if command.args and command.args.strip().isdigit():
        quiz_id = int(command.args.strip())
        quiz = db.get_quiz(quiz_id)
        if quiz is None:
            await message.answer("Bunday ID li test topilmadi. /listquizzes orqali tekshiring.")
            return
        await state.update_data(quiz_id=quiz_id)
        await state.set_state(AddQuestion.waiting_question)
        await message.answer(
            f"\"{quiz['title']}\" uchun savol qo'shyapsiz.\n\nSavol matnini yuboring:"
        )
    else:
        await state.set_state(AddQuestion.waiting_quiz_id)
        quizzes = db.list_quizzes()
        listing = "\n".join(f"ID {q['id']}: {q['title']}" for q in quizzes) or "(hozircha test yo'q)"
        await message.answer(f"Qaysi test uchun savol qo'shmoqchisiz? Test ID sini yuboring:\n\n{listing}")


@dp.message(StateFilter(AddQuestion.waiting_quiz_id))
async def addq_got_quiz_id(message: Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Iltimos, faqat test ID raqamini yuboring.")
        return
    quiz_id = int(message.text.strip())
    quiz = db.get_quiz(quiz_id)
    if quiz is None:
        await message.answer("Bunday ID li test topilmadi. Qaytadan urinib ko'ring.")
        return
    await state.update_data(quiz_id=quiz_id)
    await state.set_state(AddQuestion.waiting_question)
    await message.answer(f"\"{quiz['title']}\" uchun savol matnini yuboring:")


@dp.message(StateFilter(AddQuestion.waiting_question))
async def addq_got_question(message: Message, state: FSMContext):
    if message.text and message.text.strip().lower() == "/done":
        await state.clear()
        await message.answer("Savol qo'shish tugatildi.")
        return
    if not message.text:
        await message.answer("Iltimos, savol matnini yuboring.")
        return
    await state.update_data(question_text=message.text)
    await state.set_state(AddQuestion.waiting_options)
    await message.answer(
        "Endi javob variantlarini yuboring — har birini alohida qatorda, masalan:\n\n"
        "A) 1991\nB) 1992\nC) 1993\nD) 1994"
    )


@dp.message(StateFilter(AddQuestion.waiting_options))
async def addq_got_options(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Iltimos, variantlarni matn ko'rinishida yuboring.")
        return
    options = [line.strip() for line in message.text.split("\n") if line.strip()]
    if len(options) < 2:
        await message.answer("Kamida 2 ta variant kerak. Qaytadan yuboring (har biri alohida qatorda).")
        return
    await state.update_data(options=options)
    await state.set_state(AddQuestion.waiting_correct)
    numbered = "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options))
    await message.answer(f"Qaysi variant to'g'ri? Raqamini yuboring:\n\n{numbered}")


@dp.message(StateFilter(AddQuestion.waiting_correct))
async def addq_got_correct(message: Message, state: FSMContext):
    data = await state.get_data()
    options = data["options"]
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Iltimos, faqat raqam yuboring.")
        return
    correct_num = int(message.text.strip())
    if correct_num < 1 or correct_num > len(options):
        await message.answer(f"1 dan {len(options)} gacha raqam yuboring.")
        return

    await state.update_data(correct_num=correct_num)
    await state.set_state(AddQuestion.waiting_explanation)
    await message.answer(
        "Endi izoh yozing (nega bu javob to'g'ri — foydalanuvchi javobdan keyin ko'radi).\n"
        "Izoh kerak bo'lmasa, - (chiziqcha) yuboring."
    )


@dp.message(StateFilter(AddQuestion.waiting_explanation))
async def addq_got_explanation(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Iltimos, izoh matnini yoki - yuboring.")
        return
    data = await state.get_data()
    explanation = None if message.text.strip() == "-" else message.text.strip()

    quiz_id = data["quiz_id"]
    order_index = db.count_questions(quiz_id)
    db.add_question(
        quiz_id=quiz_id,
        question_text=data["question_text"],
        options=data["options"],
        correct_index=data["correct_num"] - 1,
        order_index=order_index,
        explanation=explanation,
    )
    await state.update_data(question_text=None, options=None, correct_num=None)
    await state.set_state(AddQuestion.waiting_question)
    total = db.count_questions(quiz_id)
    await message.answer(
        f"✅ Savol qo'shildi! (Jami: {total} ta)\n\n"
        f"Yana savol qo'shish uchun savol matnini yuboring, "
        f"yoki /done deb yozib tugating."
    )


# ---------------- entrypoint ----------------

async def main():
    db.init_db()

    scheduler = AsyncIOScheduler()
    # Checked hourly; a user only gets pinged once per inactivity crossing
    # (see get_inactive_users), so an hourly check just controls how soon
    # after the 24h mark they hear from us - not how often they're pinged.
    scheduler.add_job(send_inactivity_reminders, "interval", hours=1)
    scheduler.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
