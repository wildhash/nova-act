# Voice Integration Audit — Nova Clerk

**Date:** 2026-01  
**Author:** Engineering  
**Status:** Baseline snapshot — pre-implementation

---

## 1. Current FastAPI App Entrypoint

**Finding: NONE.**  
There is no FastAPI application in the repository. The codebase is a pure Python SDK
(`nova-act`) distributed as a pip package. All execution paths are CLI scripts or
importable library code. A backend must be built from scratch.

---

## 2. Existing API Endpoints

**Finding: NONE.**  
There are no HTTP endpoints, no REST routes, no route decorators (`@app.get`, `@router.post`,
etc.) anywhere in the repository. The only "API surface" is the Python class hierarchy:

| Class / Function | File | Purpose |
|---|---|---|
| `NovaHackathonAssistant` | `src/nova_act/samples/nova_hackathon_assistant.py` | Text-based orchestration |
| `SonicStreamAdapter` | same file | Bedrock bidirectional stream wrapper |
| `NovaAct` | `src/nova_act/client.py` | Main SDK client |

---

## 3. WebSocket Support

**Finding: NONE in the application layer.**  
There is no WebSocket server, no websocket library import in the application code, and no
async event loop management exposed to the outside world.

The Bedrock connection itself is a bidirectional HTTP/2 stream accessed via
`boto3.client("bedrock-runtime").invoke_model_with_bidirectional_stream()`, but this is
encapsulated inside `SonicStreamAdapter` and not exposed over any network interface.

**Gap:** A WebSocket layer must be added to bridge browser mic audio to the Bedrock stream.

---

## 4. Sonic-Related Files

| Path | Contents | Relevance |
|---|---|---|
| `src/nova_act/samples/nova_hackathon_assistant.py` | `SonicStreamAdapter`, `NovaHackathonAssistant` | Core Sonic integration |

`SonicStreamAdapter` wraps the low-level Bedrock bidirectional stream and provides:
- `_send_audio_frame(stream, chunk)` — pushes a PCM frame
- `_end_input(stream)` — signals end of audio input
- `_event_iter(stream)` — async-compatible iterator over Bedrock response events
- `_extract_text(event)` — pulls transcript text from an event
- `_extract_audio_bytes(event)` — pulls TTS audio bytes from an event

**Reuse strategy:** Import `SonicStreamAdapter` directly in `sonic_service.py` via
`sys.path` injection rather than copying the file.

---

## 5. UI

**Finding: NONE.**  
The repository has no frontend assets, no HTML, no JavaScript, no CSS. The only user
interface is the command line. A web frontend must be built from scratch.

---

## 6. Cleanest Implementation Path

Two complementary approaches cover the full requirement:

### Path A — WebSocket Streaming (real-time, preferred)

```
Browser mic → getUserMedia → ScriptProcessor → Int16 PCM
  → WebSocket binary frames → FastAPI WS endpoint
  → asyncio.Queue → SonicVoiceService / MockVoiceService
  → Bedrock bidirectional stream (background thread)
  → JSON events back over WebSocket → UI updates
```

**Pros:** True real-time streaming; natural latency for voice UX; matches Sonic API design.  
**Cons:** Requires WebSocket-capable proxy in production (nginx `proxy_pass upgrade`).

### Path B — POST Upload Fallback (safe, no streaming)

```
Browser mic → MediaRecorder → Blob → POST /api/voice/upload
  → Bedrock transcription → text → Nova Lite orchestration
  → JSON response
```

**Pros:** Works everywhere; no WebSocket proxy needed; simpler error surface.  
**Cons:** No real-time feedback; full round-trip latency; no partial transcripts.

**Decision:** Implement both. WebSocket is the primary path; POST upload is available
as a degraded fallback for environments where WebSocket is blocked.

---

## 7. Minimum Viable Architecture

```
┌─────────────────────────────────┐
│  Browser (services/frontend/)   │
│  index.html + vanilla JS        │
│  ┌─────────────┐                │
│  │ getMicAudio │─── WS binary ──┼──► FastAPI WS /api/voice/stream/{id}
│  └─────────────┘                │         │
│  ┌─────────────┐                │         ▼
│  │  UI Events  │◄── WS JSON  ───┼──── asyncio.Queue
└─────────────────────────────────┘         │
                                            ▼
                               ┌─────────────────────────┐
                               │  MockVoiceService        │
                               │  (no AWS needed)         │
                               └─────────────────────────┘
                                    or
                               ┌─────────────────────────┐
                               │  SonicVoiceService       │
                               │  ┌───────────────────┐  │
                               │  │ SonicStreamAdapter│  │
                               │  │ (background thread)│  │
                               │  └───────────────────┘  │
                               │  ┌───────────────────┐  │
                               │  │  Nova 2 Lite       │  │
                               │  │  (orchestration)   │  │
                               │  └───────────────────┘  │
                               └─────────────────────────┘
                                            │
                               ┌────────────▼──────────────┐
                               │  VoiceSessionManager       │
                               │  (in-memory sessions/runs) │
                               └───────────────────────────┘
```

### Component inventory

| Component | File | Status |
|---|---|---|
| FastAPI app | `services/backend/app/main.py` | ✅ Built |
| Session model | `services/backend/app/voice/session.py` | ✅ Built |
| Session manager | `services/backend/app/voice/manager.py` | ✅ Built |
| Mock voice service | `services/backend/app/voice/mock_voice_service.py` | ✅ Built |
| Sonic voice service | `services/backend/app/voice/sonic_service.py` | ✅ Built |
| Voice API routes | `services/backend/app/api/voice.py` | ✅ Built |
| Runs API routes | `services/backend/app/api/runs.py` | ✅ Built |
| Operator console UI | `services/frontend/index.html` | ✅ Built |
| Unit + integration tests | `tests/` | ✅ Built |

---

## Security Considerations

- CORS is `allow_origins=["*"]` — acceptable for local dev; restrict to specific origins in production.
- No authentication on any endpoint — add API key or OAuth2 before any public deployment.
- Audio queue has `maxsize=512` to prevent unbounded memory growth from a fast sender.
- `SonicVoiceService` uses `daemon=True` reader thread so it does not block process shutdown.
- boto3 credentials are sourced from the standard AWS credential chain — no credentials are hardcoded.
