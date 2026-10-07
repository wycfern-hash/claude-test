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
CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL,   -- 英文外貌/穿搭描述，直接放進產圖與影片提示詞
  voice TEXT NOT NULL DEFAULT '',
  ref_image TEXT NOT NULL DEFAULT '',  -- 形象照（相對 DATA_DIR）；有的話產圖時一併當參考，角色更一致
  builtin INTEGER NOT NULL DEFAULT 0
);
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
  video_mode TEXT NOT NULL DEFAULT '',
  character_id INTEGER NOT NULL DEFAULT 0,
  image_source TEXT NOT NULL DEFAULT '',   -- web=上網找圖當參考 | ai=純 AI 生成 | 空白=用設定頁預設
  ref_notes TEXT NOT NULL DEFAULT '',      -- 參考圖來源與授權紀錄
  source_url TEXT NOT NULL DEFAULT '',     -- 匯入時的原始連結（可能是分潤短連結），上架標記商品時用
  cloud_url TEXT NOT NULL DEFAULT '',      -- 影片上傳雲端後的下載連結
  video_path TEXT NOT NULL DEFAULT '',
  video_title TEXT NOT NULL DEFAULT '',
  video_caption TEXT NOT NULL DEFAULT '',
  posts TEXT NOT NULL DEFAULT '{}',        -- 臉書/Threads 情境貼文（posts.py）
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  uploaded_at TEXT
);

CREATE TABLE IF NOT EXISTS social_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  platform TEXT NOT NULL,                 -- threads | facebook
  text TEXT NOT NULL DEFAULT '',
  comment TEXT NOT NULL DEFAULT '',       -- 第一則留言（放分潤連結）
  status TEXT NOT NULL DEFAULT 'posted',
  created_at TEXT NOT NULL,
  posted_at TEXT,
  UNIQUE(product_id, platform)            -- 一個商品在一個平台只發一次
);
CREATE TABLE IF NOT EXISTS social_leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,               -- 別人的貼文網址（同一篇只出現一次）
  author TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  draft TEXT NOT NULL DEFAULT '',         -- 回覆草稿（你可以改）
  status TEXT NOT NULL DEFAULT 'new',     -- new | sent | skipped | failed
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT
);
"""

# 內建主角：全是虛構的 AI 角色（不是真人）。description 為英文，直接進提示詞。
BUILTIN_CHARACTERS = [
    ("hands", "只露手（不露臉）",
     "Only the presenter's hands and forearms are visible, no face, clean natural nails, simple casual sleeves; the hands unbox and demonstrate the product",
     "zh-TW-HsiaoChenNeural"),
    ("girl20", "清新女生（20 多歲）",
     "a friendly Taiwanese woman in her mid-20s, shoulder-length black hair, natural makeup, warm smile, casual white knit top",
     "zh-TW-HsiaoChenNeural"),
    ("office_f", "上班族女生（30 歲）",
     "a polished Taiwanese office-worker woman around 30, hair in a low ponytail, light blazer over a plain blouse, calm and confident",
     "zh-TW-HsiaoChenNeural"),
    ("mom", "親切媽媽（30~40 歲）",
     "a warm Taiwanese mother in her late 30s, short bob haircut, soft cardigan, relaxed home setting, approachable expression",
     "zh-TW-HsiaoYuNeural"),
    ("student", "活潑大學生",
     "a cheerful Taiwanese college student around 21, ponytail, casual hoodie, bright energetic expression",
     "zh-TW-HsiaoYuNeural"),
    ("guy20", "陽光男生（20 多歲）",
     "a friendly Taiwanese man in his mid-20s, short neat black hair, light-colored casual shirt, easygoing smile",
     "zh-TW-YunJheNeural"),
]
_MIGRATE_COLS = {"products": {"video_mode": "TEXT NOT NULL DEFAULT ''", "character_id": "INTEGER NOT NULL DEFAULT 0",
                              "image_source": "TEXT NOT NULL DEFAULT ''", "ref_notes": "TEXT NOT NULL DEFAULT ''",
                              "source_url": "TEXT NOT NULL DEFAULT ''", "cloud_url": "TEXT NOT NULL DEFAULT ''",
                              "posts": "TEXT NOT NULL DEFAULT '{}'"}}

_KEY_RES = (
    re.compile(r"-i\.(\d+)\.(\d+)"),
    re.compile(r"/product/(\d+)/(\d+)"),
    re.compile(r"i\.(\d+)\.(\d+)"),
)


AFF_HOSTS = ("s.shopee.tw", "shp.ee", "shope.ee", "vn.shp.ee", "s.shopee.com")


def is_affiliate(url: str) -> bool:
    """分潤短連結（由分潤後台產生）才算分潤連結；一般商品網址不算。"""
    return bool(url) and any(h in url for h in AFF_HOSTS)


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
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, cols in _MIGRATE_COLS.items():  # 舊資料庫補欄位
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, ddl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    have_keys = {r["key"] for r in conn.execute("SELECT key FROM characters")}
    for key, name, desc, voice in BUILTIN_CHARACTERS:
        if key not in have_keys:  # 只在缺的時候才寫入，避免每次連線都搶寫入鎖（其他連線有未提交的寫入時會卡住）
            conn.execute("INSERT OR IGNORE INTO characters (key,name,description,voice,builtin) VALUES (?,?,?,?,1)",
                         (key, name, desc, voice))
    conn.commit()  # 建表/補欄位/內建資料立刻提交，不要讓這條連線一開始就佔著寫入鎖
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_product(conn, url: str, title="", price="", description="", ref_images=None, source_url: str = "") -> int | None:
    """新增商品；已存在（同商品）回傳 None，這就是『一商品一影片』的防線。"""
    key = shopee_key(url)
    if not key:
        raise ValueError(f"無法從網址解析商品 ID（短連結請先在瀏覽器展開）: {url}")
    t = now()
    cur = conn.execute(
        "INSERT OR IGNORE INTO products (shopee_key,url,title,price,description,ref_images,character_id,source_url,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (key, url, title, price, description, json.dumps(ref_images or []), config.DEFAULT_CHARACTER_ID,
         source_url if source_url != url else "", t, t),
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
    """今天用 AI 生成的影片數（以 updated_at 近似），用來擋 API 花費/Flow 點數；圖片合成不計。"""
    day = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM products WHERE video_mode='ai' AND video_path!='' AND status IN ('video_review','video_approved','uploaded') AND updated_at LIKE ?",
        (day + "%",),
    ).fetchone()[0]


def images_today(conn) -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM products WHERE images!='[]' AND updated_at LIKE ?", (day + "%",)).fetchone()[0]


def list_characters(conn):
    return conn.execute("SELECT * FROM characters ORDER BY builtin DESC, id").fetchall()


def get_character(conn, cid: int):
    return conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone() if cid else None


def all_products(conn, status: str = ""):
    if status:
        return conn.execute("SELECT * FROM products WHERE status=? ORDER BY id", (status,)).fetchall()
    return conn.execute("SELECT * FROM products ORDER BY id").fetchall()
