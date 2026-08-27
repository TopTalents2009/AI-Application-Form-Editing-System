"""uvicorn 启动入口"""
import uvicorn
from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3777, log_level="info", use_colors=False)
