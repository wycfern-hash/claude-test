"""python -m shopee_clips            啟動整個程式（背景自動流程 + 審核網頁）
python -m shopee_clips import-excel 選品.xlsx
python -m shopee_clips check-key     測 Gemini key
python -m shopee_clips probe         擷取自動化 Chrome 目前所有分頁的畫面結構到 data/debug
"""
import argparse
import socket

from . import config


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    ap = argparse.ArgumentParser(prog="shopee_clips")
    sub = ap.add_subparsers(dest="cmd")
    x = sub.add_parser("import-excel")
    x.add_argument("path")
    sub.add_parser("check-key")
    sub.add_parser("probe")
    args = ap.parse_args()

    if args.cmd == "import-excel":
        from . import db, sourcing

        with db.connect() as conn:
            r = sourcing.import_excel(conn, args.path)
        print(f"新增 {r['added']}、重複 {r['dup']}、失敗 {len(r['failed'])}")
        for where, why in r["failed"]:
            print(f"  ✗ {where}：{why}")
    elif args.cmd == "check-key":
        from . import gemini_client

        print("\n".join(gemini_client.check()))
    elif args.cmd == "probe":
        from . import browser, webauto

        with browser.open_context() as ctx:
            print("\n".join(webauto.probe_tabs(ctx)))
    else:
        import uvicorn

        print(f"電腦開 http://localhost:{config.PORT}   手機（同一個 Wi-Fi）開 http://{lan_ip()}:{config.PORT}")
        uvicorn.run("shopee_clips.web:app", host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
