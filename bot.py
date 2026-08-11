import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database as db

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AddQuestion(StatesGroup):
    waiting_quiz_id = State()
    waiting_question = State()
    waiting_options = State()
    waiting_correct = State()


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


async def send_question(chat_id, quiz_id, attempt, user_id):
    questions = db.get_questions(quiz_id)
    idx = attempt["current_index"]
    quiz = db.get_quiz(quiz_id)

    # Free-trial paywall: once a non-paying user hits the free question limit, stop and ask to pay.
    if not db.has_access(user_id, quiz_id) and idx >= quiz["free_questions"]:
        db.finish_attempt(attempt["id"])
        status = db.purchase_status(user_id, quiz_id)
        if status == "pending":
            await bot.send_message(chat_id, "Bepul savollar tugadi! To'lovingiz hali tasdiqlanmagan. Iltimos kuting.")
            return
        db.request_purchase(user_id, quiz_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ To'ladim, screenshot yubordim", callback_data=f"paid:{quiz_id}")
        ]])
        await bot.send_message(
            chat_id,
            f"Bepul {quiz['free_questions']} ta savol tugadi!\n\n"
            f"Qolgan savollarni yechish uchun to'lov qiling:\n"
            f"{quiz['title']}\nNarxi: {quiz['price_uzs']:,} so'm\n\n"
            f"{config.PAYMENT_INSTRUCTIONS}\n\n"
            f"To'lov qilgach, screenshotni shu botga rasm qilib yuboring.",
            reply_markup=kb,
        )
        return

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

    status = db.purchase_status(user_id, quiz_id)
    if status == "pending" and not db.has_access(user_id, quiz_id):
        await callback.message.answer("To'lovingiz hali tasdiqlanmagan. Iltimos kuting.")
        await callback.answer()
        return

    attempt = db.get_active_attempt(user_id, quiz_id)
    if attempt is None:
        db.start_attempt(user_id, quiz_id)
        attempt = db.get_active_attempt(user_id, quiz_id)
    await callback.message.answer(f"{quiz['title']} boshlandi!")
    await send_question(callback.message.chat.id, quiz_id, attempt, user_id)
    await callback.answer()


@dp.callback_query(F.data.startswith("paid:"))
async def on_paid_pressed(callback: CallbackQuery):
    await callback.message.answer("Rasmni (screenshotni) shu chatga yuboring — biz tekshiramiz.")
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
            caption += f"\nTest: {quiz['title']}"
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
    quiz = db.get_quiz(quiz_id)
    await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ TASDIQLANDI")
    await bot.send_message(user_id, f"To'lovingiz tasdiqlandi! /start bosing va \"{quiz['title']}\" ni qayta tanlang.")
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

    db.advance_attempt(attempt["id"], correct)
    feedback = "✅ To'g'ri!" if correct else f"❌ Noto'g'ri. To'g'ri javob: {q['options'][q['correct_index']]}"
    await callback.answer(feedback, show_alert=not correct)

    updated_attempt = db.get_active_attempt(user_id, quiz_id)
    if updated_attempt is None:
        # attempt just got marked finished mid-flight in edge cases
        return
    await send_question(callback.message.chat.id, quiz_id, updated_attempt, user_id)


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
        lines.append(f"ID {q['id']}: {q['title']} — {q['price_uzs']:,} so'm — {n} ta savol — bepul: {q['free_questions']}")
    await message.answer("\n".join(lines))


# ---------------- admin: add question conversation ----------------

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

    quiz_id = data["quiz_id"]
    order_index = db.count_questions(quiz_id)
    db.add_question(
        quiz_id=quiz_id,
        question_text=data["question_text"],
        options=options,
        correct_index=correct_num - 1,
        order_index=order_index,
    )
    await state.update_data(question_text=None, options=None)
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
