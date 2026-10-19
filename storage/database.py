"""存储层（阶段 2）：SQLite 单文件库 + 6 张表 + 去重。

MVP 用 SQLite（零部署）；生产换 PostgreSQL 只需改这里。
设计要点：
- articles 表按 URL 哈希去重，避免重复抓取污染统计（去重键由 processors.deduplicator 提供）；
- sales_metrics / financial_reports 存抽取后的结构化指标；
- charts / reports 存产出物索引；
- filter_log 留痕被过滤样本，供验收复核（需求书硬要求）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from processors.deduplicator import content_hash as _hash

DB_PATH = Path(__file__).resolve().parent.parent / "output" / "intel.db"


def _connect(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str | Path] = None) -> None:
    conn = _connect(db_path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            url TEXT UNIQUE,
            url_hash TEXT UNIQUE,
            title TEXT,
            publish_time TEXT,
            content TEXT,
            crawl_time TEXT,
            is_sample INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sales_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            event_date TEXT,
            subject TEXT,
            event TEXT,
            value REAL,
            unit TEXT,
            yoy TEXT,
            source_name TEXT,
            crawled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS financial_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            stock_code TEXT,
            company TEXT,
            report_period TEXT,
            metric_name TEXT,
            value REAL,
            unit TEXT,
            crawled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS charts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT,
            title TEXT,
            kind TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            path_md TEXT,
            path_html TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS filter_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            url TEXT,
            title TEXT,
            reason TEXT,
            score REAL,
            logged_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def insert_article(conn: sqlite3.Connection, item: dict) -> bool:
    """插入原始文章，URL 已存在则跳过（去重）。返回是否新插入。"""
    url = item.get("url", "")
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO articles (source, url, url_hash, title, publish_time, content, crawl_time, is_sample)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url_hash) DO NOTHING
            """,
            (
                item.get("source"),
                url,
                _hash(url),
                item.get("title", ""),
                item.get("publish_time"),
                item.get("content", ""),
                item.get("crawl_time"),
                1 if item.get("is_sample") else 0,
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def insert_metric(conn: sqlite3.Connection, m: dict) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sales_metrics
        (source, event_date, subject, event, value, unit, yoy, source_name, crawled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            m.get("source"),
            m.get("event_date"),
            m.get("subject"),
            m.get("event"),
            m.get("value"),
            m.get("unit"),
            m.get("yoy"),
            m.get("source_name") or m.get("source"),  # 抽取器字段名为 source，兜底取它
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def insert_chart(conn: sqlite3.Connection, path: str, title: str, kind: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO charts (path, title, kind, created_at) VALUES (?, ?, ?, ?)",
        (path, title, kind, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def insert_report(conn: sqlite3.Connection, date: str, path_md: str, path_html: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO reports (date, path_md, path_html, created_at) VALUES (?, ?, ?, ?)",
        (date, path_md, path_html, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def insert_filter_log(conn: sqlite3.Connection, rec: dict) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO filter_log (source, url, title, reason, score, logged_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            rec.get("source"),
            rec.get("url"),
            rec.get("title"),
            rec.get("reason"),
            rec.get("score"),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def get_all_metrics(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM sales_metrics ORDER BY id")
    return [dict(r) for r in cur.fetchall()]


def get_all_articles(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM articles ORDER BY id")
    return [dict(r) for r in cur.fetchall()]


if __name__ == "__main__":
    init_db()
    print("SQLite 初始化完成：", DB_PATH)
