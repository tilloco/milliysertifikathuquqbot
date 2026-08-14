"""
Parse quiz txt files (question blocks separated by "===") and load them
into the database as questions for a given quiz.

Usage:
    python add_questions_from_txt.py <quiz_id> file1.txt file2.txt ...

Question ids are assigned automatically by SQLite (AUTOINCREMENT) - you
never set them yourself. The first question you ever insert becomes id=1,
the next id=2, and so on, no matter which file it came from.

Each block in the txt file must look like:

    [Bob nomi] N-moddaga ko'ra ... ?
    A) ...
    B) ...
    C) ...
    D) ...
    Javob: B
    Izoh: [Bob nomi] N-modda. ...

The Javob letter (A/B/C/D) is converted to a 0-based correct_index.
article_number is auto-extracted from the "N-modda" pattern by
database.add_question - no extra work needed for that either.
"""

import sys
import database as db

LETTER_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}


def parse_file(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()

    blocks = [b.strip() for b in content.split("===") if b.strip()]
    questions = []

    for block in blocks:
        lines = [l.rstrip() for l in block.splitlines() if l.strip()]
        question_text = lines[0]
        options = []
        correct_index = None
        explanation = None

        i = 1
        while i < len(lines) and lines[i][:2] in ("A)", "B)", "C)", "D)"):
            options.append(lines[i])
            i += 1

        for line in lines[i:]:
            if line.startswith("Javob:"):
                letter = line.split(":", 1)[1].strip()
                correct_index = LETTER_TO_INDEX.get(letter)
            elif line.startswith("Izoh:"):
                explanation = line.split(":", 1)[1].strip()

        if len(options) != 4 or correct_index is None:
            raise ValueError(f"Could not parse block properly:\n{block[:200]}")

        questions.append({
            "question_text": question_text,
            "options": options,
            "correct_index": correct_index,
            "explanation": explanation,
        })

    return questions


def main():
    if len(sys.argv) < 3:
        print("Usage: python add_questions_from_txt.py <quiz_id> file1.txt [file2.txt ...]")
        sys.exit(1)

    quiz_id = int(sys.argv[1])
    files = sys.argv[2:]

    db.init_db()
    order_index = db.count_questions(quiz_id)  # continue numbering after whatever's already there
    total_added = 0

    for path in files:
        questions = parse_file(path)
        for q in questions:
            db.add_question(
                quiz_id=quiz_id,
                question_text=q["question_text"],
                options=q["options"],
                correct_index=q["correct_index"],
                order_index=order_index,
                explanation=q["explanation"],
            )
            order_index += 1
            total_added += 1
        print(f"{path}: added {len(questions)} questions")

    print(f"Done. Added {total_added} questions total to quiz_id={quiz_id}.")


if __name__ == "__main__":
    main()
