"""SQLite 狀態機。一個商品永遠只有一列、一支影片（shopee_key UNIQUE）。"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

# 狀態流程
#   sourced -> image_review -> image_approved -> video_review -> video_approved -> uploaded
#   圖被退回 -> sourced（重產）；影片被退回 -> image_approved（重產）
STATES = (
    "sourced", "image_review", "image_approved",
    "video_review", "video_approved", "uploaded", "failed", "skipped",
)
TRANSITIONS = {
    "sourced": {"image_review", "image_approved", "skipped", "failed"},
    "image_review": {"image_approved", "sourced", "skipped"},
    "image_approved": {"video_review", "failed", "skipped"},
    "video_review": {"video_approved", "image_approved", "skipped"},
    "video_approved": {"uploaded", "failed", "video_review"},
    "failed": {"sourced", "image_approved", "video_approved"},
    "uploaded": set(),
    "skipped": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shopee_key TEXT NOT NULL UNIQUE,
  url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  price TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  ref_images TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'sourced',
  images TEXT NOT NULL DEFAULT '[]',
  selected_image TEXT NOT NULL DEFAULT '',
  selected_images TEXT NOT NULL DEFAULT '[]',
  script TEXT NOT NULL DEFAULT '{}',
  video_path TEXT NOT NULL DEFAULT '',
  video_title TEXT NOT NULL DEFAULT '',
  video_caption TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  uploaded_at TEXT
);
"""

_KEY_RES = (
    re.compile(r"-i\.(\d+)\.(\d+)"),
    re.compile(r"/product/(\d+)/(\d+)"),
    re.compile(r"i\.(\d+)\.(\d+)"),
)


def shopee_key(url: str) -> str | None:
    """從商品網址取出 shop_id.item_id；解析不到回傳 None（短連結需先展開）。"""
    for rx in _KEY_RES:
        m = rx.search(url)
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    return None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_product(conn, url: str, title="", price="", description="", ref_images=None) -> int | None:
    """新增商品；已存在（同商品）回傳 None，這就是『一商品一影片』的防線。"""
    key = shopee_key(url)
    if not key:
        raise ValueError(f"無法從網址解析商品 ID（短連結請先在瀏覽器展開）: {url}")
    t = now()
    cur = conn.execute(
        "INSERT OR IGNORE INTO products (shopee_key,url,title,price,description,ref_images,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (key, url, title, price, description, json.dumps(ref_images or []), t, t),
    )
    return cur.lastrowid if cur.rowcount else None


def by_status(conn, status: str):
    return conn.execute("SELECT * FROM products WHERE status=? ORDER BY id", (status,)).fetchall()


def get(conn, pid: int):
    return conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()


def update(conn, pid: int, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE products SET {cols} WHERE id=?", (*fields.values(), pid))


def move(conn, pid: int, new: str, **fields) -> None:
    cur = get(conn, pid)["status"]
    if new not in TRANSITIONS[cur]:
        raise ValueError(f"不允許的狀態轉換 {cur} -> {new}")
    update(conn, pid, status=new, **fields)


def uploaded_today(conn) -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM products WHERE status='uploaded' AND uploaded_at LIKE ?", (day + "%",)
    ).fetchone()[0]


def counts(conn) -> dict:
    rows = conn.execute("SELECT status, COUNT(*) n FROM products GROUP BY status").fetchall()
    return {s: 0 for s in STATES} | {r["status"]: r["n"] for r in rows}


def generated_today(conn) -> int:
    """今天產出影片的數量（以 updated_at 近似），用來擋 Veo 花費。"""
    day = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM products WHERE video_path!='' AND status IN ('video_review','video_approved','uploaded') AND updated_at LIKE ?",
        (day + "%",),
    ).fetchone()[0]
