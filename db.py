import sqlite3
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "messages.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
                id            TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                direction     TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
                text          TEXT,
                media_url     TEXT,
                media_type    TEXT,
                timestamp     TEXT NOT NULL,
                raw           TEXT,
                ai_processed  INTEGER DEFAULT 0,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            );

            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, timestamp);
        """)


def upsert_conversation(sender_id: str, username: str = None, profile_pic: str = None):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM conversations WHERE id = ?", (sender_id,)
        ).fetchone()
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


def save_message(
    msg_id: str,
    conversation_id: str,
    direction: str,
    text: str = None,
    media_url: str = None,
    media_type: str = None,
    timestamp: str = None,
    raw: str = None,
):
    ts = timestamp or datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO messages
               (id, conversation_id, direction, text, media_url, media_type, timestamp, raw)
               VALUES (?,?,?,?,?,?,?,?)""",
            (msg_id, conversation_id, direction, text, media_url, media_type, ts, raw),
        )
    upsert_conversation(conversation_id)


def get_conversations():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*, 
                      (SELECT text FROM messages WHERE conversation_id=c.id ORDER BY timestamp DESC LIMIT 1) AS last_message,
                      (SELECT timestamp FROM messages WHERE conversation_id=c.id ORDER BY timestamp DESC LIMIT 1) AS last_ts
               FROM conversations c
               ORDER BY c.updated_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages(conversation_id: str, limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY timestamp ASC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
