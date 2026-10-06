"""python -m shopee_clips  → 啟動整個程式（背景自動流程 + 審核網頁）。"""
import socket

import uvicorn

from . import config


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    print(f"電腦開 http://localhost:{config.PORT}   手機（同一個 Wi-Fi）開 http://{lan_ip()}:{config.PORT}")
    uvicorn.run("shopee_clips.web:app", host="0.0.0.0", port=config.PORT)
