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
"""Mock voice service for demo mode — no real Bedrock credentials required."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

# Pre-baked demo scenario used in mock mode
MOCK_DEMO_TRANSCRIPT = (
    "Review my unread emails, draft replies to the two highest-priority messages, "
    "and propose three meeting times for next week."
)
MOCK_ASSISTANT_RESPONSE = (
    "Got it! I will review your unread emails, draft replies to the two most "
    "important messages, and propose three meeting slots for next week. Starting now."
)
MOCK_STEPS = [
    "Scanning inbox — 12 unread messages found.",
    "Priority 1: Q4 budget request from CFO. Drafting reply.",
    "Priority 2: Product launch blocker from CTO. Drafting reply.",
    "Proposing meeting slots: Mon 10 am, Tue 2 pm, Wed 11 am.",
]


class MockVoiceService:
    """Simulates the full voice-to-voice pipeline without any AWS credentials.

    Sequence:
    1. Consume audio for ~1.5 s (listening).
    2. Emit progressive partial transcripts.
    3. Emit transcript_final.
    4. Emit thinking.
    5. Emit assistant_text (the spoken response).
    6. Create a run with timeline steps.
    7. Emit run_completed with receipts.
    """

    async def stream_events(
        self,
        audio_queue: asyncio.Queue[bytes | None],
        session_id: str,
    ) -> AsyncIterator[str]:
        yield json.dumps({"type": "listening_started"})
        logger.info("mock_listening_started session_id=%s", session_id)

        # Drain audio queue for a short window (simulates listening)
        start = time.monotonic()
        while time.monotonic() - start < 1.5:
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                if chunk is None:
                    return  # Stop signal received
            except asyncio.TimeoutError:
                pass

        # Emit partial transcript in waves
        words = MOCK_DEMO_TRANSCRIPT.split()
        partial_windows = [
            words[: max(4, len(words) // 4)],
            words[: max(8, len(words) // 2)],
            words[: max(12, 3 * len(words) // 4)],
            words,
        ]
        for window in partial_windows:
            await asyncio.sleep(0.3)
            yield json.dumps({"type": "transcript_partial", "text": " ".join(window)})

        yield json.dumps({"type": "transcript_final", "text": MOCK_DEMO_TRANSCRIPT})
        logger.info("mock_transcript_final session_id=%s", session_id)

        yield json.dumps({"type": "thinking"})
        await asyncio.sleep(0.9)

        # Emit assistant spoken response text
        yield json.dumps({"type": "assistant_text", "text": MOCK_ASSISTANT_RESPONSE})
        logger.info("mock_response_sent session_id=%s", session_id)

        # Create a run
        run_id = str(uuid.uuid4())
        yield json.dumps({"type": "run_started", "run_id": run_id})
        logger.info("run_created run_id=%s session_id=%s", run_id, session_id)

        # Emit timeline steps
        for index, step_text in enumerate(MOCK_STEPS, start=1):
            await asyncio.sleep(0.7)
            yield json.dumps(
                {"type": "run_step", "run_id": run_id, "step": index, "text": step_text}
            )

        # Emit completion with receipts
        await asyncio.sleep(0.3)
        result = {
            "emails_reviewed": 12,
            "drafts_created": 2,
            "meeting_slots": ["Mon 10 am", "Tue 2 pm", "Wed 11 am"],
            "transcript": MOCK_DEMO_TRANSCRIPT,
            "mode": "mock",
            "session_id": session_id,
            "run_id": run_id,
        }
        yield json.dumps(
            {
                "type": "run_completed",
                "run_id": run_id,
                "result": result,
                "receipts": [
                    {
                        "receipt_id": str(uuid.uuid4()),
                        "run_id": run_id,
                        "session_id": session_id,
                        "mode": "mock",
                        "transcript_final": MOCK_DEMO_TRANSCRIPT,
                        "created_at": __import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc
                        ).isoformat(),
                    }
                ],
            }
        )
        logger.info("run_completed run_id=%s session_id=%s", run_id, session_id)
