"""Shared memory — SQLite + FTS5 for cross-session knowledge."""

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from chorus.models import Message, Session

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".chorus" / "chorus.db"


class Memory:
    """Persistent memory layer for all conversations."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                timestamp TEXT NOT NULL,
                duration_ms INTEGER DEFAULT 0,
                cross_from TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                provider,
                content='messages',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content, provider)
                VALUES (new.id, new.content, new.provider);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content, provider)
                VALUES ('delete', old.id, old.content, old.provider);
            END;

            CREATE TABLE IF NOT EXISTS session_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                summary TEXT NOT NULL,
                key_topics TEXT NOT NULL DEFAULT '',
                message_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)
        self.conn.commit()

    def _build_fts_query(self, query: str) -> str:
        """Build an OR-based FTS5 query with prefix matching."""
        cleaned = re.sub(r'["\'\*\(\)\{\}\[\]:^~!@#$%&]', ' ', query)
        words = [w.strip() for w in cleaned.split() if w.strip() and len(w.strip()) > 1]
        if not words:
            return query
        return " OR ".join(f'"{w}"*' for w in words)

    # ─── Sessions ───

    def create_session(self, title: str = "") -> Session:
        session = Session(
            id=str(uuid.uuid4())[:8],
            title=title or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            created_at=datetime.now().isoformat(),
        )
        self.conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session.id, session.title, session.created_at, session.created_at),
        )
        self.conn.commit()
        return session

    def list_sessions(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── Messages ───

    def save_message(self, session_id: str, msg: Message):
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, provider, model, timestamp, duration_ms, cross_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, msg.role, msg.content, msg.provider, msg.model, msg.timestamp, msg.duration_ms, msg.cross_from),
        )
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), session_id),
        )
        self.conn.commit()

    def get_message_count(self, session_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["cnt"] if row else 0

    # ─── Search ───

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search across all conversations with OR logic."""
        fts_query = self._build_fts_query(query)
        try:
            rows = self.conn.execute("""
                SELECT m.content, m.provider, m.model, m.timestamp, m.session_id, s.title
                FROM messages_fts fts
                JOIN messages m ON m.id = fts.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            words = [w.strip() for w in query.split() if w.strip()]
            if not words:
                return []
            like_clauses = " OR ".join("m.content LIKE ?" for _ in words)
            like_params = [f"%{w}%" for w in words]
            rows = self.conn.execute(f"""
                SELECT m.content, m.provider, m.model, m.timestamp, m.session_id, s.title
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE {like_clauses}
                ORDER BY m.timestamp DESC
                LIMIT ?
            """, (*like_params, limit)).fetchall()
            return [dict(r) for r in rows]

    # ─── Summaries ───

    def save_session_summary(self, session_id: str, summary: str, key_topics: str, message_count: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO session_summaries (session_id, summary, key_topics, message_count, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, summary, key_topics, message_count, datetime.now().isoformat()),
        )
        self.conn.commit()

    def search_summaries(self, query: str, limit: int = 5) -> list[dict]:
        """Search session summaries by keywords."""
        words = [w.strip() for w in query.split() if w.strip()]
        if not words:
            return []
        clauses = []
        params = []
        for w in words:
            clauses.append("(ss.summary LIKE ? OR ss.key_topics LIKE ?)")
            params.extend([f"%{w}%", f"%{w}%"])
        where = " OR ".join(clauses)
        rows = self.conn.execute(f"""
            SELECT ss.session_id, ss.summary, ss.key_topics, ss.message_count, ss.created_at, s.title
            FROM session_summaries ss
            JOIN sessions s ON s.id = ss.session_id
            WHERE {where}
            ORDER BY ss.created_at DESC
            LIMIT ?
        """, (*params, limit)).fetchall()
        return [dict(r) for r in rows]
