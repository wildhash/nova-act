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
"""Run and receipt endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .voice import get_session_manager

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs() -> list[dict[str, Any]]:
    """Return all runs ordered newest first."""
    return get_session_manager().list_runs()


@router.get("/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Return details for a single run."""
    run = get_session_manager().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{run_id}/receipts")
def get_run_receipts(run_id: str) -> list[dict[str, Any]]:
    """Return receipts attached to a run."""
    run = get_session_manager().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.get("receipts", [])
