import sqlite3
import os
from encryption import encrypt, decrypt

DB_PATH = os.getenv("DB_PATH", "db.sqlite")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            role            TEXT DEFAULT 'user',
            prpt_username   TEXT DEFAULT '',
            prpt_password   TEXT DEFAULT '',
            prpt_rep_id     TEXT DEFAULT '',
            ghl_api_token   TEXT DEFAULT '',
            ghl_location_id TEXT DEFAULT '',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            status      TEXT DEFAULT 'pending',
            started_at  TIMESTAMP,
            finished_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS job_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id     INTEGER NOT NULL,
            message    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );
    """)
    conn.commit()
    conn.close()


# ── Users ──────────────────────────────────────────────────────────────────

def create_user(email: str, password_hash: str, role: str = "user") -> int:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
        (email, password_hash, role),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def _decrypt_user(user):
    if user is None:
        return None
    u = dict(user)
    u["prpt_password"] = decrypt(u.get("prpt_password") or "")
    u["ghl_api_token"] = decrypt(u.get("ghl_api_token") or "")
    return u


def get_user_by_email(email: str):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return _decrypt_user(user)


def get_user_by_id(user_id: int):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return _decrypt_user(user)


def get_all_users():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return [_decrypt_user(u) for u in users]


def update_user_prpt(user_id: int, prpt_username: str, prpt_password: str, prpt_rep_id: str):
    conn = get_db()
    conn.execute(
        "UPDATE users SET prpt_username=?, prpt_password=?, prpt_rep_id=? WHERE id=?",
        (prpt_username, encrypt(prpt_password), prpt_rep_id, user_id),
    )
    conn.commit()
    conn.close()


def update_user_ghl(user_id: int, ghl_api_token: str, ghl_location_id: str):
    conn = get_db()
    conn.execute(
        "UPDATE users SET ghl_api_token=?, ghl_location_id=? WHERE id=?",
        (encrypt(ghl_api_token), ghl_location_id, user_id),
    )
    conn.commit()
    conn.close()


def delete_user(user_id: int):
    conn = get_db()
    conn.execute("DELETE FROM job_logs WHERE job_id IN (SELECT id FROM jobs WHERE user_id=?)", (user_id,))
    conn.execute("DELETE FROM jobs WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


# ── Jobs ───────────────────────────────────────────────────────────────────

def create_job(user_id: int) -> int:
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO jobs (user_id, status, started_at) VALUES (?, 'pending', CURRENT_TIMESTAMP)",
        (user_id,),
    )
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    return job_id


def update_job_status(job_id: int, status: str):
    conn = get_db()
    if status in ("completed", "failed"):
        conn.execute(
            "UPDATE jobs SET status=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, job_id),
        )
    else:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    conn.commit()
    conn.close()


def get_job(job_id: int):
    conn = get_db()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return job


def get_active_job_for_user(user_id: int):
    conn = get_db()
    job = conn.execute(
        "SELECT * FROM jobs WHERE user_id=? AND status NOT IN ('completed','failed') ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return job


def get_last_job_for_user(user_id: int):
    conn = get_db()
    job = conn.execute(
        "SELECT * FROM jobs WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return job


def get_recent_jobs_for_user(user_id: int, limit: int = 5):
    conn = get_db()
    jobs = conn.execute(
        "SELECT * FROM jobs WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return jobs


def append_job_log(job_id: int, message: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO job_logs (job_id, message) VALUES (?, ?)",
        (job_id, message),
    )
    conn.commit()
    conn.close()


def get_job_logs(job_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT message, created_at FROM job_logs WHERE job_id=? ORDER BY id ASC",
        (job_id,),
    ).fetchall()
    conn.close()
    return rows


def get_job_logs_since(job_id: int, since_id: int):
    """Return (id, message) rows newer than since_id — used by SSE stream."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, message FROM job_logs WHERE job_id=? AND id>? ORDER BY id ASC",
        (job_id, since_id),
    ).fetchall()
    conn.close()
    return rows
