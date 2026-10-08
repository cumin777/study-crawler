"""SQLite 状态库：按 URL 记录已处理条目，增量抓取全靠它。

一个 URL 在某个来源下成功处理过（status=done）就不再重复抓；
失败的（status=error）下一轮会重试。
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path


class StateDB:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS items (
                url_hash   TEXT PRIMARY KEY,
                url        TEXT,
                source     TEXT NOT NULL,
                title      TEXT,
                status     TEXT NOT NULL,
                path       TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        self.conn.commit()

    @staticmethod
    def _key(source: str, url: str) -> str:
        return hashlib.sha1(f"{source}|{url}".encode("utf-8")).hexdigest()

    def seen(self, url: str, source: str) -> bool:
        """只把成功处理过的当作已见，失败的下轮重试。"""
        row = self.conn.execute(
            "SELECT 1 FROM items WHERE url_hash=? AND status='done'",
            (self._key(source, url),),
        ).fetchone()
        return row is not None

    def mark(
        self,
        url: str,
        source: str,
        title: str = "",
        status: str = "done",
        path: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?)",
            (
                self._key(source, url),
                url,
                source,
                title,
                status,
                path,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def recent(self, n: int = 20) -> list[tuple]:
        """最近处理的条目，调试用。"""
        return self.conn.execute(
            "SELECT created_at, source, status, title FROM items "
            "ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
