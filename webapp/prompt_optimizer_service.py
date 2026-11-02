"""Temporary standalone optimizer while the main workbench has active jobs.

The main app contains the same router after its next normal restart.
Does not import app, runner, scheduler or start background pipeline tasks.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prompt_optimizer import router
from reverse_voice import router as voice_router

app = FastAPI(title="Prompt Optimizer local companion")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8789", "http://localhost:8789"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.include_router(router)
app.include_router(voice_router)

if __name__ == "__main__":
    import sys
    from pathlib import Path
    import uvicorn
    log = Path(__file__).resolve().parent / "logs" / "prompt_optimizer_service.log"
    log.parent.mkdir(exist_ok=True)
    stream = log.open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    uvicorn.run(app, host="127.0.0.1", port=8791, log_level="warning")
