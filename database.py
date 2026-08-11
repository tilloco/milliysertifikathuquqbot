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
            order_index INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT
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


# ---------- users ----------

def upsert_user(telegram_id, username, first_name):
    with get_db() as db:
        db.execute(
            "INSERT INTO users (telegram_id, username, first_name) VALUES (?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (telegram_id, username, first_name),
        )


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


def add_question(quiz_id, question_text, options, correct_index, order_index=0):
    with get_db() as db:
        db.execute(
            "INSERT INTO questions (quiz_id, question_text, options, correct_index, order_index) "
            "VALUES (?, ?, ?, ?, ?)",
            (quiz_id, question_text, json.dumps(options, ensure_ascii=False), correct_index, order_index),
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

def request_purchase(user_id, quiz_id):
    with get_db() as db:
        db.execute(
            "INSERT INTO purchases (user_id, quiz_id, status) VALUES (?, ?, 'pending') "
            "ON CONFLICT(user_id, quiz_id) DO UPDATE SET status='pending'",
            (user_id, quiz_id),
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
