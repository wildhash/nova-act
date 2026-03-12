# Nova Clerk Voice Console — Demo Guide

## Architecture Overview

```
Browser mic ──► WebSocket binary PCM ──► FastAPI
                                            │
                              ┌─────────────┴──────────────┐
                              │ MockVoiceService (no AWS)   │
                              │   OR                        │
                              │ SonicVoiceService           │
                              │  └── SonicStreamAdapter     │
                              │       (Bedrock Nova Sonic)  │
                              │  └── Nova 2 Lite converse   │
                              └─────────────┬──────────────┘
                                            │
                              JSON events ──► WebSocket ──► Browser UI
```

Two operating modes:

| Mode | AWS credentials required? | Audio sent to AWS? | Best for |
|------|--------------------------|-------------------|---------|
| **Mock** | No | No (simulated) | Demos, CI, offline dev |
| **Real** | Yes (Bedrock access) | Yes (Nova 2 Sonic) | Production / full demo |

---

## Prerequisites

### All modes
- Python 3.11+
- Git clone of this repository

### Mock mode only
```bash
pip install fastapi "uvicorn[standard]" websockets pydantic
```

### Real mode (additional)
- AWS account with Amazon Bedrock access
- Model access enabled: `amazon.nova-2-sonic-v1:0` and `amazon.nova-2-lite-v1:0`
- AWS credentials configured (`~/.aws/credentials` or environment variables)

```bash
pip install boto3
export AWS_DEFAULT_REGION=us-east-1
export AWS_ACCESS_KEY_ID=<your key>
export AWS_SECRET_ACCESS_KEY=<your secret>
```

---

## Quick Start — Mock Mode (no AWS required)

### 1. Install dependencies
```bash
cd /path/to/nova-clerk
pip install -r services/backend/requirements.txt
```

### 2. Start the backend
```bash
cd services/backend
uvicorn app.main:app --reload --port 8000
```

You should see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 3. Open the operator console
Navigate to [http://localhost:8000](http://localhost:8000) in your browser.

### 4. Run the mock demo
1. Confirm **Mode: Mock** is selected (top right)
2. Click the 🎤 microphone button
3. The console will automatically simulate the full pipeline:
   - **Listening** state with waveform animation (1.5 s)
   - **Partial transcripts** scroll in progressively
   - **Final transcript** locks in
   - **Thinking** state
   - **Assistant response** displayed and spoken aloud (browser TTS)
   - **Run timeline** shows 4 steps completing
   - **Receipt** card appears with run metadata

No microphone permission is required in mock mode.

---

## Quick Start — Real Mode (AWS credentials required)

### 1. Configure AWS
```bash
export AWS_DEFAULT_REGION=us-east-1
export AWS_ACCESS_KEY_ID=<your key>
export AWS_SECRET_ACCESS_KEY=<your secret>
# or use an AWS profile:
export AWS_PROFILE=my-bedrock-profile
```

### 2. Start the backend
```bash
cd services/backend
uvicorn app.main:app --reload --port 8000
```

### 3. Open the console and switch to Real mode
1. Navigate to [http://localhost:8000](http://localhost:8000)
2. Click **Real** in the Mode selector (top right)
3. Click 🎤 — browser will request microphone permission
4. Speak your command (e.g. "Review my emails and schedule a meeting")
5. The pipeline:
   - Your PCM audio streams to Bedrock Nova 2 Sonic via WebSocket
   - Live transcript appears as Sonic processes your speech
   - Nova 2 Lite generates an orchestration reply
   - TTS audio (if available) plays back in the browser

---

## 60–90 Second Demo Script

**Presenter setup:** backend running, browser open at localhost:8000, Mode = Mock.

> "Nova Clerk is a voice-driven operator console powered by Amazon Nova.
> I can speak a multi-step task — no typing required.
> Watch what happens when I click the microphone."

*[Click 🎤]*

> "The console is now listening. In mock mode, it simulates the complete
> pipeline so you can see all states without any AWS credentials."

*[Waveform animates, partial transcript scrolls in]*

> "You can see the transcript building in real time — this is how Nova 2 Sonic's
> bidirectional stream delivers low-latency speech recognition."

*[Transcript locks: "Review my unread emails…"]*

> "The final transcript is locked. Now watch the assistant think…"

*[Thinking → Speaking states, browser reads the response aloud]*

> "The assistant confirmed the task and is executing it.
> The run timeline shows each agent step completing."

*[Steps 1–4 animate green]*

> "When complete, a receipt is stamped with the run ID, session ID, and transcript.
> Every action is auditable."

*[Receipt card appears]*

> "In real mode, switch to AWS credentials and the exact same UI drives
> Nova 2 Sonic for speech recognition and Nova 2 Lite for orchestration.
> The browser sends raw PCM over WebSocket — zero latency, full duplex."

---

## Local Development

### Run tests
```bash
pip install pytest pytest-asyncio httpx fastapi
cd /path/to/nova-clerk
python -m pytest tests/ -v
```

### API docs (Swagger UI)
[http://localhost:8000/docs](http://localhost:8000/docs)

### WebSocket protocol reference
See the docstring at the top of `services/backend/app/api/voice.py`.

### Project layout
```
nova-clerk/
├── services/
│   ├── backend/
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── main.py              ← FastAPI entry point
│   │       ├── api/
│   │       │   ├── voice.py         ← REST + WebSocket routes
│   │       │   └── runs.py          ← Run/receipt endpoints
│   │       └── voice/
│   │           ├── session.py       ← VoiceSession dataclass
│   │           ├── manager.py       ← In-memory session registry
│   │           ├── mock_voice_service.py
│   │           └── sonic_service.py ← Real Bedrock integration
│   └── frontend/
│       └── index.html               ← Single-page operator console
├── tests/
│   ├── conftest.py
│   ├── test_voice_session.py
│   └── test_voice_api.py
└── docs/
    ├── VOICE_INTEGRATION_AUDIT.md
    └── VOICE_DEMO.md
```

---

## Troubleshooting

### "Session not found" on WebSocket connect
You opened the WebSocket URL directly without calling `POST /api/voice/session/start` first.
Always create a session via REST before opening the WebSocket.

### No audio capture in real mode
- Check browser mic permissions (address bar → 🔒 icon → allow microphone)
- Ensure HTTPS or localhost (browsers block `getUserMedia` on non-secure origins)

### "SonicStreamAdapter import failed"
The `nova-act` SDK is not on PYTHONPATH. Run from the repo root or install the package:
```bash
pip install -e .
```

### boto3 credentials error in real mode
Verify credentials with:
```bash
aws sts get-caller-identity
aws bedrock list-foundation-models --region us-east-1 | grep nova
```

### Port already in use
```bash
lsof -i :8000 | awk 'NR>1 {print $2}' | xargs kill -9
```

### CORS error from a different origin
Add your frontend origin to the `allow_origins` list in `services/backend/app/main.py`.
