from fastapi import APIRouter

from .endpoints import (
    files,
    fs,
    graph,
    inspect,
    interact,
    narration_presets,
    novel_reference,
    novels,
    prompt_evolution,
    prompt_evolution_run,
    render,
    runs,
    voices,
)

api_router = APIRouter()
api_router.include_router(runs.router, tags=["runs"])
api_router.include_router(novel_reference.router, tags=["novel-reference"])
api_router.include_router(novels.router, tags=["novels"])
api_router.include_router(fs.router, tags=["fs"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(graph.router, tags=["graph"])
api_router.include_router(interact.router, tags=["interact"])
api_router.include_router(inspect.router, tags=["inspect"])
api_router.include_router(prompt_evolution.router, tags=["prompt-evolution"])
api_router.include_router(prompt_evolution_run.router, tags=["prompt-evolution-run"])
api_router.include_router(render.router, tags=["render"])
api_router.include_router(voices.router, tags=["voices"])
api_router.include_router(narration_presets.router, tags=["narration-presets"])
