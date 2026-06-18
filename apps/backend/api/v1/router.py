from fastapi import APIRouter
from .endpoints import runs, novels, files, graph, interact

api_router = APIRouter()
api_router.include_router(runs.router, tags=["runs"])
api_router.include_router(novels.router, tags=["novels"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(graph.router, tags=["graph"])
api_router.include_router(interact.router, tags=["interact"])
