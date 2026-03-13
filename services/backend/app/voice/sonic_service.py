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
"""Real Bedrock / Nova 2 Sonic voice service.

Bridges browser WebSocket audio to Amazon Bedrock's bidirectional streaming API,
and feeds transcripts into Nova 2 Lite for orchestration.

Key references:
- SonicStreamAdapter lives in src/nova_act/samples/nova_hackathon_assistant.py
- Bedrock bidirectional stream: boto3 invoke_model_with_bidirectional_stream()
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import threading
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Expose SonicStreamAdapter from the existing sample without copying it
_SRC_PATH = Path(__file__).parents[5] / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

_SONIC_AVAILABLE = False
_SonicStreamAdapter: Any = None

try:
    from nova_act.samples.nova_hackathon_assistant import (  # type: ignore[import]
        SonicStreamAdapter as _SonicStreamAdapter,
    )
    _SONIC_AVAILABLE = True
except Exception as import_err:  # pragma: no cover
    logger.warning("SonicStreamAdapter import failed: %s", import_err)


class SonicVoiceService:
    """Bridges browser audio to Nova 2 Sonic via Bedrock bidirectional streaming.

    Audio flow:
        browser mic → WebSocket binary frames → audio_queue
        audio_queue → Bedrock bidirectional stream (background thread)
        Bedrock events (transcript, audio TTS) → event_queue
        event_queue → WebSocket JSON events → browser
    """

    def __init__(
        self,
        region: str = "us-east-1",
        model_id: str = "amazon.nova-2-sonic-v1:0",
        lite_model_id: str = "amazon.nova-2-lite-v1:0",
    ) -> None:
        self.region = region
        self.model_id = model_id
        self.lite_model_id = lite_model_id

    async def stream_events(
        self,
        audio_queue: asyncio.Queue[bytes | None],
        session_id: str,
    ) -> AsyncIterator[str]:
        if not _SONIC_AVAILABLE:
            yield json.dumps(
                {
                    "type": "error",
                    "message": (
                        "SonicStreamAdapter is not available. "
                        "Ensure the nova-act SDK is on PYTHONPATH and boto3 supports "
                        "invoke_model_with_bidirectional_stream."
                    ),
                }
            )
            return

        import boto3  # type: ignore[import]

        yield json.dumps({"type": "listening_started"})
        logger.info("sonic_listening_started session_id=%s", session_id)

        bedrock = boto3.client("bedrock-runtime", region_name=self.region)
        try:
            response = bedrock.invoke_model_with_bidirectional_stream(
                modelId=self.model_id,
                contentType="audio/l16;rate=16000;channels=1",
                accept="application/json",
            )
        except Exception as ex:
            logger.error("sonic_stream_open_failed %s", ex)
            yield json.dumps({"type": "error", "message": f"Stream open failed: {ex}"})
            return

        stream_obj = response.get("stream")
        if not stream_obj:
            yield json.dumps({"type": "error", "message": "No stream in Bedrock response"})
            return

        adapter = _SonicStreamAdapter(region=self.region, model_id=self.model_id)
        loop = asyncio.get_running_loop()
        out_q: asyncio.Queue[str | None] = asyncio.Queue()
        transcripts: list[str] = []

        # ---- Background thread: read events from Bedrock ------------------
        def _reader() -> None:
            try:
                for event in adapter._event_iter(stream_obj):
                    text = adapter._extract_text(event)
                    if text:
                        transcripts.append(text)
                        loop.call_soon_threadsafe(
                            out_q.put_nowait,
                            json.dumps({"type": "transcript_partial", "text": text}),
                        )
                    audio = adapter._extract_audio_bytes(event)
                    if audio:
                        loop.call_soon_threadsafe(
                            out_q.put_nowait,
                            json.dumps(
                                {
                                    "type": "assistant_audio_chunk",
                                    "data": base64.b64encode(audio).decode("ascii"),
                                }
                            ),
                        )
            except Exception as ex:
                loop.call_soon_threadsafe(
                    out_q.put_nowait,
                    json.dumps({"type": "error", "message": str(ex)}),
                )
            finally:
                loop.call_soon_threadsafe(out_q.put_nowait, None)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        # ---- Coroutine: feed audio from queue to Bedrock ------------------
        async def _feed_audio() -> None:
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    adapter._end_input(stream_obj)
                    break
                try:
                    adapter._send_audio_frame(stream_obj, chunk)
                except Exception as ex:
                    logger.warning("sonic_feed_audio_error %s", ex)
                    break

        asyncio.create_task(_feed_audio())

        # ---- Yield events from reader thread ------------------------------
        while True:
            event = await out_q.get()
            if event is None:
                break
            yield event

        # Emit final transcript and trigger orchestration
        if transcripts:
            final_text = " ".join(transcripts)
            yield json.dumps({"type": "transcript_final", "text": final_text})
            logger.info("sonic_transcript_final session_id=%s", session_id)

            yield json.dumps({"type": "thinking"})
            run_id = str(uuid.uuid4())
            yield json.dumps({"type": "run_started", "run_id": run_id})

            # Run Nova 2 Lite orchestration (optional — best-effort)
            orchestration_result = await self._run_lite_orchestration(
                user_text=final_text, run_id=run_id, session_id=session_id
            )
            if orchestration_result.get("ok"):
                yield json.dumps(
                    {
                        "type": "assistant_text",
                        "text": orchestration_result.get("reply", ""),
                    }
                )
                yield json.dumps(
                    {
                        "type": "run_completed",
                        "run_id": run_id,
                        "result": orchestration_result,
                    }
                )
            else:
                yield json.dumps(
                    {
                        "type": "run_failed",
                        "run_id": run_id,
                        "error": orchestration_result.get("error", "Unknown error"),
                    }
                )
        logger.info("sonic_session_complete session_id=%s", session_id)

    async def _run_lite_orchestration(
        self,
        user_text: str,
        run_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Run Nova 2 Lite Bedrock converse to get an assistant reply."""
        import boto3  # type: ignore[import]

        try:
            client = boto3.client("bedrock-runtime", region_name=self.region)
            response = await asyncio.to_thread(
                client.converse,
                modelId=self.lite_model_id,
                system=[{"text": "You are a concise enterprise voice assistant. Respond in 1–2 sentences."}],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                inferenceConfig={"maxTokens": 200, "temperature": 0.2},
            )
            reply_content = response["output"]["message"]["content"]
            reply_text = " ".join(
                block.get("text", "") for block in reply_content if isinstance(block, dict)
            ).strip()
            return {
                "ok": True,
                "reply": reply_text,
                "run_id": run_id,
                "session_id": session_id,
                "transcript": user_text,
                "mode": "real",
            }
        except Exception as ex:
            logger.error("lite_orchestration_failed run_id=%s error=%s", run_id, ex)
            return {"ok": False, "error": str(ex)}
