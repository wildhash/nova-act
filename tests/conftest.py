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
"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make services/backend importable
_BACKEND = Path(__file__).parents[1] / "services" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture()
def voice_manager():
    """Return a fresh VoiceSessionManager for each test."""
    from app.voice.manager import VoiceSessionManager
    return VoiceSessionManager()


@pytest.fixture()
def fastapi_client():
    """Return a TestClient for the Nova Clerk FastAPI app."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)
