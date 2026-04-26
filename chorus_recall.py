#!/usr/bin/env python3
"""chorus_recall — progressive session recall over local AI-agent JSONL transcripts.

MVP: Claude Code only. Schema designed so Codex/Copilot can be added later via the `source` column.

Usage:
    chorus_recall index            # incremental index of new turns
    chorus_recall list [--project PATH] [--limit N] [--json]
    chorus_recall files [--project PATH] [--limit N] [--json]
    chorus_recall search QUERY [--project PATH] [--limit N] [--json]
    chorus_recall show SESSION_ID [--json]
    chorus_recall health [--json]
    chorus_recall rebuild          # drop + reindex everything

Stdlib only. Read-only over source JSONL; writes only to ~/.chorus/recall.db.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

SCHEMA_VERSION = 2
DB_PATH = Path(os.path.expanduser("~/.chorus/recall.db"))
CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
CODEX_SESSIONS = Path(os.path.expanduser("~/.codex/sessions"))
TEXT_CAP = 8 * 1024  # bytes per turn text after curation

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    project     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    text        TEXT NOT NULL,
    truncated   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_turns_project_ts  ON turns(project, ts DESC, id);
CREATE INDEX IF NOT EXISTS idx_turns_source_ts   ON turns(source, ts DESC, id);
CREATE INDEX IF NOT EXISTS idx_turns_session     ON turns(source, session_id, ts, id);

CREATE TABLE IF NOT EXISTS turn_files (
    turn_id    INTEGER NOT NULL,
    source     TEXT NOT NULL,
    project    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    path       TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    PRIMARY KEY (turn_id, path)
);
CREATE INDEX IF NOT EXISTS idx_tf_project_ts ON turn_files(project, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tf_path_ts    ON turn_files(path, ts DESC);

CREATE TABLE IF NOT EXISTS sessions (
    source        TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    project       TEXT NOT NULL,
    started_at    INTEGER NOT NULL,
    last_activity INTEGER NOT NULL,
    turn_count    INTEGER NOT NULL,
    summary       TEXT,
    PRIMARY KEY (source, session_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_proj_recent   ON sessions(project, last_activity DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_source_recent ON sessions(source, last_activity DESC);

CREATE TABLE IF NOT EXISTS indexed_files (
    path         TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    inode        INTEGER,
    size         INTEGER,
    mtime_ns     INTEGER,
    last_offset  INTEGER NOT NULL DEFAULT 0,
    last_indexed INTEGER,
    error        TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    text,
    source     UNINDEXED,
    project    UNINDEXED,
    session_id UNINDEXED,
    ts         UNINDEXED,
    tokenize='unicode61 remove_diacritics 2 tokenchars ''_/'''
);
"""


def db_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Detect schema version drift before applying schema (FTS5 tokenizer changes
    # require a full rebuild because token stream is baked into the index).
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone() is not None
    stored = None
    if has_meta:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        stored = int(row[0]) if row else None
    if stored is not None and stored != SCHEMA_VERSION:
        for tbl in ("turns_fts", "turn_files", "turns", "sessions", "indexed_files", "meta"):
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()

    conn.executescript(SCHEMA)
    if conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
    return conn


# ---------- Curation: JSONL event -> (turn_dict | None) ----------

def _parse_iso_ts(s: str) -> int:
    if not s:
        return 0
    try:
        # Python 3.10 doesn't accept trailing 'Z' on fromisoformat in some cases; normalize.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return 0


def _extract_text_and_files(content) -> tuple[str, list[str]]:
    """Pull plain text and referenced file paths out of a Claude content array (or string)."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []
    parts: list[str] = []
    files: list[str] = []
    for c in content:
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t == "text":
            txt = c.get("text") or ""
            if txt:
                parts.append(txt)
        elif t == "tool_use":
            name = c.get("name") or "tool"
            inp = c.get("input") or {}
            fp = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
            if isinstance(fp, str) and fp:
                files.append(fp)
                parts.append(f"[{name} {fp}]")
            else:
                # Keep a tiny breadcrumb so FTS can still find tool intent.
                cmd = inp.get("command") or inp.get("query") or inp.get("pattern")
                if isinstance(cmd, str) and cmd:
                    parts.append(f"[{name} {cmd[:200]}]")
                else:
                    parts.append(f"[{name}]")
        elif t == "tool_result":
            # Skip large tool outputs — too noisy for curated index.
            pass
    return "\n".join(parts).strip(), files


def curate_claude_event(ev: dict) -> dict | None:
    """Return a normalized turn dict or None to skip."""
    typ = ev.get("type")
    if typ not in ("user", "assistant"):
        return None
    if ev.get("isSidechain"):
        return None
    msg = ev.get("message") or {}
    role = msg.get("role") or typ
    text, files = _extract_text_and_files(msg.get("content"))
    if not text:
        return None
    truncated = 0
    raw = text.encode("utf-8", "replace")
    if len(raw) > TEXT_CAP:
        text = raw[:TEXT_CAP].decode("utf-8", "replace")
        truncated = 1
    return {
        "source": "claude",
        "project": ev.get("cwd") or "",
        "session_id": ev.get("sessionId") or "",
        "role": role,
        "ts": _parse_iso_ts(ev.get("timestamp") or ""),
        "text": text,
        "truncated": truncated,
        "files": files,
    }


# ---------- Indexer ----------

def _file_stat(path: Path) -> tuple[int, int, int]:
    st = path.stat()
    return st.st_ino, st.st_size, st.st_mtime_ns


def _iter_jsonl_from_offset(path: Path, offset: int):
    """Yield (next_offset_after_complete_line, parsed_event_or_None) tuples.

    Only yields complete lines (terminated with \\n). Partial trailing line is left.
    """
    with path.open("rb") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                # Incomplete final line — stop without consuming it.
                break
            offset += len(line)
            try:
                ev = json.loads(line)
            except Exception:
                ev = None
            yield offset, ev


def index_claude(conn: sqlite3.Connection, verbose: bool = False) -> dict:
    if not CLAUDE_PROJECTS.exists():
        return {"files_seen": 0, "files_indexed": 0, "turns_added": 0}

    files_seen = files_indexed = turns_added = 0
    for jsonl in CLAUDE_PROJECTS.rglob("*.jsonl"):
        files_seen += 1
        try:
            inode, size, mtime_ns = _file_stat(jsonl)
        except FileNotFoundError:
            continue
        path_s = str(jsonl)
        row = conn.execute(
            "SELECT inode, size, mtime_ns, last_offset FROM indexed_files WHERE path=?",
            (path_s,),
        ).fetchone()

        start_offset = 0
        if row:
            prev_inode, prev_size, prev_mtime, prev_offset = row
            if prev_inode == inode and size >= prev_size and mtime_ns >= prev_mtime:
                if size == prev_size and mtime_ns == prev_mtime:
                    continue  # nothing to do
                start_offset = prev_offset
            else:
                # Rotated/replaced — wipe and reindex.
                conn.execute("DELETE FROM turns WHERE source='claude' AND id IN (SELECT turn_id FROM turn_files WHERE source='claude' AND project IN (SELECT project FROM indexed_files WHERE path=?))", (path_s,))
                # simpler: just drop turns belonging to sessions in this file by reparsing — but cheaper:
                # We don't track turn->file. Safer: full per-file reset is rare; use rebuild for now.
                start_offset = 0

        new_offset = start_offset
        added = 0
        try:
            with conn:
                for next_off, ev in _iter_jsonl_from_offset(jsonl, start_offset):
                    new_offset = next_off
                    if ev is None:
                        continue
                    turn = curate_claude_event(ev)
                    if not turn:
                        continue
                    cur = conn.execute(
                        "INSERT INTO turns(source, project, session_id, role, ts, text, truncated) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (turn["source"], turn["project"], turn["session_id"], turn["role"],
                         turn["ts"], turn["text"], turn["truncated"]),
                    )
                    turn_id = cur.lastrowid
                    conn.execute(
                        "INSERT INTO turns_fts(rowid, text, source, project, session_id, ts) "
                        "VALUES(?,?,?,?,?,?)",
                        (turn_id, turn["text"], turn["source"], turn["project"],
                         turn["session_id"], turn["ts"]),
                    )
                    for fp in turn["files"]:
                        conn.execute(
                            "INSERT OR IGNORE INTO turn_files(turn_id, source, project, session_id, path, ts) "
                            "VALUES(?,?,?,?,?,?)",
                            (turn_id, turn["source"], turn["project"], turn["session_id"], fp, turn["ts"]),
                        )
                    # Upsert session aggregate.
                    conn.execute(
                        "INSERT INTO sessions(source, session_id, project, started_at, last_activity, turn_count, summary) "
                        "VALUES(?,?,?,?,?,1,?) "
                        "ON CONFLICT(source, session_id) DO UPDATE SET "
                        "  last_activity=MAX(last_activity, excluded.last_activity), "
                        "  started_at=MIN(started_at, excluded.started_at), "
                        "  turn_count=turn_count+1, "
                        "  summary=COALESCE(sessions.summary, excluded.summary)",
                        (turn["source"], turn["session_id"], turn["project"],
                         turn["ts"], turn["ts"],
                         turn["text"][:200] if turn["role"] == "user" else None),
                    )
                    added += 1
                conn.execute(
                    "INSERT INTO indexed_files(path, source, inode, size, mtime_ns, last_offset, last_indexed, error) "
                    "VALUES(?,?,?,?,?,?,?,NULL) "
                    "ON CONFLICT(path) DO UPDATE SET "
                    "  inode=excluded.inode, size=excluded.size, mtime_ns=excluded.mtime_ns, "
                    "  last_offset=excluded.last_offset, last_indexed=excluded.last_indexed, error=NULL",
                    (path_s, "claude", inode, size, mtime_ns, new_offset, int(time.time())),
                )
        except Exception as e:
            conn.execute(
                "INSERT INTO indexed_files(path, source, last_offset, error) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET error=excluded.error",
                (path_s, "claude", new_offset, str(e)[:500]),
            )
            conn.commit()
            if verbose:
                print(f"error indexing {path_s}: {e}", file=sys.stderr)
            continue

        if added:
            files_indexed += 1
            turns_added += added
            if verbose:
                print(f"indexed {added} turns from {jsonl.name}")
    return {"files_seen": files_seen, "files_indexed": files_indexed, "turns_added": turns_added}


# ---------- Codex curation + indexer ----------

_CODEX_WRAPPER_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
    "<INSTRUCTIONS>",
)


def _is_codex_wrapper(text: str) -> bool:
    head = text.lstrip()[:80]
    return any(head.startswith(p) for p in _CODEX_WRAPPER_PREFIXES)


def _extract_codex_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("input_text", "output_text", "text"):
            txt = c.get("text") or ""
            if txt:
                parts.append(txt)
    return "\n".join(parts).strip()


def curate_codex_event(ev: dict, session_ctx: dict) -> dict | None:
    if ev.get("type") != "response_item":
        return None
    payload = ev.get("payload") or {}
    if payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in ("user", "assistant"):
        return None
    text = _extract_codex_text(payload.get("content"))
    if not text:
        return None
    if role == "user" and _is_codex_wrapper(text):
        return None
    truncated = 0
    raw = text.encode("utf-8", "replace")
    if len(raw) > TEXT_CAP:
        text = raw[:TEXT_CAP].decode("utf-8", "replace")
        truncated = 1
    return {
        "source": "codex",
        "project": session_ctx.get("cwd") or "",
        "session_id": session_ctx.get("id") or "",
        "role": role,
        "ts": _parse_iso_ts(ev.get("timestamp") or "") or session_ctx.get("started_at", 0),
        "text": text,
        "truncated": truncated,
        "files": [],
    }


def _read_codex_session_meta(path: Path) -> dict | None:
    """Read line 1 of a Codex rollout file → {id, cwd, started_at}."""
    try:
        with path.open("rb") as f:
            line = f.readline()
        if not line:
            return None
        ev = json.loads(line)
    except Exception:
        return None
    if ev.get("type") != "session_meta":
        return None
    p = ev.get("payload") or {}
    cwd = p.get("cwd") or ""
    if cwd:
        try:
            cwd = str(Path(cwd).expanduser().resolve())
        except Exception:
            pass
    return {
        "id": p.get("id") or "",
        "cwd": cwd,
        "started_at": _parse_iso_ts(p.get("timestamp") or ev.get("timestamp") or ""),
    }


def index_codex(conn: sqlite3.Connection, verbose: bool = False) -> dict:
    if not CODEX_SESSIONS.exists():
        return {"files_seen": 0, "files_indexed": 0, "turns_added": 0}

    files_seen = files_indexed = turns_added = 0
    for jsonl in CODEX_SESSIONS.rglob("rollout-*.jsonl"):
        files_seen += 1
        try:
            inode, size, mtime_ns = _file_stat(jsonl)
        except FileNotFoundError:
            continue
        path_s = str(jsonl)
        row = conn.execute(
            "SELECT inode, size, mtime_ns, last_offset FROM indexed_files WHERE path=?",
            (path_s,),
        ).fetchone()

        start_offset = 0
        if row:
            prev_inode, prev_size, prev_mtime, prev_offset = row
            if prev_inode == inode and size >= prev_size and mtime_ns >= prev_mtime:
                if size == prev_size and mtime_ns == prev_mtime:
                    continue
                start_offset = prev_offset

        ctx = _read_codex_session_meta(jsonl)
        if not ctx or not ctx.get("id"):
            continue

        new_offset = start_offset
        added = 0
        try:
            with conn:
                for next_off, ev in _iter_jsonl_from_offset(jsonl, start_offset):
                    new_offset = next_off
                    if ev is None:
                        continue
                    turn = curate_codex_event(ev, ctx)
                    if not turn:
                        continue
                    cur = conn.execute(
                        "INSERT INTO turns(source, project, session_id, role, ts, text, truncated) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (turn["source"], turn["project"], turn["session_id"], turn["role"],
                         turn["ts"], turn["text"], turn["truncated"]),
                    )
                    turn_id = cur.lastrowid
                    conn.execute(
                        "INSERT INTO turns_fts(rowid, text, source, project, session_id, ts) "
                        "VALUES(?,?,?,?,?,?)",
                        (turn_id, turn["text"], turn["source"], turn["project"],
                         turn["session_id"], turn["ts"]),
                    )
                    conn.execute(
                        "INSERT INTO sessions(source, session_id, project, started_at, last_activity, turn_count, summary) "
                        "VALUES(?,?,?,?,?,1,?) "
                        "ON CONFLICT(source, session_id) DO UPDATE SET "
                        "  last_activity=MAX(last_activity, excluded.last_activity), "
                        "  started_at=MIN(started_at, excluded.started_at), "
                        "  turn_count=turn_count+1, "
                        "  summary=COALESCE(sessions.summary, excluded.summary)",
                        (turn["source"], turn["session_id"], turn["project"],
                         turn["ts"], turn["ts"],
                         turn["text"][:200] if turn["role"] == "user" else None),
                    )
                    added += 1
                conn.execute(
                    "INSERT INTO indexed_files(path, source, inode, size, mtime_ns, last_offset, last_indexed, error) "
                    "VALUES(?,?,?,?,?,?,?,NULL) "
                    "ON CONFLICT(path) DO UPDATE SET "
                    "  inode=excluded.inode, size=excluded.size, mtime_ns=excluded.mtime_ns, "
                    "  last_offset=excluded.last_offset, last_indexed=excluded.last_indexed, error=NULL",
                    (path_s, "codex", inode, size, mtime_ns, new_offset, int(time.time())),
                )
        except Exception as e:
            conn.execute(
                "INSERT INTO indexed_files(path, source, last_offset, error) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET error=excluded.error",
                (path_s, "codex", new_offset, str(e)[:500]),
            )
            conn.commit()
            if verbose:
                print(f"error indexing {path_s}: {e}", file=sys.stderr)
            continue

        if added:
            files_indexed += 1
            turns_added += added
            if verbose:
                print(f"indexed {added} turns from {jsonl.name}")
    return {"files_seen": files_seen, "files_indexed": files_indexed, "turns_added": turns_added}


# ---------- Queries ----------

def _normalize_project(p: str | None) -> str | None:
    if not p:
        return None
    return str(Path(p).expanduser().resolve())


def cmd_list(conn, args):
    where, params = [], []
    if args.project:
        where.append("project=?"); params.append(_normalize_project(args.project))
    if args.source:
        where.append("source=?"); params.append(args.source)
    sql = "SELECT source, session_id, project, started_at, last_activity, turn_count, summary FROM sessions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY last_activity DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    out = [
        {"source": r[0], "session_id": r[1], "project": r[2],
         "started_at": r[3], "last_activity": r[4], "turn_count": r[5],
         "summary": (r[6] or "")[:140]}
        for r in rows
    ]
    _emit(out, args)


def cmd_files(conn, args):
    where, params = [], []
    if args.project:
        where.append("project=?"); params.append(_normalize_project(args.project))
    if args.source:
        where.append("source=?"); params.append(args.source)
    sql = "SELECT path, MAX(ts) AS last_ts, COUNT(*) AS hits FROM turn_files"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY path ORDER BY last_ts DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    out = [{"path": r[0], "last_ts": r[1], "hits": r[2]} for r in rows]
    _emit(out, args)


def _prepare_fts_query(raw: str, joiner: str) -> str:
    """Loom-style: split into ≥2-char unicode words and add prefix `*`.
    joiner=' ' → implicit AND (precise); joiner=' OR ' → broad recall fallback.
    """
    words = [w for w in re.findall(r"\w+", raw, re.UNICODE) if len(w) >= 2]
    if not words:
        return raw
    return joiner.join(f"{w}*" for w in words)


def _execute_search(conn, fts_query: str, project: str | None, source: str | None, limit: int):
    where, params = ["turns_fts MATCH ?"], [fts_query]
    if project:
        where.append("t.project=?"); params.append(project)
    if source:
        where.append("t.source=?"); params.append(source)
    sql = (
        "SELECT t.id, t.source, t.session_id, t.project, t.role, t.ts, "
        "       snippet(turns_fts, 0, '«', '»', '…', 12) AS snip "
        "FROM turns_fts JOIN turns t ON t.id = turns_fts.rowid "
        "WHERE " + " AND ".join(where) + " ORDER BY t.ts DESC LIMIT ?"
    )
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def cmd_search(conn, args):
    project = _normalize_project(args.project) if args.project else None
    source = args.source or None

    if getattr(args, "raw_fts", False):
        modes = [("raw", args.query)]
    else:
        modes = [
            ("and_prefix", _prepare_fts_query(args.query, " ")),
            ("or_prefix", _prepare_fts_query(args.query, " OR ")),
        ]

    rows, mode = [], None
    for mode_name, q in modes:
        try:
            rows = _execute_search(conn, q, project, source, args.limit)
        except sqlite3.OperationalError:
            continue
        if rows:
            mode = mode_name
            break
        mode = mode_name

    out = [
        {"id": r[0], "source": r[1], "session_id": r[2], "project": r[3],
         "role": r[4], "ts": r[5], "snippet": r[6], "mode": mode}
        for r in rows
    ]
    _emit(out, args)


def cmd_show(conn, args):
    rows = conn.execute(
        "SELECT role, ts, text, truncated FROM turns "
        "WHERE source=? AND session_id=? ORDER BY ts ASC, id ASC LIMIT ?",
        (args.source or "claude", args.session_id, args.limit),
    ).fetchall()
    out = [{"role": r[0], "ts": r[1], "text": r[2], "truncated": bool(r[3])} for r in rows]
    _emit(out, args)


def cmd_health(conn, args):
    info = {}
    info["db_path"] = str(DB_PATH)
    info["db_size_bytes"] = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    info["schema_version"] = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    info["turns_total"] = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    info["sessions_total"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    info["files_indexed"] = conn.execute("SELECT COUNT(*) FROM indexed_files WHERE error IS NULL").fetchone()[0]
    info["files_with_errors"] = conn.execute("SELECT COUNT(*) FROM indexed_files WHERE error IS NOT NULL").fetchone()[0]
    info["last_activity"] = conn.execute("SELECT MAX(last_activity) FROM sessions").fetchone()[0]
    by_source = conn.execute("SELECT source, COUNT(*) FROM turns GROUP BY source").fetchall()
    info["turns_by_source"] = {r[0]: r[1] for r in by_source}
    # Tier-1 latency probe
    t0 = time.perf_counter()
    conn.execute("SELECT path FROM turn_files ORDER BY ts DESC LIMIT 10").fetchall()
    info["tier1_latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    _emit(info, args)


def cmd_rebuild(conn, args):
    conn.executescript(
        "DELETE FROM turns_fts; DELETE FROM turn_files; DELETE FROM turns; "
        "DELETE FROM sessions; DELETE FROM indexed_files;"
    )
    conn.commit()
    claude = index_claude(conn, verbose=args.verbose)
    codex = index_codex(conn, verbose=args.verbose)
    _emit({"rebuilt": True, "claude": claude, "codex": codex}, args)


def cmd_index(conn, args):
    claude = index_claude(conn, verbose=args.verbose)
    codex = index_codex(conn, verbose=args.verbose)
    _emit({"claude": claude, "codex": codex}, args)


# ---------- Output ----------

def _emit(data, args):
    if getattr(args, "json", False):
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
        sys.stdout.write("\n")
        return
    if isinstance(data, dict):
        for k, v in data.items():
            print(f"{k}: {v}")
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                print(" | ".join(f"{k}={v}" for k, v in item.items() if v not in (None, "")))
            else:
                print(item)


# ---------- CLI ----------

def main(argv=None):
    p = argparse.ArgumentParser(prog="chorus_recall")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--pretty", action="store_true", help="indent JSON")
    p.add_argument("--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("index"); sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("list")
    sp.add_argument("--project"); sp.add_argument("--source")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("files")
    sp.add_argument("--project"); sp.add_argument("--source")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_files)

    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--project"); sp.add_argument("--source")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--raw-fts", action="store_true", help="pass query directly to FTS5 (advanced)")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show")
    sp.add_argument("session_id")
    sp.add_argument("--source", default="claude")
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("health"); sp.set_defaults(func=cmd_health)
    sp = sub.add_parser("rebuild"); sp.set_defaults(func=cmd_rebuild)

    args = p.parse_args(argv)
    conn = db_connect()
    try:
        args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
