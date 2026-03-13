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
"""Voice session state model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class SessionState(str, Enum):
    CREATED = "created"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    STOPPED = "stopped"
    ERROR = "error"


class SessionMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


@dataclass
class VoiceSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mode: SessionMode = SessionMode.MOCK
    state: SessionState = SessionState.CREATED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    transcript_final: str = ""
    run_id: str | None = None
    error: str | None = None
    receipts: list[dict[str, Any]] = field(default_factory=list)

    def update_state(self, state: SessionState) -> None:
        self.state = state
        self.updated_at = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode": self.mode.value,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "transcript_final": self.transcript_final,
            "run_id": self.run_id,
            "error": self.error,
        }
