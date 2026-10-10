"""SQLite 保存合同、规则快照和审计记录；无需安装独立数据库服务。"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


DEFAULT_DB_PATH = Path(__file__).parent / "data" / "reviews.sqlite3"


class ReviewNotFound(LookupError):
    pass


class ResultNotFound(LookupError):
    pass


class StaleStatus(ValueError):
    pass


class NoStatusChange(ValueError):
    pass


def utc_now() -> str:
    # 固定输出微秒，保证时间字符串等宽、可以按字典序比较。
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path or os.environ.get("CONTRACT_DB_PATH") or DEFAULT_DB_PATH)


@contextmanager
def database(db_path: str | Path | None = None):
    connection = sqlite3.connect(resolve_db_path(db_path), timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(db_path: str | Path | None = None) -> None:
    resolve_db_path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with database(db_path) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                results_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT NOT NULL REFERENCES reviews(id),
                rule_id TEXT NOT NULL,
                rule_name TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                old_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_review ON audit(review_id, id);
        """)
        connection.commit()


def _read_review(connection: sqlite3.Connection, review_id: str) -> dict:
    row = connection.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        raise ReviewNotFound(review_id)
    review = dict(row)
    review["results"] = json.loads(review.pop("results_json"))
    review["audit"] = [dict(item) for item in connection.execute(
        "SELECT id, rule_id, rule_name, actor, reason, old_status, new_status, created_at "
        "FROM audit WHERE review_id = ? ORDER BY id", (review_id,)
    ).fetchall()]
    return review


def create_review(filename: str, content: str, results: list[dict], db_path=None) -> dict:
    review_id, now = str(uuid4()), utc_now()
    with database(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO reviews (id, filename, content, created_at, updated_at, results_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (review_id, filename, content, now, now, json.dumps(results, ensure_ascii=False)),
        )
        review = _read_review(connection, review_id)
        connection.commit()
        return review


def get_review(review_id: str, db_path=None) -> dict:
    with database(db_path) as connection:
        # 在同一个读事务内读取结果和审计，避免看到一半更新的快照。
        connection.execute("BEGIN")
        return _read_review(connection, review_id)


def list_reviews(db_path=None) -> list[dict]:
    with database(db_path) as connection:
        rows = connection.execute(
            "SELECT id, filename, created_at, updated_at, results_json FROM reviews "
            # rowid 随插入递增；Windows 等时钟精度较低的系统上也不会因时间相同而乱序。
            "ORDER BY rowid DESC LIMIT 20"
        ).fetchall()
    summaries = []
    for row in rows:
        summary = dict(row)
        results = json.loads(summary.pop("results_json"))
        summary["risk_count"] = sum(result["final_status"] == "risk" for result in results)
        summary["total_count"] = len(results)
        summaries.append(summary)
    return summaries


def update_result(review_id: str, rule_id: str, status: str, actor: str,
                  reason: str, expected_status: str, db_path=None) -> dict:
    with database(db_path) as connection:
        # 先获取写锁：状态修改和审计要一起成功或一起回滚。
        connection.execute("BEGIN IMMEDIATE")
        review = _read_review(connection, review_id)
        result = next((item for item in review["results"] if item["rule_id"] == rule_id), None)
        if result is None:
            raise ResultNotFound(rule_id)
        old_status = result["final_status"]
        if old_status != expected_status:
            raise StaleStatus("结果已被修改，请重新加载后操作")
        if old_status == status:
            raise NoStatusChange("请选择不同于当前结果的状态")
        now = utc_now()
        result["final_status"] = status
        connection.execute(
            "UPDATE reviews SET results_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(review["results"], ensure_ascii=False), now, review_id),
        )
        connection.execute(
            "INSERT INTO audit (review_id, rule_id, rule_name, actor, reason, old_status, new_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (review_id, rule_id, result["name"], actor, reason, old_status, status, now),
        )
        updated = _read_review(connection, review_id)
        connection.commit()
        return updated
