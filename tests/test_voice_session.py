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
"""Unit tests for VoiceSession and VoiceSessionManager."""

from __future__ import annotations

import asyncio

import pytest


def test_create_session_mock(voice_manager):
    session = voice_manager.create_session(mode="mock")
    assert session.session_id
    assert session.mode.value == "mock"
    assert session.state.value == "created"


def test_create_session_real(voice_manager):
    session = voice_manager.create_session(mode="real")
    assert session.mode.value == "real"


def test_create_session_invalid_mode_defaults_to_mock(voice_manager):
    session = voice_manager.create_session(mode="bogus")
    assert session.mode.value == "mock"


def test_get_session(voice_manager):
    session = voice_manager.create_session()
    fetched = voice_manager.get_session(session.session_id)
    assert fetched is session


def test_get_session_missing_returns_none(voice_manager):
    assert voice_manager.get_session("nonexistent") is None


def test_stop_session(voice_manager):
    session = voice_manager.create_session()
    stopped = voice_manager.stop_session(session.session_id)
    assert stopped is not None
    assert stopped.state.value == "stopped"


def test_stop_session_missing_returns_none(voice_manager):
    assert voice_manager.stop_session("nonexistent") is None


def test_session_to_dict(voice_manager):
    session = voice_manager.create_session(mode="mock")
    d = session.to_dict()
    assert d["mode"] == "mock"
    assert d["state"] == "created"
    assert "session_id" in d
    assert "created_at" in d


def test_create_run(voice_manager):
    session = voice_manager.create_session()
    run = voice_manager.create_run(
        session_id=session.session_id,
        transcript="Test command",
        mode="mock",
    )
    assert run["run_id"]
    assert run["transcript"] == "Test command"
    assert run["state"] == "started"


def test_complete_run(voice_manager):
    session = voice_manager.create_session()
    run = voice_manager.create_run(session_id=session.session_id, transcript="test")
    result = {"emails_reviewed": 5}
    receipts = [{"receipt_id": "r1"}]
    completed = voice_manager.complete_run(
        run_id=run["run_id"], result=result, receipts=receipts
    )
    assert completed is not None
    assert completed["state"] == "completed"
    assert completed["result"] == result
    assert len(completed["receipts"]) == 1


def test_fail_run(voice_manager):
    session = voice_manager.create_session()
    run = voice_manager.create_run(session_id=session.session_id, transcript="test")
    failed = voice_manager.fail_run(run_id=run["run_id"], error="Bedrock timeout")
    assert failed is not None
    assert failed["state"] == "failed"
    assert "Bedrock timeout" in failed["error"]


def test_list_runs_ordered_newest_first(voice_manager):
    session = voice_manager.create_session()
    r1 = voice_manager.create_run(session_id=session.session_id, transcript="first")
    r2 = voice_manager.create_run(session_id=session.session_id, transcript="second")
    runs = voice_manager.list_runs()
    assert len(runs) == 2
    # Newest first
    assert runs[0]["run_id"] == r2["run_id"]


@pytest.mark.asyncio
async def test_mock_voice_service_emits_expected_events():
    """Mock voice service must emit transcript_final and run_completed."""
    import asyncio
    from app.voice.mock_voice_service import MockVoiceService, MOCK_DEMO_TRANSCRIPT
    import json

    svc = MockVoiceService()
    q: asyncio.Queue[bytes | None] = asyncio.Queue()
    # Feed a dummy audio byte to simulate mic input
    await q.put(b"\x00\x00")

    events = []
    async for evt_json in svc.stream_events(audio_queue=q, session_id="test-session"):
        events.append(json.loads(evt_json))
        if any(e["type"] == "run_completed" for e in events):
            break

    types = [e["type"] for e in events]
    assert "listening_started" in types
    assert "transcript_final" in types
    assert "run_completed" in types
    # Transcript should match the mock transcript
    final_evts = [e for e in events if e["type"] == "transcript_final"]
    assert final_evts[0]["text"] == MOCK_DEMO_TRANSCRIPT
