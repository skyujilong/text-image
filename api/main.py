from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import api.graph_runner as runner
from api.routers import runs, interact, novels, files


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

app.include_router(runs.router)
app.include_router(interact.router)
app.include_router(novels.router)
app.include_router(files.router)
