from __future__ import annotations
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 添加 packages 到路径，确保 novel2media-core 能被导入
ROOT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT_DIR / "packages" / "novel2media-core" / "src"))

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
