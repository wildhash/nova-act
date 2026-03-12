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
"""Voice API routes — REST + WebSocket.

REST endpoints:
  POST /api/voice/session/start     → create session, return session_id
  POST /api/voice/session/stop      → stop session cleanly
  GET  /api/voice/session/{id}      → get session status

WebSocket:
  WS /api/voice/stream/{session_id} → bidirectional audio/event stream

WebSocket protocol (client → server):
  Binary frames: raw PCM audio (16-bit signed, 16 kHz, mono)
  JSON text: {"type": "stop"}

WebSocket protocol (server → client):
  {"type": "listening_started"}
  {"type": "transcript_partial", "text": "..."}
  {"type": "transcript_final",   "text": "..."}
  {"type": "thinking"}
  {"type": "assistant_audio_chunk", "data": "<base64 PCM>"}
  {"type": "assistant_text",    "text": "..."}
  {"type": "run_started",       "run_id": "..."}
  {"type": "run_step",          "run_id": "...", "step": N, "text": "..."}
  {"type": "run_completed",     "run_id": "...", "result": {...}}
  {"type": "run_failed",        "run_id": "...", "error": "..."}
  {"type": "session_stopped"}
  {"type": "error",             "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..voice.manager import VoiceSessionManager
from ..voice.mock_voice_service import MockVoiceService
from ..voice.session import SessionMode, SessionState
from ..voice.sonic_service import SonicVoiceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Module-level singletons — shared across request handlers
_session_manager = VoiceSessionManager()


# ---- Pydantic request/response models ------------------------------------

class StartSessionRequest(BaseModel):
    mode: str = "mock"
    region: str = "us-east-1"
    sonic_model_id: str = "amazon.nova-2-sonic-v1:0"
    lite_model_id: str = "amazon.nova-2-lite-v1:0"


class StartSessionResponse(BaseModel):
    session_id: str
    mode: str
    state: str


class StopSessionRequest(BaseModel):
    session_id: str


# ---- REST routes ---------------------------------------------------------

@router.post("/session/start", response_model=StartSessionResponse)
def start_session(req: StartSessionRequest) -> Any:
    """Create a new voice session and return the session_id."""
    session = _session_manager.create_session(mode=req.mode)
    return StartSessionResponse(
        session_id=session.session_id,
        mode=session.mode.value,
        state=session.state.value,
    )


@router.post("/session/stop")
def stop_session(req: StopSessionRequest) -> Any:
    """Cleanly stop a voice session."""
    session = _session_manager.stop_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@router.get("/session/{session_id}")
def get_session(session_id: str) -> Any:
    """Return current session status."""
    session = _session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


# ---- WebSocket -----------------------------------------------------------

@router.websocket("/stream/{session_id}")
async def voice_stream(websocket: WebSocket, session_id: str) -> None:
    """Bidirectional audio stream for a voice session.

    The client sends binary audio frames (PCM 16-bit 16 kHz mono) and
    the server responds with JSON events describing the session progress.
    """
    await websocket.accept()
    logger.info("websocket_connected session_id=%s", session_id)

    session = _session_manager.get_session(session_id)
    if not session:
        await websocket.send_text(
            json.dumps({"type": "error", "message": "Session not found"})
        )
        await websocket.close()
        return

    session.update_state(SessionState.LISTENING)

    # Queue that bridges WebSocket receiver to the voice service coroutine
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=512)

    # Choose voice service based on session mode
    if session.mode == SessionMode.REAL:
        service = SonicVoiceService(
            region="us-east-1",
            model_id="amazon.nova-2-sonic-v1:0",
            lite_model_id="amazon.nova-2-lite-v1:0",
        )
    else:
        service = MockVoiceService()  # type: ignore[assignment]

    # Task: receive audio frames from browser → push into audio_queue
    async def _receive_audio() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    try:
                        audio_queue.put_nowait(msg["bytes"])
                    except asyncio.QueueFull:
                        pass  # Drop frame if queue is full
                elif msg.get("text") is not None:
                    try:
                        ctrl = json.loads(msg["text"])
                        if ctrl.get("type") == "stop":
                            logger.info("stop_signal_received session_id=%s", session_id)
                            break
                    except json.JSONDecodeError:
                        pass
        except asyncio.CancelledError:
            pass  # Graceful shutdown — do not propagate
        finally:
            await audio_queue.put(None)  # Signal end-of-stream

    receive_task = asyncio.create_task(_receive_audio())

    # Stream events from voice service → send to browser
    try:
        async for event_json in service.stream_events(  # type: ignore[call-arg]
            audio_queue=audio_queue,
            session_id=session_id,
        ):
            try:
                await websocket.send_text(event_json)
                # Update session state based on event type
                event = json.loads(event_json)
                etype = event.get("type", "")
                if etype == "thinking":
                    session.update_state(SessionState.THINKING)
                elif etype in {"assistant_text", "assistant_audio_chunk"}:
                    session.update_state(SessionState.SPEAKING)
                elif etype == "run_completed":
                    run_id = event.get("run_id", "")
                    result = event.get("result", {})
                    receipts = event.get("receipts", [])
                    _session_manager.complete_run(
                        run_id=run_id,
                        result=result,
                        receipts=receipts,
                    )
                    transcript = session.transcript_final or ""
                    _session_manager.create_run(
                        session_id=session_id,
                        transcript=transcript,
                        mode=session.mode.value,
                    )
                elif etype == "transcript_final":
                    session.transcript_final = event.get("text", "")
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        logger.info("websocket_disconnected session_id=%s", session_id)
    except Exception as ex:
        logger.error("websocket_error session_id=%s error=%s", session_id, ex)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(ex)}))
        except Exception:
            pass
    finally:
        receive_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(receive_task), timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        session.update_state(SessionState.STOPPED)
        try:
            await websocket.send_text(json.dumps({"type": "session_stopped"}))
        except Exception:
            pass
        logger.info("websocket_closed session_id=%s", session_id)


# ---- Expose manager so runs.py can share it ------------------------------

def get_session_manager() -> VoiceSessionManager:
    return _session_manager
