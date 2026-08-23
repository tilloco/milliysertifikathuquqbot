import sqlite3
import json
import re
import datetime
from contextlib import contextmanager

from config import DB_PATH

MODULE_SIZE = 10
DAILY_FREE_LIMIT = 10


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            price_uzs INTEGER NOT NULL,
            language TEXT DEFAULT 'uz',
            free_questions INTEGER DEFAULT 10
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
            question_text TEXT NOT NULL,
            options TEXT NOT NULL,      -- JSON list, e.g. ["A) ...","B) ...","C) ...","D) ..."]
            correct_index INTEGER NOT NULL,  -- 0-based index into options
            order_index INTEGER DEFAULT 0,
            explanation TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            referred_by INTEGER,
            last_daily_date TEXT
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            quiz_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | rejected | refunded
            requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, quiz_id)
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            quiz_id INTEGER NOT NULL,
            current_index INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            finished INTEGER DEFAULT 0,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS attempt_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            selected_index INTEGER NOT NULL,
            is_correct INTEGER NOT NULL
        );
        """)
        cols = [r["name"] for r in db.execute("PRAGMA table_info(quizzes)").fetchall()]
        if "free_questions" not in cols:
            db.execute("ALTER TABLE quizzes ADD COLUMN free_questions INTEGER DEFAULT 10")

        qcols = [r["name"] for r in db.execute("PRAGMA table_info(questions)").fetchall()]
        if "explanation" not in qcols:
            db.execute("ALTER TABLE questions ADD COLUMN explanation TEXT")
        if "article_number" not in qcols:
            db.execute("ALTER TABLE questions ADD COLUMN article_number INTEGER")

        ucols = [r["name"] for r in db.execute("PRAGMA table_info(users)").fetchall()]
        if "referred_by" not in ucols:
            db.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "last_daily_date" not in ucols:
            db.execute("ALTER TABLE users ADD COLUMN last_daily_date TEXT")
        if "free_answers_count" not in ucols:
            db.execute("ALTER TABLE users ADD COLUMN free_answers_count INTEGER DEFAULT 0")
        if "free_answers_date" not in ucols:
            db.execute("ALTER TABLE users ADD COLUMN free_answers_date TEXT")

        pcols = [r["name"] for r in db.execute("PRAGMA table_info(purchases)").fetchall()]
        if "price_uzs" not in pcols:
            db.execute("ALTER TABLE purchases ADD COLUMN price_uzs INTEGER")
        if "confirmed_at" not in pcols:
            db.execute("ALTER TABLE purchases ADD COLUMN confirmed_at TEXT")

        acols = [r["name"] for r in db.execute("PRAGMA table_info(attempts)").fetchall()]
        if "module_number" not in acols:
            db.execute("ALTER TABLE attempts ADD COLUMN module_number INTEGER DEFAULT 1")


# ---------- users ----------

def upsert_user(telegram_id, username, first_name, referred_by=None):
    with get_db() as db:
        existing = db.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        if existing:
            db.execute(
                "UPDATE users SET username=?, first_name=? WHERE telegram_id=?",
                (username, first_name, telegram_id),
            )
        else:
            db.execute(
                "INSERT INTO users (telegram_id, username, first_name, referred_by) VALUES (?, ?, ?, ?)",
                (telegram_id, username, first_name, referred_by),
            )


def count_referrals(telegram_id):
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE referred_by=?", (telegram_id,)
        ).fetchone()
        return row["c"]


def get_daily_free_used(telegram_id):
    """How many free questions this (unpaid) user has already answered today."""
    today = datetime.date.today().isoformat()
    with get_db() as db:
        row = db.execute(
            "SELECT free_answers_count, free_answers_date FROM users WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()
        if not row or row["free_answers_date"] != today:
            return 0
        return row["free_answers_count"] or 0


def daily_free_remaining(telegram_id):
    return max(0, DAILY_FREE_LIMIT - get_daily_free_used(telegram_id))


def record_daily_free_answer(telegram_id):
    """Call once per answered question for a non-paying user. Resets automatically on a new day."""
    today = datetime.date.today().isoformat()
    with get_db() as db:
        row = db.execute(
            "SELECT free_answers_count, free_answers_date FROM users WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()
        if not row or row["free_answers_date"] != today:
            db.execute(
                "UPDATE users SET free_answers_count=1, free_answers_date=? WHERE telegram_id=?",
                (today, telegram_id),
            )
        else:
            db.execute(
                "UPDATE users SET free_answers_count=free_answers_count+1 WHERE telegram_id=?",
                (telegram_id,),
            )


def has_discount(telegram_id, threshold=3):
    return count_referrals(telegram_id) >= threshold


def get_last_daily_date(telegram_id):
    with get_db() as db:
        row = db.execute("SELECT last_daily_date FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        return row["last_daily_date"] if row else None


def set_last_daily_date(telegram_id, date_str):
    with get_db() as db:
        db.execute("UPDATE users SET last_daily_date=? WHERE telegram_id=?", (date_str, telegram_id))


def get_random_question():
    with get_db() as db:
        row = db.execute("SELECT * FROM questions ORDER BY RANDOM() LIMIT 1").fetchone()
        if row is None:
            return None
        d = dict(row)
        d["options"] = json.loads(d["options"])
        return d


def get_question_by_id(question_id):
    with get_db() as db:
        row = db.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["options"] = json.loads(d["options"])
        return d


# ---------- analytics ----------

def count_users():
    with get_db() as db:
        return db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def count_users_since(date_str):
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE telegram_id IN "
            "(SELECT DISTINCT user_id FROM attempts WHERE started_at >= ?)",
            (date_str,),
        ).fetchone()
        return row["c"]


def count_attempts_started():
    with get_db() as db:
        return db.execute("SELECT COUNT(*) AS c FROM attempts").fetchone()["c"]


def count_attempts_finished():
    with get_db() as db:
        return db.execute("SELECT COUNT(*) AS c FROM attempts WHERE finished=1").fetchone()["c"]


def count_purchases_by_status():
    with get_db() as db:
        rows = db.execute(
            "SELECT status, COUNT(*) AS c FROM purchases GROUP BY status"
        ).fetchall()
        return {r["status"]: r["c"] for r in rows}


def sum_confirmed_revenue():
    with get_db() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(price_uzs), 0) AS total FROM purchases WHERE status='confirmed'"
        ).fetchone()
        return row["total"]


def quiz_popularity():
    with get_db() as db:
        rows = db.execute(
            "SELECT q.title, COUNT(a.id) AS attempts "
            "FROM quizzes q LEFT JOIN attempts a ON a.quiz_id = q.id "
            "GROUP BY q.id ORDER BY attempts DESC"
        ).fetchall()
        return rows


# ---------- quizzes ----------

def list_quizzes():
    with get_db() as db:
        return db.execute("SELECT * FROM quizzes ORDER BY id").fetchall()


def get_quiz(quiz_id):
    with get_db() as db:
        return db.execute("SELECT * FROM quizzes WHERE id=?", (quiz_id,)).fetchone()


def add_quiz(title, description, price_uzs, language="uz", free_questions=10):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO quizzes (title, description, price_uzs, language, free_questions) VALUES (?, ?, ?, ?, ?)",
            (title, description, price_uzs, language, free_questions),
        )
        return cur.lastrowid


def count_questions(quiz_id):
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) AS c FROM questions WHERE quiz_id=?", (quiz_id,)).fetchone()
        return row["c"]


ARTICLES_PER_MODULE = 10  # for article-tagged quizzes (e.g. the Constitution): 10 distinct moddas per test


def get_module_boundaries(quiz_id):
    """Splits a quiz's questions into modules (tests).

    For quizzes where every question is tagged with an article_number (e.g. the
    Constitution), a module is a run of consecutive questions covering exactly
    ARTICLES_PER_MODULE distinct article numbers - question count per module can
    vary (one module might be 10 questions, another 14, if some articles needed
    extra questions). The last, still-filling module can be smaller than 10
    articles until more questions get appended.

    For quizzes without article numbers, falls back to the old fixed-size
    MODULE_SIZE (10 questions) chunking.

    Returns a list of (start_index, end_index) tuples, end exclusive.
    """
    questions = get_questions(quiz_id)
    if not questions:
        return []

    has_articles = all(q.get("article_number") for q in questions)
    boundaries = []

    if has_articles:
        start = 0
        seen = set()
        for i, q in enumerate(questions):
            seen.add(q["article_number"])
            if len(seen) >= ARTICLES_PER_MODULE:
                boundaries.append((start, i + 1))
                start = i + 1
                seen = set()
        if start < len(questions):
            boundaries.append((start, len(questions)))
    else:
        for start in range(0, len(questions), MODULE_SIZE):
            boundaries.append((start, min(start + MODULE_SIZE, len(questions))))

    return boundaries


def get_module_bounds(quiz_id, module_number):
    """(start_index, end_index) for a specific 1-based module number, or None if it doesn't exist."""
    boundaries = get_module_boundaries(quiz_id)
    if 1 <= module_number <= len(boundaries):
        return boundaries[module_number - 1]
    return None


def count_modules(quiz_id):
    return len(get_module_boundaries(quiz_id))


_ARTICLE_RE = re.compile(r"(\d+)-modda")


def _extract_article_number(text):
    """Pulls the first 'N-modda' reference out of a question's text, if any."""
    m = _ARTICLE_RE.search(text or "")
    return int(m.group(1)) if m else None


def add_question(quiz_id, question_text, options, correct_index, order_index=0, explanation=None, article_number=None):
    if article_number is None:
        article_number = _extract_article_number(question_text) or _extract_article_number(explanation)
    with get_db() as db:
        db.execute(
            "INSERT INTO questions (quiz_id, question_text, options, correct_index, order_index, explanation, article_number) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (quiz_id, question_text, json.dumps(options, ensure_ascii=False), correct_index, order_index, explanation, article_number),
        )


def get_questions(quiz_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM questions WHERE quiz_id=? ORDER BY order_index, id", (quiz_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["options"] = json.loads(d["options"])
            result.append(d)
        return result


def get_module_label(quiz_id, module_number):
    """Returns 'X-Y-moddalar' for article-tagged quizzes (based on actual module
    boundaries, which can span more or fewer than 10 questions), otherwise 'Test N'."""
    bounds = get_module_bounds(quiz_id, module_number)
    if not bounds:
        return f"Test {module_number}"
    start, end = bounds
    questions = get_questions(quiz_id)[start:end]
    numbers = [q.get("article_number") for q in questions]
    if questions and all(numbers):
        lo, hi = min(numbers), max(numbers)
        return f"{lo}-moddalar" if lo == hi else f"{lo}-{hi}-moddalar"
    return f"Test {module_number}"


def delete_questions_for_quiz(quiz_id):
    """Admin helper: wipe every question for a quiz so it can be re-uploaded in clean order."""
    with get_db() as db:
        db.execute("DELETE FROM questions WHERE quiz_id=?", (quiz_id,))


# ---------- purchases ----------

def request_purchase(user_id, quiz_id, price_uzs=None):
    with get_db() as db:
        db.execute(
            "INSERT INTO purchases (user_id, quiz_id, status, price_uzs) VALUES (?, ?, 'pending', ?) "
            "ON CONFLICT(user_id, quiz_id) DO UPDATE SET status='pending', price_uzs=excluded.price_uzs",
            (user_id, quiz_id, price_uzs),
        )


def list_pending_purchases():
    """Safety net for the admin: every pending purchase with user info, in case
    the automatic screenshot notification was ever missed or misconfigured."""
    with get_db() as db:
        rows = db.execute(
            "SELECT p.user_id, p.quiz_id, p.price_uzs, p.requested_at, "
            "u.username, u.first_name, q.title AS quiz_title "
            "FROM purchases p "
            "LEFT JOIN users u ON u.telegram_id = p.user_id "
            "LEFT JOIN quizzes q ON q.id = p.quiz_id "
            "WHERE p.status='pending' "
            "ORDER BY p.requested_at ASC"
        ).fetchall()
        return rows


def confirm_purchase(user_id, quiz_id):
    with get_db() as db:
        db.execute(
            "UPDATE purchases SET status='confirmed', confirmed_at=CURRENT_TIMESTAMP "
            "WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        )


def reject_purchase(user_id, quiz_id):
    with get_db() as db:
        db.execute(
            "UPDATE purchases SET status='rejected' WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        )


def refund_purchase(user_id, quiz_id):
    """Admin calls this AFTER manually sending the money back to the buyer's
    card. Marks the purchase refunded, which revokes access immediately
    (has_any_confirmed_purchase only counts status='confirmed')."""
    with get_db() as db:
        db.execute(
            "UPDATE purchases SET status='refunded' WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        )


def get_purchase(user_id, quiz_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM purchases WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        ).fetchone()


def days_since_confirmed(user_id, quiz_id):
    """Days elapsed since this purchase was confirmed, or None if not confirmed
    (or confirmed before this column existed - treat as unknown)."""
    row = get_purchase(user_id, quiz_id)
    if not row or not row["confirmed_at"]:
        return None
    try:
        confirmed = datetime.datetime.fromisoformat(row["confirmed_at"])
    except ValueError:
        return None
    return (datetime.datetime.utcnow() - confirmed).days


def has_access(user_id, quiz_id):
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND quiz_id=? AND status='confirmed'",
            (user_id, quiz_id),
        ).fetchone()
        return row is not None


def has_any_confirmed_purchase(user_id):
    """Global access check: one confirmed payment (for any topic) unlocks every topic."""
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND status='confirmed' LIMIT 1",
            (user_id,),
        ).fetchone()
        return row is not None


def has_any_pending_purchase(user_id):
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND status='pending' LIMIT 1",
            (user_id,),
        ).fetchone()
        return row is not None


def get_confirmed_purchase(user_id):
    """The user's confirmed purchase row (any topic - one payment unlocks all), or None."""
    with get_db() as db:
        return db.execute(
            "SELECT * FROM purchases WHERE user_id=? AND status='confirmed' LIMIT 1",
            (user_id,),
        ).fetchone()


def purchase_price(user_id, quiz_id):
    with get_db() as db:
        row = db.execute(
            "SELECT price_uzs FROM purchases WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        ).fetchone()
        return row["price_uzs"] if row else None


def purchase_status(user_id, quiz_id):
    with get_db() as db:
        row = db.execute(
            "SELECT status FROM purchases WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        ).fetchone()
        return row["status"] if row else None


def reset_user_purchases(user_id):
    """Admin/testing helper: wipe a user's purchase history so they hit the paywall again."""
    with get_db() as db:
        db.execute("DELETE FROM purchases WHERE user_id=?", (user_id,))


def get_pending_quiz_id(user_id):
    with get_db() as db:
        row = db.execute(
            "SELECT quiz_id FROM purchases WHERE user_id=? AND status='pending' "
            "ORDER BY requested_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["quiz_id"] if row else None


# ---------- leaderboard ----------

def get_leaderboard(limit=10):
    """Ranking among paying users only, for the CURRENT calendar month:
    most questions answered, then most correct answers. Resets automatically each month."""
    with get_db() as db:
        rows = db.execute(
            "SELECT a.user_id, u.username, u.first_name, "
            "COALESCE(SUM(a.current_index), 0) AS total_answered, "
            "COALESCE(SUM(a.score), 0) AS total_correct "
            "FROM attempts a "
            "JOIN users u ON u.telegram_id = a.user_id "
            "WHERE a.user_id IN (SELECT DISTINCT user_id FROM purchases WHERE status='confirmed') "
            "AND strftime('%Y-%m', a.started_at) = strftime('%Y-%m', 'now') "
            "GROUP BY a.user_id "
            "ORDER BY total_answered DESC, total_correct DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return rows


# ---------- attempts ----------

def start_attempt(user_id, quiz_id, module_number=1):
    bounds = get_module_bounds(quiz_id, module_number)
    start_index = bounds[0] if bounds else (module_number - 1) * MODULE_SIZE
    with get_db() as db:
        old = db.execute(
            "SELECT id FROM attempts WHERE user_id=? AND quiz_id=? AND finished=0",
            (user_id, quiz_id),
        ).fetchall()
        for row in old:
            db.execute("DELETE FROM attempt_answers WHERE attempt_id=?", (row["id"],))
        db.execute(
            "DELETE FROM attempts WHERE user_id=? AND quiz_id=? AND finished=0",
            (user_id, quiz_id),
        )
        cur = db.execute(
            "INSERT INTO attempts (user_id, quiz_id, current_index, score, module_number) VALUES (?, ?, ?, 0, ?)",
            (user_id, quiz_id, start_index, module_number),
        )
        return cur.lastrowid


def get_active_attempt(user_id, quiz_id):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM attempts WHERE user_id=? AND quiz_id=? AND finished=0 "
            "ORDER BY id DESC LIMIT 1",
            (user_id, quiz_id),
        ).fetchone()


def advance_attempt(attempt_id, correct):
    with get_db() as db:
        if correct:
            db.execute(
                "UPDATE attempts SET current_index = current_index + 1, score = score + 1 WHERE id=?",
                (attempt_id,),
            )
        else:
            db.execute(
                "UPDATE attempts SET current_index = current_index + 1 WHERE id=?",
                (attempt_id,),
            )


def finish_attempt(attempt_id):
    with get_db() as db:
        db.execute("UPDATE attempts SET finished=1 WHERE id=?", (attempt_id,))


def record_answer(attempt_id, question_id, selected_index, is_correct):
    with get_db() as db:
        db.execute(
            "INSERT INTO attempt_answers (attempt_id, question_id, selected_index, is_correct) "
            "VALUES (?, ?, ?, ?)",
            (attempt_id, question_id, selected_index, 1 if is_correct else 0),
        )


def get_attempt_answers(attempt_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT aa.selected_index, aa.is_correct, q.question_text, q.options, q.correct_index, q.explanation "
            "FROM attempt_answers aa JOIN questions q ON q.id = aa.question_id "
            "WHERE aa.attempt_id=? ORDER BY aa.id",
            (attempt_id,),
        ).fetchall()
        result = []
        for r in rows:
            opts = json.loads(r["options"])
            result.append({
                "question_text": r["question_text"],
                "selected_text": opts[r["selected_index"]],
                "correct_text": opts[r["correct_index"]],
                "is_correct": bool(r["is_correct"]),
                "explanation": r["explanation"],
            })
        return result


def get_completed_modules(user_id, quiz_id):
    """Module numbers the user has fully answered (not just paywall-cut-short)."""
    if not count_questions(quiz_id):
        return set()
    with get_db() as db:
        rows = db.execute(
            "SELECT id, module_number FROM attempts WHERE user_id=? AND quiz_id=? AND finished=1",
            (user_id, quiz_id),
        ).fetchall()
        completed = set()
        for r in rows:
            answered = db.execute(
                "SELECT COUNT(*) AS c FROM attempt_answers WHERE attempt_id=?", (r["id"],)
            ).fetchone()["c"]
            bounds = get_module_bounds(quiz_id, r["module_number"])
            if not bounds:
                continue
            start, end = bounds
            module_size = end - start
            if module_size > 0 and answered >= module_size:
                completed.add(r["module_number"])
        return completed
