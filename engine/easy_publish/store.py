"""Small local SQLite store. No credentials or browser cookies are written here."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .catalog import bilibili_categories
from .article_templates import TEMPLATE_IDS


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid():
    return str(uuid.uuid4())


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(data_dir / "library.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        schema_version = self.db.execute("PRAGMA user_version").fetchone()[0]
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS contents(id TEXT PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL, path TEXT NOT NULL, text TEXT);
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, updated TEXT NOT NULL, configured INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS account_metadata(account_id TEXT PRIMARY KEY, bilibili_categories TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS account_preferences(account_id TEXT PRIMARY KEY, default_selected INTEGER NOT NULL DEFAULT 1 CHECK(default_selected IN (0,1)), wechat_template_id TEXT NOT NULL DEFAULT 'wenyan-default');
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, content_id TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, mode TEXT NOT NULL, created TEXT NOT NULL, snapshot TEXT NOT NULL, selection TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, account_id TEXT NOT NULL, account_label TEXT NOT NULL, platform TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, remote_id TEXT, url TEXT, fence INTEGER NOT NULL DEFAULT 0, started TEXT, finished TEXT);
        CREATE INDEX IF NOT EXISTS tasks_batch ON tasks(batch_id);
        CREATE INDEX IF NOT EXISTS batches_content ON batches(content_id);
        """)
        preference_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(account_preferences)")}
        if "wechat_template_id" not in preference_columns:
            self.db.execute("ALTER TABLE account_preferences ADD COLUMN wechat_template_id TEXT NOT NULL DEFAULT 'clean'")
        if schema_version < 5:
            # Upgrade the former default for future submissions only. Frozen
            # batches keep their original template IDs; other chosen themes stay.
            self.db.execute("UPDATE account_preferences SET wechat_template_id='wenyan-default' WHERE wechat_template_id='clean' AND account_id IN (SELECT id FROM accounts WHERE platform='wechat')")
        self.db.execute("PRAGMA user_version=5")
        self.db.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def one(self, sql, params=()):
        with self.lock:
            row = self.db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        with self.transaction() as db:
            db.execute(sql, params)

    def recover(self):
        with self.transaction() as db:
            db.execute("UPDATE tasks SET status=CASE WHEN fence=1 THEN 'unknown' ELSE 'cancelled' END, message=CASE WHEN fence=1 THEN '上次退出时正在提交，请先到平台核对结果' ELSE '应用已退出，任务未继续发布' END, finished=? WHERE status IN ('queued','running','submitting')", (now(),))
            db.execute("UPDATE accounts SET status='login_required',message='上次登录已中断，请重新连接',updated=? WHERE status='logging_in'", (now(),))
            db.execute("DELETE FROM account_metadata WHERE account_id NOT IN (SELECT id FROM accounts WHERE platform='bilibili' AND status='ready')")

    def content(self, row):
        data = json.loads(row["data"])
        from .article_formatting import format_ready
        data.setdefault("articleTemplateId", "clean")
        data.setdefault("inputFormat", "markdown")
        data.setdefault("sourceText", "")
        data["formatReady"] = format_ready(data)
        data.setdefault("formatModel", None)
        data.pop("formatSourceHash", None)
        return {**data, "id": row["id"], "createdAt": row["created"],
                "updatedAt": row["updated"], "revision": row["revision"],
                "platformPublicationStates": self.publication_states(row["id"])}

    def publication_states(self, content_id):
        # Include all history, even when the state UI shows only the newest 200
        # batches. Failed tasks and remote drafts do not erase earlier receipts.
        rows = self.all("""SELECT tasks.platform,tasks.status FROM tasks JOIN batches
                        ON batches.id=tasks.batch_id WHERE batches.content_id=?
                        AND tasks.status IN ('submitted','published','unknown')
                        ORDER BY tasks.rowid""", (content_id,))
        return {row["platform"]: row["status"] for row in rows}

    def account_categories(self, identifier):
        row = self.one("SELECT bilibili_categories FROM account_metadata WHERE account_id=?", (identifier,))
        try:
            return bilibili_categories(json.loads(row["bilibili_categories"])) if row else []
        except (TypeError, ValueError):
            return []

    def account(self, row):
        result = {"id": row["id"], "platformId": row["platform"], "label": row["label"],
                "status": row["status"], "message": row["message"], "updatedAt": row["updated"],
                "credentialConfigured": bool(row["configured"]),
                "defaultSelected": self.account_default_selected(row["id"])}
        if row["platform"] == "bilibili":
            result["bilibiliCategories"] = self.account_categories(row["id"]) if row["status"] == "ready" else []
        if row["platform"] == "wechat":
            result["wechatTemplateId"] = self.account_wechat_template(row["id"])
        return result

    def account_default_selected(self, identifier):
        row = self.one("SELECT default_selected FROM account_preferences WHERE account_id=?", (identifier,))
        return bool(row["default_selected"]) if row else True

    def account_wechat_template(self, identifier):
        row = self.one("SELECT wechat_template_id FROM account_preferences WHERE account_id=?", (identifier,))
        value = row["wechat_template_id"] if row else "wenyan-default"
        return value if value in TEMPLATE_IDS else "wenyan-default"

    @staticmethod
    def asset(row):
        result = {k: row[k] for k in ("id", "name", "kind", "size", "mime")}
        if row["kind"] == "article":
            result["text"] = row["text"]
        return result

    @staticmethod
    def task(row):
        return {"id": row["id"], "accountId": row["account_id"], "accountLabel": row["account_label"],
                "platformId": row["platform"], "status": row["status"], "message": row["message"],
                "remoteId": row["remote_id"], "url": row["url"],
                "canRetry": row["status"] in {"failed", "cancelled"} and not row["fence"],
                "startedAt": row["started"], "finishedAt": row["finished"]}

    def batch(self, row):
        return {"id": row["id"], "contentId": row["content_id"], "title": row["title"], "kind": row["kind"],
                "mode": row["mode"], "createdAt": row["created"],
                "tasks": [self.task(t) for t in self.all("SELECT * FROM tasks WHERE batch_id=? ORDER BY rowid", (row["id"],))]}

    def close(self):
        self.db.close()
