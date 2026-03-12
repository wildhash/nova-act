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
"""Voice session lifecycle manager."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .session import SessionMode, SessionState, VoiceSession

logger = logging.getLogger(__name__)


class VoiceSessionManager:
    """Thread-safe in-memory voice session registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}
        self._runs: dict[str, dict[str, Any]] = {}

    # -- Sessions ----------------------------------------------------------

    def create_session(self, mode: str = "mock") -> VoiceSession:
        try:
            session_mode = SessionMode(mode)
        except ValueError:
            session_mode = SessionMode.MOCK
        session = VoiceSession(mode=session_mode)
        self._sessions[session.session_id] = session
        logger.info("voice_session_started session_id=%s mode=%s", session.session_id, mode)
        return session

    def get_session(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)

    def stop_session(self, session_id: str) -> VoiceSession | None:
        session = self._sessions.get(session_id)
        if session:
            session.update_state(SessionState.STOPPED)
            logger.info("voice_session_stopped session_id=%s", session_id)
        return session

    # -- Runs --------------------------------------------------------------

    def create_run(
        self,
        session_id: str,
        transcript: str,
        mode: str = "mock",
    ) -> dict[str, Any]:
        import uuid
        run_id = str(uuid.uuid4())
        run: dict[str, Any] = {
            "run_id": run_id,
            "session_id": session_id,
            "transcript": transcript,
            "mode": mode,
            "state": "started",
            "steps": [],
            "receipts": [],
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._runs[run_id] = run
        session = self._sessions.get(session_id)
        if session:
            session.run_id = run_id
        logger.info("run_created run_id=%s session_id=%s", run_id, session_id)
        return run

    def complete_run(
        self,
        run_id: str,
        result: dict[str, Any],
        receipts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        run = self._runs.get(run_id)
        if run:
            run["state"] = "completed"
            run["result"] = result
            run["receipts"] = receipts or []
            run["updated_at"] = datetime.now(UTC).isoformat()
            logger.info("run_completed run_id=%s", run_id)
        return run

    def fail_run(self, run_id: str, error: str) -> dict[str, Any] | None:
        run = self._runs.get(run_id)
        if run:
            run["state"] = "failed"
            run["error"] = error
            run["updated_at"] = datetime.now(UTC).isoformat()
            logger.info("run_failed run_id=%s error=%s", run_id, error)
        return run

    def list_runs(self) -> list[dict[str, Any]]:
        return sorted(
            self._runs.values(),
            key=lambda r: r.get("created_at", ""),
            reverse=True,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._runs.get(run_id)
