"""影片上傳雲端（S3 相容：AWS S3 / Cloudflare R2 / Backblaze B2 / MinIO）。
核准的影片會自動上傳並產生下載連結；手機不在同一個 Wi-Fi 也能下載（「上架包」頁有連結與 QR）。
bucket 沒有公開就用 7 天有效的預簽名連結；已公開則填 CLOUD_PUBLIC_BASE 用固定網址。
"""
from . import config, db, package


def enabled() -> bool:
    return bool(config.CLOUD_BUCKET and config.CLOUD_ACCESS_KEY and config.CLOUD_SECRET_KEY)


def client():
    import boto3

    return boto3.client("s3", endpoint_url=config.CLOUD_ENDPOINT or None, aws_access_key_id=config.CLOUD_ACCESS_KEY,
                        aws_secret_access_key=config.CLOUD_SECRET_KEY, region_name="auto")


def upload(row, c=None) -> str:
    c = c or client()
    d = package.export_package(row)
    base = f"shopee_clips/{row['id']}"
    c.upload_file(str(d / "video.mp4"), config.CLOUD_BUCKET, f"{base}/video.mp4", ExtraArgs={"ContentType": "video/mp4"})
    c.upload_file(str(d / "文案.txt"), config.CLOUD_BUCKET, f"{base}/caption.txt", ExtraArgs={"ContentType": "text/plain; charset=utf-8"})
    if config.CLOUD_PUBLIC_BASE:
        return f"{config.CLOUD_PUBLIC_BASE.rstrip('/')}/{base}/video.mp4"
    return c.generate_presigned_url("get_object", Params={"Bucket": config.CLOUD_BUCKET, "Key": f"{base}/video.mp4"},
                                    ExpiresIn=7 * 24 * 3600)


def run(conn, c=None) -> int:
    if not enabled():
        return 0
    n = 0
    for r in db.by_status(conn, "video_approved"):
        if r["video_path"] and not r["cloud_url"]:
            try:
                db.update(conn, r["id"], cloud_url=upload(r, c))
            except Exception as e:  # noqa: BLE001
                db.update(conn, r["id"], error=f"cloud: {str(e)[:200]}")
                conn.commit()
                break  # 設定/網路有問題，不要每支都重試
            conn.commit()
            n += 1
    return n


def qr_svg(url: str) -> str:
    import segno

    return segno.make(url, error="m").svg_inline(scale=4, border=1)
