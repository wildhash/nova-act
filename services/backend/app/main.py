# Copyright 2026 Amazon Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Nova Clerk FastAPI application entry point.

Start the server:
    uvicorn services.backend.app.main:app --reload --port 8000
Or from the repo root:
    cd services/backend && uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.runs import router as runs_router
from .api.voice import router as voice_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

app = FastAPI(
    title="Nova Clerk",
    description="Voice-driven operator console powered by Amazon Nova.",
    version="1.0.0",
)

# Allow browser requests from any origin (dev convenience; tighten in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(voice_router)
app.include_router(runs_router)

# Serve the frontend SPA from /services/frontend
_FRONTEND_DIR = Path(__file__).parents[3] / "frontend"


@app.get("/", include_in_schema=False)
def serve_frontend() -> FileResponse:
    """Serve the operator console UI."""
    index = _FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return FileResponse(str(Path(__file__).parent / "_fallback.html"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
