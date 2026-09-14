"""供 start.cmd 调用：打印 CONFIGURED 或 NOT_CONFIGURED"""
import sys
sys.path.insert(0, r"C:\Users\1\Desktop\work\825\agent修改申报书_py")
from app.config import load_config
print("CONFIGURED" if load_config()["configured"] else "NOT_CONFIGURED")
