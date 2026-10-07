"""Android 手機自動上架。蝦皮短影音只有手機版，所以：電腦用 USB 偵錯（uiautomator2）操作你手機上的蝦皮 App。
流程：把影片推進手機相簿 → 依 config/shopee_app_steps.json 一步步操作（選影片、填文案、標記商品、發佈）。
UPLOAD_MODE：phone_dryrun = 做到「按發佈」前停下讓你檢查（第一次務必先跑這個）；phone_auto = 直接發佈（受每日上限限制）。
"""
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config, db
from .package import export_package

REMOTE_DIR = "/sdcard/Movies/ShopeeClips"
PHONE_HELP = ("請用 USB 接上手機、解鎖螢幕、開啟「開發人員選項 → USB 偵錯」並在手機上允許這台電腦；"
              "小米/紅米等機型模擬點擊另需開啟「USB 偵錯（安全設定）」。")


NO_AFF_MSG = ("no_aff: 沒有分潤連結，已略過（不會用一般連結上架）。請用「蝦皮特賣分潤小幫手」轉成分潤連結，"
              "再把 CSV 重新匯入（會自動補上）。")


class PhoneError(RuntimeError):
    pass


def debug_dir() -> Path:
    return config.DATA_DIR / "debug"


def connect():
    try:
        import uiautomator2 as u2

        d = u2.connect(config.PHONE_SERIAL or None)
        _ = d.info  # 真的連一次，沒接手機/沒授權會在這裡丟錯
        return d
    except Exception as e:  # noqa: BLE001
        raise PhoneError(f"連不上手機。{PHONE_HELP}（原始錯誤：{str(e)[:150]}）") from e


def load_steps() -> dict:
    return json.loads(config.PHONE_STEPS_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 診斷（把畫面結構存下來，校正步驟用）
def parse_hierarchy(xml: str) -> list[dict]:
    out = []
    for n in ET.fromstring(xml).iter("node"):
        a = n.attrib
        if not (a.get("text") or a.get("content-desc") or a.get("clickable") == "true"):
            continue
        out.append({"text": a.get("text", ""), "desc": a.get("content-desc", ""), "id": a.get("resource-id", ""),
                    "class": a.get("class", "").rsplit(".", 1)[-1], "clickable": a.get("clickable") == "true",
                    "bounds": a.get("bounds", "")})
    return out[:400]


def probe(d, tag: str) -> str:
    """存截圖 + 目前畫面所有文字/按鈕清單，回傳檔名前綴。"""
    debug_dir().mkdir(parents=True, exist_ok=True)
    base = debug_dir() / f"phone_{time.strftime('%m%d_%H%M%S')}_{re.sub(r'[^0-9A-Za-z_一-鿿]', '_', tag)}"
    try:
        d.screenshot(f"{base}.png")
    except Exception:  # noqa: BLE001
        pass
    try:
        info = {"app": d.app_current(), "elements": parse_hierarchy(d.dump_hierarchy())}
        Path(f"{base}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return str(base)


# ------------------------------------------------------------------ 步驟引擎
def variables(row, video_name: str = "") -> dict:
    return {"title": row["video_title"], "caption": f"{row['video_title']}\n{row['video_caption']}".strip(),
            "product_title": row["title"][:40], "product_link": row["source_url"] or row["url"],
            "product_url": row["url"], "video_name": video_name}


def subst(text: str, vars_: dict) -> str:
    """只替換已知變數，使用者文案裡的其他大括號不受影響。"""
    return re.sub(r"\{(\w+)\}", lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ else m.group(0), text)


def _el(d, sel: dict):
    kw = {}
    for key, arg in (("text", "textMatches"), ("desc", "descriptionMatches"), ("id", "resourceIdMatches")):
        if key in sel:
            kw[arg] = f"(?i).*(?:{sel[key]}).*"
    if "class" in sel:
        kw["className"] = sel["class"]
    if "instance" in sel:
        kw["instance"] = sel["instance"]
    if not kw:
        raise PhoneError("步驟的選擇條件是空的（要有 text / desc / id）")
    return d(**kw)


def run_step(d, step: dict, vars_: dict, meta: dict) -> None:
    timeout = step.get("timeout", meta.get("default_timeout", 15))
    if step.get("launch"):
        d.app_start(step.get("package") or meta.get("package") or config.PHONE_PACKAGE, stop=True)
    if "tap" in step:
        el = _el(d, step["tap"])
        if not el.wait(timeout=timeout):
            raise PhoneError(f"畫面上找不到：{step['tap']}")
        el.click()
    if "tap_ratio" in step:
        w, h = d.window_size()
        d.click(int(w * step["tap_ratio"][0]), int(h * step["tap_ratio"][1]))
    if "type" in step:
        t = step["type"]
        if t.get("into"):
            el = _el(d, t["into"])
            if not el.wait(timeout=timeout):
                raise PhoneError(f"找不到輸入框：{t['into']}")
            el.click()
        d.send_keys(subst(t["text"], vars_), clear=t.get("clear", True))  # u2 的輸入法支援中文
    if "wait" in step and not _el(d, step["wait"]).wait(timeout=timeout):
        raise PhoneError(f"等不到：{step['wait']}")
    if "wait_gone" in step and not _el(d, step["wait_gone"]).wait_gone(timeout=timeout):
        raise PhoneError(f"一直沒消失：{step['wait_gone']}")
    if step.get("back"):
        d.press("back")
    if step.get("swipe"):
        d.swipe_ext(step["swipe"])
    if step.get("sleep"):
        time.sleep(step["sleep"])


def run_steps(d, steps: list[dict], vars_: dict, meta: dict, publish: bool, say=lambda m: None) -> str:
    """回傳 'published' 或 'stopped_before_publish'。任何一步失敗都先存診斷檔再丟錯。"""
    lo, hi = meta.get("gap_seconds", [0.8, 2.0])
    for i, step in enumerate(steps, 1):
        name = step.get("name", f"第{i}步")
        if step.get("publish") and not publish:
            say(f"（dryrun）停在「{name}」之前，請檢查手機畫面")
            return "stopped_before_publish"
        try:
            run_step(d, step, vars_, meta)
        except Exception as e:  # noqa: BLE001
            if step.get("optional"):
                say(f"略過可選步驟「{name}」")
                continue
            base = probe(d, f"step{i}_{name}")
            raise PhoneError(f"卡在第 {i} 步「{name}」（診斷檔 {base}.png/.json）：{str(e)[:150]}") from e
        time.sleep(random.uniform(lo, hi))
    return "published"


def push_video(d, local: Path, name: str) -> str:
    """把影片放進手機相簿（Movies/ShopeeClips），蝦皮選影片時就看得到。"""
    remote = f"{REMOTE_DIR}/{name}"
    d.shell(["mkdir", "-p", REMOTE_DIR])
    d.push(str(local), remote)
    d.shell(["am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote}"])
    time.sleep(2)
    return remote


def run(conn, mode: str, connect_fn=None, say=lambda m: None) -> int:
    rows = [r for r in db.by_status(conn, "video_approved") if r["video_path"]]
    if not config.ALLOW_PLAIN_LINK:  # 沒有分潤連結的商品不上架（標記一般連結不會有分潤）
        for r in [r for r in rows if not db.is_affiliate(r["source_url"])]:
            if not r["error"].startswith("no_aff:"):
                db.update(conn, r["id"], error=NO_AFF_MSG)
                conn.commit()
        rows = [r for r in rows if db.is_affiliate(r["source_url"])]
    if not rows:
        return 0
    publish = mode == "phone_auto"
    cap = config.DAILY_UPLOAD_CAP - db.uploaded_today(conn) if publish else 1
    rows = rows[: max(cap, 0)]
    if not rows:
        return 0
    d = (connect_fn or connect)()
    meta = load_steps()
    n = 0
    for idx, r in enumerate(rows):
        name = f"{r['id']}.mp4"
        try:
            remote = push_video(d, config.DATA_DIR / r["video_path"], name)
            result = run_steps(d, meta["steps"], variables(r, name), meta, publish, say)
        except Exception as e:  # noqa: BLE001
            export_package(r)  # 失敗就留上架包，你可以手動上
            db.move(conn, r["id"], "failed", error=f"upload: {str(e)[:300]}")
            conn.commit()
            break  # App 狀態可能亂了，不要在這之上繼續發下一支
        if result != "published":
            break  # dryrun：留在發佈前的畫面給你看
        if meta.get("delete_after_success"):
            d.shell(["rm", "-f", remote])
        db.move(conn, r["id"], "uploaded", uploaded_at=db.now(), error="")
        conn.commit()
        n += 1
        if idx < len(rows) - 1:
            lo, hi = meta.get("between_videos_seconds", [30, 90])
            time.sleep(random.uniform(lo, hi))  # 兩支之間隔一下，不要連發
    return n
