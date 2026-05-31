"""
read_session_sqlite.py
======================
直接讀取 Hermes 的兩個 SQLite 資料庫，不依賴任何 hermes 模組。

資料庫位置（預設）：
  state.db          → $HERMES_HOME/state.db
  response_store.db → $HERMES_HOME/response_store.db

用法範例
--------
    from tmp.read_session_sqlite import StateDB, ResponseStoreDB

    # --- state.db ---
    db = StateDB()                          # 自動找 HERMES_HOME
    # db = StateDB("/path/to/state.db")     # 或指定路徑

    sessions = db.list_sessions(limit=20)
    for s in sessions:
        print(s["id"], s["title"], s["source"])

    msgs = db.get_messages("SESSION_ID")
    for m in msgs:
        print(m["role"], m["content"][:80])

    # --- response_store.db ---
    rs = ResponseStoreDB()
    for r in rs.list_responses():
        print(r["response_id"], r["accessed_at"])

    data = rs.get_response("resp_abc123")
    print(data["response"])                 # OpenAI response object
    print(data["conversation_history"])     # 完整對話歷史
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


# ---------------------------------------------------------------------------
# 路徑解析
# ---------------------------------------------------------------------------

def _hermes_home() -> Path:
    """回傳 HERMES_HOME，與 hermes_constants.get_hermes_home() 邏輯相同。"""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".hermes"


def _default_state_db() -> Path:
    return _hermes_home() / "state.db"


def _default_response_store_db() -> Path:
    return _hermes_home() / "response_store.db"


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _ts(unix: Optional[float]) -> Optional[str]:
    """Unix timestamp → ISO-8601 字串（UTC），None 保持 None。"""
    if unix is None:
        return None
    return datetime.fromtimestamp(unix, tz=timezone.utc).isoformat()


def _json_or_none(text: Optional[str]) -> Any:
    """嘗試 JSON 解析，失敗時回傳原始字串。"""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _row_to_dict(cursor: sqlite3.Cursor, row: sqlite3.Row) -> Dict[str, Any]:
    return dict(zip([col[0] for col in cursor.description], row))


# ---------------------------------------------------------------------------
# StateDB — 讀取 state.db
# ---------------------------------------------------------------------------

class StateDB:
    """
    唯讀介面，對應 hermes_state.SessionDB 寫入的 state.db。

    Schema（SCHEMA_VERSION 14）：
      sessions          — 每個對話 session 的 metadata
      messages          — 每條訊息（role / content / tool_calls …）
      state_meta        — key/value 全域設定
      compression_locks — 壓縮鎖（通常為空）
      messages_fts      — FTS5 全文索引（unicode61）
      messages_fts_trigram — FTS5 trigram 索引（CJK 子字串搜尋）
    """

    def __init__(self, db_path: str | Path | None = None, *, read_only: bool = True):
        path = Path(db_path).resolve() if db_path else _default_state_db()
        if not path.exists():
            raise FileNotFoundError(f"state.db not found: {path}")
        uri = path.as_uri() + ("?mode=ro" if read_only else "")
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._path = path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateDB":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # sessions 表
    # ------------------------------------------------------------------

    def list_sessions(
        self,
        *,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        include_ended: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        列出 sessions，預設按 started_at 降序（最新在前）。

        參數
        ----
        source        : 過濾來源，例如 'cli', 'telegram', 'tui'
        limit         : 最多回傳幾筆
        offset        : 分頁偏移
        include_ended : False 時只回傳尚未結束的 session
        """
        where_clauses = []
        params: list = []

        if source:
            where_clauses.append("source = ?")
            params.append(source)
        if not include_ended:
            where_clauses.append("ended_at IS NULL")

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        sql = f"""
            SELECT * FROM sessions
            {where}
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?
        """
        params += [limit, offset]
        rows = self._conn.execute(sql, params).fetchall()
        return [self._decode_session(dict(r)) for r in rows]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """取得單一 session 的完整 metadata。"""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return self._decode_session(dict(row)) if row else None

    def get_session_chain(self, session_id: str) -> List[Dict[str, Any]]:
        """
        沿 parent_session_id 往上追溯，回傳整條壓縮鏈（含自身），
        由最舊到最新排列。
        """
        chain: List[Dict[str, Any]] = []
        current_id: Optional[str] = session_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            s = self.get_session(current_id)
            if s is None:
                break
            chain.append(s)
            current_id = s.get("parent_session_id")
        chain.reverse()
        return chain

    def _decode_session(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """將 session row 的 JSON 欄位解析，timestamp 轉 ISO 字串。"""
        row["model_config"] = _json_or_none(row.get("model_config"))
        for ts_col in ("started_at", "ended_at"):
            row[ts_col + "_iso"] = _ts(row.get(ts_col))
        return row

    # ------------------------------------------------------------------
    # messages 表
    # ------------------------------------------------------------------

    def get_messages(
        self,
        session_id: str,
        *,
        roles: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        取得某 session 的所有訊息，按 timestamp 升序。

        參數
        ----
        roles  : 過濾角色，例如 ['user', 'assistant']
        limit  : 最多回傳幾筆
        offset : 分頁偏移
        """
        where = "WHERE session_id = ?"
        params: list = [session_id]

        if roles:
            placeholders = ",".join("?" * len(roles))
            where += f" AND role IN ({placeholders})"
            params.extend(roles)

        limit_clause = f"LIMIT {limit} OFFSET {offset}" if limit else ""
        sql = f"SELECT * FROM messages {where} ORDER BY timestamp ASC {limit_clause}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._decode_message(dict(r)) for r in rows]

    def iter_messages(
        self,
        session_id: str,
        *,
        chunk_size: int = 200,
    ) -> Iterator[Dict[str, Any]]:
        """逐筆 yield 訊息（大 session 用，避免一次載入全部）。"""
        offset = 0
        while True:
            batch = self.get_messages(session_id, limit=chunk_size, offset=offset)
            if not batch:
                break
            yield from batch
            if len(batch) < chunk_size:
                break
            offset += chunk_size

    def get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """以 messages.id（INTEGER PK）取得單條訊息。"""
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return self._decode_message(dict(row)) if row else None

    def _decode_message(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """解析 messages row 的 JSON 欄位，timestamp 轉 ISO 字串。"""
        for col in (
            "tool_calls",
            "reasoning_details",
            "codex_reasoning_items",
            "codex_message_items",
        ):
            row[col] = _json_or_none(row.get(col))

        # content 可能是純文字，也可能是 JSON 編碼的 multimodal list
        content = row.get("content")
        if content and content.startswith("["):
            row["content_parsed"] = _json_or_none(content)
        else:
            row["content_parsed"] = content

        row["timestamp_iso"] = _ts(row.get("timestamp"))
        row["observed"] = bool(row.get("observed"))
        return row

    # ------------------------------------------------------------------
    # FTS5 全文搜尋
    # ------------------------------------------------------------------

    def search_messages(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        limit: int = 20,
        use_trigram: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        全文搜尋訊息內容。

        參數
        ----
        query        : FTS5 查詢字串（支援 AND / OR / phrase / prefix*）
        session_id   : 限定在某個 session 內搜尋
        limit        : 最多回傳幾筆
        use_trigram  : True 時使用 trigram 索引（適合 CJK 子字串搜尋）
        """
        fts_table = "messages_fts_trigram" if use_trigram else "messages_fts"
        # 確認 FTS 表存在
        exists = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (fts_table,),
        ).fetchone()
        if not exists:
            # 降級：用 LIKE 搜尋
            return self._search_messages_like(query, session_id=session_id, limit=limit)

        where_extra = "AND m.session_id = ?" if session_id else ""
        params: list = [query]
        if session_id:
            params.append(session_id)
        params.append(limit)

        sql = f"""
            SELECT m.*
            FROM messages m
            JOIN {fts_table} fts ON fts.rowid = m.id
            WHERE {fts_table} MATCH ?
            {where_extra}
            ORDER BY m.timestamp DESC
            LIMIT ?
        """
        rows = self._conn.execute(sql, params).fetchall()
        return [self._decode_message(dict(r)) for r in rows]

    def _search_messages_like(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """FTS 不可用時的 LIKE 降級搜尋。"""
        where = "WHERE content LIKE ?"
        params: list = [f"%{query}%"]
        if session_id:
            where += " AND session_id = ?"
            params.append(session_id)
        params.append(limit)
        sql = f"SELECT * FROM messages {where} ORDER BY timestamp DESC LIMIT ?"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._decode_message(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # state_meta 表
    # ------------------------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        """讀取 state_meta 的 key/value 設定。"""
        row = self._conn.execute(
            "SELECT value FROM state_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def list_meta(self) -> Dict[str, str]:
        """回傳所有 state_meta 項目。"""
        rows = self._conn.execute("SELECT key, value FROM state_meta").fetchall()
        return {r[0]: r[1] for r in rows}

    # ------------------------------------------------------------------
    # 統計 / 摘要
    # ------------------------------------------------------------------

    def session_stats(self, session_id: str) -> Dict[str, Any]:
        """
        回傳 session 的統計摘要：
          - 各 role 的訊息數
          - token 用量（從 sessions 表）
          - 時間範圍
        """
        session = self.get_session(session_id)
        if session is None:
            return {}

        role_counts = {}
        rows = self._conn.execute(
            "SELECT role, COUNT(*) as cnt FROM messages WHERE session_id = ? GROUP BY role",
            (session_id,),
        ).fetchall()
        for r in rows:
            role_counts[r[0]] = r[1]

        return {
            "session_id": session_id,
            "title": session.get("title"),
            "source": session.get("source"),
            "model": session.get("model"),
            "started_at": session.get("started_at_iso"),
            "ended_at": session.get("ended_at_iso"),
            "end_reason": session.get("end_reason"),
            "message_count": session.get("message_count", 0),
            "tool_call_count": session.get("tool_call_count", 0),
            "api_call_count": session.get("api_call_count", 0),
            "input_tokens": session.get("input_tokens", 0),
            "output_tokens": session.get("output_tokens", 0),
            "cache_read_tokens": session.get("cache_read_tokens", 0),
            "cache_write_tokens": session.get("cache_write_tokens", 0),
            "reasoning_tokens": session.get("reasoning_tokens", 0),
            "estimated_cost_usd": session.get("estimated_cost_usd"),
            "actual_cost_usd": session.get("actual_cost_usd"),
            "role_counts": role_counts,
        }

    def schema_version(self) -> Optional[int]:
        """回傳 schema_version 表中的版本號。"""
        try:
            row = self._conn.execute(
                "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        except sqlite3.OperationalError:
            return None


# ---------------------------------------------------------------------------
# ResponseStoreDB — 讀取 response_store.db
# ---------------------------------------------------------------------------

class ResponseStoreDB:
    """
    唯讀介面，對應 gateway/platforms/api_server.ResponseStore 寫入的
    response_store.db。

    Schema：
      responses     — response_id (PK) / data (JSON) / accessed_at (REAL)
      conversations — name (PK) / response_id

    data 欄位的 JSON 結構：
      {
        "response":             <OpenAI Responses API 格式的 response 物件>,
        "conversation_history": <完整內部訊息列表，含 tool calls/results>
      }
    """

    def __init__(self, db_path: str | Path | None = None, *, read_only: bool = True):
        path = Path(db_path).resolve() if db_path else _default_response_store_db()
        if not path.exists():
            raise FileNotFoundError(f"response_store.db not found: {path}")
        uri = path.as_uri() + ("?mode=ro" if read_only else "")
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._path = path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ResponseStoreDB":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # responses 表
    # ------------------------------------------------------------------

    def list_responses(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order: str = "DESC",
    ) -> List[Dict[str, Any]]:
        """
        列出所有 responses，預設按 accessed_at 降序（最近存取在前）。

        回傳的每筆資料包含：
          response_id, accessed_at, accessed_at_iso
          （不含 data 欄位，避免一次載入大量 JSON）
        """
        order = "DESC" if order.upper() != "ASC" else "ASC"
        sql = f"""
            SELECT response_id, accessed_at
            FROM responses
            ORDER BY accessed_at {order}
            LIMIT ? OFFSET ?
        """
        rows = self._conn.execute(sql, (limit, offset)).fetchall()
        return [
            {
                "response_id": r["response_id"],
                "accessed_at": r["accessed_at"],
                "accessed_at_iso": _ts(r["accessed_at"]),
            }
            for r in rows
        ]

    def get_response(self, response_id: str) -> Optional[Dict[str, Any]]:
        """
        取得單一 response 的完整資料（含 conversation_history）。

        回傳結構：
          {
            "response_id": str,
            "accessed_at": float,
            "accessed_at_iso": str,
            "response": dict,               # OpenAI response 物件
            "conversation_history": list,   # 完整對話歷史
          }
        """
        row = self._conn.execute(
            "SELECT * FROM responses WHERE response_id = ?", (response_id,)
        ).fetchone()
        if row is None:
            return None
        data = _json_or_none(row["data"]) or {}
        return {
            "response_id": row["response_id"],
            "accessed_at": row["accessed_at"],
            "accessed_at_iso": _ts(row["accessed_at"]),
            "response": data.get("response") if isinstance(data, dict) else data,
            "conversation_history": (
                data.get("conversation_history") if isinstance(data, dict) else None
            ),
        }

    def iter_responses(self, *, chunk_size: int = 100) -> Iterator[Dict[str, Any]]:
        """逐筆 yield 完整 response（含 data），適合大量匯出。"""
        offset = 0
        while True:
            sql = """
                SELECT * FROM responses
                ORDER BY accessed_at DESC
                LIMIT ? OFFSET ?
            """
            rows = self._conn.execute(sql, (chunk_size, offset)).fetchall()
            if not rows:
                break
            for row in rows:
                data = _json_or_none(row["data"]) or {}
                yield {
                    "response_id": row["response_id"],
                    "accessed_at": row["accessed_at"],
                    "accessed_at_iso": _ts(row["accessed_at"]),
                    "response": data.get("response") if isinstance(data, dict) else data,
                    "conversation_history": (
                        data.get("conversation_history")
                        if isinstance(data, dict)
                        else None
                    ),
                }
            if len(rows) < chunk_size:
                break
            offset += chunk_size

    # ------------------------------------------------------------------
    # conversations 表
    # ------------------------------------------------------------------

    def list_conversations(self) -> List[Dict[str, str]]:
        """列出所有具名對話（name → response_id 的映射）。"""
        rows = self._conn.execute(
            "SELECT name, response_id FROM conversations ORDER BY name"
        ).fetchall()
        return [{"name": r["name"], "response_id": r["response_id"]} for r in rows]

    def get_conversation_response(self, name: str) -> Optional[Dict[str, Any]]:
        """
        以對話名稱取得最新的完整 response。
        等同於 ResponseStore.get_conversation(name) + get(response_id)。
        """
        row = self._conn.execute(
            "SELECT response_id FROM conversations WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return self.get_response(row["response_id"])

    # ------------------------------------------------------------------
    # 統計
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """回傳資料庫的基本統計。"""
        resp_count = self._conn.execute(
            "SELECT COUNT(*) FROM responses"
        ).fetchone()[0]
        conv_count = self._conn.execute(
            "SELECT COUNT(*) FROM conversations"
        ).fetchone()[0]
        oldest = self._conn.execute(
            "SELECT MIN(accessed_at) FROM responses"
        ).fetchone()[0]
        newest = self._conn.execute(
            "SELECT MAX(accessed_at) FROM responses"
        ).fetchone()[0]
        return {
            "response_count": resp_count,
            "conversation_count": conv_count,
            "oldest_accessed_at": _ts(oldest),
            "newest_accessed_at": _ts(newest),
            "db_path": str(self._path),
        }


# ---------------------------------------------------------------------------
# CLI 快速檢視（python -m tmp.read_session_sqlite）
# ---------------------------------------------------------------------------

def _cli_main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="直接讀取 Hermes state.db / response_store.db"
    )
    sub = parser.add_subparsers(dest="cmd")

    # sessions
    p_sess = sub.add_parser("sessions", help="列出 sessions")
    p_sess.add_argument("--db", help="state.db 路徑")
    p_sess.add_argument("--source", help="過濾 source（cli/telegram/tui…）")
    p_sess.add_argument("--limit", type=int, default=20)

    # messages
    p_msg = sub.add_parser("messages", help="列出某 session 的訊息")
    p_msg.add_argument("session_id")
    p_msg.add_argument("--db", help="state.db 路徑")
    p_msg.add_argument("--roles", nargs="+", help="過濾 role")
    p_msg.add_argument("--limit", type=int, default=50)

    # search
    p_srch = sub.add_parser("search", help="全文搜尋訊息")
    p_srch.add_argument("query")
    p_srch.add_argument("--db", help="state.db 路徑")
    p_srch.add_argument("--session", help="限定 session_id")
    p_srch.add_argument("--trigram", action="store_true", help="使用 trigram 索引（CJK）")
    p_srch.add_argument("--limit", type=int, default=10)

    # stats
    p_stat = sub.add_parser("stats", help="session 統計摘要")
    p_stat.add_argument("session_id")
    p_stat.add_argument("--db", help="state.db 路徑")

    # responses
    p_resp = sub.add_parser("responses", help="列出 response_store.db 的 responses")
    p_resp.add_argument("--db", help="response_store.db 路徑")
    p_resp.add_argument("--limit", type=int, default=20)

    # response detail
    p_rdet = sub.add_parser("response", help="取得單一 response 詳情")
    p_rdet.add_argument("response_id")
    p_rdet.add_argument("--db", help="response_store.db 路徑")
    p_rdet.add_argument("--history", action="store_true", help="同時顯示 conversation_history")

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        sys.exit(0)

    def _print_json(obj: Any) -> None:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))

    if args.cmd == "sessions":
        with StateDB(args.db) as db:
            rows = db.list_sessions(source=args.source, limit=args.limit)
            for s in rows:
                ended = s.get("ended_at_iso") or "—"
                title = (s.get("title") or "")[:50]
                print(
                    f"{s['id']}  [{s['source']}]  "
                    f"{s.get('started_at_iso','?')[:19]}  "
                    f"→ {ended[:19]}  "
                    f"msgs={s.get('message_count',0)}  "
                    f"{title}"
                )

    elif args.cmd == "messages":
        with StateDB(args.db) as db:
            msgs = db.get_messages(
                args.session_id, roles=args.roles, limit=args.limit
            )
            for m in msgs:
                ts = (m.get("timestamp_iso") or "")[:19]
                content = str(m.get("content") or "")[:120].replace("\n", " ")
                tool = f"  [{m['tool_name']}]" if m.get("tool_name") else ""
                print(f"{ts}  {m['role']:12s}{tool}  {content}")

    elif args.cmd == "search":
        with StateDB(args.db) as db:
            results = db.search_messages(
                args.query,
                session_id=args.session,
                limit=args.limit,
                use_trigram=args.trigram,
            )
            for m in results:
                ts = (m.get("timestamp_iso") or "")[:19]
                content = str(m.get("content") or "")[:120].replace("\n", " ")
                print(f"{ts}  {m['session_id'][:8]}…  {m['role']:12s}  {content}")

    elif args.cmd == "stats":
        with StateDB(args.db) as db:
            _print_json(db.session_stats(args.session_id))

    elif args.cmd == "responses":
        with ResponseStoreDB(args.db) as db:
            rows = db.list_responses(limit=args.limit)
            for r in rows:
                print(f"{r['response_id']}  {r['accessed_at_iso']}")

    elif args.cmd == "response":
        with ResponseStoreDB(args.db) as db:
            r = db.get_response(args.response_id)
            if r is None:
                print("Not found.")
                sys.exit(1)
            out = {k: v for k, v in r.items() if k != "conversation_history"}
            _print_json(out)
            if args.history:
                print("\n--- conversation_history ---")
                _print_json(r.get("conversation_history"))


if __name__ == "__main__":
    _cli_main()
