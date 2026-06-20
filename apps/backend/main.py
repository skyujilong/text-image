from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 添加 packages 到路径，确保 novel2media-core / novel2media-logging 能被导入
ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "packages" / "novel2media-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "novel2media-logging" / "src"))

# 尽早接管日志：在 import 任何 backend/core 模块之前配置好 root logger + 接管
# uvicorn logger，使 uvicorn 启动期 error 日志也落 data/logs/backend.log。
# setup_logging 幂等，后续 graph import 时再调一次也安全。
from novel2media_logging import setup_logging

setup_logging()

import services.graph_runner as runner
from api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runner.init_runner()
    yield
    await runner.shutdown_runner()


app = FastAPI(title="novel2media API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
