"""
Run this once to load a quiz + its questions into the database:
    python seed_data.py

Edit QUIZZES below with your real content, or write a small script
that reads from a spreadsheet/CSV and calls db.add_quiz / db.add_question
the same way.
"""

import database as db

db.init_db()

quiz_id = db.add_quiz(
    title="Konstitutsiyaviy huquq",
    description="50 ta savoldan iborat sinov testi",
    price_uzs=15000,
    language="uz",
)

sample_questions = [
    {
        "question_text": "O'zbekiston Respublikasi Konstitutsiyasi qachon qabul qilingan?",
        "options": ["A) 1991", "B) 1992", "C) 1993", "D) 1994"],
        "correct_index": 1,
    },
    {
        "question_text": "Oliy Majlis nechta palatadan iborat?",
        "options": ["A) 1", "B) 2", "C) 3", "D) 4"],
        "correct_index": 1,
    },
    # Add the rest of your real questions here in the same shape.
]

for i, q in enumerate(sample_questions):
    db.add_question(
        quiz_id=quiz_id,
        question_text=q["question_text"],
        options=q["options"],
        correct_index=q["correct_index"],
        order_index=i,
    )

print(f"Seeded quiz id={quiz_id} with {len(sample_questions)} questions.")
