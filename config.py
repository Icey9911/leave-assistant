"""配置文件 —— 通过 Streamlit secrets 或环境变量设置 API Key"""

import os

# Claude API 配置
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your-api-key-here")

# 数据文件路径
import pathlib
DATA_DIR = pathlib.Path(__file__).parent / "data"
EMPLOYEES_FILE = DATA_DIR / "employees.json"
LEAVE_RECORDS_FILE = DATA_DIR / "leave_records.json"
