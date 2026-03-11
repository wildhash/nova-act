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
"""Digital robot runtime built on Nova assistant primitives.

This module wraps the production sample assistant with a mission runtime that is
intended to be evolved beyond hackathon scope.

Key ideas:
- Mission + constraints are explicit inputs.
- Execution follows an O-P-A-V-L cycle: Observe, Plan, Act, Verify, Learn.
- Checkpoints are persisted so sessions can resume safely.
- Autonomous and supervised modes are both supported.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fire  # type: ignore

from nova_act.samples.nova_hackathon_assistant import (
    AssistantConfig,
    NovaHackathonAssistant,
)


@dataclass
class MissionSpec:
    objective: str
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    max_cycles: int = 10
    mission_pack: str = "tri-domain"
    deadline_days: int = 3


@dataclass(frozen=True)
class MissionPack:
    name: str
    description: str
    default_constraints: tuple[str, ...]
    default_success_criteria: tuple[str, ...]


def _mission_pack(name: str, deadline_days: int) -> MissionPack:
    normalized = name.strip().lower()
    if normalized == "browser":
        return MissionPack(
            name="browser",
            description="Browser-first operator with API fallback.",
            default_constraints=(
                "Prefer browser_act for UI workflows and API tools for structured data.",
                "Ask for cowork help for login, MFA, CAPTCHA, or account selection.",
            ),
            default_success_criteria=(
                "Browser workflows complete with evidence.",
                "No unresolved defects in browser path.",
                f"Delivery plan fits within {max(deadline_days, 1)} days.",
            ),
        )

    if normalized == "workspace":
        return MissionPack(
            name="workspace",
            description="Email/calendar/API assistant with cowork collaboration.",
            default_constraints=(
                "Prefer API tools for reliability and auditability.",
                "When blocked by human permissions, ask user with a clear cowork request.",
            ),
            default_success_criteria=(
                "Email and calendar tasks complete end-to-end.",
                "Assistant communicates clearly with user and asks for cowork actions when needed.",
                f"Delivery plan fits within {max(deadline_days, 1)} days.",
            ),
        )

    if normalized == "desktop":
        return MissionPack(
            name="desktop",
            description="Desktop operator using guarded OS control.",
            default_constraints=(
                "Use os_control only for approved action scopes.",
                "Never bypass safety policy or deny-list restrictions.",
                "Ask for cowork assistance before privileged local actions.",
            ),
            default_success_criteria=(
                "Desktop tasks complete with audit evidence.",
                "No policy violations or unsafe command sequences.",
                f"Delivery plan fits within {max(deadline_days, 1)} days.",
            ),
        )

    return MissionPack(
        name="tri-domain",
        description="Unified browser + workspace + desktop digital robot.",
        default_constraints=(
            "Prefer API tools first, browser_act second, os_control only when necessary.",
            "Stay in communication with the user and request cowork actions when blocked.",
            "Default to supervised operation; allow explicit full-auto activation.",
            "Report any suspected defects immediately as BUG_REPORT lines.",
        ),
        default_success_criteria=(
            "Can operate browser, email, calendar, and local programs end-to-end.",
            "Can request cowork actions clearly when human participation is required.",
            "Runs stably with no known unresolved defects.",
            f"Execution plan is realistic for completion within {max(deadline_days, 1)} days.",
        ),
    )


@dataclass
class CycleResult:
    cycle: int
    phase: str
    response: str
    approved: bool = True
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class CheckpointStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            return loaded
        return None


class DigitalRobotRuntime:
    def __init__(
        self,
        assistant: NovaHackathonAssistant,
        checkpoint_store: CheckpointStore,
        mode: str,
    ) -> None:
        self.assistant = assistant
        self.checkpoint_store = checkpoint_store
        self.mode = mode

    @staticmethod
    def _join_lines(lines: list[str]) -> str:
        cleaned = [line.strip() for line in lines if line.strip()]
        if not cleaned:
            return "None"
        return "\n".join([f"- {line}" for line in cleaned])

    def _phase_prompt(self, mission: MissionSpec, phase: str, prior_summary: str) -> str:
        constraints = self._join_lines(mission.constraints)
        criteria = self._join_lines(mission.success_criteria)
        collaboration_protocol = (
            "If human action is required, add a single line: "
            "COWORK_REQUEST: <exact action user should take>. "
            "If a bug is found, add: BUG_REPORT: <issue and likely cause>."
        )

        if phase == "observe":
            return (
                "You are a digital robot operating inside a computer. "
                "Observe current state, identify unknowns, and propose what facts to gather next.\n"
                f"Objective:\n{mission.objective}\n"
                f"Constraints:\n{constraints}\n"
                f"Success criteria:\n{criteria}\n"
                f"Prior summary:\n{prior_summary}\n"
                f"{collaboration_protocol}\n"
                "Output concise situational awareness and risks."
            )

        if phase == "plan":
            return (
                "Create a concrete execution plan with numbered steps. "
                "Prefer API tools first, then browser_act, then os_control only if needed.\n"
                f"Objective:\n{mission.objective}\n"
                f"Constraints:\n{constraints}\n"
                f"Success criteria:\n{criteria}\n"
                f"Prior summary:\n{prior_summary}\n"
                f"{collaboration_protocol}\n"
                "Output steps, expected evidence, and stop conditions."
            )

        if phase == "act":
            return (
                "Execute the next best actions now using available tools. "
                "Be explicit, safe, and auditable.\n"
                f"Objective:\n{mission.objective}\n"
                f"Constraints:\n{constraints}\n"
                f"Success criteria:\n{criteria}\n"
                f"Prior summary:\n{prior_summary}\n"
                f"{collaboration_protocol}\n"
                "Perform work and report what changed."
            )

        if phase == "verify":
            return (
                "Verify progress against success criteria and identify remaining gaps.\n"
                f"Objective:\n{mission.objective}\n"
                f"Success criteria:\n{criteria}\n"
                f"Prior summary:\n{prior_summary}\n"
                f"{collaboration_protocol}\n"
                "Output: criteria status (met/not met), confidence, and next move."
            )

        return (
            "Extract durable lessons for future runs.\n"
            f"Objective:\n{mission.objective}\n"
            f"Prior summary:\n{prior_summary}\n"
            f"{collaboration_protocol}\n"
            "Output: what worked, what failed, what to automate next."
        )

    def _supervised_gate(self, phase: str, response: str) -> bool:
        if self.mode != "supervised":
            return True

        print("\n----- SUPERVISED CHECKPOINT -----")
        print(f"Phase: {phase}")
        print(response)
        answer = input("Approve next phase? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    def run_mission(self, mission: MissionSpec, resume: bool = False) -> dict[str, Any]:
        if not mission.objective.strip():
            raise ValueError("Mission objective cannot be empty")

        cycles: list[CycleResult] = []
        prior_summary = "No prior context yet."

        if resume:
            loaded = self.checkpoint_store.load()
            if loaded:
                prior_summary = str(loaded.get("latest_summary", prior_summary))

        phases = ["observe", "plan", "act", "verify", "learn"]

        for cycle_idx in range(1, mission.max_cycles + 1):
            for phase in phases:
                prompt = self._phase_prompt(mission=mission, phase=phase, prior_summary=prior_summary)
                response = self.assistant.run_turn(prompt, keep_session=True)
                approved = self._supervised_gate(phase=phase, response=response)

                result = CycleResult(
                    cycle=cycle_idx,
                    phase=phase,
                    response=response,
                    approved=approved,
                )
                cycles.append(result)

                if not approved:
                    payload = {
                        "status": "paused",
                        "mode": self.mode,
                        "mission": asdict(mission),
                        "latest_summary": response,
                        "cycles": [asdict(item) for item in cycles],
                    }
                    self.checkpoint_store.save(payload)
                    return payload

                prior_summary = response

                if phase == "verify":
                    lower = response.lower()
                    criteria_met = (
                        "all criteria met" in lower
                        or "success criteria met" in lower
                        or "objective complete" in lower
                    )
                    if criteria_met:
                        break

            if "objective complete" in prior_summary.lower() or "all criteria met" in prior_summary.lower():
                break

        final_payload = {
            "status": "completed",
            "mode": self.mode,
            "mission": asdict(mission),
            "latest_summary": prior_summary,
            "cycles": [asdict(item) for item in cycles],
        }
        self.checkpoint_store.save(final_payload)
        return final_payload


class DigitalRobotShell:
    def __init__(self, runtime: DigitalRobotRuntime) -> None:
        self.runtime = runtime
        self.full_auto_enabled = False

    def interactive_loop(self, default_max_cycles: int) -> None:
        print("Digital Robot Runtime")
        print("Commands:")
        print("  /run <objective>")
        print("  /resume")
        print("  /auto-on")
        print("  /auto-off")
        print("  /voice-status")
        print("  /voice-start")
        print("  /voice-live <seconds>")
        print("  /system <shell command>")
        print("  /exit")

        while True:
            user_input = input("\nrobot> ").strip()
            if not user_input:
                continue
            if user_input in {"/exit", "exit", "quit"}:
                return
            if user_input == "/auto-on":
                self.full_auto_enabled = True
                self.runtime.mode = "autonomous"
                print("Full-auto enabled (autonomous mode).")
                continue
            if user_input == "/auto-off":
                self.full_auto_enabled = False
                self.runtime.mode = "supervised"
                print("Full-auto disabled (supervised mode).")
                continue
            if user_input == "/voice-status":
                print(json.dumps(self.runtime.assistant.sonic.stream_capability_status(), indent=2))
                continue
            if user_input.startswith("/system "):
                command = user_input.replace("/system ", "", 1).strip()
                if not command:
                    print("Usage: /system <shell command>")
                    continue
                completed = subprocess.run(
                    command,
                    shell=True,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.stdout.strip():
                    print(completed.stdout.strip())
                if completed.stderr.strip():
                    print(completed.stderr.strip())
                print(f"[exit_code={completed.returncode}]")
                continue
            if user_input == "/voice-start":
                try:
                    print(json.dumps(self.runtime.assistant.start_voice_stream(), indent=2))
                except Exception as ex:
                    fallback = self.runtime.assistant.sonic.stream_capability_status()
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "command": "/voice-start",
                                "error": str(ex),
                                "capability": fallback,
                            },
                            indent=2,
                        )
                    )
                continue
            if user_input.startswith("/voice-live"):
                parts = user_input.split(" ")
                duration = 20
                if len(parts) == 2 and parts[1].isdigit():
                    duration = int(parts[1])
                try:
                    print(
                        json.dumps(
                            self.runtime.assistant.start_voice_live(
                                duration_seconds=duration,
                                input_sample_rate=16000,
                                output_sample_rate=24000,
                            ),
                            indent=2,
                        )
                    )
                except Exception as ex:
                    fallback = self.runtime.assistant.sonic.stream_capability_status()
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "command": "/voice-live",
                                "error": str(ex),
                                "capability": fallback,
                            },
                            indent=2,
                        )
                    )
                continue
            if user_input.startswith("/run "):
                objective = user_input.replace("/run ", "", 1).strip()
                mission = MissionSpec(
                    objective=objective,
                    constraints=[
                        "Stay in communication with the user.",
                        "Ask for cowork actions when blocked by auth or permissions.",
                    ],
                    success_criteria=[
                        "Objective complete",
                        "No known unresolved defects",
                    ],
                    max_cycles=default_max_cycles,
                    mission_pack="tri-domain",
                    deadline_days=3,
                )
                result = self.runtime.run_mission(mission=mission, resume=False)
                print(json.dumps(result, indent=2))
                continue
            if user_input == "/resume":
                loaded = self.runtime.checkpoint_store.load()
                if not loaded:
                    print("No checkpoint found.")
                    continue
                mission_data = loaded.get("mission", {})
                mission = MissionSpec(
                    objective=str(mission_data.get("objective", "")),
                    constraints=[str(item) for item in mission_data.get("constraints", [])],
                    success_criteria=[str(item) for item in mission_data.get("success_criteria", [])],
                    max_cycles=int(mission_data.get("max_cycles", default_max_cycles)),
                )
                result = self.runtime.run_mission(mission=mission, resume=True)
                print(json.dumps(result, indent=2))
                continue

            print(
                "Unknown command. Use /run, /resume, /auto-on, /auto-off, "
                "/voice-status, /voice-start, /voice-live, /system, or /exit."
            )


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main(
    objective: str = "",
    constraints: str = "",
    success_criteria: str = "",
    mode: str = "supervised",
    mission_pack: str = "tri-domain",
    deadline_days: int = 3,
    interactive: bool = True,
    resume: bool = False,
    max_cycles: int = 10,
    checkpoint_path: str = "/tmp/nova-assistant/digital-robot-checkpoint.json",
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
) -> None:
    """Run the digital robot runtime.

    Example:
    PYTHONPATH=src python -m nova_act.samples.digital_robot_system \
      --objective "Triage inbox and prepare a follow-up calendar plan" \
      --success_criteria "summary delivered,meeting created" \
      --mode supervised \
      --interactive False
    """

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"supervised", "autonomous"}:
        raise ValueError("mode must be one of: supervised, autonomous")

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
        os_allowed_actions=tuple(_parse_csv(os_allowed_actions)),
        os_denied_actions=tuple(_parse_csv(os_denied_actions)),
        os_denied_hotkeys=tuple(_parse_csv(os_denied_hotkeys)),
        os_max_type_chars=os_max_type_chars,
        os_max_scroll_abs=os_max_scroll_abs,
        audit_log_path=audit_log_path,
        audit_cloudwatch_log_group=audit_cloudwatch_log_group,
        audit_cloudwatch_log_stream=audit_cloudwatch_log_stream,
        audit_s3_bucket=audit_s3_bucket,
        audit_s3_prefix=audit_s3_prefix,
    )

    assistant = NovaHackathonAssistant(config=config, starting_page=starting_page)
    runtime = DigitalRobotRuntime(
        assistant=assistant,
        checkpoint_store=CheckpointStore(path=checkpoint_path),
        mode=normalized_mode,
    )

    if interactive:
        shell = DigitalRobotShell(runtime=runtime)
        shell.interactive_loop(default_max_cycles=max_cycles)
        return

    if not objective.strip() and not resume:
        raise ValueError("Provide --objective or set --resume True")

    pack = _mission_pack(name=mission_pack, deadline_days=deadline_days)
    merged_constraints = list(pack.default_constraints)
    merged_constraints.extend(_parse_csv(constraints))

    merged_success_criteria = list(pack.default_success_criteria)
    merged_success_criteria.extend(_parse_csv(success_criteria))

    mission = MissionSpec(
        objective=objective.strip(),
        constraints=merged_constraints,
        success_criteria=merged_success_criteria,
        max_cycles=max_cycles,
        mission_pack=pack.name,
        deadline_days=deadline_days,
    )
    result = runtime.run_mission(mission=mission, resume=resume)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    fire.Fire(main)
