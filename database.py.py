import sqlite3
import json
from contextlib import contextmanager

from config import DB_PATH


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
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | rejected
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
        """)
        cols = [r["name"] for r in db.execute("PRAGMA table_info(quizzes)").fetchall()]
        if "free_questions" not in cols:
            db.execute("ALTER TABLE quizzes ADD COLUMN free_questions INTEGER DEFAULT 10")

        qcols = [r["name"] for r in db.execute("PRAGMA table_info(questions)").fetchall()]
        if "explanation" not in qcols:
            db.execute("ALTER TABLE questions ADD COLUMN explanation TEXT")

        ucols = [r["name"] for r in db.execute("PRAGMA table_info(users)").fetchall()]
        if "referred_by" not in ucols:
            db.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "last_daily_date" not in ucols:
            db.execute("ALTER TABLE users ADD COLUMN last_daily_date TEXT")

        pcols = [r["name"] for r in db.execute("PRAGMA table_info(purchases)").fetchall()]
        if "price_uzs" not in pcols:
            db.execute("ALTER TABLE purchases ADD COLUMN price_uzs INTEGER")


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


def add_question(quiz_id, question_text, options, correct_index, order_index=0, explanation=None):
    with get_db() as db:
        db.execute(
            "INSERT INTO questions (quiz_id, question_text, options, correct_index, order_index, explanation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (quiz_id, question_text, json.dumps(options, ensure_ascii=False), correct_index, order_index, explanation),
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


# ---------- purchases ----------

def request_purchase(user_id, quiz_id, price_uzs=None):
    with get_db() as db:
        db.execute(
            "INSERT INTO purchases (user_id, quiz_id, status, price_uzs) VALUES (?, ?, 'pending', ?) "
            "ON CONFLICT(user_id, quiz_id) DO UPDATE SET status='pending', price_uzs=excluded.price_uzs",
            (user_id, quiz_id, price_uzs),
        )


def confirm_purchase(user_id, quiz_id):
    with get_db() as db:
        db.execute(
            "UPDATE purchases SET status='confirmed' WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        )


def reject_purchase(user_id, quiz_id):
    with get_db() as db:
        db.execute(
            "UPDATE purchases SET status='rejected' WHERE user_id=? AND quiz_id=?",
            (user_id, quiz_id),
        )


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
    """Ranking among paying users only: most questions answered, then most correct answers."""
    with get_db() as db:
        rows = db.execute(
            "SELECT a.user_id, u.username, u.first_name, "
            "COALESCE(SUM(a.current_index), 0) AS total_answered, "
            "COALESCE(SUM(a.score), 0) AS total_correct "
            "FROM attempts a "
            "JOIN users u ON u.telegram_id = a.user_id "
            "WHERE a.user_id IN (SELECT DISTINCT user_id FROM purchases WHERE status='confirmed') "
            "GROUP BY a.user_id "
            "ORDER BY total_answered DESC, total_correct DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return rows


# ---------- attempts ----------

def start_attempt(user_id, quiz_id):
    with get_db() as db:
        db.execute(
            "DELETE FROM attempts WHERE user_id=? AND quiz_id=? AND finished=0",
            (user_id, quiz_id),
        )
        cur = db.execute(
            "INSERT INTO attempts (user_id, quiz_id, current_index, score) VALUES (?, ?, 0, 0)",
            (user_id, quiz_id),
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
