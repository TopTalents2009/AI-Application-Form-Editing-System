"""uvicorn 启动入口"""
import os
import socket
import sys
import uvicorn
from app.main import app

def _port() -> int:
    return int(os.environ.get("SHENBAOSHU_PORT") or "3777")

def _probe(port: int) -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return 0
    except OSError:
        return 1
    finally:
        s.close()

if __name__ == "__main__":
    port = _port()
    if "--probe" in sys.argv:
        raise SystemExit(_probe(port))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", use_colors=False)
