import sqlite3
import os
import secrets
import hashlib
from datetime import datetime
import config

DB_PATH = config.DB_PATH

# Ensure the directory exists (important when DB_PATH is e.g. /data/messages.db)
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                username    TEXT,
                profile_pic TEXT,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                direction       TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
                text            TEXT,
                media_url       TEXT,
                media_type      TEXT,
                timestamp       TEXT NOT NULL,
                raw             TEXT,
                ai_processed    INTEGER DEFAULT 0,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, timestamp);

            CREATE TABLE IF NOT EXISTS known_users (
                ig_id       TEXT PRIMARY KEY,
                username    TEXT,
                profile_pic TEXT,
                first_seen  TEXT NOT NULL,
                last_seen   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key_hash    TEXT UNIQUE NOT NULL,
                key_prefix  TEXT NOT NULL,
                name        TEXT NOT NULL,
                description TEXT,
                created_at  TEXT NOT NULL,
                last_used   TEXT,
                enabled     INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS message_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id    TEXT NOT NULL,
                recipient_name  TEXT,
                message         TEXT,
                media_url       TEXT,
                media_type      TEXT,
                scheduled_at    TEXT NOT NULL,
                status          TEXT DEFAULT 'pending'
                                     CHECK(status IN ('pending','sent','failed','cancelled')),
                sent_at         TEXT,
                error           TEXT,
                created_at      TEXT NOT NULL,
                api_key_id      INTEGER,
                source          TEXT DEFAULT 'ui'
            );

            CREATE TABLE IF NOT EXISTS wakewords (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                phrase        TEXT NOT NULL,
                match_type    TEXT DEFAULT 'contains'
                                   CHECK(match_type IN ('contains','exact','starts_with','regex')),
                reply_text    TEXT NOT NULL,
                enabled       INTEGER DEFAULT 1,
                created_at    TEXT NOT NULL,
                trigger_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ai_agent (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT DEFAULT 'Support Agent',
                enabled         INTEGER DEFAULT 0,
                wakeword        TEXT,
                gemini_api_key  TEXT,
                gemini_model    TEXT DEFAULT 'gemini-1.5-flash',
                system_prompt   TEXT DEFAULT 'You are a helpful support assistant.',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
        """)


def seed_admin(email: str, password_hash: str):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM app_users WHERE email=?", (email,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO app_users(email, password_hash, created_at) VALUES(?,?,?)",
                (email, password_hash, now),
            )


def seed_ai_agent():
    now = datetime.utcnow().isoformat()
    gemini_key = config.GEMINI_API_KEY
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM ai_agent").fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO ai_agent
                   (name, enabled, gemini_api_key, system_prompt, created_at, updated_at)
                   VALUES(?,?,?,?,?,?)""",
                ("Support Agent", 0, gemini_key,
                 "You are a helpful support assistant. Be friendly and concise.", now, now),
            )
        elif gemini_key:
            # Update existing row only if key is currently blank.
            conn.execute(
                "UPDATE ai_agent SET gemini_api_key=? WHERE gemini_api_key IS NULL OR gemini_api_key=''",
                (gemini_key,),
            )


# ── Conversations & messages ────────────────────────────────────────────────

def upsert_conversation(sender_id: str, username: str = None, profile_pic: str = None):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM conversations WHERE id=?", (sender_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE conversations SET updated_at=?, username=COALESCE(?,username), profile_pic=COALESCE(?,profile_pic) WHERE id=?",
                (now, username, profile_pic, sender_id),
            )
        else:
            conn.execute(
                "INSERT INTO conversations(id, username, profile_pic, updated_at) VALUES(?,?,?,?)",
                (sender_id, username or sender_id, profile_pic, now),
            )


def save_message(msg_id, conversation_id, direction, text=None, media_url=None,
                 media_type=None, timestamp=None, raw=None):
    ts = timestamp or datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO messages
               (id, conversation_id, direction, text, media_url, media_type, timestamp, raw)
               VALUES(?,?,?,?,?,?,?,?)""",
            (msg_id, conversation_id, direction, text, media_url, media_type, ts, raw),
        )
    upsert_conversation(conversation_id)


def get_conversations():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*,
               (SELECT text FROM messages WHERE conversation_id=c.id ORDER BY timestamp DESC LIMIT 1) AS last_message,
               (SELECT timestamp FROM messages WHERE conversation_id=c.id ORDER BY timestamp DESC LIMIT 1) AS last_ts
               FROM conversations c ORDER BY c.updated_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages(conversation_id, limit=100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY timestamp ASC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Known users ─────────────────────────────────────────────────────────────

def upsert_known_user(ig_id, username=None, profile_pic=None):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute("SELECT ig_id FROM known_users WHERE ig_id=?", (ig_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE known_users SET last_seen=?, username=COALESCE(?,username), profile_pic=COALESCE(?,profile_pic) WHERE ig_id=?",
                (now, username, profile_pic, ig_id),
            )
        else:
            conn.execute(
                "INSERT INTO known_users(ig_id, username, profile_pic, first_seen, last_seen) VALUES(?,?,?,?,?)",
                (ig_id, username or ig_id, profile_pic, now, now),
            )


def search_known_users(query):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM known_users WHERE username LIKE ? OR ig_id LIKE ? ORDER BY last_seen DESC LIMIT 20",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    return [dict(r) for r in rows]


def get_known_user_by_username(username):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM known_users WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()
    return dict(row) if row else None


# ── Auth ────────────────────────────────────────────────────────────────────

def get_user_by_email(email):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM app_users WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None


# ── API keys ─────────────────────────────────────────────────────────────────

def generate_api_key(name, description=""):
    raw_key = "igk_" + secrets.token_urlsafe(32)
    key_prefix = raw_key[:12]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_keys(key_hash, key_prefix, name, description, created_at) VALUES(?,?,?,?,?)",
            (key_hash, key_prefix, name, description, now),
        )
    return raw_key


def get_api_keys():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, key_prefix, name, description, created_at, last_used, enabled FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def validate_api_key(raw_key):
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash=? AND enabled=1", (key_hash,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE api_keys SET last_used=? WHERE key_hash=?",
                (datetime.utcnow().isoformat(), key_hash),
            )
    return dict(row) if row else None


def revoke_api_key(key_id):
    with get_conn() as conn:
        conn.execute("UPDATE api_keys SET enabled=0 WHERE id=?", (key_id,))


# ── Message queue ────────────────────────────────────────────────────────────

def queue_message(recipient_id, message=None, media_url=None, media_type=None,
                  scheduled_at=None, recipient_name=None, source="ui", api_key_id=None):
    now = datetime.utcnow().isoformat()
    scheduled_at = scheduled_at or now
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO message_queue
               (recipient_id, recipient_name, message, media_url, media_type,
                scheduled_at, created_at, source, api_key_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (recipient_id, recipient_name, message, media_url, media_type,
             scheduled_at, now, source, api_key_id),
        )
        return cursor.lastrowid


def get_pending_queue_messages():
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM message_queue WHERE status='pending' AND scheduled_at<=? ORDER BY scheduled_at ASC",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_queue_status(msg_id, status, error=None):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        if status == "sent":
            conn.execute(
                "UPDATE message_queue SET status=?, sent_at=? WHERE id=?", (status, now, msg_id)
            )
        else:
            conn.execute(
                "UPDATE message_queue SET status=?, error=? WHERE id=?", (status, error, msg_id)
            )


def get_queue_messages(limit=100, status=None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM message_queue WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM message_queue ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def cancel_queue_message(msg_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE message_queue SET status='cancelled' WHERE id=? AND status='pending'", (msg_id,)
        )


# ── Wakewords ────────────────────────────────────────────────────────────────

def get_wakewords(enabled_only=False):
    with get_conn() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT * FROM wakewords WHERE enabled=1 ORDER BY id ASC"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM wakewords ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]


def create_wakeword(phrase, match_type, reply_text):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO wakewords(phrase, match_type, reply_text, created_at) VALUES(?,?,?,?)",
            (phrase, match_type, reply_text, now),
        )
        return cursor.lastrowid


def update_wakeword(ww_id, **fields):
    allowed = {"phrase", "match_type", "reply_text", "enabled"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [ww_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE wakewords SET {sets} WHERE id=?", vals)


def delete_wakeword(ww_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM wakewords WHERE id=?", (ww_id,))


def increment_wakeword_trigger(ww_id):
    with get_conn() as conn:
        conn.execute("UPDATE wakewords SET trigger_count=trigger_count+1 WHERE id=?", (ww_id,))


# ── AI Agent ─────────────────────────────────────────────────────────────────

def get_ai_agent():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_agent ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row else None


def update_ai_agent(**kwargs):
    agent = get_ai_agent()
    if not agent:
        return
    allowed = {"name", "enabled", "wakeword", "gemini_api_key", "gemini_model", "system_prompt"}
    kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    if not kwargs:
        return
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [agent["id"]]
    with get_conn() as conn:
        conn.execute(f"UPDATE ai_agent SET {sets} WHERE id=?", vals)


# ── Stats ────────────────────────────────────────────────────────────────────

def get_stats():
    with get_conn() as conn:
        msgs_in = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='inbound'"
        ).fetchone()[0]
        msgs_out = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='outbound'"
        ).fetchone()[0]
        convs = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        queue_pending = conn.execute(
            "SELECT COUNT(*) FROM message_queue WHERE status='pending'"
        ).fetchone()[0]
        ww_triggers = conn.execute(
            "SELECT COALESCE(SUM(trigger_count),0) FROM wakewords"
        ).fetchone()[0]
        api_keys = conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE enabled=1"
        ).fetchone()[0]
        today = datetime.utcnow().date().isoformat()
        msgs_today = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp LIKE ?", (f"{today}%",)
        ).fetchone()[0]
    return {
        "messages_in": msgs_in,
        "messages_out": msgs_out,
        "conversations": convs,
        "queue_pending": queue_pending,
        "wakeword_triggers": ww_triggers,
        "api_keys": api_keys,
        "messages_today": msgs_today,
    }
