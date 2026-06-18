from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# 加载 .env.local（如果存在）；override=False 表示不覆盖已有环境变量
load_dotenv(Path(__file__).parent.parent / ".env.local", override=False)
