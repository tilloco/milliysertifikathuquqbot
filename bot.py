import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import config
import database as db

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


# ---------------- helpers ----------------

def quizzes_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for q in db.list_quizzes():
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{q['title']} — {q['price_uzs']:,} so'm",
                callback_data=f"quiz:{q['id']}",
            )
        ])
    return kb


def question_keyboard(quiz_id, q_index, options):
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for i, opt in enumerate(options):
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=opt, callback_data=f"ans:{quiz_id}:{q_index}:{i}")
        ])
    return kb


async def send_question(chat_id, quiz_id, attempt):
    questions = db.get_questions(quiz_id)
    idx = attempt["current_index"]
    if idx >= len(questions):
        db.finish_attempt(attempt["id"])
        score = attempt["score"]
        total = len(questions)
        await bot.send_message(
            chat_id,
            f"Test tugadi!\nNatija: {score}/{total}",
        )
        return
    q = questions[idx]
    await bot.send_message(
        chat_id,
        f"Savol {idx + 1}/{len(questions)}:\n\n{q['question_text']}",
        reply_markup=question_keyboard(quiz_id, idx, q["options"]),
    )


# ---------------- user commands ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    quizzes = db.list_quizzes()
    if not quizzes:
        await message.answer("Hozircha testlar mavjud emas. Tez orada qo'shiladi!")
        return
    await message.answer(
        "Assalomu alaykum! Imtihonga tayyorlanish uchun testni tanlang:",
        reply_markup=quizzes_keyboard(),
    )


@dp.callback_query(F.data.startswith("quiz:"))
async def on_quiz_selected(callback: CallbackQuery):
    quiz_id = int(callback.data.split(":")[1])
    quiz = db.get_quiz(quiz_id)
    user_id = callback.from_user.id

    if db.has_access(user_id, quiz_id):
        attempt = db.get_active_attempt(user_id, quiz_id)
        if attempt is None:
            attempt_id = db.start_attempt(user_id, quiz_id)
            attempt = db.get_active_attempt(user_id, quiz_id)
        await callback.message.answer(f"{quiz['title']} boshlandi!")
        await send_question(callback.message.chat.id, quiz_id, attempt)
        await callback.answer()
        return

    status = db.purchase_status(user_id, quiz_id)
    if status == "pending":
        await callback.message.answer("To'lovingiz hali tasdiqlanmagan. Iltimos kuting.")
        await callback.answer()
        return

    db.request_purchase(user_id, quiz_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ To'ladim, screenshot yubordim", callback_data=f"paid:{quiz_id}")
    ]])
    await callback.message.answer(
        f"{quiz['title']}\nNarxi: {quiz['price_uzs']:,} so'm\n\n"
        f"{config.PAYMENT_INSTRUCTIONS}\n\n"
        f"To'lov qilgach, screenshotni shu botga rasm qilib yuboring.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("paid:"))
async def on_paid_pressed(callback: CallbackQuery):
    await callback.message.answer("Rasmni (screenshotni) shu chatga yuboring — biz tekshiramiz.")
    await callback.answer()


@dp.message(F.photo)
async def on_payment_screenshot(message: Message):
    # Forward the screenshot to the admin along with the buyer's info,
    # so the admin can confirm with /confirm <user_id> <quiz_id>
    caption = (
        f"To'lov screenshoti\n"
        f"Foydalanuvchi: {message.from_user.full_name} (@{message.from_user.username})\n"
        f"ID: {message.from_user.id}\n\n"
        f"Tasdiqlash uchun: /confirm {message.from_user.id} <quiz_id>"
    )
    if config.ADMIN_ID:
        await bot.send_photo(config.ADMIN_ID, message.photo[-1].file_id, caption=caption)
    await message.answer("Rahmat! To'lovingiz tekshirilmoqda, tez orada tasdiqlaymiz.")


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

    db.advance_attempt(attempt["id"], correct)
    feedback = "✅ To'g'ri!" if correct else f"❌ Noto'g'ri. To'g'ri javob: {q['options'][q['correct_index']]}"
    await callback.answer(feedback, show_alert=not correct)

    updated_attempt = db.get_active_attempt(user_id, quiz_id)
    if updated_attempt is None:
        # attempt just got marked finished mid-flight in edge cases
        return
    await send_question(callback.message.chat.id, quiz_id, updated_attempt)


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
    await message.answer(f"Tasdiqlandi: user {user_id}, quiz {quiz_id}")
    quiz = db.get_quiz(quiz_id)
    await bot.send_message(user_id, f"To'lovingiz tasdiqlandi! /start bosing va \"{quiz['title']}\" ni qayta tanlang.")


@dp.message(Command("addquiz"))
async def cmd_addquiz(message: Message, command: CommandObject):
    # Quick admin helper: /addquiz Title | description | price
    if message.from_user.id != config.ADMIN_ID:
        return
    if not command.args or "|" not in command.args:
        await message.answer("Foydalanish: /addquiz Sarlavha | Tavsif | Narx")
        return
    title, description, price = [p.strip() for p in command.args.split("|")]
    quiz_id = db.add_quiz(title, description, int(price))
    await message.answer(f"Test qo'shildi. ID = {quiz_id}. Endi savollarni seed_data.py orqali yoki qo'lda qo'shing.")


# ---------------- entrypoint ----------------

async def main():
    db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
