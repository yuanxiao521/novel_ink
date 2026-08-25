"""测试包。"""
# 让 pytest 能找到 backend 下的 app（从 backend/ 目录运行：APP_ROOT=backend）
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1]  # backend/
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))