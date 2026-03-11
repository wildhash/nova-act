# Copyright 2026 Amazon Inc

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
"""Production-oriented Nova assistant.

What this sample includes:
1) Nova 2 Lite orchestration with native Bedrock tool use.
2) Real Gmail and Google Calendar API tools with AWS Secrets Manager storage.
3) Nova Act browser fallback for visual automation tasks.
4) Nova 2 Sonic bidirectional stream bootstrap.
5) Optional OS control tool for desktop automation, behind explicit safety gates.

Usage:
python -m nova_act.samples.nova_hackathon_assistant --interactive True

Bootstrap requirements:
- AWS credentials configured for Bedrock + Secrets Manager + Nova Act.
- NOVA_ACT_API_KEY set, or IAM auth configured for Nova Act.
- Install optional deps for Google and OS control:
    pip install -r src/nova_act/samples/requirements-nova-hackathon-assistant.txt
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import botocore
import fire  # type: ignore

from nova_act import NovaAct


SYSTEM_PROMPT = (
    "You are an enterprise operations voice assistant. "
    "Prefer API tools first for reliability and auditability. "
    "Use browser_act only when visual proof is explicitly requested or API access is unavailable. "
    "Use os_control only when explicitly asked and when action scope is safe and specific."
)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


@dataclass(frozen=True)
class AssistantConfig:
    region: str = "us-east-1"
    lite_model_id: str = "amazon.nova-2-lite-v1:0"
    sonic_model_id: str = "amazon.nova-2-sonic-v1:0"
    default_starting_page: str = "https://mail.google.com/"
    google_client_secret_id: str = "nova-assistant/google-client"
    google_token_secret_id: str = "nova-assistant/google-token"
    allow_interactive_google_auth: bool = False
    allow_os_control: bool = False
    os_screenshot_dir: str = "/tmp/nova-assistant"
    os_allowed_actions: tuple[str, ...] = (
        "move_mouse",
        "click",
        "type_text",
        "hotkey",
        "press",
        "scroll",
        "screenshot",
    )
    os_denied_actions: tuple[str, ...] = ()
    os_denied_hotkeys: tuple[str, ...] = (
        "alt+tab",
        "alt+f4",
        "ctrl+alt+delete",
        "ctrl+shift+esc",
        "cmd+q",
        "cmd+space",
        "win+r",
    )
    os_max_type_chars: int = 800
    os_max_scroll_abs: int = 2000
    audit_log_path: str = "/tmp/nova-assistant/audit.jsonl"
    audit_cloudwatch_log_group: str = ""
    audit_cloudwatch_log_stream: str = "nova-assistant"
    audit_s3_bucket: str = ""
    audit_s3_prefix: str = "nova-assistant/audit"
    auto_mode_completion_token: str = "AUTO_TASK_COMPLETE"


def _require_google_deps() -> tuple[Any, Any, Any, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as ex:
        raise RuntimeError(
            "Google API dependencies missing. Install with: "
            "pip install -r src/nova_act/samples/requirements-nova-hackathon-assistant.txt"
        ) from ex
    return Request, Credentials, InstalledAppFlow, build


def _require_os_control_deps() -> tuple[Any, Any]:
    try:
        import mss  # type: ignore
        import pyautogui  # type: ignore
    except ImportError as ex:
        raise RuntimeError(
            "OS control dependencies missing. Install with: "
            "pip install -r src/nova_act/samples/requirements-nova-hackathon-assistant.txt"
        ) from ex
    return pyautogui, mss


def _require_audio_deps() -> tuple[Any, Any]:
    try:
        import numpy as np
        import sounddevice as sd  # type: ignore
    except ImportError as ex:
        raise RuntimeError(
            "Audio dependencies missing. Install with: "
            "pip install -r src/nova_act/samples/requirements-nova-hackathon-assistant.txt"
        ) from ex
    return np, sd


class SecretsJsonStore:
    def __init__(self, region: str) -> None:
        self.client = boto3.client("secretsmanager", region_name=region)

    def get_json(self, secret_id: str) -> dict[str, Any]:
        response = self.client.get_secret_value(SecretId=secret_id)
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise RuntimeError(f"Secret {secret_id} did not contain SecretString")
        payload = json.loads(secret_string)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Secret {secret_id} JSON payload must be an object")
        return payload

    def put_json(self, secret_id: str, payload: dict[str, Any]) -> None:
        secret_value = json.dumps(payload)
        try:
            self.client.put_secret_value(SecretId=secret_id, SecretString=secret_value)
        except self.client.exceptions.ResourceNotFoundException:
            self.client.create_secret(Name=secret_id, SecretString=secret_value)


class GoogleWorkspaceClient:
    def __init__(
        self,
        secrets: SecretsJsonStore,
        client_secret_id: str,
        token_secret_id: str,
        allow_interactive_auth: bool,
    ) -> None:
        self.secrets = secrets
        self.client_secret_id = client_secret_id
        self.token_secret_id = token_secret_id
        self.allow_interactive_auth = allow_interactive_auth

    def _build_credentials(self) -> Any:
        Request, Credentials, InstalledAppFlow, _ = _require_google_deps()

        token_payload: dict[str, Any] = {}
        try:
            token_payload = self.secrets.get_json(self.token_secret_id)
        except Exception:
            token_payload = {}

        creds = None
        if token_payload:
            creds = Credentials.from_authorized_user_info(token_payload, GOOGLE_SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.secrets.put_json(self.token_secret_id, json.loads(creds.to_json()))
            return creds

        if not self.allow_interactive_auth:
            raise RuntimeError(
                "Google token is missing or invalid and interactive auth is disabled. "
                "Set --allow_interactive_google_auth True once to bootstrap the token."
            )

        client_payload = self.secrets.get_json(self.client_secret_id)
        flow = InstalledAppFlow.from_client_config(client_payload, GOOGLE_SCOPES)
        creds = flow.run_local_server(port=0)
        self.secrets.put_json(self.token_secret_id, json.loads(creds.to_json()))
        return creds

    def search_email(self, sender: str, query: str, max_results: int) -> dict[str, Any]:
        _, _, _, build = _require_google_deps()
        creds = self._build_credentials()
        gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)

        q = f"from:{sender} {query}".strip()
        result = (
            gmail.users()
            .messages()
            .list(userId="me", q=q, maxResults=max_results)
            .execute()
        )
        messages = result.get("messages", [])
        if not isinstance(messages, list):
            messages = []

        parsed: list[dict[str, Any]] = []
        for item in messages:
            message_id = str(item.get("id", ""))
            if not message_id:
                continue

            message = (
                gmail.users()
                .messages()
                .get(userId="me", id=message_id, format="metadata", metadataHeaders=["Subject", "From"])
                .execute()
            )
            payload = message.get("payload", {})
            headers = payload.get("headers", []) if isinstance(payload, dict) else []

            subject = ""
            from_value = ""
            for header in headers:
                if not isinstance(header, dict):
                    continue
                name = str(header.get("name", "")).lower()
                value = str(header.get("value", ""))
                if name == "subject":
                    subject = value
                if name == "from":
                    from_value = value

            parsed.append(
                {
                    "id": message_id,
                    "subject": subject,
                    "from": from_value,
                    "snippet": str(message.get("snippet", "")),
                }
            )

        return {
            "ok": True,
            "source": "gmail_api",
            "sender": sender,
            "query": query,
            "count": len(parsed),
            "emails": parsed,
        }

    def create_calendar_event(
        self,
        title: str,
        start_iso: str,
        end_iso: str,
        attendees: list[str],
    ) -> dict[str, Any]:
        _, _, _, build = _require_google_deps()
        creds = self._build_credentials()
        calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)

        event = {
            "summary": title,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
            "attendees": [{"email": email} for email in attendees],
        }
        created = calendar.events().insert(calendarId="primary", body=event).execute()

        return {
            "ok": True,
            "source": "google_calendar_api",
            "event_id": str(created.get("id", "")),
            "html_link": str(created.get("htmlLink", "")),
        }


class SonicStreamAdapter:
    """Sonic stream bootstrap.

    This class intentionally focuses on session bootstrap for production wiring.
    Audio capture and event-loop wiring can vary by runtime stack.
    """

    def __init__(self, region: str, model_id: str) -> None:
        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id

    def stream_capability_status(self) -> dict[str, Any]:
        supported = hasattr(self.client, "invoke_model_with_bidirectional_stream")
        status = {
            "ok": supported,
            "feature": "invoke_model_with_bidirectional_stream",
            "supported": supported,
            "model_id": self.model_id,
            "boto3_version": boto3.__version__,
            "botocore_version": botocore.__version__,
        }
        if not supported:
            status["remediation"] = (
                "Upgrade SDKs in your environment, then retry: "
                "pip install -U boto3 botocore"
            )
        return status

    def start_stream(self, content_type: str = "audio/l16;rate=16000;channels=1") -> dict[str, Any]:
        capability = self.stream_capability_status()
        if not capability["supported"]:
            raise RuntimeError(
                "Installed boto3/botocore does not expose "
                "invoke_model_with_bidirectional_stream. "
                f"boto3={capability['boto3_version']}, "
                f"botocore={capability['botocore_version']}. "
                "Run: pip install -U boto3 botocore"
            )

        response = self.client.invoke_model_with_bidirectional_stream(
            modelId=self.model_id,
            contentType=content_type,
            accept="application/json",
        )
        return {
            "ok": True,
            "model_id": self.model_id,
            "stream_open": "stream" in response,
            "notes": "Use response['stream'] to write audio events and read transcript/TTS events.",
        }

    @staticmethod
    def _event_iter(stream_obj: Any) -> Any:
        if hasattr(stream_obj, "output_stream"):
            return stream_obj.output_stream
        if isinstance(stream_obj, dict) and "output_stream" in stream_obj:
            return stream_obj["output_stream"]
        return stream_obj

    @staticmethod
    def _send_audio_frame(stream_obj: Any, frame_bytes: bytes) -> None:
        for candidate_name in ("send_audio_event", "send", "write"):
            candidate = getattr(stream_obj, candidate_name, None)
            if callable(candidate):
                try:
                    candidate(frame_bytes)
                    return
                except TypeError:
                    continue

        input_stream = getattr(stream_obj, "input_stream", None)
        if input_stream is not None:
            for candidate_name in ("send_audio_event", "send", "write"):
                candidate = getattr(input_stream, candidate_name, None)
                if callable(candidate):
                    try:
                        candidate(frame_bytes)
                        return
                    except TypeError:
                        continue

        raise RuntimeError(
            "Could not locate writable audio stream API on bidirectional stream object"
        )

    @staticmethod
    def _end_input(stream_obj: Any) -> None:
        for candidate_name in ("end_stream", "close", "finish"):
            candidate = getattr(stream_obj, candidate_name, None)
            if callable(candidate):
                candidate()
                return

        input_stream = getattr(stream_obj, "input_stream", None)
        if input_stream is not None:
            for candidate_name in ("end_stream", "close", "finish"):
                candidate = getattr(input_stream, candidate_name, None)
                if callable(candidate):
                    candidate()
                    return

    @staticmethod
    def _extract_audio_bytes(event: Any) -> bytes | None:
        if isinstance(event, (bytes, bytearray)):
            return bytes(event)
        if isinstance(event, dict):
            for value in event.values():
                audio = SonicStreamAdapter._extract_audio_bytes(value)
                if audio is not None:
                    return audio
        if isinstance(event, list):
            for item in event:
                audio = SonicStreamAdapter._extract_audio_bytes(item)
                if audio is not None:
                    return audio
        return None

    @staticmethod
    def _extract_text(event: Any) -> str:
        chunks: list[str] = []
        if isinstance(event, dict):
            for key, value in event.items():
                if key.lower() in {"text", "transcript", "outputtext"} and isinstance(value, str):
                    chunks.append(value)
                else:
                    nested = SonicStreamAdapter._extract_text(value)
                    if nested:
                        chunks.append(nested)
        elif isinstance(event, list):
            for item in event:
                nested = SonicStreamAdapter._extract_text(item)
                if nested:
                    chunks.append(nested)
        return " ".join(chunks).strip()

    def start_live_audio_loop(
        self,
        duration_seconds: int,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        block_ms: int = 100,
    ) -> dict[str, Any]:
        _, sd = _require_audio_deps()
        response = self.client.invoke_model_with_bidirectional_stream(
            modelId=self.model_id,
            contentType=f"audio/l16;rate={input_sample_rate};channels=1",
            accept="application/json",
        )
        stream_obj = response.get("stream")
        if stream_obj is None:
            raise RuntimeError("Bidirectional stream response did not include a stream object")

        event_iter = self._event_iter(stream_obj)
        transcripts: list[str] = []
        received_audio_bytes = 0
        stop_reading = threading.Event()
        errors: list[str] = []

        def reader() -> None:
            nonlocal received_audio_bytes
            try:
                with sd.RawOutputStream(
                    samplerate=output_sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=max(1, (output_sample_rate * block_ms) // 1000),
                ) as out_stream:
                    for event in event_iter:
                        if stop_reading.is_set():
                            break
                        text = self._extract_text(event)
                        if text:
                            transcripts.append(text)
                        audio = self._extract_audio_bytes(event)
                        if audio:
                            received_audio_bytes += len(audio)
                            out_stream.write(audio)
            except Exception as ex:
                errors.append(f"reader_error: {ex}")

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        frame_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        block_frames = max(1, (input_sample_rate * block_ms) // 1000)

        def input_callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            del frames
            del time_info
            if status:
                errors.append(f"input_status: {status}")
            try:
                frame_queue.put_nowait(bytes(indata))
            except queue.Full:
                pass

        start_time = time.time()
        with sd.RawInputStream(
            samplerate=input_sample_rate,
            channels=1,
            dtype="int16",
            blocksize=block_frames,
            callback=input_callback,
        ):
            while time.time() - start_time < max(duration_seconds, 1):
                try:
                    frame = frame_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                self._send_audio_frame(stream_obj, frame)

        self._end_input(stream_obj)
        time.sleep(0.4)
        stop_reading.set()
        reader_thread.join(timeout=2.0)

        return {
            "ok": not errors,
            "duration_seconds": duration_seconds,
            "transcripts": transcripts[-20:],
            "received_audio_bytes": received_audio_bytes,
            "errors": errors,
        }


class OSControlClient:
    def __init__(self, enabled: bool, screenshot_dir: str, policy: "OSControlPolicy") -> None:
        self.enabled = enabled
        self.screenshot_dir = Path(screenshot_dir)
        self.policy = policy

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError(
                "OS control disabled. Start with --allow_os_control True to enable desktop automation."
            )

        self.policy.validate(action=action, params=params)

        pyautogui, mss = _require_os_control_deps()
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1

        if action == "move_mouse":
            x = int(params["x"])
            y = int(params["y"])
            duration_ms = int(params.get("duration_ms", 200))
            pyautogui.moveTo(x, y, duration=max(duration_ms, 0) / 1000.0)
            return {"ok": True, "action": action, "x": x, "y": y}

        if action == "click":
            button = str(params.get("button", "left"))
            double = bool(params.get("double", False))
            if double:
                pyautogui.doubleClick(button=button)
            else:
                pyautogui.click(button=button)
            return {"ok": True, "action": action, "button": button, "double": double}

        if action == "type_text":
            text = str(params["text"])
            interval_ms = int(params.get("interval_ms", 10))
            pyautogui.write(text, interval=max(interval_ms, 0) / 1000.0)
            return {"ok": True, "action": action, "chars": len(text)}

        if action == "hotkey":
            keys = params.get("keys", [])
            if not isinstance(keys, list) or not keys:
                raise ValueError("hotkey action requires a non-empty keys array")
            pyautogui.hotkey(*[str(k) for k in keys])
            return {"ok": True, "action": action, "keys": [str(k) for k in keys]}

        if action == "press":
            key = str(params["key"])
            pyautogui.press(key)
            return {"ok": True, "action": action, "key": key}

        if action == "scroll":
            amount = int(params["amount"])
            pyautogui.scroll(amount)
            return {"ok": True, "action": action, "amount": amount}

        if action == "screenshot":
            file_name = str(params.get("file_name", "desktop.png"))
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            target = self.screenshot_dir / file_name
            with mss.mss() as sct:
                sct.shot(output=str(target))
            return {"ok": True, "action": action, "path": str(target)}

        raise ValueError(f"Unsupported os_control action: {action}")


class OSControlPolicy:
    def __init__(self, config: AssistantConfig) -> None:
        self.allowed_actions = set(config.os_allowed_actions)
        self.denied_actions = set(config.os_denied_actions)
        self.denied_hotkeys = {self._normalize_combo(value) for value in config.os_denied_hotkeys}
        self.max_type_chars = max(config.os_max_type_chars, 1)
        self.max_scroll_abs = max(config.os_max_scroll_abs, 1)

    @staticmethod
    def _normalize_combo(combo: str) -> str:
        tokens = [item.strip().lower() for item in combo.split("+") if item.strip()]
        return "+".join(tokens)

    def validate(self, action: str, params: dict[str, Any]) -> None:
        if action in self.denied_actions:
            raise PermissionError(f"os_control action '{action}' is denied by policy")
        if self.allowed_actions and action not in self.allowed_actions:
            raise PermissionError(f"os_control action '{action}' is not allowed by policy")

        if action == "type_text":
            text = str(params.get("text", ""))
            if len(text) > self.max_type_chars:
                raise PermissionError(
                    f"type_text blocked: length {len(text)} exceeds max {self.max_type_chars}"
                )
            if re.search(r"(rm\s+-rf|shutdown|reboot|format\s+|mkfs)", text, flags=re.IGNORECASE):
                raise PermissionError("type_text blocked: dangerous command pattern detected")

        if action == "scroll":
            amount = int(params.get("amount", 0))
            if abs(amount) > self.max_scroll_abs:
                raise PermissionError(
                    f"scroll blocked: absolute value {abs(amount)} exceeds max {self.max_scroll_abs}"
                )

        if action == "hotkey":
            keys = params.get("keys", [])
            if not isinstance(keys, list) or not keys:
                raise PermissionError("hotkey blocked: keys array is required")
            normalized = self._normalize_combo("+".join([str(k) for k in keys]))
            if normalized in self.denied_hotkeys:
                raise PermissionError(f"hotkey blocked by policy: {normalized}")


class AuditLogger:
    def __init__(self, enabled: bool, log_path: str, config: AssistantConfig) -> None:
        self.enabled = enabled
        self.log_path = Path(log_path)
        self.config = config
        self.logs_client = boto3.client("logs", region_name=config.region)
        self.s3_client = boto3.client("s3", region_name=config.region)
        self._sequence_token: str | None = None

    def _log_cloudwatch(self, message: str, timestamp_ms: int) -> None:
        group_name = self.config.audit_cloudwatch_log_group.strip()
        if not group_name:
            return

        stream_name = self.config.audit_cloudwatch_log_stream.strip() or "nova-assistant"

        try:
            self.logs_client.create_log_group(logGroupName=group_name)
        except self.logs_client.exceptions.ResourceAlreadyExistsException:
            pass

        try:
            self.logs_client.create_log_stream(logGroupName=group_name, logStreamName=stream_name)
        except self.logs_client.exceptions.ResourceAlreadyExistsException:
            pass

        kwargs: dict[str, Any] = {
            "logGroupName": group_name,
            "logStreamName": stream_name,
            "logEvents": [{"timestamp": timestamp_ms, "message": message}],
        }
        if self._sequence_token:
            kwargs["sequenceToken"] = self._sequence_token

        response = self.logs_client.put_log_events(**kwargs)
        self._sequence_token = response.get("nextSequenceToken")

    def _log_s3(self, message: str, event_id: str) -> None:
        bucket = self.config.audit_s3_bucket.strip()
        if not bucket:
            return

        prefix = self.config.audit_s3_prefix.strip().strip("/")
        key = f"{prefix}/{datetime.now(UTC).strftime('%Y/%m/%d/%H')}/{event_id}.json"
        self.s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=message.encode("utf-8"),
            ContentType="application/json",
        )

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        event_id = str(uuid.uuid4())
        event = {
            "event_id": event_id,
            "ts": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        message = json.dumps(event)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

        timestamp_ms = int(time.time() * 1000)
        try:
            self._log_cloudwatch(message=message, timestamp_ms=timestamp_ms)
        except Exception:
            pass
        try:
            self._log_s3(message=message, event_id=event_id)
        except Exception:
            pass


class ToolExecutor:
    def __init__(self, config: AssistantConfig, starting_page: str) -> None:
        self.config = config
        self.starting_page = starting_page
        secrets = SecretsJsonStore(region=config.region)
        self.google = GoogleWorkspaceClient(
            secrets=secrets,
            client_secret_id=config.google_client_secret_id,
            token_secret_id=config.google_token_secret_id,
            allow_interactive_auth=config.allow_interactive_google_auth,
        )
        self.os_control_client = OSControlClient(
            enabled=config.allow_os_control,
            screenshot_dir=config.os_screenshot_dir,
            policy=OSControlPolicy(config=config),
        )

    def email_search(self, sender: str, query: str, max_results: int) -> dict[str, Any]:
        return self.google.search_email(sender=sender, query=query, max_results=max_results)

    def calendar_create(
        self,
        title: str,
        start_iso: str,
        end_iso: str,
        attendees: list[str],
    ) -> dict[str, Any]:
        return self.google.create_calendar_event(
            title=title,
            start_iso=start_iso,
            end_iso=end_iso,
            attendees=attendees,
        )

    def browser_act(self, task: str) -> dict[str, Any]:
        with NovaAct(starting_page=self.starting_page) as nova:
            result = nova.act_get(task)

        return {
            "ok": True,
            "source": "nova_act",
            "task": task,
            "response": result.response,
        }

    def os_control(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.os_control_client.execute(action=action, params=params)


class NovaHackathonAssistant:
    def __init__(self, config: AssistantConfig, starting_page: str) -> None:
        self.config = config
        self.executor = ToolExecutor(config=config, starting_page=starting_page)
        self.client = boto3.client("bedrock-runtime", region_name=config.region)
        self.sonic = SonicStreamAdapter(region=config.region, model_id=config.sonic_model_id)
        self.messages: list[dict[str, Any]] = []
        self.audit = AuditLogger(enabled=True, log_path=config.audit_log_path, config=config)

    @staticmethod
    def _tool_config() -> dict[str, Any]:
        return {
            "tools": [
                {
                    "toolSpec": {
                        "name": "email_search",
                        "description": "Search user emails by sender and query.",
                        "inputSchema": {
                            "json": {
                                "type": "object",
                                "properties": {
                                    "sender": {"type": "string"},
                                    "query": {"type": "string"},
                                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                                },
                                "required": ["sender", "query", "max_results"],
                            }
                        },
                    }
                },
                {
                    "toolSpec": {
                        "name": "calendar_create",
                        "description": "Create a calendar event.",
                        "inputSchema": {
                            "json": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "start_iso": {"type": "string"},
                                    "end_iso": {"type": "string"},
                                    "attendees": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["title", "start_iso", "end_iso", "attendees"],
                            }
                        },
                    }
                },
                {
                    "toolSpec": {
                        "name": "browser_act",
                        "description": "Use Nova Act to complete a browser UI task.",
                        "inputSchema": {
                            "json": {
                                "type": "object",
                                "properties": {
                                    "task": {"type": "string"},
                                },
                                "required": ["task"],
                            }
                        },
                    }
                },
                {
                    "toolSpec": {
                        "name": "os_control",
                        "description": "Execute desktop UI actions like move/click/type/screenshot.",
                        "inputSchema": {
                            "json": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": [
                                            "move_mouse",
                                            "click",
                                            "type_text",
                                            "hotkey",
                                            "press",
                                            "scroll",
                                            "screenshot",
                                        ],
                                    },
                                    "params": {"type": "object"},
                                },
                                "required": ["action", "params"],
                            }
                        },
                    }
                },
            ]
        }

    def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "email_search":
            return self.executor.email_search(
                sender=str(tool_input["sender"]),
                query=str(tool_input["query"]),
                max_results=int(tool_input["max_results"]),
            )
        if tool_name == "calendar_create":
            attendees = tool_input.get("attendees", [])
            if not isinstance(attendees, list):
                raise ValueError("attendees must be a list of email strings")
            return self.executor.calendar_create(
                title=str(tool_input["title"]),
                start_iso=str(tool_input["start_iso"]),
                end_iso=str(tool_input["end_iso"]),
                attendees=[str(item) for item in attendees],
            )
        if tool_name == "browser_act":
            return self.executor.browser_act(task=str(tool_input["task"]))
        if tool_name == "os_control":
            action = str(tool_input["action"])
            params = tool_input.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("os_control params must be an object")
            return self.executor.os_control(action=action, params=params)

        raise ValueError(f"Unknown tool requested: {tool_name}")

    def start_voice_stream(self) -> dict[str, Any]:
        return self.sonic.start_stream()

    def start_voice_live(self, duration_seconds: int, input_sample_rate: int, output_sample_rate: int) -> dict[str, Any]:
        return self.sonic.start_live_audio_loop(
            duration_seconds=duration_seconds,
            input_sample_rate=input_sample_rate,
            output_sample_rate=output_sample_rate,
        )

    @staticmethod
    def _extract_text(content: list[dict[str, Any]]) -> str:
        text_chunks: list[str] = []
        for block in content:
            text = block.get("text")
            if isinstance(text, str):
                text_chunks.append(text)
        return "\n".join(text_chunks).strip()

    @staticmethod
    def _extract_tool_uses(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tool_uses: list[dict[str, Any]] = []
        for block in content:
            tool_use = block.get("toolUse")
            if isinstance(tool_use, dict):
                tool_uses.append(tool_use)
        return tool_uses

    def reset_session(self) -> None:
        self.messages = []

    def run_turn(self, user_text: str, keep_session: bool = True) -> str:
        if keep_session:
            messages = list(self.messages)
        else:
            messages = []

        user_message = {"role": "user", "content": [{"text": user_text}]}
        messages.append(user_message)
        self.audit.log("user_turn", {"text": user_text, "keep_session": keep_session})

        first_response = self.client.converse(
            modelId=self.config.lite_model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig=self._tool_config(),
            inferenceConfig={"maxTokens": 800, "temperature": 0.2},
        )

        assistant_message = first_response["output"]["message"]
        assistant_content = assistant_message["content"]
        tool_uses = self._extract_tool_uses(assistant_content)

        if not tool_uses:
            assistant_text = self._extract_text(assistant_content)
            self.audit.log("assistant_reply", {"text": assistant_text, "tool_used": False})
            if keep_session:
                messages.append(assistant_message)
                self.messages = messages
            return assistant_text

        messages.append(assistant_message)

        tool_result_blocks: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            tool_name = str(tool_use["name"])
            tool_input = tool_use.get("input", {})
            tool_use_id = str(tool_use["toolUseId"])

            try:
                if not isinstance(tool_input, dict):
                    raise ValueError("Tool input was not a JSON object")
                result_payload = self._execute_tool(tool_name, tool_input)
                status = "success"
            except Exception as ex:
                result_payload = {"ok": False, "error": str(ex), "tool": tool_name}
                status = "error"

            self.audit.log(
                "tool_result",
                {
                    "tool": tool_name,
                    "status": status,
                    "tool_use_id": tool_use_id,
                    "input": tool_input,
                    "result": result_payload,
                },
            )

            tool_result_blocks.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "status": status,
                        "content": [{"json": result_payload}],
                    }
                }
            )

        messages.append({"role": "user", "content": tool_result_blocks})

        final_response = self.client.converse(
            modelId=self.config.lite_model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig=self._tool_config(),
            inferenceConfig={"maxTokens": 800, "temperature": 0.2},
        )

        final_content = final_response["output"]["message"]["content"]
        final_text = self._extract_text(final_content)
        self.audit.log("assistant_reply", {"text": final_text, "tool_used": True})
        if keep_session:
            messages.append(final_response["output"]["message"])
            self.messages = messages
        return final_text

    def run_full_auto(self, goal: str, max_steps: int) -> dict[str, Any]:
        if not goal.strip():
            raise ValueError("full_auto requires a non-empty --goal")

        completion_token = self.config.auto_mode_completion_token
        self.reset_session()

        prompt = (
            "Execute this objective end-to-end with available tools: "
            f"{goal}. "
            "Work in concrete steps and do not ask the user follow-up questions unless absolutely required. "
            f"When the objective is complete, include the exact token '{completion_token}' in your response."
        )

        steps: list[dict[str, Any]] = []
        completed = False
        for index in range(1, max_steps + 1):
            reply = self.run_turn(prompt, keep_session=True)
            steps.append({"step": index, "reply": reply})
            self.audit.log("auto_step", {"step": index, "reply": reply})

            if completion_token in reply:
                completed = True
                break

            prompt = (
                "Continue from current context and execute the next best actions. "
                f"Only emit '{completion_token}' when the objective is actually complete."
            )

        return {
            "ok": completed,
            "completion_token": completion_token,
            "max_steps": max_steps,
            "steps": steps,
        }


def main(
    interactive: bool = True,
    text: str = "",
    region: str = "us-east-1",
    lite_model_id: str = "amazon.nova-2-lite-v1:0",
    sonic_model_id: str = "amazon.nova-2-sonic-v1:0",
    starting_page: str = "https://mail.google.com/",
    google_client_secret_id: str = "nova-assistant/google-client",
    google_token_secret_id: str = "nova-assistant/google-token",
    allow_interactive_google_auth: bool = False,
    allow_os_control: bool = False,
    os_screenshot_dir: str = "/tmp/nova-assistant",
    os_allowed_actions: str = "move_mouse,click,type_text,hotkey,press,scroll,screenshot",
    os_denied_actions: str = "",
    os_denied_hotkeys: str = "alt+tab,alt+f4,ctrl+alt+delete,ctrl+shift+esc,cmd+q,cmd+space,win+r",
    os_max_type_chars: int = 800,
    os_max_scroll_abs: int = 2000,
    audit_log_path: str = "/tmp/nova-assistant/audit.jsonl",
    audit_cloudwatch_log_group: str = "",
    audit_cloudwatch_log_stream: str = "nova-assistant",
    audit_s3_bucket: str = "",
    audit_s3_prefix: str = "nova-assistant/audit",
    full_auto: bool = False,
    goal: str = "",
    max_auto_steps: int = 12,
    auto_mode_completion_token: str = "AUTO_TASK_COMPLETE",
    start_sonic_stream: bool = False,
    start_sonic_live: bool = False,
    sonic_live_seconds: int = 20,
    sonic_input_rate: int = 16000,
    sonic_output_rate: int = 24000,
) -> None:
    """Run the production-oriented Nova assistant."""
    config = AssistantConfig(
        region=region,
        lite_model_id=lite_model_id,
        sonic_model_id=sonic_model_id,
        default_starting_page=starting_page,
        google_client_secret_id=google_client_secret_id,
        google_token_secret_id=google_token_secret_id,
        allow_interactive_google_auth=allow_interactive_google_auth,
        allow_os_control=allow_os_control,
        os_screenshot_dir=os_screenshot_dir,
        os_allowed_actions=tuple([item.strip() for item in os_allowed_actions.split(",") if item.strip()]),
        os_denied_actions=tuple([item.strip() for item in os_denied_actions.split(",") if item.strip()]),
        os_denied_hotkeys=tuple([item.strip() for item in os_denied_hotkeys.split(",") if item.strip()]),
        os_max_type_chars=os_max_type_chars,
        os_max_scroll_abs=os_max_scroll_abs,
        audit_log_path=audit_log_path,
        audit_cloudwatch_log_group=audit_cloudwatch_log_group,
        audit_cloudwatch_log_stream=audit_cloudwatch_log_stream,
        audit_s3_bucket=audit_s3_bucket,
        audit_s3_prefix=audit_s3_prefix,
        auto_mode_completion_token=auto_mode_completion_token,
    )
    assistant = NovaHackathonAssistant(config=config, starting_page=starting_page)

    if start_sonic_stream:
        stream_status = assistant.start_voice_stream()
        print(json.dumps(stream_status, indent=2))

    if start_sonic_live:
        live_result = assistant.start_voice_live(
            duration_seconds=sonic_live_seconds,
            input_sample_rate=sonic_input_rate,
            output_sample_rate=sonic_output_rate,
        )
        print(json.dumps(live_result, indent=2))
        return

    if full_auto:
        auto_result = assistant.run_full_auto(goal=goal, max_steps=max_auto_steps)
        print(json.dumps(auto_result, indent=2))
        return

    if interactive:
        print("Nova Hackathon Assistant (text-mode shell)")
        print("Type 'exit' to quit.")
        print("Type '/voice-start' to validate Nova 2 Sonic stream bootstrap.")
        print("Type '/voice-live <seconds>' to run live mic/speaker Sonic loop.")
        while True:
            user_text = input("\nYou> ").strip()
            if user_text.lower() in {"exit", "quit"}:
                break
            if not user_text:
                continue
            if user_text == "/voice-start":
                print(json.dumps(assistant.start_voice_stream(), indent=2))
                continue
            if user_text.startswith("/voice-live"):
                parts = user_text.split(" ")
                duration = sonic_live_seconds
                if len(parts) == 2 and parts[1].isdigit():
                    duration = int(parts[1])
                print(
                    json.dumps(
                        assistant.start_voice_live(
                            duration_seconds=duration,
                            input_sample_rate=sonic_input_rate,
                            output_sample_rate=sonic_output_rate,
                        ),
                        indent=2,
                    )
                )
                continue
            if user_text.startswith("/auto "):
                auto_goal = user_text.replace("/auto ", "", 1).strip()
                auto_result = assistant.run_full_auto(goal=auto_goal, max_steps=max_auto_steps)
                print(json.dumps(auto_result, indent=2))
                continue
            reply = assistant.run_turn(user_text)
            print(f"Assistant> {reply}")
        return

    if not text.strip():
        raise ValueError("When interactive=False, provide a non-empty --text prompt")

    reply = assistant.run_turn(text.strip())
    print(reply)


if __name__ == "__main__":
    fire.Fire(main)
